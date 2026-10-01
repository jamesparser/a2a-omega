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
  A2A_ANSWER_PROFILES     per-agent brain/persona file (default notes/answer_profiles.json)
  A2A_ANSWER_MAX_TOKENS   brain max_tokens, default 400 (200 truncated real answers)
  A2A_ANSWER_MAX_CHARS    reply body cap, default 800; over-cap answers say [truncated]
  A2A_MAX_ATTEMPTS        retries per envelope before it is dropped, default 3.
                          Stops one undeliverable message from blocking a mailbox.
  A2A_REPLY_FALLBACK      where to send an answer when the envelope's sender is
                          not a registered Agentverse agent (reply 404s). A fleet
                          name or an agent1... address. Default "jason-parser";
                          empty disables redirection.

Behaviour: every [a2a] message gets a REAL answer, never a bare acknowledgement.
Status questions are answered from the agent's own ledger (grounded); everything
else goes to that agent's own brain slot so it answers as itself. If no brain is
reachable the reply says so explicitly instead of inventing an answer.

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

# Answer length. 200 tokens truncated real answers mid-sentence, which read as
# evasive; both are tunable without a code change.
ANSWER_MAX_TOKENS = int(os.environ.get("A2A_ANSWER_MAX_TOKENS", "400"))
ANSWER_MAX_CHARS = int(os.environ.get("A2A_ANSWER_MAX_CHARS", "800"))


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
    body = json.dumps({"model": pmodel, "messages": msgs,
                       "max_tokens": ANSWER_MAX_TOKENS}).encode()
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


# ---------------------------------------------------------------------------
# Bounded retry state. Added after the 2026-10-01 stuck-mailbox incident: a
# single unanswerable envelope used to raise out of serve_once before save_seen
# ran, so the SAME message was retried every poll forever and blocked every
# later message in that agent's mailbox. Attempts are now persisted, and a
# message is dropped (loudly) after MAX_ATTEMPTS instead of looping.
# ---------------------------------------------------------------------------
MAX_ATTEMPTS = int(os.environ.get("A2A_MAX_ATTEMPTS", "3"))


def load_attempts(name):
    fp = os.path.join(STATE_DIR, f".av_attempts_{name}.json")
    try:
        d = json.load(open(fp))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_attempts(name, d):
    with open(os.path.join(STATE_DIR, f".av_attempts_{name}.json"), "w") as f:
        json.dump(d, f)


def _ack(name, key, me_addr, uid):
    """Delete the envelope so Agentverse stops redelivering it."""
    try:
        http("DELETE", f"{BASE}/v2/agents/{me_addr}/mailbox/{uid}", key=key)
    except Exception:
        pass


REPLY_FALLBACK = os.environ.get("A2A_REPLY_FALLBACK", "jason-parser")

# How much of an inbound reply is transcribed to the log (flattened to one line).
INBOUND_LOG_CHARS = int(os.environ.get("A2A_INBOUND_LOG_CHARS", "1200"))


def _fallback_addr():
    """Resolve A2A_REPLY_FALLBACK: either an agent1... address or a fleet name.

    Used when the envelope's sender is not a registered Agentverse agent, so a
    real answer is redirected somewhere readable instead of being dropped.
    """
    v = (REPLY_FALLBACK or "").strip()
    if not v:
        return ""
    if v.startswith("agent1"):
        return v
    try:
        return identity(v).address
    except Exception:
        return ""


def _deliver(name, key, me_addr, sender_addr, reply, uid, kind):
    """Send one reply. True when accepted (envelope can be ACKed), False to retry.

    A 404 means the sender address is not a registered Agentverse agent - which
    is exactly what the hub's old unregistered "a2a-omega-hub" signing identity
    produced, losing every answer. In that case the reply is redirected to
    A2A_REPLY_FALLBACK rather than discarded.
    """
    dst = sender_addr or me_addr  # degenerate: no sender, answer in place
    rs, rb = send_reply(key, name, dst, reply)
    if rs in (200, 202):
        log(f"{name}: ANSWERED {dst} task {uid[:12]} [{kind}] ok")
        _ack(name, key, me_addr, uid)
        return True
    if rs == 404:
        fb = _fallback_addr()
        if fb and fb != dst and fb != me_addr:
            log(f"{name}: sender {dst} is not a registered agent (404); "
                f"redirecting answer to fallback {fb}")
            rs2, rb2 = send_reply(key, name, fb, reply)
            if rs2 in (200, 202):
                log(f"{name}: ANSWERED via fallback {fb} task {uid[:12]} [{kind}] ok")
                _ack(name, key, me_addr, uid)
                return True
            rs, rb = rs2, rb2
    log(f"{name}: reply FAILED to {dst} task {uid[:12]} [{kind}] "
        f"-> {rs} {str(rb)[:120]}")
    return False


def _handle_one(name, key, me_addr, it, uid):
    """Deal with ONE envelope and answer it for real.

    Returns True when the envelope is fully handled (safe to mark seen), False
    when delivery failed and it should be retried on a later cycle.
    """
    from uagents_core.envelope import Envelope
    env = it.get("envelope") or {}
    text, sender_addr = "", env.get("sender", "")
    try:
        e = Envelope.model_validate(env)
        text = e.decode_payload()
    except Exception:
        text = str(env)[:200]
    t = text.strip()

    # An inbound reply is terminal. Never chain-reply (that is what made agents
    # ping-pong forever when they batch-sent to each other). 'ACK from' is the
    # legacy wording still emitted by lcb_responder; 'REPLY from' is ours.
    if "ACK from" in t or "REPLY from" in t:
        # Record it before sweeping: this is how an operator reads what the rest
        # of the fleet answered, so it must not vanish silently from the mailbox.
        # Newlines are flattened so each transcript entry stays ONE log line -
        # otherwise a markdown answer spans many lines and grep/tail shows only
        # its heading, which reads as a truncated or empty answer.
        flat = " ".join(t[:INBOUND_LOG_CHARS].split())
        log(f"{name}: INBOUND from {sender_addr}: {flat}")
        _ack(name, key, me_addr, uid)
        return True
    # Only react to fleet [a2a] traffic; sweep anything else out of the mailbox.
    if "[a2a]" not in t and "task" not in t.lower():
        _ack(name, key, me_addr, uid)
        return True

    # Closed-loop work: a 'start your top task' directive pulls the top of THIS
    # agent's own queue into active and confirms it (real self-assignment).
    confirmed = consume_directive(name, t)
    if confirmed:
        reply = (f"[a2a] REPLY from {name} (agentverse): task {uid[:12]} "
                 f"received. {confirmed}")
        return _deliver(name, key, me_addr, sender_addr, reply, uid, "directive")

    # Everything else gets a REAL answer, never a bare acknowledgement.
    # STATUS questions are answered from the agent's own ledger (grounded, no
    # confabulation); all other traffic goes to that agent's own brain slot, so
    # it answers as itself with its persona and live ledger as context.
    if looks_like_status(t):
        ans, kind = status_answer(name, t), "ledger"
    else:
        ans, kind = llm_answer(t, agent=name), "brain"
    if ans:
        ans = ans.strip()
        if len(ans) > ANSWER_MAX_CHARS:
            ans = ans[:ANSWER_MAX_CHARS].rstrip() + " [truncated]"
        reply = (f"[a2a] REPLY from {name} (agentverse): task {uid[:12]} received. "
                 f"ANSWER: {ans}")
    else:
        # Brain unavailable: say so honestly rather than faking an answer.
        reply = (f"[a2a] REPLY from {name} (agentverse): task {uid[:12]} received, "
                 f"but my answer backend is unavailable (A2A_ANSWER_* brain unset or "
                 f"it errored). Logged for the operator - I did NOT guess an answer.")
        kind = "no-brain"
    return _deliver(name, key, me_addr, sender_addr, reply, uid, kind)


def serve_once(name, key):
    """Poll one agent's mailbox and answer every new [a2a] message for real.

    Robustness contract:
      * each envelope is handled in its own try/except, so one bad message can
        never block the rest of the mailbox;
      * seen + attempt state is persisted in a finally block, so a crash
        mid-cycle cannot make the same envelope retry forever;
      * a message that keeps failing is dropped after A2A_MAX_ATTEMPTS and
        logged as a permanent failure.
    """
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
    attempts = load_attempts(name)
    try:
        for it in items:
            uid = it.get("uuid")
            if not uid or uid in seen:
                continue
            ok = False
            try:
                ok = _handle_one(name, key, me_addr, it, uid)
            except Exception as e:
                log(f"{name}: handler error task {uid[:12]}: {type(e).__name__}: {e}")
            if ok:
                seen.add(uid)
                attempts.pop(uid, None)
                continue
            n = attempts.get(uid, 0) + 1
            if n >= MAX_ATTEMPTS:
                seen.add(uid)
                attempts.pop(uid, None)
                _ack(name, key, me_addr, uid)
                log(f"{name}: DROPPED task {uid[:12]} after {n} failed attempts "
                    f"(not retrying; mailbox unblocked)")
            else:
                attempts[uid] = n
                log(f"{name}: task {uid[:12]} attempt {n}/{MAX_ATTEMPTS} failed, will retry")
    finally:
        save_seen(name, seen)
        save_attempts(name, attempts)


def main():
    global OWN_AGENTS   # narrowed by __actor= below; declared here because
                        # OWN_AGENTS is read earlier in this function
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

    # __actor=<name> restricts THIS process to a single fleet agent. The
    # per-actor launcher (run_poller_one.sh) passes it. Making it functional
    # matters: without it a process started with a five-name A2A_OWN_AGENTS
    # polls the whole fleet, so every mailbox had two pollers racing on the same
    # .av_seen file and answering the same envelope twice.
    for a in sys.argv[1:]:
        if a.startswith("__actor="):
            want = a.split("=", 1)[1].strip()
            if want:
                if want not in OWN_AGENTS:
                    log(f"WARNING: __actor={want} is not in A2A_OWN_AGENTS={OWN_AGENTS}; "
                        f"restricting to {want} anyway")
                OWN_AGENTS = [want]
                log(f"__actor={want}: this process answers for {want} only")

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
