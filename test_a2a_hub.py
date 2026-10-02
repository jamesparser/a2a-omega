#!/usr/bin/env python3
"""Regression tests for the a2a_hub.py transport chain.

Locks in the canonical precedence the fleet depends on:

    agentverse  ->  e2a  ->  agentmail

and the removal of MailSlurp as a 4th fallback (2026-09-25). Before the fix the
committed chain was [agentverse, e2a, agentmail, mailslurp] while the deployed
hub had already dropped mailslurp, so the repo and the running process
disagreed and a redeploy from git would have silently re-added a transport that
needs an unused sandbox inbox.

Stdlib only. `python3 test_a2a_hub.py`
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Keys must look present so agentverse/e2a are considered usable.
os.environ.setdefault("A2A_AGENTVERSE_API_KEY", "test-agentverse-key")
os.environ.setdefault("E2A_API_KEY", "test-e2a-key")

import a2a_hub as H  # noqa: E402

CANONICAL = ["agentverse", "e2a", "agentmail"]

# Obviously fake placeholders, matching config/peers.example.json. This suite
# only exercises chain construction, so no real fleet address is needed here.
FULL_PEER = {
    "agentverse_address": "agent1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "e2a_email": "your-main-agent@agents.e2a.dev",
    "inbox": "your-main-agent@agentmail.to",
    "agent_mail_key": "am_us_REPLACE_ME",
}

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + (("  :: " + detail) if detail and not cond else ""))


def with_keys(fn):
    """Run fn with agentverse + e2a credentials present."""
    saved = (H.AGENTVERSE_API_KEY, H.AGENTVERSE_KEY_SITE, H.E2A_API_KEYS, H.A2A_TRANSPORT)
    H.AGENTVERSE_API_KEY = "test-agentverse-key"
    H.AGENTVERSE_KEY_SITE = ""
    H.E2A_API_KEYS = "test-e2a-key"
    H.A2A_TRANSPORT = "agentverse"
    try:
        return fn()
    finally:
        (H.AGENTVERSE_API_KEY, H.AGENTVERSE_KEY_SITE,
         H.E2A_API_KEYS, H.A2A_TRANSPORT) = saved


def test_canonical_order():
    def run():
        chain = H._transport_chain(dict(FULL_PEER))
        check("fully-configured peer gets agentverse -> e2a -> agentmail, in that order",
              chain == CANONICAL, f"got {chain}")
        check("chain has exactly three transports", len(chain) == 3, f"got {chain}")
        check("agentverse is first (the default)", chain[0] == "agentverse", f"got {chain}")
    with_keys(run)


def test_mailslurp_never_appears():
    def run():
        peers = [
            dict(FULL_PEER),
            {"inbox": "a@agentmail.to", "agent_mail_key": "am_us_test"},          # agentmail only
            {"e2a_email": "x@agents.e2a.dev", "inbox": "a@agentmail.to",
             "agent_mail_key": "am_us_test"},                                     # no agentverse
            {"mailslurp_inbox": "legacy-sandbox-id", "mailslurp_email": "x@sandbox",
             "inbox": "a@agentmail.to", "agent_mail_key": "am_us_test"},          # legacy ms peer
            {},                                                                    # empty peer
        ]
        for p in peers:
            chain = H._transport_chain(p)
            check(f"mailslurp absent from chain for peer {sorted(p)[:2] or '(empty)'}",
                  "mailslurp" not in chain, f"got {chain}")
    with_keys(run)


def test_source_has_no_mailslurp_transport():
    src = open(os.path.join(HERE, "a2a_hub.py"), encoding="utf-8").read()
    check("no mailslurp dispatch branch left in _route",
          'elif t == "mailslurp"' not in src)
    check("no ms_retry helper left", "def ms_retry" not in src)
    check("no ms_send_to_inbox helper left", "def ms_send_to_inbox" not in src)
    check("chain literal is the three-transport canonical order",
          'full = ["agentverse", "e2a", "agentmail"]' in src)


def test_unusable_transports_are_dropped_in_order():
    def run():
        # agentmail-only peer -> [agentmail]
        c = H._transport_chain({"inbox": "a@agentmail.to", "agent_mail_key": "am_us_test"})
        check("agentmail-only peer degrades to [agentmail]", c == ["agentmail"], f"got {c}")

        # no agentverse address, but e2a + agentmail
        c = H._transport_chain({"e2a_email": "x@agents.e2a.dev", "inbox": "a@agentmail.to",
                                "agent_mail_key": "am_us_test"})
        check("peer without agentverse_address gets [e2a, agentmail] (order preserved)",
              c == ["e2a", "agentmail"], f"got {c}")

        # agentverse address but no hub key
        H.AGENTVERSE_API_KEY = ""
        H.AGENTVERSE_KEY_SITE = ""
        c = H._transport_chain(dict(FULL_PEER))
        check("agentverse dropped when the hub has no Agentverse key",
              c == ["e2a", "agentmail"], f"got {c}")
        H.AGENTVERSE_API_KEY = "test-agentverse-key"

        # no e2a keys
        H.E2A_API_KEYS = ""
        saved_environ = os.environ.pop("E2A_KEY_FILES", None)
        c = H._transport_chain(dict(FULL_PEER))
        check("e2a dropped when no e2a key is configured",
              c == ["agentverse", "agentmail"], f"got {c}")
        H.E2A_API_KEYS = "test-e2a-key"
        if saved_environ is not None:
            os.environ["E2A_KEY_FILES"] = saved_environ
    with_keys(run)


def test_explicit_transport_rotates_without_removing():
    def run():
        for primary in CANONICAL:
            H.A2A_TRANSPORT = primary
            c = H._transport_chain(dict(FULL_PEER))
            check(f"A2A_TRANSPORT={primary} puts it first", c[0] == primary, f"got {c}")
            check(f"A2A_TRANSPORT={primary} keeps all three transports",
                  sorted(c) == sorted(CANONICAL), f"got {c}")
        # unknown value must not break routing
        H.A2A_TRANSPORT = "carrier-pigeon"
        c = H._transport_chain(dict(FULL_PEER))
        check("unknown A2A_TRANSPORT falls back to canonical order",
              c == CANONICAL, f"got {c}")
    with_keys(run)


def test_default_transport_is_agentverse():
    src = open(os.path.join(HERE, "a2a_hub.py"), encoding="utf-8").read()
    check("A2A_TRANSPORT defaults to agentverse",
          'os.environ.get("A2A_TRANSPORT", "agentverse")' in src)


def test_route_never_dispatches_mailslurp():
    """_route must only ever call the three canonical senders."""
    calls = []

    def fake_send_agentverse(p, entry):
        calls.append("agentverse")
        return {"error": "simulated agentverse failure"}

    def fake_send_e2a(p, entry):
        calls.append("e2a")
        return {"error": "simulated e2a failure"}

    def fake_am_send(inbox, subject, text):
        calls.append("agentmail")
        return {"error": "simulated agentmail failure"}

    saved = (H._send_agentverse, H._send_e2a, H.am_send, H.load_peers,
             H._set_status, H._persist)
    H._send_agentverse = fake_send_agentverse
    H._send_e2a = fake_send_e2a
    H.am_send = fake_am_send
    H.load_peers = lambda: {"test-peer": dict(FULL_PEER)}
    H._set_status = lambda *a, **k: None
    H._persist = lambda *a, **k: None
    try:
        with_keys(lambda: H._route("test-peer", {"peer": "test-peer", "task": "x"}))
    finally:
        (H._send_agentverse, H._send_e2a, H.am_send, H.load_peers,
         H._set_status, H._persist) = saved
    check("_route tries transports in canonical order", calls == CANONICAL, f"got {calls}")
    check("_route never invokes a mailslurp sender", "mailslurp" not in calls, f"got {calls}")


def test_route_completes_on_first_success():
    calls = []
    saved = (H._send_agentverse, H._send_e2a, H.am_send, H.load_peers,
             H._set_status, H._persist)
    H._send_agentverse = lambda p, e: (calls.append("agentverse"), {"ok": True})[1]
    H._send_e2a = lambda p, e: (calls.append("e2a"), {"ok": True})[1]
    H.am_send = lambda i, s, t: (calls.append("agentmail"), {})[1]
    H.load_peers = lambda: {"test-peer": dict(FULL_PEER)}
    statuses = []
    H._set_status = lambda entry, peer, st, note=None: statuses.append(st)
    H._persist = lambda *a, **k: None
    try:
        with_keys(lambda: H._route("test-peer", {"peer": "test-peer", "task": "x"}))
    finally:
        (H._send_agentverse, H._send_e2a, H.am_send, H.load_peers,
         H._set_status, H._persist) = saved
    check("stops at the first working transport", calls == ["agentverse"], f"got {calls}")
    check("marks the task completed", "completed" in statuses, f"statuses={statuses}")


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
