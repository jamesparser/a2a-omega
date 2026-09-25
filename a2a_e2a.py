"""e2a.dev mail transport for the A2A hub.

Each fleet identity owns an @agents.e2a.dev inbox. Two free accounts hold
three agents each. Send/receive use the e2a REST API:

    POST /v1/agents/{from}/messages   {"to": [addr], "subject": ..., "text": ...}
    GET  /v1/agents/{addr}/messages
    GET  /v1/agents/{addr}/messages/{id}   -> parsed.text

API keys are account-scoped (e2a_acct_...). The hub holds one or more keys
and routes by the sender's owning account. Browser-like User-Agent is
required (Cloudflare otherwise returns 1010).
"""
import json
import os
import urllib.error
import urllib.request

E2A_BASE = os.environ.get("E2A_BASE", "https://api.e2a.dev").rstrip("/")
UA = os.environ.get(
    "E2A_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 a2a-omega/1.2",
)


def _keys():
    """Return API keys from env (comma-separated) and/or e2a.env-style files."""
    keys = []
    for var in ("E2A_API_KEY", "E2A2_API_KEY", "A2A_E2A_API_KEY"):
        v = os.environ.get(var, "").strip()
        if v:
            keys.append(v)
    joined = os.environ.get("E2A_API_KEYS", "").strip()
    if joined:
        keys.extend(k.strip() for k in joined.split(",") if k.strip())
    # optional: E2A_KEY_FILES=path1:path2 with E2A_API_KEY=... lines
    for path in filter(None, os.environ.get("E2A_KEY_FILES", "").split(os.pathsep)):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("E2A") and "=" in line:
                        keys.append(line.split("=", 1)[1].strip())
        except OSError:
            continue
    # dedupe, keep order
    seen, out = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _req(method, url, key, body=None, timeout=20):
    data = json.dumps(body).encode() if body is not None else None
    if isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        return e.code, {"error": raw[:400]}
    except Exception as e:  # noqa: BLE001
        return None, {"error": f"{type(e).__name__}: {e}"}


def key_for(from_email):
    """Pick the API key that owns `from_email` (falls back to first key)."""
    keys = _keys()
    if not keys:
        return ""
    # Fast path: peer registry env maps email -> key
    override = os.environ.get("E2A_EMAIL_KEYS", "")
    if override:
        for part in override.split(","):
            if "=" in part:
                em, k = part.split("=", 1)
                if em.strip() == from_email:
                    return k.strip()
    # Discover which account owns this agent
    for k in keys:
        st, body = _req("GET", f"{E2A_BASE}/v1/agents/{from_email}", k)
        if st == 200 and body.get("email"):
            return k
    return keys[0]


def e2a_send(from_email, to_email, subject, text):
    """Send one message. Returns {"ok": True, "message_id": ...} or {"error": ...}."""
    key = key_for(from_email)
    if not key:
        return {"error": "e2a unavailable: no E2A_API_KEY configured"}
    st, body = _req(
        "POST",
        f"{E2A_BASE}/v1/agents/{from_email}/messages",
        key,
        {"to": [to_email], "subject": subject, "text": text},
    )
    if st in (200, 202) and isinstance(body, dict) and body.get("message_id"):
        return {"ok": True, "message_id": body["message_id"], "method": body.get("method")}
    return {"error": f"e2a HTTP {st}: {body}"}


def e2a_list(email, key=None):
    """List inbound messages for an agent address."""
    key = key or key_for(email)
    if not key:
        return []
    st, body = _req("GET", f"{E2A_BASE}/v1/agents/{email}/messages", key)
    if st == 200 and isinstance(body, dict):
        return body.get("items", [])
    return []


def e2a_get(email, message_id, key=None):
    """Full message; body text lives at parsed.text."""
    key = key or key_for(email)
    if not key:
        return {}
    st, body = _req(
        "GET", f"{E2A_BASE}/v1/agents/{email}/messages/{message_id}", key
    )
    return body if st == 200 else {}


def e2a_text(email, message_id, key=None):
    msg = e2a_get(email, message_id, key=key)
    parsed = msg.get("parsed") or {}
    return parsed.get("text") or msg.get("subject") or ""


# ---- hub integration ----------------------------------------------------

def hub_deliver(entry, from_email, to_email):
    """Deliver a hub task entry as an e2a message from from_email to to_email."""
    subject = f"[a2a] {entry.get('peer', to_email)}"
    text = entry if isinstance(entry, str) else json.dumps(entry, default=str)
    return e2a_send(from_email, to_email, subject, text)
