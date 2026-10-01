#!/usr/bin/env python3
"""Regression tests for omega_poller.py.

Guards the 2026-10-01 stuck-mailbox outage: a duplicate `llm_answer` definition
shadowed the agent-aware one, so every non-status question raised
    TypeError: llm_answer() got an unexpected keyword argument 'agent'
out of serve_once BEFORE save_seen ran. The same envelope was retried every
poll forever and blocked every later message in that agent's mailbox, so the
fleet answered nothing at all.

Runs with stdlib only: the uagents SDK is stubbed, and all network calls are
monkeypatched. `python3 test_omega_poller.py`
"""
import ast
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- SDK stubs
def _install_sdk_stubs():
    """Minimal uagents_core stand-ins so the module imports off-box."""
    core = types.ModuleType("uagents_core")

    ident_mod = types.ModuleType("uagents_core.identity")

    class Identity:
        def __init__(self, address):
            self.address = address

        @staticmethod
        def from_seed(seed, _iter):
            # deterministic, address-shaped
            return Identity("agent1" + format(abs(hash(seed)) % (16 ** 38), "038x"))

    ident_mod.Identity = Identity

    env_mod = types.ModuleType("uagents_core.envelope")

    class Envelope:
        def __init__(self, payload):
            self._payload = payload

        @classmethod
        def model_validate(cls, env):
            return cls((env or {}).get("payload", ""))

        def decode_payload(self):
            return self._payload

    env_mod.Envelope = Envelope

    core.identity = ident_mod
    core.envelope = env_mod
    sys.modules["uagents_core"] = core
    sys.modules["uagents_core.identity"] = ident_mod
    sys.modules["uagents_core.envelope"] = env_mod


_install_sdk_stubs()
os.environ.setdefault("A2A_OWN_AGENTS", "omega-man")
sys.path.insert(0, HERE)
import omega_poller as P  # noqa: E402

# Pristine module functions. Each Harness restores these before patching so a
# stub from one test can never leak into the next (alphabetical run order).
_REAL = {k: getattr(P, k) for k in
         ("identity", "http", "send_reply", "llm_answer", "load_ledger", "_handle_one",
          "REPLY_FALLBACK", "MAX_ATTEMPTS", "serve_once", "load_key", "OWN_AGENTS", "log",
          "INBOUND_LOG_CHARS")}
_REAL_INBOUND = _REAL["INBOUND_LOG_CHARS"]


# ------------------------------------------------------------- test harness
class Harness:
    """Fakes the mailbox + delivery layer and records every outbound reply."""

    def __init__(self, items=None, ledger=None, brain=None, raise_on=()):
        self.items = items or []
        self.ledger = ledger or {}
        self.brain = brain                      # callable(prompt, agent) -> str|None
        self.raise_on = set(raise_on)           # uids whose handler must explode
        self.sent = []                          # (dst, text)
        self.deleted = []                       # acked uids
        self.mailbox_calls = 0
        self._patch()

    def _patch(self):
        for k, v in _REAL.items():          # isolation: undo any previous test's stubs
            setattr(P, k, v)
        P.STATE_DIR = tempfile.mkdtemp(prefix="av-state-")
        P.MAX_ATTEMPTS = 3
        P.identity = lambda name: types.SimpleNamespace(address="agent1" + name)
        P.load_ledger = lambda name: dict(self.ledger.get(name, {"active": "none", "queue": []}))

        def http(method, url, key=None, raw=None):
            if method == "GET" and url.endswith("/mailbox"):
                self.mailbox_calls += 1
                return 200, list(self.items)
            if method == "DELETE":
                self.deleted.append(url.rstrip("/").split("/")[-1])
                return 200, {}
            return 200, {}

        def send_reply(key, as_agent, dst_addr, text):
            self.sent.append((dst_addr, text))
            return 202, {}

        P.http = http
        P.send_reply = send_reply
        P._deliver_orig = P._deliver

        if self.brain is not None:
            P.llm_answer = lambda prompt, agent=None: self.brain(prompt, agent)

        # explode inside the handler for chosen uids
        orig_handle = P._handle_one

        def handle(name, key, me_addr, it, uid):
            if uid in self.raise_on:
                raise RuntimeError("simulated handler blow-up")
            return orig_handle(name, key, me_addr, it, uid)

        P._handle_one = handle

    def arm(self):
        """First cycle arms the seen-set against an EMPTY mailbox.

        serve_once marks every pre-existing envelope as seen on first run (no
        retroactive replies), so arming must happen before the test messages
        arrive - exactly like a real poller start.
        """
        real = self.items
        self.items = []
        P.serve_once("omega-man", "k")
        self.items = real

    def cycle(self):
        P.serve_once("omega-man", "k")


def item(uid, text, sender="agent1jason-parser"):
    return {"uuid": uid, "envelope": {"sender": sender, "payload": text}}


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name + (("  :: " + detail) if detail and not cond else ""))


# ------------------------------------------------------------------- tests
def test_no_duplicate_llm_answer():
    tree = ast.parse(open(os.path.join(HERE, "omega_poller.py"), encoding="utf-8").read())
    defs = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "llm_answer"]
    check("llm_answer defined exactly once (no shadowing)", len(defs) == 1,
          f"found {len(defs)}")
    if defs:
        args = [a.arg for a in defs[0].args.args]
        check("llm_answer accepts the agent kwarg", "agent" in args, f"args={args}")


def test_llm_answer_callable_with_agent():
    """The exact call site that produced the production TypeError.

    Calls the pristine shipped function, not a stub, so it fails loudly if the
    duplicate definition is ever reintroduced.
    """
    real = _REAL["llm_answer"]
    saved = {k: os.environ.get(k) for k in ("A2A_ANSWER_BASE", "A2A_ANSWER_MODEL")}
    os.environ.pop("A2A_ANSWER_BASE", None)   # no brain configured -> returns None
    try:
        r = real("what is 2+2?", agent="omega-man")
        check("llm_answer(prompt, agent=...) does not raise TypeError", True, "")
        check("llm_answer returns None when no brain configured", r is None, f"got {r!r}")
    except TypeError as e:
        check("llm_answer(prompt, agent=...) does not raise TypeError", False, str(e))
        check("llm_answer returns None when no brain configured", False, "skipped")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_question_gets_real_answer_not_ack():
    h = Harness(
        items=[item("u1", "[a2a] task from=jason-parser: which cipher would you pick for a TLS terminator and why?")],
        brain=lambda prompt, agent=None: f"As {agent}: I'd pick ChaCha20-Poly1305 for mobile, AES-GCM elsewhere.",
    )
    h.arm()
    h.cycle()
    check("question produced exactly one reply", len(h.sent) == 1, f"sent={len(h.sent)}")
    if h.sent:
        txt = h.sent[0][1]
        check("reply carries a real ANSWER payload", "ANSWER:" in txt, txt[:160])
        check("reply is not a bare acknowledgement",
              "received." != txt.strip().split("task")[-1].strip()[:9] and "ChaCha20" in txt, txt[:160])
        check("reply is signed with the agent identity", "REPLY from omega-man" in txt, txt[:80])
    check("envelope ACKed after answering", h.deleted == ["u1"], f"deleted={h.deleted}")


def test_brain_receives_agent_identity():
    seen_agents = []

    def brain(prompt, agent=None):
        seen_agents.append(agent)
        return "grounded answer"

    h = Harness(items=[item("u2", "[a2a] task: what do you think about X?")], brain=brain)
    h.arm()
    h.cycle()
    check("brain is called with the agent name (per-agent persona)",
          seen_agents == ["omega-man"], f"agents={seen_agents}")


def test_status_question_grounded_in_ledger():
    h = Harness(
        items=[item("u3", "[a2a] task: what are you working on and what is in your queue?")],
        ledger={"omega-man": {"active": "audit hydra bridge", "queue": ["triage CVE-1", "write report"]}},
        brain=lambda prompt, agent=None: "HALLUCINATED - should not be used",
    )
    h.arm()
    h.cycle()
    txt = h.sent[0][1] if h.sent else ""
    check("status answered from the real ledger", "audit hydra bridge" in txt, txt[:200])
    check("status answer lists the real queue", "triage CVE-1" in txt, txt[:200])
    check("status answer did NOT use the blank-slate brain", "HALLUCINATED" not in txt, txt[:200])


def test_missing_brain_is_honest_not_fake():
    h = Harness(items=[item("u4", "[a2a] task: explain the vuln")], brain=lambda p, a=None: None)
    h.arm()
    h.cycle()
    txt = h.sent[0][1] if h.sent else ""
    check("still replies when the brain is down", len(h.sent) == 1, f"sent={len(h.sent)}")
    check("says the backend is unavailable instead of inventing an answer",
          "unavailable" in txt and "did NOT guess" in txt, txt[:200])


def test_inbound_reply_is_not_chained():
    for label, body in (("legacy ACK", "[a2a] ACK from my-liberclaw (agentverse): task abc received."),
                        ("new REPLY", "[a2a] REPLY from my-liberclaw (agentverse): task abc received. ANSWER: hi")):
        h = Harness(items=[item("u5", body)], brain=lambda p, a=None: "should never run")
        h.arm()
        captured = []
        P.log = lambda m: captured.append(m)
        try:
            h.cycle()
        finally:
            P.log = _REAL["log"]
        check(f"inbound {label} does not trigger a chain-reply", len(h.sent) == 0, f"sent={h.sent}")
        check(f"inbound {label} is swept out of the mailbox", h.deleted == ["u5"], f"deleted={h.deleted}")
        fragment = "ANSWER: hi" if label == "new REPLY" else "task abc received."
        check(f"inbound {label} is transcribed to the log, not silently dropped",
              any("INBOUND from" in m and fragment in m for m in captured),
              f"log={captured}")


def test_one_bad_message_does_not_block_the_mailbox():
    """The core outage behaviour: a raising message must not stop later ones."""
    h = Harness(
        items=[
            item("bad1", "[a2a] task: this one explodes"),
            item("good1", "[a2a] task: which hash would you use?"),
            item("good2", "[a2a] task: what is your queue?"),
        ],
        ledger={"omega-man": {"active": "x", "queue": ["y"]}},
        brain=lambda p, a=None: "a real answer",
        raise_on={"bad1"},
    )
    h.arm()
    h.cycle()
    answered = [t for _, t in h.sent]
    check("later messages are still answered after one raises",
          len(h.sent) == 2, f"sent={len(h.sent)}: {[t[:50] for t in answered]}")
    check("attempt counter recorded for the bad message",
          P.load_attempts("omega-man").get("bad1") == 1,
          f"attempts={P.load_attempts('omega-man')}")
    # good messages must be marked seen so they are not re-answered
    seen = P.load_seen("omega-man")
    check("successfully answered messages are marked seen",
          {"good1", "good2"} <= seen, f"seen={sorted(seen)}")
    check("failed message is NOT marked seen (retried, not lost)", "bad1" not in seen, f"seen={sorted(seen)}")


def test_bounded_retry_then_drop():
    h = Harness(items=[item("bad2", "[a2a] task: permanently broken")],
                brain=lambda p, a=None: "x", raise_on={"bad2"})
    h.arm()
    for i in range(P.MAX_ATTEMPTS + 2):
        h.cycle()
    seen = P.load_seen("omega-man")
    check("permanently failing message is dropped after MAX_ATTEMPTS",
          "bad2" in seen, f"seen={sorted(seen)}")
    check("dropped message is ACKed so Agentverse stops redelivering",
          "bad2" in h.deleted, f"deleted={h.deleted}")
    check("attempt state cleared after drop",
          P.load_attempts("omega-man") == {}, f"attempts={P.load_attempts('omega-man')}")
    check("no infinite reply spam to the sender", len(h.sent) == 0, f"sent={len(h.sent)}")


def test_transient_delivery_failure_retries_then_succeeds():
    h = Harness(items=[item("u6", "[a2a] task: what do you prefer?")],
                brain=lambda p, a=None: "I prefer agentverse")
    h.arm()
    # first delivery fails (503), second succeeds
    calls = {"n": 0}

    def flaky(key, as_agent, dst, text):
        calls["n"] += 1
        return (503, {"err": "upstream"}) if calls["n"] == 1 else (202, {})

    P.send_reply = flaky
    h.cycle()
    check("failed delivery is retried, not marked seen", "u6" not in P.load_seen("omega-man"))
    P.send_reply = lambda key, a, d, t: (h.sent.append((d, t)), (202, {}))[1]
    h.cycle()
    check("retry succeeds and marks seen", "u6" in P.load_seen("omega-man"))
    check("answer delivered on the retry", any("agentverse" in t for _, t in h.sent), f"sent={h.sent}")


def test_unregistered_sender_404_is_redirected_not_dropped():
    """The hub used to sign as the unregistered 'a2a-omega-hub' identity
    (agent1q03yav3l...), so agent answers 404'd and vanished."""
    h = Harness(items=[item("u7", "[a2a] task: what did you find?",
                            sender="agent1q03yav3leqdx0mhfscega0qummq9vuwjayy76u83revn7jayfpu5glsn003")],
                brain=lambda p, a=None: "found an open S3 bucket")
    h.arm()
    attempts = {"n": 0}
    delivered = []

    def sender_404_then_ok(key, as_agent, dst, text):
        attempts["n"] += 1
        delivered.append(dst)
        # first try: the bogus sender address -> 404; fallback -> 202
        return (404, {"detail": "Target agent not found"}) if attempts["n"] == 1 else (202, {})

    P.send_reply = sender_404_then_ok
    P.REPLY_FALLBACK = "jason-parser"
    h.cycle()
    check("answer is retried against the fallback address", attempts["n"] == 2,
          f"attempts={attempts['n']}")
    check("fallback destination is the resolved jason-parser identity",
          delivered[-1] == P.identity("jason-parser").address, f"delivered={delivered}")
    check("message marked seen after the redirected answer",
          "u7" in P.load_seen("omega-man"), f"seen={sorted(P.load_seen('omega-man'))}")
    check("envelope ACKed after the redirected answer", "u7" in h.deleted, f"deleted={h.deleted}")


def test_404_with_no_fallback_retries_then_drops():
    h = Harness(items=[item("u8", "[a2a] task: hello?", sender="agent1bogus")],
                brain=lambda p, a=None: "hi")
    h.arm()
    P.send_reply = lambda key, a, d, t: (404, {"detail": "Target agent not found"})
    P.REPLY_FALLBACK = ""            # no fallback configured
    for _ in range(P.MAX_ATTEMPTS + 1):
        h.cycle()
    check("undeliverable message is eventually dropped, not looped forever",
          "u8" in P.load_seen("omega-man"))
    check("undeliverable message is ACKed so Agentverse stops redelivering",
          "u8" in h.deleted, f"deleted={h.deleted}")


def test_fallback_accepts_a_raw_address():
    h = Harness(items=[], brain=lambda p, a=None: "x")
    h.arm()
    P.REPLY_FALLBACK = "agent1explicit-address"
    check("A2A_REPLY_FALLBACK accepts a literal agent1... address",
          P._fallback_addr() == "agent1explicit-address", P._fallback_addr())
    P.REPLY_FALLBACK = "omega-man"
    check("A2A_REPLY_FALLBACK accepts a fleet name and resolves it",
          P._fallback_addr() == P.identity("omega-man").address, P._fallback_addr())
    P.REPLY_FALLBACK = ""
    check("empty A2A_REPLY_FALLBACK disables redirection", P._fallback_addr() == "")


def test_answer_length_is_configurable():
    check("ANSWER_MAX_TOKENS defaults above the old truncating 200",
          P.ANSWER_MAX_TOKENS > 200, f"got {P.ANSWER_MAX_TOKENS}")
    long_ans = "x" * (P.ANSWER_MAX_CHARS + 500)
    h = Harness(items=[item("u9", "[a2a] task: tell me everything")],
                brain=lambda p, a=None: long_ans)
    h.arm()
    h.cycle()
    txt = h.sent[0][1] if h.sent else ""
    check("over-long answer is truncated with a visible marker",
          txt.endswith("[truncated]"), txt[-40:])
    check("truncation respects ANSWER_MAX_CHARS",
          len(txt) < len(long_ans), f"len={len(txt)}")


def test_actor_flag_restricts_process_to_one_agent():
    """__actor= was a decorative label the code never parsed, so a process
    started with a five-name A2A_OWN_AGENTS polled the whole fleet. Two pollers
    per mailbox raced on the same .av_seen file and answered twice."""
    FIVE = ["omega-man", "my-liberclaw", "my-betterclaw", "omega-liberclaw", "omega-betterclaw"]
    h = Harness(items=[], brain=lambda p, a=None: "x")   # isolates module state
    served = []
    saved_argv = sys.argv[:]
    P.load_key = lambda: "test-key"
    P.serve_once = lambda name, key: served.append(name)
    try:
        P.OWN_AGENTS = list(FIVE)
        sys.argv = ["omega_poller.py", "__actor=my-liberclaw", "--once"]
        P.main()
        check("__actor= narrows the poll loop to that single agent",
              served == ["my-liberclaw"], f"served={served}")
        check("OWN_AGENTS is narrowed, not left at the full fleet",
              P.OWN_AGENTS == ["my-liberclaw"], f"OWN_AGENTS={P.OWN_AGENTS}")

        # without the flag the process still serves everything it was told to
        served.clear()
        P.OWN_AGENTS = list(FIVE)
        sys.argv = ["omega_poller.py", "--once"]
        P.main()
        check("no __actor flag -> serves the configured fleet (unchanged behaviour)",
              served == FIVE, f"served={served}")
    finally:
        sys.argv = saved_argv
        for k, v in _REAL.items():
            setattr(P, k, v)


def test_multiline_answer_stays_one_transcript_line():
    """Markdown answers contain newlines. Unflattened, one transcript entry spans
    many log lines, so grep/tail shows only the heading and a healthy agent reads
    as having answered with 33 chars."""
    body = ("[a2a] REPLY from omega-man (agentverse): task abc123 received. "
            "ANSWER: # Routing Order Summary\n\n1. **agentverse** first\n"
            "2. then e2a\n3. then agentmail\n\nTradeoff: latency vs reach.")
    h = Harness(items=[item("u10", body)], brain=lambda p, a=None: "unused")
    h.arm()
    captured = []
    P.log = lambda m: captured.append(m)
    try:
        h.cycle()
    finally:
        P.log = _REAL["log"]
    inbound = [m for m in captured if "INBOUND from" in m]
    check("inbound reply is transcribed exactly once", len(inbound) == 1, f"n={len(inbound)}")
    if inbound:
        check("transcript entry is a single line (newlines flattened)",
              "\n" not in inbound[0], repr(inbound[0][:120]))
        check("flattened entry keeps the answer body, not just the heading",
              "agentmail" in inbound[0] and "Tradeoff" in inbound[0], inbound[0][:200])
        check("flattened entry preserves the sender address",
              "agent1jason-parser" in inbound[0], inbound[0][:120])


def test_transcript_respects_length_cap():
    body = "[a2a] REPLY from omega-man (agentverse): task x received. ANSWER: " + ("y" * 5000)
    h = Harness(items=[item("u11", body)], brain=lambda p, a=None: "unused")
    h.arm()
    captured = []
    P.INBOUND_LOG_CHARS = 300
    P.log = lambda m: captured.append(m)
    try:
        h.cycle()
    finally:
        P.log = _REAL["log"]
        P.INBOUND_LOG_CHARS = _REAL_INBOUND
    inbound = [m for m in captured if "INBOUND from" in m]
    check("transcript entry is capped (log cannot be flooded by one answer)",
          inbound and len(inbound[0]) < 500, f"len={len(inbound[0]) if inbound else 0}")


def test_mailbox_read_failure_is_not_fatal():
    h = Harness(items=[], brain=lambda p, a=None: "x")
    h.arm()
    P.http = lambda method, url, key=None, raw=None: (503, "upstream connect error")
    try:
        P.serve_once("omega-man", "k")
        check("503 mailbox read is tolerated (no exception)", True)
    except Exception as e:
        check("503 mailbox read is tolerated (no exception)", False, str(e))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print("\n--- " + t.__name__ + " ---")
        t()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [n for n, ok, d in RESULTS if not ok]
    print("\n" + "=" * 62)
    print(f"{passed}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
