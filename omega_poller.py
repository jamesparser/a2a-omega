#!/usr/bin/env python3
"""omega Agentverse poller - the FIX for the down pollers (a2a-omega).

Drop this on each agent's machine/VPS and run it. It:
  1. POLLS this machine's agent's Agentverse mailbox (GET /v2/agents/{addr}/mailbox)
  2. For every NEW [a2a] message, DECODES it and REPLIES to the SENDER's mailbox,
     signed AS THIS AGENT (its own Ed25519 identity) - so the reply carries the
     real sender identity, not the hub's.
  3. Optionally (A2A_ANSWER_* set) routes question-like messages through an LLM
     and includes the answer in the reply.
  4. ACKs (DELETE) the envelope so Agentverse stops redelivering.

It is stdlib-only + the uagents/uagents_core SDK for signing. One process can act
on a single agent (default) or a comma-list of agents (fix several pollers at once).

CONFIG (env, none committed) -------------------------------
  A2A_AGENTVERSE_ENV      path to agentverse.env holding AGENTVERSE_API_KEY
                          (default /opt/omega/workspace/notes/agentverse.env)
  A2A_AGENTVERSE_API_KEY  the Agentverse JWT directly (overrides the file)
  A2A_OWN_AGENTS          comma list of agent names THIS machine acts for.
                          default "omega-man". For the 4 LC/BC:
                          "my-liberclaw,my-betterclaw,omega-liberclaw,omega-betterclaw"
  A2A_SEED_PREFIX         default "a2a-omega-e2a-fleet-" (identity seed = prefix+name)
  A2A_POLL_SEC            mailbox poll interval, default 2
  A2A_ANSWER_BASE         (optional) OpenAI-compatible /chat/completions base URL
  A2A_ANSWER_KEY          (optional) Bearer key for that endpoint
  A2A_ANSWER_MODEL        (optional) model id; if all three set, questions get answered

RUN ---------------------------------------------------------
  python omega_poller.py --once     # single cycle, then exit (smoke test)
  python omega_poller.py           # run forever (log to stdout; nohup it)

On the omega VPS:
  cd /opt/omega/workspace/a2a-omega
  python3 -m pip install uagents uagents_core   # if not already
  A2A_AGENTVERSE_ENV=/opt/omega/workspace/notes/agentverse.env \
  nohup python3 omega_poller.py > omega_poller.log 2>&1 &
"""
import json
import os
import re
import sys
import time
import uuid
import secrets
import urllib.request
import urllib.error
from datetime import datetime

BASE = os.environ.get("A2A_AGENTVERSE_BASE", "https://agentverse.ai").rstrip("/")
SEED_PREFIX = os.environ.get("A2A_SEED_PREFIX", "a2a-omega-e2a-fleet-")
POLL_SEC = int(os.environ.get("A2A_POLL_SEC", "2"))
KEY_FILE = os.environ.get("A2A_AGENTVERSE_ENV", "/opt/omega/workspace/notes/agentverse.env")
OWN_AGENTS = [a.strip() for a in os.environ.get("A2A_OWN_AGENTS", "omega-man").split(",") if a.strip()]
STATE_DIR = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_key():
    k = os.environ.get("A2A_AGENTVERSE_API_KEY", "").strip()
    if k:
        return k
    if os.path.exists(KEY_FILE):
        for line in open(KEY_FILE, encoding="utf-8-sig"):
            if line.strip().startswith("AGENTVERSE_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def http(method, url, key=None, raw=None):
    h = {"User-Agent": "omega-poller/1.0", "Accept": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    if raw is not None:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=(raw.encode() if raw is not None else None),
                               method=method, headers=h)
    try:
        return 200, json.loads(urllib.request.urlopen(r, timeout=20).read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]
    except Exception as e:
        return "ERR", f"{type(e).__name__}:{e}"


def identity(name):
    from uagents_core.identity import Identity
    return Identity.from_seed(SEED_PREFIX + name, 0)


def send_reply(key, as_agent, dst_addr, text):
    """Sign an envelope AS this agent and submit to dst_addr's mailbox."""
    from uagents_core.envelope import Envelope
    ident = identity(as_agent)
    env = Envelope(
        version=1, sender=ident.address, target=dst_addr, session=uuid.uuid4(),
        schema_digest="0x" + "a2a".encode().hex().ljust(64, "0")[:64],
        protocol_digest="0x" + "0" * 64,
        expires=int(time.time()) + 3600,
        nonce=int(secrets.token_hex(4), 16),
    )
    env.encode_payload(text)
    env.sign(ident)
    return http("POST", f"{BASE}/v2/agents/mailbox/submit", key=key, raw=env.model_dump_json())


# Fleet-wide roster for batch send (a2a-omega-mesh broadcast semantics:
# one logical message, shared batch id, fanned out to every other agent).
FLEET = [a.strip() for a in os.environ.get(
    "A2A_FLEET",
    "jason-parser,omega-man,my-liberclaw,my-betterclaw,omega-liberclaw,omega-betterclaw"
).split(",") if a.strip()]

LEDGER_DIR = os.environ.get("A2A_LEDGER_DIR",
                            os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes", "ledger"))


def load_ledger(name):
    """Grounded live state for an agent: {active, queue[], note}. Empty if absent.
    This is what makes status answers reflect reality instead of confabulation."""
    fp = os.path.join(LEDGER_DIR, f"{name}.json")
    try:
        with open(fp) as f:
            return json.load(f)
    except Exception:
        return {"active": None, "queue": [], "note": ""}


def queue_work(name, task, active=None):
    """Queue a NEW task into an agent's own ledger so it can self-assign work
    AND its next status answer reflects it. Returns the updated ledger."""
    led = load_ledger(name)
    q = list(led.get("queue") or [])
    q.append(str(task))
    led["queue"] = q
    if active:
        led["active"] = str(active)
    led.setdefault("note", "")
    led["note"] = (led.get("note", "") + f" Queued '{task}' at {datetime.now().strftime('%H:%M')}." if led.get("note") else f"Queued '{task}' at {datetime.now().strftime('%H:%M')}.").strip()
    os.makedirs(LEDGER_DIR, exist_ok=True)
    with open(os.path.join(LEDGER_DIR, f"{name}.json"), "w") as f:
        json.dump(led, f, indent=2)
    return led


def status_answer(name, question):
    """Answer status-style questions from the agent's REAL ledger, not from a
    blank-slate LLM. If queue is empty it genuinely needs work; if not, it does not."""
    led = load_ledger(name)
    active = led.get("active") or "none"
    queue = led.get("queue") or []
    note = led.get("note", "")
    needs = f"NO - queue has {len(queue)} item(s)" if queue else "YES - queue is empty"
    out = (f"(a) ACTIVE: {active}. "
           f"(b) QUEUE ({len(queue)}): {('; '.join(queue)) if queue else 'empty'}. "
           f"(c) NEED MORE WORK: {needs}.")
    if note:
        out += f" NOTE: {note}"
    return out


def looks_like_status(t):
    return re.search(r"\bworking on\b|\bqueue\b|\bqueued\b|\bneed (more )?work\b|fleet status|\bcurrently\b", t, re.I)


def consume_directive(name, t):
    """Closed-loop: when jason sends a 'start your top task' directive, this agent
    pulls the top of ITS OWN queue into 'active' (working now) and confirms it.
    Returns a confirmation string, or None if it is not a start-directive."""
    if not re.search(r"\bDIRECTIVE\b|\bstart your top task\b|\bstart the top\b|\bbegin your top\b", t, re.I):
        return None
    led = load_ledger(name)
    q = list(led.get("queue") or [])
    if not q:
        return (f"NO - queue is empty, nothing to start. Say 'queue more work' and I will "
                f"pull it on the next directive.")
    top = q[0]
    led["queue"] = q[1:]
    led["active"] = top
    led["note"] = (led.get("note", "") + f" Started '{top}' at {datetime.now().strftime('%H:%M')}.").strip()
    os.makedirs(LEDGER_DIR, exist_ok=True)
    with open(os.path.join(LEDGER_DIR, f"{name}.json"), "w") as f:
        json.dump(led, f, indent=2)
    return (f"STARTED: '{top}' now ACTIVE. {len(led['queue'])} item(s) left in queue: "
            f"{('; '.join(led['queue'])) if led['queue'] else 'none'}.")


PROFILE_FILE = os.environ.get("A2A_ANSWER_PROFILES",
                              os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes", "answer_profiles.json"))


def load_profile(name):
    """Per-agent brain profile: {system, model?, base?, key?}. Empty if absent.
    This is how each agent 'answers as itself' rather than one blank-slate voice."""
    try:
        profs = json.load(open(PROFILE_FILE))
    except Exception:
        return {}
    return profs.get(name, {}) if isinstance(profs, dict) else {}


def llm_answer(prompt, agent=None):
    base = os.environ.get("A2A_ANSWER_BASE", "")
    key = os.environ.get("A2A_ANSWER_KEY", "")
    model = os.environ.get("A2A_ANSWER_MODEL", "")
    if not (base and model):
        return None
    prof = load_profile(agent) if agent else {}
    pmodel = prof.get("model") or model
    pbase = (prof.get("base") or base).rstrip("/") + "/chat/completions"
    pkey = prof.get("key") or key
    msgs = []
    system = prof.get("system", "")
    if agent:
        ctx = (f"You are {agent}, a whitehat security agent on the jason-parser fleet. "
               f"Answer as {agent}. Your live work ledger: {json.dumps(load_ledger(agent))}")
        system = (ctx + "\n" + system).strip() if system else ctx
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    body = json.dumps({"model": pmodel, "messages": msgs, "max_tokens": 200}).encode()
    h = {"Content-Type": "application/json"}
    if pkey:
        h["Authorization"] = "Bearer " + pkey
    try:
        r = urllib.request.Request(pbase, data=body, headers=h, method="POST")
        d = json.loads(urllib.request.urlopen(r, timeout=60).read().decode())
        return (d["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        log(f"  LLM answer failed: {e}")
        return None


def send_as(key, as_name, target_name, text):
    """Sign as `as_name`, deliver one task to `target_name`'s mailbox."""
    return send_reply(key, as_name, identity(target_name).address, text)


def llm_answer(prompt):
    base = os.environ.get("A2A_ANSWER_BASE", "")
    key = os.environ.get("A2A_ANSWER_KEY", "")
    model = os.environ.get("A2A_ANSWER_MODEL", "")
    if not (base and model):
        return None
    body = json.dumps({"model": model,
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 200}).encode()
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    try:
        r = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                   data=body, headers=h, method="POST")
        d = json.loads(urllib.request.urlopen(r, timeout=60).read().decode())
        return (d["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        log(f"  LLM answer failed: {e}")
        return None


def load_seen(name):
    fp = os.path.join(STATE_DIR, f".av_seen_{name}.json")
    if os.path.exists(fp):
        try:
            return set(json.load(open(fp)))
        except Exception:
            return set()
    return None


def save_seen(name, s):
    with open(os.path.join(STATE_DIR, f".av_seen_{name}.json"), "w") as f:
        json.dump(sorted(s), f)


def serve_once(name, key):
    """Poll one agent's mailbox and auto-reply to any new [a2a] message."""
    from uagents_core.envelope import Envelope
    me_addr = identity(name).address
    st, box = http("GET", f"{BASE}/v2/agents/{me_addr}/mailbox", key=key)
    if st != 200:
        log(f"{name}: mailbox read {st} {str(box)[:120]}")
        return
    items = box if isinstance(box, list) else []
    seen = load_seen(name)
    if seen is None:
        save_seen(name, set(it.get("uuid") for it in items if it.get("uuid")))
        log(f"{name}: armed; {len(items)} pre-existing marked seen")
        return
    for it in items:
        uid = it.get("uuid")
        if not uid or uid in seen:
            continue
        seen.add(uid)
        env = it.get("envelope") or {}
        text, sender_addr = "", env.get("sender", "")
        try:
            e = Envelope.model_validate(env)
            text = e.decode_payload()
        except Exception:
            text = str(env)[:200]
        # Only react to fleet [a2a] traffic; ignore our own echoes.
        t = text.strip()
        if "ACK from" in t:
            # It is already a reply - never chain-ACK (stops infinite loops
            # when agents batch-send to each other). Just clean up the mailbox.
            try:
                http("DELETE", f"{BASE}/v2/agents/{me_addr}/mailbox/{uid}", key=key)
            except Exception:
                pass
            continue
        if "[a2a]" not in t and "task" not in t.lower():
            try:
                http("DELETE", f"{BASE}/v2/agents/{me_addr}/mailbox/{uid}", key=key)
            except Exception:
                pass
            continue
        # Closed-loop work: a 'start your top task' directive pulls the top of
        # THIS agent's own queue into active and confirms it (real self-assignment).
        if "[a2a]" in t or "task" in t.lower():
            confirmed = consume_directive(name, t)
            if confirmed:
                reply = f"[a2a] ACK from {name} (agentverse): task {uid[:12]} received. {confirmed}"
                rs, rb = send_reply(key, name, sender_addr or me_addr, reply)
                log(f"{name}: DIRECTIVE-CONSUME task {uid[:12]} -> "
                    + ("ok" if rs in (200, 202) else f"FAIL {rs}"))
                try:
                    http("DELETE", f"{BASE}/v2/agents/{me_addr}/mailbox/{uid}", key=key)
                except Exception:
                    pass
                continue
        # Build the reply: an ACK, plus an answer if it looks like a question.
        # STATUS questions are answered from the agent's REAL ledger (grounded);
        # other questions still go to the LLM. This stops confabulated status.
        ans = None
        reply = f"[a2a] ACK from {name} (agentverse): task {uid[:12]} received."
        qmatch = re.search(r"\?|\bwhat\b|\bwho\b|\bwhere\b|\bwhen\b|\breport\b|\bpick\b|\bprefer\b", t, re.I)
        if looks_like_status(t):
            ans = status_answer(name, t)
            if ans:
                reply += f" ANSWER: {ans[:300]}"
        elif qmatch:
            ans = llm_answer(t, agent=name)
            if ans:
                reply += f" ANSWER: {ans[:300]}"
        if not sender_addr:
            sender_addr = me_addr  # degenerate; ack in place
        has_ans = bool(ans)
        rs, rb = send_reply(key, name, sender_addr, reply)
        if rs in (200, 202):
            log(f"{name}: REPLIED to {sender_addr[:14]}... task {uid[:12]} ok"
                + (" (with LLM answer)" if has_ans else ""))
        else:
            log(f"{name}: reply FAILED {sender_addr[:14]} -> {rs} {str(rb)[:120]}")
        try:
            http("DELETE", f"{BASE}/v2/agents/{me_addr}/mailbox/{uid}", key=key)
        except Exception:
            pass
    save_seen(name, seen)


def main():
    key = load_key()
    if not key:
        log("ERROR: no AGENTVERSE_API_KEY (set A2A_AGENTVERSE_ENV / A2A_AGENTVERSE_API_KEY)")
        return 2
    try:
        import uagents_core  # noqa: F401
    except Exception as e:
        log(f"ERROR: uagents SDK missing ({e}). Run: pip install uagents uagents_core")
        return 2

    # One-shot OUTBOUND commands (ANY agent can use these - not just jason):
    #   python omega_poller.py --send <as_name> <target> "<text>"
    #       sign as <as_name> (must be in OWN_AGENTS), deliver to <target>'s mailbox
    #   python omega_poller.py --batch "<text>"
    #       sign as OWN_AGENTS[0], ONE message fanned out to every other fleet
    #       agent (a2a-omega-mesh broadcast semantics: shared batch id, per-target task ids)
    if len(sys.argv) > 1 and sys.argv[1] == "--send" and len(sys.argv) >= 5:
        as_name, target = sys.argv[2], sys.argv[3]
        text = " ".join(sys.argv[4:])
        if as_name not in OWN_AGENTS:
            print(f"ERROR: --send as {as_name} not in OWN_AGENTS={OWN_AGENTS}")
            return 2
        st, body = send_as(key, as_name, target, f"[a2a] task from={as_name}: {text}")
        print(f"send {as_name} -> {target} HTTP {st} {str(body)[:120]}")
        return 0 if st in (200, 202) else 1
    if len(sys.argv) > 1 and sys.argv[1] == "--batch" and len(sys.argv) >= 3:
        text = " ".join(sys.argv[2:])
        as_name = OWN_AGENTS[0] if OWN_AGENTS else "omega-man"
        batch_id = "bc-" + secrets.token_hex(4)
        log(f"BATCH as {as_name} id={batch_id} to {len(FLEET)-1} peers")
        ok = 0
        for peer in FLEET:
            if peer == as_name:
                continue
            st, body = send_as(key, as_name, peer, f"[a2a] batch {batch_id} task from={as_name}: {text}")
            if st in (200, 202):
                ok += 1
                log(f"  {peer}: ok")
            else:
                log(f"  {peer}: FAILED {st} {str(body)[:100]}")
        print(f"batch {batch_id} as {as_name}: {ok}/{len(FLEET)-1} delivered")
        return 0 if ok else 1
    if len(sys.argv) > 1 and sys.argv[1] == "--work" and len(sys.argv) >= 3:
        # Queue NEW work onto an agent's own ledger (operator capability):
        #   python omega_poller.py --work <agent> "<task>"
        # The agent's next status answer will reflect it; a start-directive
        # pulls it from the queue automatically.
        target, task = sys.argv[2], " ".join(sys.argv[3:])
        if target not in OWN_AGENTS:
            print(f"ERROR: {target} not in OWN_AGENTS={OWN_AGENTS}")
            return 2
        led = queue_work(target, task)
        print(f"{target}: queue now {len(led.get('queue', []))} item(s) - last: {led.get('queue') and led['queue'][-1][:60]}")
        return 0

    log(f"omega poller starting: acting for {OWN_AGENTS}, poll every {POLL_SEC}s")
    once = "--once" in sys.argv
    while True:
        for name in OWN_AGENTS:
            try:
                serve_once(name, key)
            except Exception as e:
                log(f"{name}: cycle error: {e}")
        if once:
            break
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    sys.exit(main())
