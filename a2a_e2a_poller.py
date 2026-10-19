#!/usr/bin/env python3
"""a2a_e2a_poller.py - stable e2a.dev read+reply leg for the A2A omega fleet (10-02).

Companion to omega_poller.py (the Agentverse leg). Agentverse flaps 503; e2a is
stable. One process per fleet agent (__actor=NAME), mirroring omega_poller.py:

  * polls NAME's e2a inbox every A2A_POLL_SEC
  * answers new [a2a] tasks with NAME's own brain/ledger (reuses
    omega_poller.status_answer / llm_answer / looks_like_status)
  * replies to the SENDER's e2a inbox
  * "REPLY from"/"ACK from" inbound is terminal - never chain-reply
  * bounded attempts: one bad message can never wedge the loop

Isolation from the Agentverse leg: separate seen/attempt files
(.e2a_seen_<agent>.json / .e2a_attempts_<agent>.json) in the same STATE_DIR.
The two legs never share state, and the hub delivers each task on exactly one
transport, so a task is never answered twice.

Env (all optional):
  A2A_E2A_BASE           default https://api.e2a.dev
  A2A_E2A_KEY_FILES      comma list of KEY files, default /workspace/notes + /workspace/tmp
  A2A_POLL_SEC           default 5
  A2A_MAX_ATTEMPTS      default 3
  A2A_OWN_AGENTS         comma list (narrowed by __actor=)
Run:
  python3 a2a_e2a_poller.py __actor=agent-two
  python3 a2a_e2a_poller.py __actor=agent-two --once   (smoke test)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE = os.environ.get("A2A_E2A_BASE", "https://api.e2a.dev").rstrip("/")
UA = os.environ.get(
    "A2A_E2A_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 a2a-omega/1.2",
)
KEY_FILES = [
    p.strip() for p in os.environ.get(
        "A2A_E2A_KEY_FILES",
        "/workspace/notes/e2a.env,/workspace/notes/e2a2.env,"
        "/workspace/tmp/e2a.env,/workspace/tmp/e2a2.env",
    ).split(",") if p.strip()
]
POLL_SEC = int(os.environ.get("A2A_POLL_SEC", "5"))
MAX_ATTEMPTS = int(os.environ.get("A2A_MAX_ATTEMPTS", "3"))
REPLY_MAX = int(os.environ.get("A2A_E2A_REPLY_MAX_CHARS", "1200"))
INBOX_DOMAIN = "agents.e2a.dev"
STATE_DIR = os.path.dirname(os.path.abspath(__file__))
OWN_AGENTS = [
    a.strip() for a in os.environ.get("A2A_OWN_AGENTS", "agent-two").split(",") if a.strip()
]

# Reuse the Agentverse poller's answer pipeline (brain + ledger) so e2a answers
# are as real as agentverse answers. omega_poller is stdlib at module level;
# uagents_core is only needed in its agentverse-only functions (not called here).
sys.path.insert(0, STATE_DIR)
try:
    import omega_poller as op  # noqa: F401
    HAVE_OP = True
except Exception as _e:  # noqa: BLE001
    HAVE_OP = False


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] e2a {msg}", flush=True)


def _keys():
    keys = []
    for var in ("E2A_API_KEY", "E2A2_API_KEY"):
        v = os.environ.get(var, "").strip()
        if v:
            keys.append(v)
    for fp in KEY_FILES:
        try:
            with open(fp, encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("E2A") and "=" in line:
                        keys.append(line.split("=", 1)[1].strip().strip('"').strip("'"))
        except OSError:
            continue
    out, seen = [], set()
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _req(method, url, key, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,  # browser-like UA REQUIRED (Cloudflare 1010 otherwise)
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        return e.code, {"error": raw[:300]}
    except Exception as e:  # noqa: BLE001
        return None, {"error": f"{type(e).__name__}:{e}"}


def key_for(email, keys):
    """First key that can READ this agent's inbox (e2a keys are account-scoped:
    each key owns 3 of the 6 agent inboxes)."""
    for k in keys:
        st, _body = _req("GET", f"{BASE}/v1/agents/{email}/messages", k, timeout=15)
        if st == 200:
            return k
    return keys[0] if keys else ""


def e2a_list(email, key):
    """Inbound items for this inbox; None when the read itself failed."""
    st, body = _req("GET", f"{BASE}/v1/agents/{email}/messages", key)
    if st == 200 and isinstance(body, dict):
        return body.get("items", [])
    return None


def e2a_get(email, mid, key):
    st, body = _req("GET", f"{BASE}/v1/agents/{email}/messages/{mid}", key)
    return body if (st == 200 and isinstance(body, dict)) else {}


def load_seen(name):
    fp = os.path.join(STATE_DIR, f".e2a_seen_{name}.json")
    if os.path.exists(fp):
        try:
            return set(json.load(open(fp, encoding="utf-8")))
        except Exception:
            return set()
    return None


def save_seen(name, s):
    with open(os.path.join(STATE_DIR, f".e2a_seen_{name}.json"), "w", encoding="utf-8") as f:
        json.dump(sorted(x for x in s if x), f)


def load_attempts(name):
    fp = os.path.join(STATE_DIR, f".e2a_attempts_{name}.json")
    if os.path.exists(fp):
        try:
            return json.load(open(fp, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_attempts(name, d):
    with open(os.path.join(STATE_DIR, f".e2a_attempts_{name}.json"), "w", encoding="utf-8") as f:
        json.dump(d, f)


def handle_one(name, key, item):
    """Deal with ONE inbox item. True = fully handled (safe to mark seen);
    False = delivery failed, retry next cycle."""
    email = f"{name}@{INBOX_DOMAIN}"
    mid = item.get("id") or item.get("message_id") or ""
    if not mid:
        return True
    full = e2a_get(email, mid, key)
    if not full:
        return False  # detail unreadable -> retry
    parsed = full.get("parsed") or {}
    text = str(parsed.get("text") or item.get("subject") or "").strip()
    sender = str(full.get("header_from") or item.get("from") or "").strip()
    subj = str(item.get("subject") or "")

    # Terminal inbound: an inbound REPLY/ACK is how we learn the fleet answered.
    # Record it, never chain-reply (that is what made agents ping-pong forever).
    if "reply from" in text.lower() or "ack from" in text.lower():
        flat = " ".join(text[:400].split())
        log(f"{name}: INBOUND reply (terminal): {flat[:220]}")
        return True

    # Extract task prompt/id; the hub ships JSON task entries, direct sends are
    # plain text.
    prompt, task_id = text, mid[:16]
    js = text.lstrip()
    if js.startswith("{"):
        try:
            d0 = json.loads(js)
            if isinstance(d0, dict):
                if str(d0.get("replied_by") or "").lower() == email.lower():
                    return True  # our own echo - never answer ourselves
                prompt = str(d0.get("prompt") or text)
                task_id = str(d0.get("id") or d0.get("task_id") or task_id)
        except Exception:
            pass

    # Only react to fleet [a2a] traffic; sweep anything else out of the inbox
    # (same behavior as the agentverse leg).
    if "[a2a]" not in (subj + " " + text).lower():
        log(f"{name}: swept non-fleet mail {mid[:16]} subj={subj[:50]!r}")
        return True

    # Real answer, never a bare acknowledgement.
    if HAVE_OP and op.looks_like_status(text):
        ans, kind = op.status_answer(name, text), "ledger"
    elif HAVE_OP:
        ans, kind = op.llm_answer(text, agent=name), "brain"
    else:
        ans, kind = None, "no-brain"
    if ans:
        ans = str(ans).strip()
        if len(ans) > REPLY_MAX:
            ans = ans[:REPLY_MAX].rstrip() + " [truncated]"
        body = (f"[a2a] REPLY from {name} (e2a): task {task_id[:12]} received. "
                f"ANSWER: {ans}")
    else:
        body = (f"[a2a] REPLY from {name} (e2a): task {task_id[:12]} received, "
                "but my answer backend is unavailable. Logged for the operator - "
                "I did NOT guess an answer.")

    if sender and sender != email:
        to = sender if sender.endswith(INBOX_DOMAIN) else f"{sender}@{INBOX_DOMAIN}"
        st2, rb = _req(
            "POST", f"{BASE}/v1/agents/{email}/messages", key,
            {"to": [to], "subject": f"[a2a] {name} reply {task_id[:12]}", "text": body})
        if st2 in (200, 202) and isinstance(rb, dict) and rb.get("message_id"):
            log(f"{name}: ANSWERED e2a {to} task {task_id[:12]} [{kind}] ok")
            return True
        log(f"{name}: e2a reply FAILED -> {to} HTTP {st2} {str(rb)[:150]}")
        return False
    # No resolvable sender: answer is logged (transcript) but undeliverable.
    log(f"{name}: no sender on task {task_id[:12]} - answer logged only: {body[:180]}")
    return True


def serve_once(name, key):
    email = f"{name}@{INBOX_DOMAIN}"
    items = e2a_list(email, key)
    if items is None:
        log(f"{name}: e2a inbox read failed this cycle")
        return
    seen = load_seen(name)
    if seen is None:
        save_seen(name, set(i.get("id") for i in items if i.get("id")))
        log(f"{name}: armed; {len(items)} pre-existing marked seen")
        return
    new_items = [i for i in items if i.get("id") and i["id"] not in seen]
    new_items.sort(key=lambda i: str(i.get("created_at") or i.get("timestamp") or ""))
    if not new_items:
        return
    attempts = load_attempts(name)
    try:
        for i in new_items:
            ok = False
            try:
                ok = handle_one(name, key, i)
            except Exception as e:  # noqa: BLE001
                log(f"{name}: handler error {str(i.get('id'))[:16]}: {type(e).__name__}: {e}")
            if ok:
                seen.add(i["id"])
                attempts.pop(i["id"], None)
                continue
            mid = i["id"]
            n = attempts.get(mid, 0) + 1
            if n >= MAX_ATTEMPTS:
                seen.add(mid)
                attempts.pop(mid, None)
                log(f"{name}: DROPPED {mid[:16]} after {n} failed attempts "
                    f"(not retrying; loop kept unblocked)")
            else:
                attempts[mid] = n
                log(f"{name}: {mid[:16]} attempt {n}/{MAX_ATTEMPTS} failed, will retry")
    finally:
        save_seen(name, seen)
        save_attempts(name, attempts)


def main():
    global OWN_AGENTS  # __actor= narrows the fleet list for THIS process
    for a in sys.argv[1:]:
        if a.startswith("__actor="):
            want = a.split("=", 1)[1].strip()
            if want:
                if want not in OWN_AGENTS:
                    log(f"__actor={want} not in A2A_OWN_AGENTS={OWN_AGENTS}; "
                        f"restricting to {want} anyway")
                OWN_AGENTS = [want]
            break
    keys = _keys()
    if not keys:
        log("ERROR: no e2a keys (set E2A_API_KEY/E2A2_API_KEY or A2A_E2A_KEY_FILES)")
        return 2
    log(f"e2a poller starting: acting for {OWN_AGENTS}, poll every {POLL_SEC}s, "
        f"{len(keys)} key(s), brain={'yes' if HAVE_OP else 'NO'}")
    key_cache = {}
    once = "--once" in sys.argv
    while True:
        for name in OWN_AGENTS:
            email = f"{name}@{INBOX_DOMAIN}"
            key = key_cache.get(name)
            if not key:
                key = key_for(email, keys)
            if not key:
                log(f"{name}: no usable e2a key; skipping cycle")
                continue
            key_cache[name] = key
            try:
                serve_once(name, key)
            except Exception as e:  # noqa: BLE001
                log(f"{name}: cycle error: {type(e).__name__}: {e}")
            # Auth failures: drop the cached key so next cycle re-resolves it.
            if e2a_list(email, key) is None:
                st, _b = _req("GET", f"{BASE}/v1/agents/{email}/messages", key, timeout=10)
                if st in (None, 401, 403):
                    key_cache.pop(name, None)
        if once:
            break
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    sys.exit(main())
