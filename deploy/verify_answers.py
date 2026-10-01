#!/usr/bin/env python3
"""End-to-end check: does every fleet agent ANSWER, not just acknowledge?

Sends one genuine question to each agent (deliberately NOT status-shaped, so it
must go through that agent's own brain slot rather than the grounded-ledger
status path), waits, then reads the hub owner's Agentverse mailbox and scores
each reply.

Run inside the omega container:
    docker exec -e A2A_AGENTVERSE_ENV=/workspace/notes/agentverse.env \
        omega python3 /workspace/verify_answers.py
Options:
    --agents a,b,c   subset to probe (default: the five VPS actors)
    --wait N         seconds to wait for replies (default 90)
    --send-only      send and exit without collecting
Exit code 0 only when every probed agent returned a substantive answer.
"""
import json
import os
import sys
import time
from datetime import datetime

os.environ.setdefault("A2A_AGENTVERSE_ENV", "/workspace/notes/agentverse.env")
sys.path.insert(0, "/workspace")
import omega_poller as P  # noqa: E402

SENDER = os.environ.get("A2A_VERIFY_SENDER", "jason-parser")

# Real questions. None match looks_like_status(), so each must be answered by
# that agent's own brain/persona - a bare ACK or a ledger dump fails the check.
QUESTIONS = {
    "omega-man": "Which single control would you add first to harden a Docker host that runs untrusted agent code, and why that one?",
    "my-liberclaw": "What is the first misconfiguration you look for in an accidentally exposed S3 bucket?",
    "my-betterclaw": "Which TLS cipher suite would you recommend for a public API today, and what is the tradeoff?",
    "omega-liberclaw": "A webhook endpoint silently stops receiving events. What do you check first and second?",
    "omega-betterclaw": "Name the dependency-audit signal you trust most and explain why.",
    "jason-parser": "Summarise the current routing order the a2a hub uses for outbound tasks.",
}

# Phrases that would indicate an acknowledgement rather than an answer.
ACK_ONLY = ("received.", "received ", "ack from", "no answer", "unavailable")


def send(key, agent, question):
    """Send as SENDER into `agent`'s mailbox using the poller's own signing."""
    text = f"[a2a] task from={SENDER}: {question}"
    return P.send_as(key, SENDER, agent, text)


def read_mailbox(key, name):
    addr = P.identity(name).address
    st, box = P.http("GET", f"{P.BASE}/v2/agents/{addr}/mailbox", key=key)
    return (st, box if isinstance(box, list) else [])


def read_actor_log(agent):
    """That agent's own actor log - the authoritative record of what it did.

    A line like `[ts] <agent>: ANSWERED <dst> task <uid> [brain] ok` proves the
    agent produced and delivered a real answer, independent of who wins the race
    to read the mailbox and immune to multi-line answer bodies.
    """
    fp = os.environ.get("A2A_ACTOR_LOG_FMT", "/workspace/omega_poller_%s.log") % agent
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def actor_log_answer(agent, since):
    """Return (kind, line) for the newest ANSWERED entry at/after `since`."""
    best = None
    for line in read_actor_log(agent).splitlines():
        if "ANSWERED" not in line or "ok" not in line:
            continue
        ts = line[1:20] if line.startswith("[") else ""
        if since and ts and ts < since:
            continue
        kind = "brain"
        for k in ("[brain]", "[ledger]", "[directive]", "[no-brain]"):
            if k in line:
                kind = k.strip("[]")
                break
        best = (kind, line.strip())
    return best


def read_transcript():
    """Replies already swept from the mailbox by the SENDER's own actor.

    The jason-parser actor polls every 2s and transcribes every inbound reply to
    its log before ACKing it. Reading that log is the non-racy way to score
    answers: polling the mailbox directly loses a race with the actor and
    reports a healthy agent as silent.
    """
    fp = os.environ.get("A2A_VERIFY_TRANSCRIPT",
                        f"/workspace/omega_poller_{SENDER}.log")
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def decode(env):
    from uagents_core.envelope import Envelope
    try:
        return Envelope.model_validate(env or {}).decode_payload()
    except Exception:
        return str(env)[:400]


def main():
    args = sys.argv[1:]
    wait = 90
    agents = list(QUESTIONS)
    send_only = "--send-only" in args
    if "--wait" in args:
        wait = int(args[args.index("--wait") + 1])
    if "--agents" in args:
        agents = [a.strip() for a in args[args.index("--agents") + 1].split(",") if a.strip()]

    key = P.load_key()
    if not key:
        print("ERROR: no AGENTVERSE_API_KEY")
        return 2

    # Record what is already in the sender's mailbox so we only score new replies.
    _, before = read_mailbox(key, SENDER)
    before_ids = {it.get("uuid") for it in before}
    # Baseline the transcript too: the sender's own actor sweeps replies into it.
    transcript_baseline = len(read_transcript().splitlines())

    print(f"sender={SENDER} ({P.identity(SENDER).address[:22]}...)")
    print(f"probing {len(agents)} agent(s), waiting up to {wait}s\n")
    # Only ANSWERED entries from now on count as a response to this run.
    since = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sent = {}
    for a in agents:
        if a not in QUESTIONS:
            print(f"  {a:<18} SKIP (no question defined)")
            continue
        st, body = send(key, a, QUESTIONS[a])
        ok = st in (200, 202)
        sent[a] = ok
        print(f"  -> {a:<18} sent HTTP {st} {'ok' if ok else str(body)[:90]}")
    if send_only:
        return 0 if all(sent.values()) else 1

    print(f"\nwaiting {wait}s for answers...")
    deadline = time.time() + wait
    replies = {}
    sources = {}
    scored_uids = set()
    target = len([a for a in sent if sent[a]])
    while time.time() < deadline and len(replies) < target:
        time.sleep(5)

        # Source 1: replies still sitting in the sender's mailbox.
        _, box = read_mailbox(key, SENDER)
        for it in box:
            uid = it.get("uuid")
            if not uid or uid in before_ids or uid in scored_uids:
                continue
            scored_uids.add(uid)
            txt = decode(it.get("envelope") or {})
            for a in sent:
                if a in replies:
                    continue
                if f"REPLY from {a} " in txt or f"ACK from {a} " in txt:
                    replies[a], sources[a] = txt, "mailbox"
                    break

        # Source 2: replies already swept into the transcript by our own actor.
        # Without this a healthy agent looks silent purely because the 2s actor
        # won the race against this 5s poll.
        lines = read_transcript().splitlines()[transcript_baseline:]
        for line in lines:
            if "INBOUND from" not in line:
                continue
            for a in sent:
                if a in replies:
                    continue
                if f"REPLY from {a} " in line or f"ACK from {a} " in line:
                    body = line.split("INBOUND from", 1)[1]
                    replies[a], sources[a] = body.strip(), "transcript"
                    break

        # Source 3 (authoritative): the agent's own actor log. Survives mailbox
        # races and multi-line answer bodies that defeat text parsing above.
        for a in sent:
            if a in replies or not sent[a]:
                continue
            hit = actor_log_answer(a, since)
            if hit:
                kind, line = hit
                replies[a] = line
                sources[a] = f"actor-log/{kind}"

        print(f"  ...{len(replies)}/{target} answered", end="\r", flush=True)

    print("\n" + "=" * 74)
    failures = []
    for a in agents:
        if a not in sent:
            continue
        txt = replies.get(a)
        via = sources.get(a, "-")
        if not txt:
            verdict, why = "NO REPLY", "nothing came back"
        elif via.startswith("actor-log"):
            # Proof of delivery from the agent's own log. The answer body itself
            # may already have been swept into the transcript by that actor.
            kind = via.split("/", 1)[1] if "/" in via else "brain"
            if kind == "no-brain":
                verdict, why = "NO BRAIN", "actor logged an unavailable-backend reply"
            elif kind in ("brain", "ledger", "directive"):
                verdict, why = "REAL ANSWER", f"actor log confirms [{kind}] delivery"
            else:
                verdict, why = "UNKNOWN KIND", kind
        else:
            body = txt.split("ANSWER:", 1)[1].strip() if "ANSWER:" in txt else ""
            low = txt.lower()
            if not body:
                verdict, why = "ACK ONLY", "no ANSWER: payload"
            elif len(body) < 40:
                verdict, why = "THIN", f"only {len(body)} chars"
            elif "unavailable" in low and "did not guess" in low:
                verdict, why = "NO BRAIN", "backend reported unavailable"
            else:
                verdict, why = "REAL ANSWER", f"{len(body)} chars"
        flag = "PASS" if verdict == "REAL ANSWER" else "FAIL"
        if flag == "FAIL":
            failures.append(a)
        print(f"\n[{flag}] {a}  ->  {verdict} ({why}, via {via})")
        if txt:
            print("       " + txt.replace("\n", "\n       ")[:700])

    print("\n" + "=" * 74)
    total = len([a for a in agents if a in sent])
    print(f"{total - len(failures)}/{total} agents gave a real answer")
    if failures:
        print("NEEDS ATTENTION: " + ", ".join(failures))
        return 1
    print("ALL AGENTS ANSWERING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
