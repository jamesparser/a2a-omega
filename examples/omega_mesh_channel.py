import os
"""Mesh channel for Omega: plugs A2A mesh mail into the SAME inbox Telegram uses.
Inbound  : AgentMail shared inbox, subject '[a2a] <peer> -> omega-man' -> enqueue(msg) so Omega's own loop answers.
Outbound : send(chat_id, text) mails '[a2a] omega-man -> <peer>' back. No poller brain, no ledger guess.
Loaded by telegram.py (start_telegram / send_message hooks). Fails soft: never breaks Telegram."""
import json, os, re, threading, time, urllib.request, logging
log = logging.getLogger("mesh")
INBOX = os.environ.get("A2A_INBOX", "fleet@agentmail.to")
ME = "omega-man"
KEYF = "/workspace/agentmail_keys_vps.json"
SEEN = "/workspace/.mesh_channel_seen.json"
PEERS = "/workspace/.mesh_channel_peers.json"
BASE = 9100000
_peers = {}
_seen = set()
_lock = threading.Lock()
SUBJ = re.compile(r"^\s*\[a2a\]\s*([\w.-]+)\s*->\s*" + re.escape(ME) + r"\s*$", re.I)


def _key():
    return json.load(open(KEYF))[INBOX]["key"]


def _api(path, data=None):
    req = urllib.request.Request(
        "https://api.agentmail.to/v0/inboxes/" + INBOX + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + _key(), "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def _save():
    try:
        json.dump(sorted(_seen)[-2000:], open(SEEN, "w"))
        json.dump(_peers, open(PEERS, "w"))
    except Exception:
        pass


def peer_id(name):
    with _lock:
        if name not in _peers:
            _peers[name] = str(BASE + len(_peers) + 1)
            _save()
        return _peers[name]


def is_mesh_id(cid):
    return str(cid) in _peers.values()


def send(chat_id, text):
    name = next((n for n, i in _peers.items() if i == str(chat_id)), None)
    if not name:
        return False
    try:
        _api("/messages/send", {"to": [INBOX], "subject": "[a2a] %s -> %s" % (ME, name), "text": str(text)})
        log.info("mesh reply sent to %s (%d chars)", name, len(str(text)))
        return True
    except Exception as e:
        log.warning("mesh send failed: %s", e)
        return False


def _poll(enqueue):
    first = not os.path.exists(SEEN)
    while True:
        try:
            msgs = _api("/messages?limit=20").get("messages", [])
            if first:
                for m in msgs:
                    _seen.add(m["message_id"])
                _save(); first = False
            for m in sorted(msgs, key=lambda x: x.get("timestamp", "")):
                mid = m["message_id"]
                if mid in _seen:
                    continue
                _seen.add(mid)
                mm = SUBJ.match(m.get("subject", "") or "")
                if not mm or INBOX not in (m.get("from", "") or "").lower():
                    _save(); continue
                full = _api("/messages/" + urllib.request.quote(mid, safe=""))
                body = (full.get("text") or "").split("\n-- \nSent via AgentMail")[0].split("\n--\nSent via AgentMail")[0].strip()
                if not body:
                    _save(); continue
                peer = mm.group(1).lower()
                cid = peer_id(peer)
                enqueue("[A2A MESH message from agent '%s'. Reply to it normally; your reply is mailed back to them.] %s" % (peer, body), cid, None)
                log.info("mesh msg from %s enqueued for Omega loop", peer)
                _save()
        except Exception as e:
            log.warning("mesh poll error: %s", e)
        time.sleep(8)


def start(enqueue):
    try:
        _peers.update(json.load(open(PEERS)))
        _seen.update(json.load(open(SEEN)))
    except Exception:
        pass
    t = threading.Thread(target=_poll, args=(enqueue,), daemon=True)
    t.start()
    log.info("mesh channel started")
    return t
