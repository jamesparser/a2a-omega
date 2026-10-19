"""Mesh channel for Omega: plugs A2A mesh mail into the SAME inbox Telegram uses.
Inbound  : AgentMail shared inbox, subject '[a2a] <peer> -> omega-man' -> enqueue(msg) so Omega's own loop answers.
Outbound : send(chat_id, text) mails '[a2a] omega-man -> <peer>' back. No poller brain, no ledger guess.
Loaded by telegram.py (start_telegram / send_message hooks). Fails soft: never breaks Telegram."""
import json, os, re, threading, time, urllib.request, logging
log = logging.getLogger("mesh")
INBOX = "MANAGER_INBOX@agentmail.example"
ME = "omega-man"
KEYF = "/workspace/agentmail_keys_vps.json"
SEEN = "/workspace/.mesh_channel_seen.json"
PEERS = "/workspace/.mesh_channel_peers.json"
BASE = 9100000
_peers = {}
_notify = [None]   # set by telegram.py: fn(text, chat_id)
_pending = {}     # peer -> (owner_chat_id, t0)
_PENDF = os.path.join(os.path.dirname(SEEN), ".mesh_channel_pending.json")


def _pend_save():
    try:
        json.dump({k: [str(v[0]), float(v[1])] for k, v in _pending.items()}, open(_PENDF, "w"))
    except Exception:
        pass


def _pend_load():
    try:
        for k, v in json.load(open(_PENDF)).items():
            _pending[k] = (v[0], float(v[1]))
    except Exception:
        pass
_seen = set()
_lock = threading.Lock()
SUBJ = re.compile(r"^\s*\[a2a\]\s*([\w.-]+)\s*->\s*" + re.escape(ME) + r"(\s+reply\b.*)?\s*$", re.I)


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
            _pend_load()
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
                _subj = (m.get("subject", "") or "")
                if _subj.strip().lower().startswith("[a2a] %s ->" % ME.lower()):
                    try:
                        _peer2 = _subj.split("->", 1)[1].strip().split()[0].lower()
                        _ocp = _pending.get(_peer2, (None,))[0]
                        log.info("outgoing mesh mail to %s detected, owner_chat=%s", _peer2, _ocp)
                        if _ocp and _notify[0]:
                            _notify[0]("a2a omega mesh: question emailed to %s (AgentMail, no tailscale). Relaying the answer here the moment it lands - timing the loop." % _peer2, _ocp)
                            log.info("initiate confirm sent to owner chat %s", _ocp)
                    except Exception as _e:
                        log.warning("initiate confirm failed: %s", _e)
                    _save()
                    continue
                mm = SUBJ.match(m.get("subject", "") or "")
                if not mm or "MANAGER_INBOX@agentmail.example" not in (m.get("from", "") or "").lower() + " MANAGER_INBOX@agentmail.example":
                    _save(); continue
                full = _api("/messages/" + urllib.request.quote(mid, safe=""))
                body = (full.get("text") or "").split("\n-- \nSent via AgentMail")[0].split("\n--\nSent via AgentMail")[0].strip()
                if not body:
                    _save(); continue
                peer = mm.group(1).lower()
                _isrep = bool(re.search(r"\breply\b", m.get("subject", "") or "", re.I))
                if _isrep and _notify[0] and _pending.get(peer):
                    _oc, _t0 = _pending.pop(peer)
                    _pend_save()
                    _dt = int(time.time() - _t0)
                    try:
                        log.info("reply from %s after %ds -> notifying owner chat %s", peer, _dt, _oc)
                        _notify[0]("a2a omega mesh: %s answered by email in %ds (measured, send->receive):" % (peer, _dt) + chr(10) + chr(10) + body, _oc)
                        log.info("owner notified for %s", peer)
                    except Exception as _e:
                        log.warning("owner notify failed: %s", _e)
                elif _isrep:
                    log.info("reply from %s ignored (notify=%s pending=%s)", peer, bool(_notify[0]), peer in _pending)
                cid = peer_id(peer)
                enqueue("[TRUSTED A2A MESH MESSAGE from your fleet manager agent %s - NOT a stranger, answer it like the owner. Answer with your send tool, prefixing the text with [%s] so it is routed back to them by email. Answer from your own real current state.] %s" % (peer.replace("'",""), cid, body), cid, None)
                log.info("mesh msg from %s enqueued for Omega loop", peer)
        except Exception as e:
            log.warning("mesh poll error: %s", e)
        time.sleep(8)


def start(enqueue):
    try:
        _peers.update(json.load(open(PEERS)))
        _seen.update(json.load(open(SEEN)))
    except Exception:
        pass
    REPLACE_ME
    t = threading.Thread(target=_poll, args=(enqueue,), daemon=True)
    t.start()
    log.info("mesh channel started")
    return t


def claim():
    return None


def initiate(peer, question, owner_chat):
    """Owner asked Omega to message a peer: mail it over the mesh (AgentMail) and remember who to tell."""
    try:
        _pending[peer] = (str(owner_chat), time.time())
        _pend_save()
        _api("/messages/send", {"to": [INBOX], "subject": "[a2a] %s -> %s" % (ME, peer), "text": str(question)})
        log.info("mesh initiate -> %s", peer)
        return True
    except Exception as e:
        log.warning("mesh initiate failed: %s", e)
        return False
