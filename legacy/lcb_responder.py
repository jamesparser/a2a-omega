#!/usr/bin/env python3
"""AgentMail-lane responder for the liberclaw/BetterClaw inboxes (gaming laptop).

AgentMail is the LAST transport in the hub chain (agentverse -> e2a -> agentmail),
so anything arriving here already failed twice. This responder exists to make
sure such traffic is never silently dropped and never falsely claimed as done.

What it does per new [a2a] message:
  1. FORWARDS the task into that agent's Agentverse mailbox, where the agent's
     own brain slot produces a real answer (the answering loop lives in
     omega_poller.py, one actor per agent, supervised on the VPS).
  2. REPLIES on the AgentMail lane with an honest status - what actually
     happened, never "received and processed" when nothing was processed.
  3. LOGS the prompt loudly, because fallback traffic means the primary
     transports are degraded and an operator should know.

Differences from the original:
  * single-instance lock: the watchdog could start a second copy, and two
    responders on one inbox produced duplicate replies. A second instance now
    exits immediately.
  * credentials are read from lcb_agents.json (never hardcoded in source).
  * seen-state is persisted in a finally block and each message is isolated in
    its own try/except, so one bad message cannot lose the batch's dedupe state
    or block the rest of the inbox.

Config: lcb_agents.json next to this file (gitignored), shape:
    {"agents": [{"peer": "agent-one",
                 "inbox": "agent-one@agentmail.to",
                 "key_env": "AM_KEY_AGENT_ONE"}],
     "reply_to": "you@agentmail.to",
     "poll_sec": 15}
Keys come from the environment (or an .env file loaded by the launcher), so no
secret is ever written into this file or the repo.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
SEEN_DIR = os.path.join(ROOT, "lcb_seen")
LOCK_FILE = os.path.join(ROOT, "lcb_responder.lock")
CONFIG_FILE = os.path.join(ROOT, "lcb_agents.json")
LOG = os.environ.get(
    "LCB_LOG", os.path.join(ROOT, "lcb_a2a_replies.log"))

AM_BASE = os.environ.get("AGENTMAIL_BASE", "https://api.agentmail.to")
# No default address. A default here would mail an installer's fallback-lane
# status reports to the maintainer's inbox.
REPLY_TO = os.environ.get("LCB_REPLY_TO", "").strip()


# ------------------------------------------------------------- single instance
def acquire_lock():
    """Exclusive lock so the watchdog can never run two responders at once.

    Returns an open file handle (must be kept alive) or None if another
    instance already holds the lock.
    """
    try:
        fh = open(LOCK_FILE, "a+")
    except OSError as e:
        print(f"cannot open lock file: {e}", flush=True)
        return None
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


# ------------------------------------------------------------------- plumbing
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        log(f"FATAL: cannot read {CONFIG_FILE}: {e}")
        return None, []
    agents = []
    for a in cfg.get("agents", []):
        peer, inbox = a.get("peer"), a.get("inbox")
        key = ""
        if a.get("key"):
            key = a["key"]
        elif a.get("key_env"):
            key = os.environ.get(a["key_env"], "")
        if not (peer and inbox and key):
            log(f"  skipping {peer}: missing peer/inbox/key (key_env={a.get('key_env')})")
            continue
        agents.append((peer, inbox, key))
    return cfg, agents


def load_seen(peer):
    fp = os.path.join(SEEN_DIR, f"{peer}.json")
    if os.path.exists(fp):
        try:
            return set(json.load(open(fp, encoding="utf-8")))
        except Exception:
            return set()
    return None


def save_seen(peer, s):
    os.makedirs(SEEN_DIR, exist_ok=True)
    with open(os.path.join(SEEN_DIR, f"{peer}.json"), "w", encoding="utf-8") as f:
        json.dump(sorted(x for x in s if x), f)


def am_get(inbox, key):
    url = f"{AM_BASE}/v0/inboxes/{urllib.parse.quote(inbox)}/messages?limit=25"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
        return d.get("data", d if isinstance(d, list) else [])
    except urllib.error.HTTPError as e:
        log(f"  am_get {inbox} HTTP {e.code}")
        return []
    except Exception as e:
        log(f"  am_get {inbox} error: {type(e).__name__}")
        return []


def am_send(inbox, key, subject, text):
    body = json.dumps({"to": [REPLY_TO], "subject": subject, "text": text}).encode()
    req = urllib.request.Request(
        f"{AM_BASE}/v0/inboxes/{urllib.parse.quote(inbox)}/messages/send",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode(errors='replace')[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ------------------------------------------------- agentverse bridge (answers)
def _load_agentverse_key():
    """Make A2A_AGENTVERSE_API_KEY available to a2a_agentverse without the
    launcher having to pre-export it. Reads agentverse.env then .env."""
    if os.environ.get("A2A_AGENTVERSE_API_KEY"):
        return os.environ["A2A_AGENTVERSE_API_KEY"]
    for fn, names in (("agentverse.env", ("AGENTVERSE_API_KEY", "A2A_AGENTVERSE_API_KEY")),
                      (".env", ("A2A_AGENTVERSE_API_KEY",))):
        fp = os.path.join(ROOT, fn)
        if not os.path.exists(fp):
            continue
        try:
            for line in open(fp, encoding="utf-8", errors="replace"):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() in names and v.strip():
                    os.environ["A2A_AGENTVERSE_API_KEY"] = v.strip()
                    return v.strip()
        except OSError:
            continue
    return ""


def forward_to_agentverse(peer, prompt, task_id):
    """Best-effort: push this task into the agent's Agentverse mailbox so its own
    brain answers it for real. Returns (ok, detail)."""
    if not _load_agentverse_key():
        return False, "no Agentverse API key available on this host"
    try:
        sys.path.insert(0, ROOT)
        import a2a_agentverse as V
    except Exception as e:
        return False, f"agentverse module unavailable ({type(e).__name__})"

    av_addr = ""
    try:
        with open(os.path.join(ROOT, "config", "peers.json"), encoding="utf-8") as f:
            av_addr = (json.load(f).get(peer) or {}).get("agentverse_address", "")
    except Exception:
        pass
    if not av_addr:
        return False, f"no agentverse_address for {peer} in config/peers.json"

    text = (f"[a2a] task from=lcb-bridge (agentmail fallback): {prompt} "
            f"(original task_id={task_id})")
    res = V.av_send(peer, text, peer_address=av_addr)
    if isinstance(res, dict) and res.get("ok"):
        return True, "forwarded to the Agentverse lane"
    err = (res or {}).get("error", "unknown") if isinstance(res, dict) else str(res)
    return False, f"agentverse forward failed: {str(err)[:160]}"


# ---------------------------------------------------------------- reply text
def reply_text(peer, inbox, task_id, prompt, forwarded, detail):
    """Honest status. Never claims work was processed when it was not."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if forwarded:
        status = "forwarded_for_answer"
        result = (f"FORWARDED from {peer}: task {task_id} was relayed to this agent's "
                  f"Agentverse mailbox, where its own brain produces the substantive "
                  f"answer (transcribed to omega_poller_{peer}.log). This AgentMail "
                  f"lane does not answer directly.")
    else:
        status = "not_answered"
        result = (f"NOT ANSWERED by {peer}: task {task_id} arrived on the AgentMail "
                  f"fallback lane and could not be relayed to a brain ({detail}). "
                  f"No answer was produced - this needs operator attention.")
    body = json.dumps({
        "task_id": task_id,
        "agent": peer,
        "replied_by": inbox,
        "status": status,
        "result": result,
        "prompt": prompt[:300],
        "ts": now,
    })
    return f"[a2a] {peer} reply {task_id}", body


# ------------------------------------------------------------------- serving
def serve_once(peer, inbox, key):
    msgs = am_get(inbox, key)
    if not msgs:
        return
    mids = [m.get("message_id") or m.get("id") for m in msgs]
    seen = load_seen(peer)
    if seen is None:
        save_seen(peer, set(m for m in mids if m))
        log(f"{peer} ({inbox}): responder armed; {len(msgs)} pre-existing marked seen")
        return
    new = [m for m in msgs if (m.get("message_id") or m.get("id")) not in seen]
    if not new:
        return
    try:
        for m in new:
            mid = m.get("message_id") or m.get("id")
            try:
                handled = handle_one(peer, inbox, key, m, mid)
            except Exception as e:
                log(f"{peer}: handler error {str(mid)[:24]}: {type(e).__name__}: {e}")
                handled = False
            if handled:
                seen.add(mid)
            else:
                # leave unseen so a later cycle retries instead of losing it
                log(f"{peer}: {str(mid)[:24]} left for retry")
    finally:
        save_seen(peer, seen)


def handle_one(peer, inbox, key, m, mid):
    """Returns True when the message is fully handled (safe to mark seen)."""
    subj = (m.get("subject") or "").lower()
    text = m.get("bodyText") or m.get("text") or m.get("preview") or ""
    frm = (m.get("from") or "").lower()

    # AgentMail echoes our own outbound sends back into the inbox listing, so
    # never reply to ourselves (that is what caused the echo cascade).
    if inbox.lower() in frm:
        return True
    t0 = text.lstrip()
    if t0.startswith("{"):
        try:
            d0 = json.loads(t0)
            if str(d0.get("replied_by") or "").lower() == inbox.lower():
                return True
        except Exception:
            pass
    if subj.startswith(f"[a2a] {peer} reply "):
        return True
    if "[a2a]" not in subj:
        return True

    task_id, prompt = None, text[:300]
    t = text.lstrip()
    if t.startswith("{"):
        try:
            d = json.loads(t)
            task_id = d.get("id") or d.get("task_id")
            prompt = d.get("prompt") or text[:300]
        except Exception:
            pass
    if not task_id:
        task_id = "task-" + str(mid).replace("<", "").replace(">", "").replace("@", "_")[:32]

    # Fallback traffic is a signal: the primary transports failed for this peer.
    log(f"{peer}: FALLBACK-LANE task {task_id} (agentverse/e2a were skipped or failed) "
        f"prompt={prompt[:160]!r}")

    forwarded, detail = forward_to_agentverse(peer, prompt, task_id)
    subj_out, body = reply_text(peer, inbox, task_id, prompt, forwarded, detail)
    res = am_send(inbox, key, subj_out, body)
    if isinstance(res, dict) and "error" in res:
        log(f"{peer}: REPLY FAILED for {task_id}: {res['error']}")
        return False
    log(f"{peer}: REPLIED to {REPLY_TO} for {task_id} status="
        f"{'forwarded' if forwarded else 'NOT-ANSWERED'} "
        f"(in={str(mid)[:24]} out={str(res.get('message_id','?'))[:24]})")
    return True


def main():
    global REPLY_TO
    lock = acquire_lock()
    if lock is None:
        print("another lcb_responder is already running; exiting "
              "(single-instance lock held)", flush=True)
        return 0

    cfg, agents = load_config()
    if not agents:
        log("FATAL: no usable agents in config; exiting")
        return 2
    REPLY_TO = cfg.get("reply_to", "").strip()
    poll_sec = int(cfg.get("poll_sec", os.environ.get("LCB_POLL_SEC", "15")))

    log(f"lcb_responder starting (pid={os.getpid()}): {len(agents)} inbox(es), "
        f"poll every {poll_sec}s, reply_to={REPLY_TO}")
    while True:
        for peer, inbox, key in agents:
            try:
                serve_once(peer, inbox, key)
            except Exception as e:
                log(f"{peer}: cycle error: {type(e).__name__}: {e}")
        time.sleep(poll_sec)


if __name__ == "__main__":
    sys.exit(main())
