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
    """Optional helper: pull the AgentMail key from a local vault CLI.

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


# ---------------------------------------------------------------- routing
def _persist(peer, entry):
    fp = os.path.join(TASKS, f"{peer}.jsonl")
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _route(peer, entry):
    """Translate an A2A task into the peer's email transport."""
    global _seen_reply_ids
    try:
        peers = load_peers()
        p = peers.get(peer, {})
        key = p.get("agent_mail_key", "")
        inbox = p.get("inbox", "")
        if not key:
            entry["result"] = f"[hub] No transport configured for {peer}. Task stored."
            entry["status"] = "stored"
            return
        if not inbox:
            entry["result"] = f"[hub] Cannot deliver to {peer}: no inbox configured."
            entry["status"] = "error"
            return
        res = am_send(inbox, f"[a2a] {peer}", json.dumps(entry, default=str))
        if isinstance(res, dict) and "error" in res:
            entry["result"] = f"[hub] delivery FAILED to {inbox}: {res['error']}"
            entry["status"] = "error"
        else:
            entry["result"] = f"[hub] Message delivered to {peer} ({inbox}). Poll for reply."
            entry["status"] = "done"
    except Exception as e:  # noqa: BLE001
        entry["result"] = f"[hub] ROUTE EXCEPTION: {type(e).__name__}: {e}"
        entry["status"] = "error"
    finally:
        _persist(peer, entry)


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
                "name": AGENT_NAME,
                "description": "Self-hosted Agent2Agent routing hub: bridges AI agents "
                               "over email transport with an A2A-shaped JSON-RPC interface.",
                "url": f"http://{HUB_HOST}:{HUB_PORT}",
                "transport": {"type": "jsonrpc/http"},
                "capabilities": {"push": False},
            }
            self._send_json(200, card)
        elif self.path.startswith("/tasks/"):
            peer = os.path.basename(self.path)
            self._send_json(200, tasks.get(peer, []))
        elif self.path == "/healthz":
            self._send_json(200, {"ok": True, "peers": len(load_peers())})
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
        # Accept "SendMessage" (A2A), "message/send"/"tasks/send" (spec),
        # and OMITTED method (some A2A SDKs send params without it).
        if method not in ("SendMessage", "message/send", "tasks/send") and method is not None:
            self._send_json(400, {"error": f"unknown method {method!r}"})
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
                 "status": "running", "result": None, "updated": time.time()}
        tasks.setdefault(peer, []).append(entry)
        threading.Thread(target=_route, args=(peer, entry), daemon=True).start()

        self._send_json(200, {
            "jsonrpc": "2.0", "id": payload.get("id"),
            "result": {"task": {"id": str(task_id), "status": "submitted",
                                 "note": f"async delivery via email; poll GET /tasks/{peer}"}},
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
                    if t["id"] == task_id and t["status"] == "running":
                        t["result"] = (data or {}).get("result") or text
                        t["status"] = "done"
                        t["updated"] = now
                        _persist(peer, t)
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
