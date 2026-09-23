"""agent2agent-hub: a small, self-hosted A2A (agent-to-agent) routing hub.

Bridges multiple AI agents over email transport (AgentMail inboxes) while
exposing an A2A-protocol-shaped JSON-RPC endpoint:

    POST /a2a/v1                    -> SendMessage (async delivery via email)
    GET  /.well-known/agent-card.json -> agent card
    GET  /tasks/<peer>              -> task history for a peer
    GET  /healthz                   -> liveness

Configuration is 100% via environment variables (see .env.example).
No secrets are baked into this file.

Security note
-------------
The hub trusts whoever can reach the port it binds. Bind to 127.0.0.1 or a
private network (e.g. Tailscale) by default -- never 0.0.0.0 on a public
interface. The hub only moves *text messages between agent inboxes*; it is
not a general proxy.
"""
import json
import os
import time
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ---------------------------------------------------------------- config
ROOT = os.path.dirname(os.path.abspath(__file__))

def _load_dotenv():
    """Load <ROOT>/.env if present (stdlib only). Never overrides an already-set
    environment variable, so real env / container config still wins."""
    fp = os.environ.get("A2A_ENV_FILE", os.path.join(ROOT, ".env"))
    if not os.path.exists(fp):
        return
    with open(fp, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_dotenv()

TASKS = os.environ.get("A2A_TASKS_DIR", os.path.join(ROOT, "tasks"))
INBOX_DIR = os.environ.get("A2A_INBOX_DIR", os.path.join(ROOT, "inbox"))
os.makedirs(TASKS, exist_ok=True)
os.makedirs(INBOX_DIR, exist_ok=True)

HUB_HOST = os.environ.get("A2A_HUB_HOST", "127.0.0.1")
HUB_PORT = int(os.environ.get("A2A_HUB_PORT", "8787"))
TICK_SEC = int(os.environ.get("A2A_TICK_SEC", "20"))

# Hub owner's AgentMail identity: which inbox sends out + the API key that
# may use it. The key comes from env (or the local vault helper, if
# A2A_VAULT_SITE is set and vault.py exists next to this tree).
HUB_SENDER_INBOX = os.environ.get("A2A_HUB_SENDER_INBOX", "")
AGENTMAIL_API_KEY = os.environ.get("A2A_AGENTMAIL_API_KEY", "")

# Optional: daily transcript of agent<->agent conversations, delivered by
# email to the hub owner. Empty = disabled.
TRANSCRIPT_EMAIL = os.environ.get("A2A_TRANSCRIPT_EMAIL", "")
TRANSCRIPT_INTERVAL_SEC = int(os.environ.get("A2A_TRANSCRIPT_INTERVAL_SEC", "86400"))

# Optional: webhook push on task terminal state (submitted->...->completed/failed).
# Empty = poll-only (default). Set to an HTTPS URL to receive JSON task events.
PUSH = os.environ.get("A2A_PUSH_WEBHOOK", "")

# Peer registry: {peer: {"inbox": ..., "agent_mail_key": ..., "note": ...}}
PEERS_FILE = os.environ.get("A2A_PEERS_FILE", os.path.join(ROOT, "config", "peers.json"))

AGENT_NAME = os.environ.get("A2A_AGENT_NAME", "a2a-hub")

# In-memory state
tasks = {}        # {peer: [ {id, sender, prompt, status, result, updated}, ...]}
_last_transcript_ts = [0.0]
_seen_reply_ids = set()


def load_peers():
    if os.path.exists(PEERS_FILE):
        with open(PEERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------- AgentMail
def _vault_key(site):
    """Optional helper: pull the API key from a local vault CLI.

    Expects `vault.py get <site> --master-password $VAULT_MASTER_PASSWORD`
    style output containing a 'password:' line. All paths/passwords come
    from the environment; nothing is hardcoded.
    """
    import subprocess
    vault_py = os.environ.get("A2A_VAULT_PY")
    vault = os.environ.get("A2A_VAULT")
    mp = os.environ.get("VAULT_MASTER_PASSWORD", "")
    if not (vault_py and vault and mp):
        raise RuntimeError("vault config incomplete (A2A_VAULT_PY/A2A_VAULT/VAULT_MASTER_PASSWORD)")
    r = subprocess.run(
        f'"{vault_py}" "{vault}" get {site} --master-password "{mp}"',
        shell=True, text=True, capture_output=True)
    for line in r.stdout.strip().splitlines():
        if line.startswith("password:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"vault missing key for site {site}: {r.stdout.strip()!r}")


def get_send_key():
    key = os.environ.get("A2A_SEND_KEY", "")
    if not key:
        site = os.environ.get("A2A_VAULT_SITE", "")
        if site:
            key = _vault_key(site)
        else:
            key = AGENTMAIL_API_KEY
    if not key:
        raise RuntimeError(
            "no AgentMail API key configured (set A2A_AGENTMAIL_API_KEY or A2A_VAULT_SITE)")
    if key.startswith("am_us_") or key.startswith("sk_"):
        return key
    # allow a 'password: am_us_...' blob
    for line in key.splitlines():
        if line.startswith("password:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"no usable key found in config: {key[:12]}...")


def am_send(to_inbox, subject, text):
    """Send via AgentMail. NOTE: AgentMail silently drops 'content'/'body'
    fields on send; 'text' is the field that reliably carries the body."""
    data = json.dumps({"to": [to_inbox], "subject": subject, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.agentmail.to/v0/inboxes/{HUB_SENDER_INBOX}/messages/send",
        data=data,
        headers={"Authorization": f"Bearer {get_send_key()}",
                 "Content-Type": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode(errors="replace")}


def am_poll(inbox, key):
    req = urllib.request.Request(
        f"https://api.agentmail.to/v0/inboxes/{inbox}/messages",
        headers={"Authorization": f"Bearer {key}"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read()).get("messages", [])
    except urllib.error.HTTPError:
        return []


# ---------------------------------------------------------------- MailSlurp fallback
MS_API_BASE = "https://api.mailslurp.com"
MS_DEFAULT_KEY_SITE = "MailSlurp"


def _vault_mailslurp_key(site_name=None):
    """Read a MailSlurp API key from the vault (never printed)."""
    site = site_name or os.environ.get("A2A_MAILSLURP_KEY_SITE", MS_DEFAULT_KEY_SITE)
    return _vault_key(site)


def ms_send_to_inbox(inbox_id, key, subject, text):
    """Send a test-style email into a MailSlurp inbox (POST /inboxes/{id}).
    Returns (status_code, response_body_or_None).
    Free tier: 1 To, plain/html body, no custom from.
    """
    url = f"{MS_API_BASE}/inboxes/{inbox_id}"
    payload = json.dumps({
        "to": [f"test+{int(time.time())}@sandbox.zazamail.link"],
        "subject": subject,
        "body": text,
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })
    try:
        r = urllib.request.urlopen(req, timeout=15)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def ms_retry(peer, entry, ms_inbox, ms_key_site=None):
    """Retry delivery via MailSlurp when AgentMail fails. Returns True on success."""
    key = _vault_mailslurp_key(ms_key_site)
    code, body = ms_send_to_inbox(ms_inbox, key, f"[a2a] {peer} retry",
                                  json.dumps(entry, default=str))
    if code == 201:
        print(f"[ms-fallback] POSTed to {ms_inbox} -> {code} OK")
        return True
    print(f"[ms-fallback] POST to {ms_inbox} -> {code} {body[:80]}")
    return False


# ---------------------------------------------------------------- routing
def _persist(peer, entry):
    fp = os.path.join(TASKS, f"{peer}.jsonl")
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _set_status(entry, peer, new_status, note=None):
    """Task state transition: submitted -> working -> completed/failed (+ optional webhook)."""
    entry["status"] = new_status
    entry["updated"] = time.time()
    entry.setdefault("history", []).append({"status": new_status, "ts": entry["updated"]})
    if note:
        entry["result"] = note
    if new_status in ("completed", "failed") and PUSH:
        _push(entry, peer)
    _persist(peer, entry)


def _push(entry, peer):
    """Fire-and-forget webhook notification on task terminal state (optional, opt-in)."""
    url = PUSH
    if not url:
        return
    payload = {"task": {"id": entry["id"], "peer": peer, "status": entry["status"],
                        "result": entry.get("result"), "history": entry.get("history")}}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": f"{AGENT_NAME}/1.1"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # noqa: BLE001 - push is best-effort
        print(f"[push] webhook {url} failed: {e}")


def _route(peer, entry):
    """Translate an A2A task into the peer's email transport."""
    global _seen_reply_ids
    _set_status(entry, peer, "working")
    try:
        peers = load_peers()
        p = peers.get(peer, {})
        key = p.get("agent_mail_key", "")
        inbox = p.get("inbox", "")
        if not key:
            _set_status(entry, peer, "failed", f"[hub] No transport configured for {peer}. Task stored.")
            return
        if not inbox:
            _set_status(entry, peer, "failed", f"[hub] Cannot deliver to {peer}: no inbox configured.")
            return
        res = am_send(inbox, f"[a2a] {peer}", json.dumps(entry, default=str))
        if isinstance(res, dict) and "error" in res:
            err_body = res["error"]
            # On HTTP 403 or error key -> retry via MailSlurp if peer has mailslurp fields
            ms_inbox = p.get("mailslurp_inbox", "")
            ms_key_site = p.get("mailslurp_api_key_site")
            if ms_inbox:
                ok = ms_retry(peer, entry, ms_inbox, ms_key_site)
                _set_status(entry, peer, "completed" if ok else "failed",
                           f"[hub] AM delivery failed ({res['error']}); MailSlurp fallback: {'ok' if ok else 'also failed'}")
            else:
                _set_status(entry, peer, "failed",
                           f"[hub] delivery FAILED to {inbox}: {res['error']}")
        else:
            _set_status(entry, peer, "completed",
                       f"[hub] Message delivered to {peer} ({inbox}). Poll for reply.")
    except Exception as e:  # noqa: BLE001
        _set_status(entry, peer, "failed", f"[hub] ROUTE EXCEPTION: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- transcript
def build_transcript(since_ts):
    """Collect agent<->agent exchanges persisted since `since_ts`."""
    lines = []
    for fp in sorted(os.listdir(TASKS)) if os.path.isdir(TASKS) else []:
        if not fp.endswith(".jsonl"):
            continue
        with open(os.path.join(TASKS, fp), encoding="utf-8") as f:
            for raw in f:
                try:
                    e = json.loads(raw)
                except Exception:
                    continue
                if e.get("updated", 0) >= since_ts:
                    lines.append((e.get("updated", 0), e))
    lines.sort(key=lambda t: t[0])
    out = []
    for _, e in lines:
        out.append(f"### {e.get('id')} [{e.get('status')}] from {e.get('sender')} -> {e.get('peer', '?')}")
        out.append(f"prompt: {(e.get('prompt') or '')[:500]}")
        if e.get("result"):
            out.append(f"result: {e.get('result')[:500]}")
        out.append("")
    return "\n".join(out) or "(no activity in the last 24h)"


def send_transcript(now):
    if not TRANSCRIPT_EMAIL:
        return
    body = build_transcript(now - TRANSCRIPT_INTERVAL_SEC)
    text = (f"Daily A2A transcript ({AGENT_NAME} hub)\n"
            f"Window: last {TRANSCRIPT_INTERVAL_SEC // 3600}h\n\n{body}")
    res = am_send(TRANSCRIPT_EMAIL, f"[a2a] daily transcript {time.strftime('%Y-%m-%d')}", text)
    print(f"[transcript] -> {TRANSCRIPT_EMAIL}: {res if isinstance(res, dict) and 'error' in res else 'sent'}")


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _send_json(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj, default=str).encode())

    def do_GET(self):
        if self.path == "/.well-known/agent-card.json":
            card = {
                "protocolVersion": "0.3.0",
                "name": AGENT_NAME,
                "description": "Self-hosted Agent2Agent routing hub: bridges AI agents "
                               "over email transport with an A2A-spec JSON-RPC interface. "
                               "Cross-ecosystem agent-to-agent messaging (OpenClaw, Hermes, "
                               "any JSON-RPC A2A client).",
                "url": f"http://{HUB_HOST}:{HUB_PORT}/a2a/v1",
                "preferredTransport": "JSONRPC",
                "capabilities": {
                    "streaming": False,
                    "pushNotifications": bool(os.environ.get("A2A_PUSH_WEBHOOK_DEFAULT", "")),
                    "stateTransitionHistory": True,
                },
                "defaultInputModes": ["text"],
                "defaultOutputModes": ["text"],
                "skills": [
                    {"id": "agent-to-agent", "name": "Cross-agent messaging",
                     "description": "Route a text task to a peer agent's inbox; async delivery, result on poll or webhook.",
                     "tags": ["a2a", "multi-agent", "whitehat"]},
                    {"id": "daily-transcript", "name": "Daily transcript",
                     "description": "Owner receives a digest of all agent<->agent exchanges.",
                     "tags": ["ops", "logging"]},
                ],
                "version": "1.1.0",
            }
            self._send_json(200, card)
        elif self.path.startswith("/tasks/"):
            peer = os.path.basename(self.path)
            self._send_json(200, tasks.get(peer, []))
        elif self.path == "/healthz":
            self._send_json(200, {"ok": True, "peers": len(load_peers()),
                                  "protocolVersion": "0.3.0"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") not in ("/a2a/v1", "/a2a/v1/"):
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body)
        except Exception:
            self._send_json(400, {"error": "bad json"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "expected a JSON object"})
            return

        method = payload.get("method")
        params = payload.get("params") or {}
        # A2A-spec methods + legacy aliases:
        #   SendMessage / message/send / tasks/send -> async task delivery
        #   tasks/get (alias GET /tasks/<id>) -> task state
        #   tasks/cancel -> mark a not-yet-completed task canceled
        #   OMITTED method (some A2A SDKs send params without a method field)
        if method in ("tasks/get", "task/get"):
            task_id = str((params.get("task_id") or params.get("id") or "")).replace("/", "_")
            for lst in tasks.values():
                for t in lst:
                    if t["id"] == task_id:
                        self._send_json(200, {"jsonrpc": "2.0", "id": payload.get("id"),
                                              "result": {"task": t}})
                        return
            self._send_json(404, {"jsonrpc": "2.0", "id": payload.get("id"),
                                  "error": f"task {task_id} not found"})
            return
        if method in ("tasks/cancel", "task/cancel"):
            task_id = str((params.get("task_id") or params.get("id") or "")).replace("/", "_")
            for lst in tasks.values():
                for t in lst:
                    if t["id"] == task_id and t["status"] in ("submitted", "working"):
                        t["_canceled"] = True
                        _set_status(t, t.get("peer", "?"), "canceled",
                                    "[hub] task canceled by client")
                        self._send_json(200, {"jsonrpc": "2.0", "id": payload.get("id"),
                                              "result": {"task": t}})
                        return
            self._send_json(404, {"jsonrpc": "2.0", "id": payload.get("id"),
                                  "error": f"task {task_id} not found or already terminal"})
            return
        if method not in ("SendMessage", "message/send", "tasks/send") and method is not None:
            self._send_json(400, {"jsonrpc": "2.0", "id": payload.get("id"),
                                  "error": f"unknown method {method!r}",
                                  "hint": "POST JSON-RPC to /a2a/v1: {\"method\":\"SendMessage\",\"peer\":\"<peer>\",\"params\":{\"message\":{\"messageId\":...,\"parts\":[{\"text\":...}],\"sender\":...}}} | tasks/get | tasks/cancel"})
            return

        params = payload.get("params") or {}
        msg = params.get("message") or {}
        if not isinstance(msg, dict):
            msg = {}
        task_id = msg.get("messageId") or payload.get("id") or f"task-{int(time.time())}"
        parts = msg.get("parts") or []
        if not isinstance(parts, list):
            parts = []
        prompt = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        sender = msg.get("sender") or payload.get("peer") or "unknown"
        peer = payload.get("peer") or sender
        peer = str(peer).replace("/", "_")

        entry = {"id": str(task_id), "peer": peer, "sender": sender, "prompt": prompt,
                 "status": "submitted", "result": None, "updated": time.time(),
                 "history": [{"status": "submitted", "ts": time.time()}]}
        tasks.setdefault(peer, []).append(entry)
        threading.Thread(target=_route, args=(peer, entry), daemon=True).start()

        self._send_json(200, {
            "jsonrpc": "2.0", "id": payload.get("id"),
            "result": {"task": {"id": str(task_id), "status": "submitted",
                                 "note": f"async delivery via email; poll GET /tasks/{peer} "
                                         f"or JSON-RPC tasks/get; history in task.stateTransitions"}},
        })


def poll_loop():
    """Every TICK_SEC: check each peer's inbox for replies; send transcript on schedule."""
    global _last_transcript_ts, _seen_reply_ids
    _last_transcript_ts[0] = time.time()
    while True:
        time.sleep(TICK_SEC)
        now = time.time()
        peers = load_peers()
        for peer, cfg in peers.items():
            inbox, key = cfg.get("inbox"), cfg.get("agent_mail_key")
            if not (inbox and key):
                continue
            for m in am_poll(inbox, key):
                mid = m.get("message_id") or m.get("id")
                if mid in _seen_reply_ids:
                    continue
                _seen_reply_ids.add(mid)
                text = m.get("bodyText") or m.get("text") or ""
                if not text:
                    continue
                data = None
                if text.lstrip().startswith("{"):
                    try:
                        data = json.loads(text)
                    except Exception:
                        data = None
                task_id = (data or {}).get("task_id") or (data or {}).get("id")
                for t in tasks.get(peer, []):
                    if t["id"] == task_id and t["status"] in ("submitted", "working"):
                        t["result"] = (data or {}).get("result") or text
                        _set_status(t, peer, "completed", "replied")
        if TRANSCRIPT_EMAIL and now - _last_transcript_ts[0] >= TRANSCRIPT_INTERVAL_SEC:
            _last_transcript_ts[0] = now
            try:
                send_transcript(now)
            except Exception as e:  # noqa: BLE001
                print(f"[transcript] failed: {e}")


def serve():
    server = ThreadingHTTPServer((HUB_HOST, HUB_PORT), Handler)
    print(f"A2A hub listening on http://{HUB_HOST}:{HUB_PORT} "
          f"(peers file: {PEERS_FILE}, transcript: {TRANSCRIPT_EMAIL or 'off'})")
    threading.Thread(target=poll_loop, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "transcript-now":
        send_transcript(time.time())
    else:
        serve()
