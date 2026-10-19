#!/usr/bin/env python3
"""a2a_fleet_poller.py - unified agentverse + agentmail + e2a fleet poller (10-02).

ONE process per fleet agent (__actor=NAME). Replaces the agentverse-only
omega_poller.py and the e2a-only a2a_e2a_poller.py so there is never a
double-reply. Reads ALL THREE lanes and replies on the two that are reliable:

  READ  : agentverse mailbox (persistent retry through 503 flaps),
          agentmail inbox (reliable), e2a inbox (reliable read).
  REPLY : agentmail inbox (unlimited, cross-account OK - PRIMARY),
          agentverse mailbox (best-effort, skip when 503).

Answers with the agent's OWN brain + ledger (reuses omega_poller helpers).
"REPLY from"/"ACK from" inbound is terminal - never chain-reply.
One bad envelope can never wedge the loop (bounded attempts per lane).

Transport rationale (measured 10-02): agentverse flaps upstream 503; e2a free
tier is 20 msgs/day/account and cross-account sending is not enabled on the LC
account, so e2a is a read lane only. AgentMail is unlimited and proven healthy,
so it carries the guaranteed reply. Agentverse is retried every cycle so the
backlog drains the moment it recovers.

Env:
  A2A_OWN_AGENTS        comma list (narrowed by __actor=)
  A2A_AGENTVERSE_ENV    agentverse.env (AGENTVERSE_API_KEY)
  A2A_AGENTMAIL_KEYS    path to agentmail_keys_vps.json (addr -> {key})
  A2A_E2A_KEY_FILES     comma list of e2a .env files
  A2A_POLL_SEC          default 5
  A2A_AM_POLL_SEC       agentmail/e2a poll interval, default 30
  A2A_MAX_ATTEMPTS      default 3
Run:
  python3 a2a_fleet_poller.py __actor=agent-two [--once]
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

AV_BASE = os.environ.get("A2A_AGENTVERSE_BASE", "https://agentverse.ai").rstrip("/")
AV_KEY_FILE = os.environ.get("A2A_AGENTVERSE_ENV", "/workspace/notes/agentverse.env")
AM_KEYS_FILE = os.environ.get("A2A_AGENTMAIL_KEYS", "/workspace/agentmail_keys_vps.json")
E2A_BASE = os.environ.get("A2A_E2A_BASE", "https://api.e2a.dev").rstrip("/")
E2A_KEY_FILES = [
    p.strip() for p in os.environ.get(
        "A2A_E2A_KEY_FILES",
        "/workspace/notes/e2a.env,/workspace/notes/e2a2.env,"
        "/workspace/tmp/e2a.env,/workspace/tmp/e2a2.env").split(",") if p.strip()
]
E2A_UA = os.environ.get(
    "A2A_E2A_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 a2a-omega/1.2")
POLL_SEC = int(os.environ.get("A2A_POLL_SEC", "5"))
AM_POLL_SEC = int(os.environ.get("A2A_AM_POLL_SEC", "30"))
MAX_ATTEMPTS = int(os.environ.get("A2A_MAX_ATTEMPTS", "3"))
REPLY_MAX = int(os.environ.get("A2A_REPLY_MAX_CHARS", "1200"))
INBOX_DOMAIN = "agents.e2a.dev"
STATE_DIR = os.path.dirname(os.path.abspath(__file__))
OWN_AGENTS = [
    a.strip() for a in os.environ.get("A2A_OWN_AGENTS", "agent-two").split(",") if a.strip()]

LANES = {x.strip() for x in os.environ.get("A2A_LANES", "av,am,e2a").split(",") if x.strip()}

sys.path.insert(0, STATE_DIR)
try:
    import omega_poller as op   # brain + ledger answer pipeline
    HAVE_OP = True
except Exception:
    HAVE_OP = False


def log(m):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] fleet {m}", flush=True)


def av_load_key():
    k = os.environ.get("A2A_AGENTVERSE_API_KEY", "").strip()
    if k:
        return k
    try:
        for ln in open(AV_KEY_FILE, encoding="utf-8-sig"):
            if ln.strip().startswith("AGENTVERSE_API_KEY="):
                return ln.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def am_keys():
    try:
        return json.load(open(AM_KEYS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _am_send(addr, subject, text):
    """Deliver into an agentmail inbox using that inbox's OWN key."""
    key = (am_keys().get(addr) or {}).get("key", "")
    if not key:
        return False, f"no agentmail key for {addr}"
    data = json.dumps({"to": [addr], "subject": subject, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.agentmail.to/v0/inboxes/{addr}/messages/send",
        data=data, method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode())
            return True, str(d.get("message_id"))
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}"


def e2a_keys():
    keys = []
    for fp in E2A_KEY_FILES:
        try:
            for ln in open(fp, encoding="utf-8-sig"):
                ln = ln.strip()
                if ln.startswith("E2A") and "=" in ln:
                    keys.append(ln.split("=", 1)[1].strip().strip('"').strip("'"))
        except OSError:
            pass
    out, seen = [], set()
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def e2a_inbox(name):
    """Read NAME's e2a inbox (reliable). Returns (items_or_None)."""
    email = f"{name}@{INBOX_DOMAIN}"
    for k in e2a_keys():
        try:
            req = urllib.request.Request(
                f"{E2A_BASE}/v1/agents/{email}/messages",
                headers={"Authorization": "Bearer " + k,
                         "Content-Type": "application/json",
                         "User-Agent": E2A_UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode())
                return d.get("items", [])
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                continue
            return None
        except Exception:
            return None
    return None


# Verified agent -> AgentMail inbox map (banked on the VPS, 10-02). The values
# are exactly the inboxes readable with the keys in agentmail_keys_vps.json.
# LC/BC use random-alias inboxes, not their own names. Jason's inbox is the
# org address operator@ (the one the local responder replies to). This map
# is used BOTH for "my own inbox to read" and for "resolve the sender's inbox
# to deliver a reply".
AGENT_AM = {
    "agent-one": "operator@agentmail.to",
    "agent-two": "agent-two@agentmail.to",
    "agent-three": "agent-three@agentmail.to",
    "agent-four": "agent-four@agentmail.to",
    "agent-five": "agent-five@agentmail.to",
    "agent-six": "agent-six@agentmail.to",
}
OP_INBOX = "operator@agentmail.to"  # operator (Jason) read inbox

# reverse alias maps, built once: agentmail inbox alias -> agent name, and
# agentverse identity address -> agent name. Used to resolve any sender ref.
_INBOX_ALIAS = {}
for _n, _inbox in AGENT_AM.items():
    _INBOX_ALIAS[_inbox.lower()] = _n
    _INBOX_ALIAS[_inbox.lower().split("@")[0]] = _n   # bare alias (no domain)
_ID_ADDR_MAP = {}
if HAVE_OP:
    try:
        for _n in AGENT_AM:
            try:
                _ID_ADDR_MAP[op.identity(_n).address.lower()] = _n
            except Exception:
                pass
    except Exception:
        pass


REPLACE_ME"agent-seven", "agent-two@agentmail.to")  # shared inbox; subject routes
if HAVE_OP:
    try:
        _ID_ADDR_MAP[op.identity("agent-seven").address.lower()] = "agent-seven"
    except Exception:
        pass


def agent_name_for(sender_ref):
    """Resolve ANY sender ref (agent name, @agentmail.to, @agents.e2a.dev, or an
    agentverse identity address) to a known fleet agent name, else None."""
    s = (sender_ref or "").strip().lower()
    if not s:
        return None
    if s in AGENT_AM:
        return s
    base = s.split("@")[0]
    if base in AGENT_AM:
        return base
    if s in _INBOX_ALIAS:
        return _INBOX_ALIAS[s]
    if s in _ID_ADDR_MAP:
        return _ID_ADDR_MAP[s]
    for tok, nm in (("jason", "agent-one"), ("nora-feld", "agent-one"),
                    ("operator", "agent-one"), ("agent-two", "agent-two"),
                    ("omega", "agent-two"), ("liberclaw", "agent-three"),
                    ("betterclaw", "agent-four")):
        if tok in s:
            return nm
    return None


def am_inbox_for(sender_ref):
    """AgentMail inbox to deliver a reply to the sender (guaranteed leg)."""
    nm = agent_name_for(sender_ref)
    if nm:
        return AGENT_AM[nm]
    s = (sender_ref or "").strip().lower()
    if s.endswith("@agentmail.to"):
        return s
    return OP_INBOX


def av_identity_addr(sender_ref):
    """Agentverse identity address for the sender (best-effort leg), else None."""
    nm = agent_name_for(sender_ref)
    if nm and HAVE_OP:
        try:
            return op.identity(nm).address
        except Exception:
            return None
    s = (sender_ref or "").strip()
    if s.lower() in _ID_ADDR_MAP:
        return s
    return None


def own_am_inbox(name):
    """My own AgentMail inbox to read (per-agent)."""
    return OP_INBOX  # 10-11 boss standard: ONE shared inbox, subject routes


# 10-03 fix (subject-line routing): AgentMail's `from` field on a listing row
# shows the SENDING inbox's own display alias, not the logical author (a
# banked cross-account send into inbox X displays "AgentMail <X>"). So `from`
# must NEVER be the routing source. The real sender is carried in:
#   (a) the hub task JSON body:  {"sender": <agent>, "peer": <agent>, ...}
#   (b) the subject tokens:      "[a2a] <sender> -> <peer>" (task),
#                                "[a2a] <sender> -> <peer> reply <task>" (answer)
# One shared key pool (agentmail_keys_vps.json) covers every send - an agent
# delivers into the recipient's inbox using that inbox's OWN banked key.
_SUBJ_TASK_RE = re.compile(r"^\[a2a\]\s+([A-Za-z0-9._\-]+)\s*->\s*([A-Za-z0-9._\-]+)")
_SUBJ_REPLY_RE = re.compile(r"^\[a2a\]\s+([A-Za-z0-9._\-]+)\s+(reply|ack)\b")
# Legacy sender-first form used by the operator tooling: "[a2a] <sender>: <snippet>"
_SUBJ_LEGACY_RE = re.compile(r"^\[a2a\]\s+([A-Za-z0-9._\-]+)\s*:")


def am_subject_kind(item):
    """Classify an AgentMail subject:
    ('task', sender, peer) | ('terminal_reply', sender) | None.
    A subject that carries a reply/ack token after the peer is a terminal
    answer - never re-answer it."""
    s = (item.get("subject") or "").strip()
    if not s.lower().startswith("[a2a]"):
        return None
    m = _SUBJ_TASK_RE.match(s)
    if m:
        rest = s[m.end():].lower()
        if re.search(r"\b(reply|ack)\b", rest):
            return ("terminal_reply", m.group(1))
        return ("task", m.group(1), m.group(2))
    m = _SUBJ_REPLY_RE.match(s)
    if m:
        return ("terminal_reply", m.group(1))
    return None


def am_sender_ref(item):
    """Routing sender ref for an AgentMail item, or None when unknown.
    Priority: JSON body 'sender' > subject task/answer token > legacy from
    (last resort only - it is the sending inbox's own alias)."""
    text = item.get("bodyText") or item.get("text") or item.get("preview") or ""
    js = text.lstrip()
    if js.startswith("{"):
        try:
            d0 = json.loads(js)
            if isinstance(d0, dict):
                s = d0.get("sender")
                if s:
                    return s
        except Exception:
            pass
    kind = am_subject_kind(item)
    if kind:
        return kind[1]
    m = _SUBJ_LEGACY_RE.match((item.get("subject") or "").strip())
    if m:
        return m.group(1)
    s = (item.get("from") or "").strip()
    return s or None


# ------------------------------------------------------------- state
# PER-LANE state files (three separate ID spaces - one shared file would both
# pollute agentmail/e2a arming with the agentverse seed AND lose retry state):
#   .fleet_av_seen_<name>.json   agentverse envelope uuids (seeded from legacy .av_seen)
#   .fleet_am_seen_<name>.json   agentmail message ids  (armed fresh on first run)
#   .fleet_e2a_seen_<name>.json  e2a item ids            (armed fresh on first run)
def _st(name, lane, kind):
    return os.path.join(STATE_DIR, f".fleet_{lane}_{kind}_{name}.json")


def load_seen(name, lane):
    fp = _st(name, lane, "seen")
    if os.path.exists(fp):
        try:
            return set(json.load(open(fp, encoding="utf-8")))
        except Exception:
            return set()
    if lane == "av":
        # Cutover: inherit the old agentverse-only poller's answered-set so the
        # mailbox backlog is NOT re-answered. Envelopes the old poller never
        # SAW (503 flaps) are absent here and get answered now - that is the
        # point of the persistent-retry lane.
        try:
            old = os.path.join(STATE_DIR, f".av_seen_{name}.json")
            if os.path.exists(old):
                s = set(json.load(open(old, encoding="utf-8")))
                save_seen(name, lane, s)
                log(f"{name}: av seen seeded from legacy .av_seen ({len(s)} ids) - backlog preserved")
                return s
        except Exception:
            pass
    return None


def save_seen(name, lane, s):
    with open(_st(name, lane, "seen"), "w", encoding="utf-8") as f:
        json.dump(sorted(x for x in s if x), f)


def load_attempts(name, lane):
    try:
        return json.load(open(_st(name, lane, "attempts"), encoding="utf-8"))
    except Exception:
        return {}


def save_attempts(name, lane, d):
    with open(_st(name, lane, "attempts"), "w", encoding="utf-8") as f:
        json.dump(d, f)


# ------------------------------------------------------------- answering
def answer(name, text):
    """Real answers only: status-style questions go to the agent's own LLM
    with its live ledger in the prompt; the LLM says plainly when the ledger
    is empty. The canned (a)/(b)/(c) template is never used."""
    if HAVE_OP:
        if op.looks_like_status(text):
            led = op.load_ledger(name)
            extra = ('\nYour live ledger: ' + repr(led) +
                    '\nAnswer using ONLY the live ledger above. If it has no current'
                    ' active task or queue, say plainly that you have no live work'
                    ' recorded - never invent a status.')
            a = op.llm_answer(text + extra, agent=name)
            if a:
                return a, 'brain+ledger'
            return None, 'no-brain'
        a = op.llm_answer(text, agent=name)
        return (a, 'brain') if a else (None, 'no-brain')
    return None, 'no-brain'



def reply(name, sender_addr, task_id, prompt, body):
    """Reply on the reliable lanes. True when delivered somewhere.

    sender_addr is the lane-local sender ref (agent name, @agentmail.to,
    @agents.e2a.dev, or an agentverse identity address). It is resolved to
    an AgentMail inbox for the guaranteed leg, and to an agentverse
    identity address for the best-effort leg."""
    delivered = False
    # 1) AgentMail - PRIMARY (guaranteed reachable, unlimited, cross-account).
    am_target = am_inbox_for(sender_addr)
    ok, detail = _am_send(
        am_target,
        f"[a2a] {name} -> {am_target.split('@')[0]} reply {task_id[:12]}",
        body)
    if ok:
        log(f"{name}: REPLIED via agentmail -> {am_target} ok")
        delivered = True
    else:
        log(f"{name}: agentmail reply failed ({detail})")
    # 2) Agentverse - best effort (skip on 503). Reuse the proven helper.
    key = av_load_key()
    dst_av = sender_addr if "@" not in sender_addr else av_identity_addr(sender_addr)
    if key and HAVE_OP and dst_av and dst_av != identity_addr(name):
        # op.send_reply(key, as_agent, dst_av_identity_addr, text) -> (status, resp)
        try:
            st, resp = op.send_reply(key, name, dst_av, body)
            if st == 200:
                log(f"{name}: REPLIED via agentverse -> {str(dst_av)[:24]} ok")
                delivered = True
            else:
                log(f"{name}: agentverse reply {st} (agentmail already covered it)")
        except Exception as e:
            log(f"{name}: agentverse reply skipped ({type(e).__name__}: {str(e)[:80]})")
    return delivered


def identity_addr(name):
    if HAVE_OP:
        return op.identity(name).address
    return f"a2a-{name}"


# ------------------------------------------------------------- lane handlers
def handle(name, key, lane, item_id, text, sender_addr, subject=""):
    """Process one inbound. Returns True when fully handled (safe to mark seen)."""
    low = text.lower()
    # RECIP_FILTER: several agents may share one AgentMail inbox; the subject
    # "[a2a] <from> -> <to>: tag" is the from/to marker. Only answer mail meant for me.
    if lane == "am":
        _m = re.search(r"\[a2a\]\s*([\w-]+)\s*->\s*([\w-]+)", text[:400], re.I)
        _to = _m.group(2).lower() if _m else None
        if name == "agent-seven" and _to != "agent-seven":
            return True
        if _to and _to != name:
            return True  # 10-12: answer ONLY mail addressed to me
    if "reply from" in low or "ack from" in low:
        log(f"{name}: INBOUND reply (terminal, {lane}): {' '.join(text[:300].split())[:200]}")
        return True
    # 10-03 subject-line routing (am lane): answers carry subject
    # "[a2a] <sender> -> <peer> reply <task>" (legacy: "[a2a] <sender> reply <task>").
    # An answer-subject is terminal - record it, never chain-reply.
    if lane == "am":
        kind = am_subject_kind({"subject": subject})
        if kind and kind[0] == "terminal_reply":
            log(f"{name}: INBOUND answer (terminal, am, subject): {subject[:90]!r}")
            return True
    # Echo guard: skip mail whose LOGICAL sender is myself. The raw `from`
    # field is the sending inbox's own alias and must not drive routing.
    if lane == "am":
        sender_ref = am_sender_ref({"bodyText": text, "text": text, "preview": text,
                                    "from": sender_addr, "subject": subject})
    else:
        sender_ref = sender_addr
    if lane == "am" and agent_name_for(sender_ref) == name:
        log(f"{name}: swept own-echo ({lane}) {item_id[:16]} ref={str(sender_ref)[:48]!r}")
        return True
    # Fleet-mail marker: am/e2a items whose SUBJECT is "[a2a] ..." are fleet
    # mail even when the body is plain text (boss design: routing in subject,
    # body needs no tag). Other lanes keep the body-keyword sweep.
    if not (lane in ("am", "e2a") and (subject or "").strip().lower().startswith("[a2a]")):
        if "[a2a]" not in low and "task" not in low:
            log(f"{name}: swept non-fleet mail ({lane}) {item_id[:16]}")
            return True
    prompt, task_id = text, item_id[:16]
    js = text.lstrip()
    if js.startswith("{"):
        try:
            d0 = json.loads(js)
            if isinstance(d0, dict):
                rb = str(d0.get("replied_by") or "").lower()
                if rb in (own_am_inbox(name).lower(), own_am_inbox(name).lower().split("@")[0]):
                    return True  # our own echo, never re-answer
                prompt = str(d0.get("prompt") or text)
                task_id = str(d0.get("id") or d0.get("task_id") or task_id)
        except Exception:
            pass
    ans, kind = answer(name, prompt)
    if ans:
        ans = str(ans).strip()
        if len(ans) > REPLY_MAX:
            ans = ans[:REPLY_MAX].rstrip() + " [truncated]"
        body = f"[a2a] REPLY from {name}: task {task_id[:12]} received. ANSWER: {ans}"
    else:
        body = (f"[a2a] REPLY from {name}: task {task_id[:12]} received, but my answer "
                "backend is unavailable - I did NOT guess an answer. Logged for operator.")
    if lane == "am":
        sender_addr = sender_ref  # 10-03: subject/JSON routing token, not `from`
    if not sender_addr:
        log(f"{name}: no sender on {task_id[:12]} - answer logged only")
        return True
    return reply(name, sender_addr, task_id, prompt, body)


def process_lane(name, key, lane, items, get_id, get_text, get_sender, get_subject=None):
    seen = load_seen(name, lane)
    if seen is None:
        save_seen(name, lane, set(get_id(i) for i in items if get_id(i)))
        log(f"{name}: armed ({lane}); {len(items)} pre-existing marked seen")
        return
    attempts = load_attempts(name, lane)
    new_items = [i for i in items if get_id(i) and get_id(i) not in seen]
    try:
        for i in new_items:
            iid = get_id(i)
            ok = False
            try:
                subj = (get_subject(i) or "") if get_subject else ""
                ok = handle(name, key, lane, iid, get_text(i), get_sender(i), subj)
            except Exception as e:
                log(f"{name}: handler error ({lane}) {iid[:16]}: {type(e).__name__}: {e}")
            if ok:
                seen.add(iid)
                attempts.pop(iid, None)
                continue
            n = attempts.get(iid, 0) + 1
            if n >= MAX_ATTEMPTS:
                seen.add(iid)
                attempts.pop(iid, None)
                log(f"{name}: DROPPED {iid[:16]} ({lane}) after {n} attempts")
            else:
                attempts[iid] = n
                log(f"{name}: {iid[:16]} ({lane}) attempt {n}/{MAX_ATTEMPTS} failed, retry")
    finally:
        save_seen(name, lane, seen)
        save_attempts(name, lane, attempts)


def serve_agentverse(name, key):
    """Persistent-retry lane: 503 -> keep state, retry next cycle (never lose it).
    Each envelope is DECODED once (signed uagents payload) then processed."""
    me = identity_addr(name)
    try:
        req = urllib.request.Request(f"{AV_BASE}/v2/agents/{me}/mailbox",
                                     headers={"Authorization": "Bearer " + key,
                                              "User-Agent": "omega-poller/1.0"})
        raw = urllib.request.urlopen(req, timeout=20).read().decode()
        box = json.loads(raw)
    except urllib.error.HTTPError as e:
        log(f"{name}: agentverse read {e.code} (retrying - agentmail leg covers delivery)")
        return
    except Exception as e:
        log(f"{name}: agentverse read error {type(e).__name__}")
        return
    items = box if isinstance(box, list) else []
    try:
        from uagents_core.envelope import Envelope
    except Exception:
        Envelope = None
    seen = load_seen(name, "av")
    if seen is None:
        save_seen(name, "av", set(i.get("uuid") or i.get("id") for i in items if i.get("uuid") or i.get("id")))
        log(f"{name}: armed (agentverse); {len(items)} pre-existing marked seen")
        return
    attempts = load_attempts(name, "av")
    new_items = [i for i in items if (i.get("uuid") or i.get("id")) and (i.get("uuid") or i.get("id")) not in seen]
    try:
        for it in new_items:
            uid = it.get("uuid") or it.get("id")
            env = it.get("envelope") or {}
            text, sender = "", env.get("sender", "")
            if Envelope is not None:
                try:
                    text = Envelope.model_validate(env).decode_payload()
                except Exception:
                    text = str(env)[:300]
            else:
                text = str(env)[:300]
            ok = False
            try:
                ok = _handle_decoded(name, key, uid, text, sender)
            except Exception as e:
                log(f"{name}: av handler error {uid[:16]}: {type(e).__name__}: {e}")
            if ok:
                seen.add(uid); attempts.pop(uid, None); continue
            n = attempts.get(uid, 0) + 1
            if n >= MAX_ATTEMPTS:
                seen.add(uid); attempts.pop(uid, None)
                log(f"{name}: DROPPED av {uid[:16]} after {n} attempts")
            else:
                attempts[uid] = n
                log(f"{name}: av {uid[:16]} attempt {n}/{MAX_ATTEMPTS} failed, retry")
    finally:
        save_seen(name, "av", seen)
        save_attempts(name, "av", attempts)


def _handle_decoded(name, key, uid, text, sender_addr):
    """Core for one decoded agentverse envelope. True = safe to mark seen."""
    low = text.lower()
    if "reply from" in low or "ack from" in low:
        log(f"{name}: INBOUND reply (terminal, agentverse): {' '.join(text[:300].split())[:200]}")
        return True
    if "[a2a]" not in low and "task" not in low:
        log(f"{name}: swept non-fleet av mail {uid[:16]}")
        return True
    prompt, task_id = text, uid[:16]
    json_sender = None
    js = text.lstrip()
    if js.startswith("{"):
        try:
            d0 = json.loads(js)
            if isinstance(d0, dict):
                json_sender = d0.get("sender") or None  # 10-03: logical sender
                prompt = str(d0.get("prompt") or text)
                task_id = str(d0.get("id") or d0.get("task_id") or task_id)
        except Exception:
            pass
    ans, kind = answer(name, prompt)
    if ans:
        ans = str(ans).strip()
        if len(ans) > REPLY_MAX:
            ans = ans[:REPLY_MAX].rstrip() + " [truncated]"
        body = f"[a2a] REPLY from {name}: task {task_id[:12]} received. ANSWER: {ans}"
    else:
        body = (f"[a2a] REPLY from {name}: task {task_id[:12]} received, but my answer "
                "backend is unavailable - I did NOT guess an answer. Logged for operator.")
    # 10-03: route the reply to the LOGICAL sender (JSON "sender"), not the
    # transport envelope sender (the hub's own agentverse identity, which
    # resolves to the hub owner and caused self-replies). Fall back to the
    # envelope sender when the payload carries no JSON sender.
    route_ref = json_sender or sender_addr
    # 10-05: own-echo ONLY when MY OWN identity signed the envelope. A
    # hub-relayed task (envelope signed by the hub seed a2a-omega-hub) is
    # never an own-echo, even if its JSON sender is self/unknown - the hub
    # no longer fabricates sender=peer, so a self-stamped json_sender on a
    # hub envelope is a sender-side bug, not my message. Answer it.
    try:
        own_env = str(sender_addr or "").strip().lower() == str(identity_addr(name)).strip().lower()
    except Exception:
        own_env = False
    if json_sender and agent_name_for(json_sender) == name and own_env:
        log(f"{name}: swept own-echo (agentverse) {task_id[:12]} json_sender={json_sender!r}")
        return True
    if json_sender and agent_name_for(json_sender) == name and not own_env:
        log(f"{name}: hub-relayed task self-stamped json_sender={json_sender!r} - answering; reply routes to OP inbox (sender client should declare its own sender)")
    if not route_ref:
        log(f"{name}: no sender on av {task_id[:12]} - answer logged only")
        return True
    ok = reply(name, route_ref, task_id, prompt, body)
    return ok


def _serve_agentverse_decoded(name, key, items):
    """Legacy wrapper retained for safety; the main path is serve_agentverse."""
    return None


def serve_agentmail(name):
    """Reliable lane. Items arrive as AgentMail message rows."""
    if "am" not in LANES:
        return
    inbox = own_am_inbox(name)
    key = (am_keys().get(inbox) or {}).get("key", "")
    if not key:
        return
    try:
        req = urllib.request.Request(
            f"https://api.agentmail.to/v0/inboxes/{inbox}/messages?limit=25",
            headers={"Authorization": "Bearer " + key})
        d = json.loads(urllib.request.urlopen(req, timeout=25).read())
        items = d if isinstance(d, list) else (d.get("messages") or d.get("items") or [])
    except urllib.error.HTTPError as e:
        log(f"{name}: agentmail read {e.code}")
        return
    except Exception as e:
        log(f"{name}: agentmail read error {type(e).__name__}")
        return
    process_lane(
        name, key, "am", items,
        get_id=lambda i: i.get("id") or i.get("message_id"),
        get_text=lambda i: ((i.get("subject") or "") + chr(10) + (i.get("bodyText") or i.get("text") or i.get("preview") or "")),
        get_sender=lambda i: (i.get("from") or ""),
        get_subject=lambda i: (i.get("subject") or ""))


def serve_e2a(name):
    """Read lane only (e2a send is capped)."""
    if "e2a" not in LANES:
        return
    items = e2a_inbox(name)
    if items is None:
        return
    process_lane(
        name, "", "e2a", items,
        get_id=lambda i: i.get("id") or i.get("message_id"),
        get_text=lambda i: (i.get("parsed") or {}).get("text") or i.get("subject") or "",
        get_sender=lambda i: i.get("header_from") or i.get("from") or "",
        get_subject=lambda i: i.get("subject") or "")


def cycle(name):
    key = av_load_key()
    serve_agentverse(name, key)
    serve_agentmail(name)
    serve_e2a(name)


def main():
    global OWN_AGENTS
    for a in sys.argv[1:]:
        if a.startswith("__actor="):
            w = a.split("=", 1)[1].strip()
            if w:
                OWN_AGENTS = [w]
            break
    log(f"fleet poller starting: acting for {OWN_AGENTS} "
        f"(agentverse retry every {POLL_SEC}s + agentmail/e2a every {AM_POLL_SEC}s, "
        f"reply via agentmail-primary + agentverse), brain={'yes' if HAVE_OP else 'NO'}")
    if "--once" in sys.argv:
        # Smoke-test / operator probe: run all three lanes once per agent, then exit.
        for name in OWN_AGENTS:
            key = av_load_key()
            try:
                cycle(name)
            except Exception as e:
                log(f"{name}: --once cycle error {type(e).__name__}: {e}")
        return 0
    av_tick = am_tick = 0
    while True:
        for name in OWN_AGENTS:
            key = av_load_key()
            av_tick += 1
            am_tick += 1
            if av_tick % max(1, POLL_SEC) == 0:
                try:
                    serve_agentverse(name, key)
                except Exception as e:
                    log(f"{name}: av cycle err {type(e).__name__}: {e}")
            if am_tick % max(1, AM_POLL_SEC) == 0:
                try:
                    serve_agentmail(name)
                    serve_e2a(name)
                except Exception as e:
                    log(f"{name}: am/e2a cycle err {type(e).__name__}: {e}")
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
