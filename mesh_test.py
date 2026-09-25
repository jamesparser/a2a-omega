#!/usr/bin/env python3
"""Full 6x6 agent mesh test over e2a and Agentverse.

For every ordered pair (src != dst) on each transport:
  1. src sends [a2a] ping with a nonce
  2. dst auto-replies [a2a] pong with the same nonce (same transport)
  3. src measures round-trip latency

Prints a matrix and latency table. Exit 0 if every pair got a pong on both
transports within the deadline.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
NAMES = [
    "jason-parser",
    "omega-man",
    "my-liberclaw",
    "omega-liberclaw",
    "my-betterclaw",
    "omega-betterclaw",
]
SEED_PREFIX = os.environ.get("A2A_SEED_PREFIX", "a2a-omega-e2a-fleet-")
DEADLINE = float(os.environ.get("MESH_DEADLINE", "45"))  # seconds per pair RTT
E2A_BASE = os.environ.get("E2A_BASE", "https://api.e2a.dev").rstrip("/")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 a2a-mesh/1.0"
)
AV_BASE = os.environ.get("A2A_AGENTVERSE_BASE", "https://agentverse.ai").rstrip("/")

# e2a accounts (keys from env or files)
E2A1 = os.environ.get("E2A_API_KEY", "").strip()
E2A2 = os.environ.get("E2A2_API_KEY", "").strip()
if not E2A1:
    for path in os.environ.get("E2A_KEY_FILES", "/tmp/a2a-work/e2a.env").split(os.pathsep):
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                if "E2A" in line and "=" in line:
                    E2A1 = line.split("=", 1)[1].strip()
if not E2A2:
    for path in os.environ.get("E2A2_KEY_FILES", "/tmp/a2a-work/e2a2.env").split(os.pathsep):
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                if "E2A" in line and "=" in line:
                    E2A2 = line.split("=", 1)[1].strip()

# which e2a key owns which name
ACCT1 = {"my-liberclaw", "omega-liberclaw", "my-betterclaw"}
ACCT2 = {"omega-man", "jason-parser", "omega-betterclaw"}

AV_KEY = os.environ.get("AGENTVERSE_API_KEY", "").strip()
if not AV_KEY:
    p = os.environ.get("AGENTVERSE_ENV", "/tmp/a2a-work/agentverse.env")
    if os.path.exists(p):
        AV_KEY = open(p, encoding="utf-8").read().split("=", 1)[1].strip()


def e2a_key_for(name: str) -> str:
    return E2A1 if name in ACCT1 else E2A2


def e2a_email(name: str) -> str:
    return f"{name}@agents.e2a.dev"


def http(method, url, key=None, token=None, body=None, raw=None, timeout=25):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if isinstance(data, str):
        data = data.encode()
    h = {"User-Agent": UA, "Accept": "application/json"}
    if data is not None:
        h["Content-Type"] = "application/json"
    if key:
        h["Authorization"] = f"Bearer {key}"
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rawb = resp.read()
            try:
                return resp.status, json.loads(rawb) if rawb else {}
            except json.JSONDecodeError:
                return resp.status, {"_raw": rawb[:500]}
    except urllib.error.HTTPError as e:
        b = e.read().decode(errors="replace")
        return e.code, {"error": b[:400]}
    except Exception as e:  # noqa: BLE001
        return None, {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# e2a transport
# ---------------------------------------------------------------------------
def e2a_send(src, dst, subject, text):
    return http(
        "POST",
        f"{E2A_BASE}/v1/agents/{e2a_email(src)}/messages",
        key=e2a_key_for(src),
        body={"to": [e2a_email(dst)], "subject": subject, "text": text},
    )


def e2a_list(name):
    st, body = http("GET", f"{E2A_BASE}/v1/agents/{e2a_email(name)}/messages", key=e2a_key_for(name))
    if st == 200 and isinstance(body, dict):
        return body.get("items", [])
    return []


def e2a_text(name, mid):
    st, body = http(
        "GET",
        f"{E2A_BASE}/v1/agents/{e2a_email(name)}/messages/{mid}",
        key=e2a_key_for(name),
    )
    if st == 200 and isinstance(body, dict):
        return (body.get("parsed") or {}).get("text") or body.get("subject") or ""
    return ""


# ---------------------------------------------------------------------------
# Agentverse transport
# ---------------------------------------------------------------------------
def av_identity(name):
    from uagents_core.identity import Identity

    return Identity.from_seed(SEED_PREFIX + name, 0)


def av_send(src_name, dst_name, text):
    import secrets as _secrets
    import time as _time

    from uagents_core.envelope import Envelope

    src = av_identity(src_name)
    dst = av_identity(dst_name)
    schema_digest = "0x" + "a2a".encode().hex().ljust(64, "0")[:64]
    env = Envelope(
        version=1,
        sender=src.address,
        target=dst.address,
        session=uuid.uuid4(),
        schema_digest=schema_digest,
        protocol_digest="0x" + "0" * 64,
        expires=int(_time.time()) + 3600,
        nonce=int(_secrets.token_hex(4), 16),
    )
    env.encode_payload(text)
    env.sign(src)
    return http(
        "POST",
        f"{AV_BASE}/v2/agents/mailbox/submit",
        token=AV_KEY,
        raw=env.model_dump_json(),
    )


def av_mailbox(name):
    addr = av_identity(name).address
    st, body = http("GET", f"{AV_BASE}/v2/agents/{addr}/mailbox", token=AV_KEY)
    if st == 200:
        return body if isinstance(body, list) else []
    return []


def av_ack(name, uuid_str):
    """Delete / acknowledge a mailbox message so it is not redelivered."""
    addr = av_identity(name).address
    return http(
        "DELETE",
        f"{AV_BASE}/v2/agents/{addr}/mailbox/{uuid_str}",
        token=AV_KEY,
    )


# ---------------------------------------------------------------------------
# Auto-responder loop per (transport, agent)
# ---------------------------------------------------------------------------
STATE = {"seen_e2a": set(), "seen_av": set()}
LOCKS = {"e2a": threading.Lock(), "av": threading.Lock()}
RESULTS = {}  # (transport, src, dst) -> rtts list
ERRORS = {}


def parse_a2a(text: str):
    """Return (kind, nonce, src_hint) from an [a2a] payload line."""
    # payload is JSON task entry or a short ping line
    t = text.strip()
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            t = obj.get("text") or obj.get("prompt") or t
        except json.JSONDecodeError:
            pass
    if "[a2a] ping" in t:
        nonce = t.split("nonce=", 1)[-1].split()[0] if "nonce=" in t else ""
        src = t.split("from=", 1)[-1].split()[0] if "from=" in t else ""
        return "ping", nonce, src
    if "[a2a] pong" in t:
        nonce = t.split("nonce=", 1)[-1].split()[0] if "nonce=" in t else ""
        return "pong", nonce, ""
    return None, None, None


def _handle_e2a_msg(name, mid):
    text = e2a_text(name, mid)
    kind, nonce, src = parse_a2a(text)
    if kind == "ping" and nonce and src:
        e2a_send(name, src, f"[a2a] pong {nonce}", f"[a2a] pong nonce={nonce} from={name} via=e2a")
    elif kind == "pong" and nonce:
        RESULTS.setdefault(("pong", "e2a", nonce), time.time())


def responder_e2a(stop_evt):
    while not stop_evt.is_set():
        pending = []
        for name in NAMES:
            try:
                items = e2a_list(name)
            except Exception:
                continue
            for it in items:
                mid = it.get("id")
                if not mid or mid in STATE["seen_e2a"]:
                    continue
                STATE["seen_e2a"].add(mid)
                pending.append((name, mid))
        # fetch bodies in parallel so RTT stays close to the SMTP floor
        if pending:
            threads = [
                threading.Thread(target=_handle_e2a_msg, args=nm, daemon=True)
                for nm in pending
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=12)
        time.sleep(0.35)


def responder_av(stop_evt):
    while not stop_evt.is_set():
        for name in NAMES:
            try:
                items = av_mailbox(name)
            except Exception:
                continue
            for it in items:
                uid = it.get("uuid")
                if not uid or uid in STATE["seen_av"]:
                    continue
                STATE["seen_av"].add(uid)
                env = it.get("envelope") or {}
                # decode payload
                text = ""
                try:
                    from uagents_core.envelope import Envelope

                    e = Envelope.model_validate(env)
                    text = e.decode_payload()
                except Exception:
                    text = str(env)[:200]
                kind, nonce, src = parse_a2a(text)
                if kind == "ping" and nonce and src:
                    av_send(name, src, f"[a2a] pong nonce={nonce} from={name} via=av")
                elif kind == "pong" and nonce:
                    RESULTS.setdefault(("pong", "av", nonce), time.time())
                try:
                    av_ack(name, uid)
                except Exception:
                    pass
        time.sleep(1.0)


def run_pair(transport, src, dst, timeout=DEADLINE):
    nonce = f"{transport}|{src}|{dst}|{time.time():.3f}|{secrets.token_hex(3)}"
    tag = f"{transport}:{src}->{dst}"
    t0 = time.time()
    ping = f"[a2a] ping nonce={nonce} from={src}"
    if transport == "e2a":
        st, body = e2a_send(src, dst, f"[a2a] ping {nonce}", ping)
    else:
        st, body = av_send(src, dst, ping)
    if st not in (200, 202):
        ERRORS[tag] = f"send HTTP {st}: {body}"
        return False, None
    # wait for pong recorded under nonce
    while time.time() - t0 < timeout:
        hit = RESULTS.get(("pong", "e2a" if transport == "e2a" else "av", nonce))
        if hit:
            rtt = hit - t0
            RESULTS.setdefault((transport, src, dst), []).append(rtt)
            return True, rtt
        time.sleep(0.15)
    ERRORS[tag] = f"no pong within {timeout}s (send st={st})"
    return False, None


def main():
    transports = sys.argv[1:] or ["e2a", "av"]
    if "av" in transports and not AV_KEY:
        print("WARN: no AGENTVERSE_API_KEY, skipping av")
        transports = [t for t in transports if t != "av"]
    if "e2a" in transports and not (E2A1 and E2A2):
        print("ERROR: need E2A_API_KEY and E2A2_API_KEY", file=sys.stderr)
        return 2

    stop = threading.Event()
    threads = []
    if "e2a" in transports:
        threads.append(threading.Thread(target=responder_e2a, args=(stop,), daemon=True))
    if "av" in transports:
        threads.append(threading.Thread(target=responder_av, args=(stop,), daemon=True))
    for t in threads:
        t.start()
    time.sleep(1.0)

    print(f"mesh: {len(NAMES)} agents x {len(transports)} transports")
    pairs = [(s, d) for s in NAMES for d in NAMES if s != d]
    ok = fail = 0
    rows = []
    for transport in transports:
        for src, dst in pairs:
            good, rtt = run_pair(transport, src, dst)
            if good:
                ok += 1
                rows.append((transport, src, dst, rtt))
                print(f"  OK  {transport:3} {src:16} -> {dst:16} rtt={rtt*1000:7.0f} ms")
            else:
                fail += 1
                print(f"  FAIL {transport:3} {src:16} -> {dst:16}  {ERRORS.get(transport+':'+src+'->'+dst,'')}")

    stop.set()
    time.sleep(0.5)

    print("\n=== LATENCY (ms) ===")
    for transport in transports:
        rtts = [r * 1000 for (tr, s, d), v in RESULTS.items() if tr == transport and isinstance(v, list) for r in v]
        if not rtts:
            print(f"  {transport}: no samples")
            continue
        rtts.sort()
        n = len(rtts)
        print(
            f"  {transport}: n={n} min={rtts[0]:.0f} p50={rtts[n//2]:.0f} "
            f"p95={rtts[min(n-1, int(n*0.95))]:.0f} max={rtts[-1]:.0f}"
        )

    print(f"\nRESULT ok={ok} fail={fail} total={ok+fail}")
    if ERRORS:
        print("\n=== ERRORS ===")
        for k, v in list(ERRORS.items())[:30]:
            print(f"  {k}: {v}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
