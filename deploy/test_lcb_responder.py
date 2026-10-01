#!/usr/bin/env python3
"""Regression tests for deploy/lcb_responder.py (the AgentMail fallback lane).

Guards the behaviours that were wrong in the original:
  * it replied "ACK from X: task N received and processed" while processing
    nothing - a false claim, and no answer;
  * two instances could run at once (the watchdog started a second), so inboxes
    got duplicate replies;
  * seen-state was saved only at the end of the loop, so an exception lost the
    batch's dedupe state and re-answered messages after a restart;
  * AgentMail org keys were hardcoded in the source.

Stdlib only; all network calls are monkeypatched. `python3 test_lcb_responder.py`
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lcb_responder as R  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + (("  :: " + detail) if detail and not cond else ""))


class Fake:
    """Fakes AgentMail + the Agentverse bridge and records what was sent."""

    def __init__(self, msgs=None, forwarded=True, send_error=None):
        self.msgs = msgs or []
        self.forwarded = forwarded
        self.send_error = send_error
        self.sent = []           # (subject, body)
        self.forwarded_calls = []
        self.loglines = []
        self._patch()

    def _patch(self):
        R.SEEN_DIR = tempfile.mkdtemp(prefix="lcb-seen-")
        R.LOCK_FILE = os.path.join(tempfile.mkdtemp(prefix="lcb-lock-"), "r.lock")
        R.LOG = os.path.join(tempfile.mkdtemp(prefix="lcb-log-"), "r.log")
        R.REPLY_TO = "jasonparser@agentmail.to"
        R.am_get = lambda inbox, key: list(self.msgs)
        R.log = lambda m: self.loglines.append(m)

        def am_send(inbox, key, subject, text):
            self.sent.append((subject, text))
            return {"error": self.send_error} if self.send_error else {"message_id": "out-1"}

        R.am_send = am_send

        def fwd(peer, prompt, task_id):
            self.forwarded_calls.append((peer, task_id))
            return (True, "forwarded to the Agentverse lane") if self.forwarded \
                else (False, "no agentverse_address for peer in config/peers.json")

        R.forward_to_agentverse = fwd


def msg(mid, peer, prompt="which cipher would you pick?", frm="hub@somewhere.test",
        subject=None):
    body = json.dumps({"id": "task-abc123", "prompt": prompt})
    return {
        "message_id": mid,
        "subject": subject if subject is not None else f"[a2a] {peer}",
        "from": frm,
        "bodyText": body,
    }


def arm(f, peer, inbox, key):
    """First run arms dedupe against whatever is already in the inbox.

    serve_once returns early on an empty inbox, so arming needs at least one
    pre-existing message - which the first run marks seen without replying
    (no retroactive answers), exactly like a real responder start.
    """
    real = f.msgs
    f.msgs = [msg("pre-existing-1", peer, frm="someone-else@agentmail.to",
                  subject=f"[a2a] {peer}"),
              msg("pre-existing-2", peer, frm="someone-else@agentmail.to",
                  subject=f"[a2a] {peer}")]
    R.serve_once(peer, inbox, key)
    f.msgs = real
    f.sent.clear()
    f.loglines.clear()
    f.forwarded_calls.clear()


# ------------------------------------------------------------------- tests
def test_no_hardcoded_credentials():
    src = open(os.path.join(HERE, "lcb_responder.py"), encoding="utf-8").read()
    check("no AgentMail org key literals in source", "am_us_" not in src)
    check("no key-looking hex literals in source",
          not re.search(r"['\"][0-9a-f]{40,}['\"]", src))
    check("config file is the credential source",
          "lcb_agents.json" in src and "key_env" in src)


def test_forwarded_reply_is_honest():
    f = Fake(msgs=[msg("m1", "my-liberclaw")])
    arm(f, "my-liberclaw", "in@agentmail.to", "k")
    R.serve_once("my-liberclaw", "in@agentmail.to", "k")
    check("exactly one reply sent", len(f.sent) == 1, f"sent={len(f.sent)}")
    if not f.sent:
        return
    body = json.loads(f.sent[0][1])
    check("task was forwarded to the Agentverse lane", f.forwarded_calls, f"calls={f.forwarded_calls}")
    check("status says forwarded_for_answer", body["status"] == "forwarded_for_answer",
          body["status"])
    check("never claims the task was 'processed'",
          "processed" not in body["result"].lower(), body["result"][:120])
    check("never emits a bare ACK", "ACK from" not in body["result"], body["result"][:120])
    check("reply names where the real answer is produced",
          "Agentverse" in body["result"] and "brain" in body["result"], body["result"][:160])
    check("prompt is preserved for the operator", body["prompt"], body.get("prompt"))


def test_unforwardable_reply_admits_failure():
    f = Fake(msgs=[msg("m2", "my-betterclaw")], forwarded=False)
    arm(f, "my-betterclaw", "in@agentmail.to", "k")
    R.serve_once("my-betterclaw", "in@agentmail.to", "k")
    body = json.loads(f.sent[0][1]) if f.sent else {}
    check("status says not_answered", body.get("status") == "not_answered", body.get("status"))
    check("states plainly that no answer was produced",
          "NOT ANSWERED" in body.get("result", "") and "No answer was produced" in body.get("result", ""),
          body.get("result", "")[:160])
    check("flags it for operator attention",
          "operator attention" in body.get("result", "").lower(), body.get("result", "")[:200])
    check("includes the reason it could not be relayed",
          "agentverse_address" in body.get("result", ""), body.get("result", "")[:200])


def test_fallback_traffic_is_logged_loudly():
    f = Fake(msgs=[msg("m3", "omega-liberclaw", prompt="check the firewall rules")])
    arm(f, "omega-liberclaw", "in@agentmail.to", "k")
    R.serve_once("omega-liberclaw", "in@agentmail.to", "k")
    joined = "\n".join(f.loglines)
    check("fallback arrival is logged as a degradation signal",
          "FALLBACK-LANE" in joined, joined[:200])
    check("the prompt is logged so nothing is silently lost",
          "check the firewall rules" in joined, joined[:200])


def test_own_echo_is_not_answered():
    f = Fake(msgs=[msg("m4", "my-liberclaw", frm="in@agentmail.to")])
    arm(f, "my-liberclaw", "in@agentmail.to", "k")
    R.serve_once("my-liberclaw", "in@agentmail.to", "k")
    check("message from our own inbox is skipped (no echo cascade)", len(f.sent) == 0,
          f"sent={f.sent}")
    check("echo is still marked seen so it is not retried",
          "m4" in R.load_seen("my-liberclaw"), str(R.load_seen("my-liberclaw")))


def test_non_a2a_mail_is_ignored():
    f = Fake(msgs=[msg("m5", "my-liberclaw", subject="newsletter: 10 tips")])
    arm(f, "my-liberclaw", "in@agentmail.to", "k")
    R.serve_once("my-liberclaw", "in@agentmail.to", "k")
    check("non-[a2a] mail is not answered", len(f.sent) == 0, f"sent={f.sent}")


def test_reply_failure_is_retried_not_lost():
    f = Fake(msgs=[msg("m6", "my-betterclaw")], send_error="HTTP 429: rate limited")
    arm(f, "my-betterclaw", "in@agentmail.to", "k")
    R.serve_once("my-betterclaw", "in@agentmail.to", "k")
    check("failed reply is NOT marked seen (will retry)",
          "m6" not in R.load_seen("my-betterclaw"), str(R.load_seen("my-betterclaw")))
    check("failure is logged", any("REPLY FAILED" in l for l in f.loglines), str(f.loglines))


def test_seen_survives_a_handler_exception():
    """The original saved seen only after the loop, so an exception lost the
    whole batch's dedupe state and re-answered everything after a restart."""
    f = Fake(msgs=[msg("good1", "my-liberclaw"), msg("good2", "my-liberclaw")])
    arm(f, "my-liberclaw", "in@agentmail.to", "k")
    orig = R.handle_one
    calls = {"n": 0}

    def flaky(peer, inbox, key, m, mid):
        calls["n"] += 1
        if mid == "good1":
            raise RuntimeError("simulated blow-up")
        return orig(peer, inbox, key, m, mid)

    R.handle_one = flaky
    try:
        R.serve_once("my-liberclaw", "in@agentmail.to", "k")
    finally:
        R.handle_one = orig
    seen = R.load_seen("my-liberclaw")
    check("handler exception does not lose the batch's seen state",
          "good2" in seen, f"seen={sorted(seen)}")
    check("the raising message is left unseen for retry", "good1" not in seen, f"seen={sorted(seen)}")
    check("later messages are still answered after one raises",
          len(f.sent) == 1, f"sent={len(f.sent)}")
    check("exception is logged with its type",
          any("handler error" in l and "RuntimeError" in l for l in f.loglines), str(f.loglines))


def test_single_instance_lock():
    """Two responders on one inbox produced duplicate replies."""
    script = os.path.join(HERE, "lcb_responder.py")
    env = dict(os.environ)
    env["LCB_TEST_HOLD"] = "1"
    # First instance holds the lock; a second must exit immediately.
    code = (
        "import sys, os, time;"
        f"sys.path.insert(0, {HERE!r});"
        "import lcb_responder as R;"
        "R.LOCK_FILE = os.environ['LCB_LOCK'];"
        "h = R.acquire_lock();"
        "print('FIRST_HOLD' if h else 'FIRST_FAILED');"
        "sys.stdout.flush();"
        "time.sleep(float(os.environ['LCB_HOLD_SEC']))"
    )
    lockdir = tempfile.mkdtemp(prefix="lcb-locktest-")
    lockpath = os.path.join(lockdir, "r.lock")
    env["LCB_LOCK"] = lockpath
    env["LCB_HOLD_SEC"] = "6"
    p1 = subprocess.Popen([sys.executable, "-c", code], env=env,
                          stdout=subprocess.PIPE, text=True)
    time.sleep(2.5)
    env2 = dict(env)
    env2["LCB_HOLD_SEC"] = "0"
    p2 = subprocess.run([sys.executable, "-c",
                         "import sys, os;"
                         f"sys.path.insert(0, {HERE!r});"
                         "import lcb_responder as R;"
                         "R.LOCK_FILE = os.environ['LCB_LOCK'];"
                         "h = R.acquire_lock();"
                         "print('SECOND_HOLD' if h else 'SECOND_BLOCKED');"
                         "sys.exit(0 if h is None else 3)"],
                        env=env2, capture_output=True, text=True)
    out1 = p1.communicate(timeout=20)[0]
    check("first instance acquires the lock", "FIRST_HOLD" in out1, out1[:200])
    check("second instance is refused while the first holds it",
          "SECOND_BLOCKED" in p2.stdout, f"stdout={p2.stdout[:200]} rc={p2.returncode}")
    check("second instance exits 0 without serving (safe for a watchdog)",
          p2.returncode == 0, f"rc={p2.returncode}")
    # after the first exits, the lock must be free again
    p3 = subprocess.run([sys.executable, "-c",
                         "import sys, os;"
                         f"sys.path.insert(0, {HERE!r});"
                         "import lcb_responder as R;"
                         "R.LOCK_FILE = os.environ['LCB_LOCK'];"
                         "print('REACQUIRED' if R.acquire_lock() else 'STILL_LOCKED')"],
                        env=env2, capture_output=True, text=True)
    check("lock is released when the holder exits (no permanent deadlock)",
          "REACQUIRED" in p3.stdout, f"stdout={p3.stdout[:200]}")


def main():
    for k, v in sorted(globals().items()):
        if k.startswith("test_") and callable(v):
            print("\n--- " + k + " ---")
            v()
    passed = sum(1 for _, ok in RESULTS if ok)
    failed = [n for n, ok in RESULTS if not ok]
    print("\n" + "=" * 62)
    print(f"{passed}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
