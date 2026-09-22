"""Persistent inbox poller: watch N AgentMail inboxes for verify-links and
new M3 / compute API keys, auto-forwarding discovered keys to the hub owner.

Configuration (all via env, see .env.example) — NO secrets in this file:
  A2A_AGENTMAIL_API_KEY        API key for the inboxes you want to poll
  A2A_INBOXES                  comma list of inboxes, e.g. a@x.com,b@x.com
  A2A_ALERT_INBOX              inbox that receives [m3-alert] forwards (default: owner inbox)
  A2A_POLL_INTERVAL_SEC        default 300
"""
import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime

KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{20,}")
KW = ("minimax", " m3", "compute", "api key", "grant")
# Never treat our own fleet traffic as external key material:
SELF_TAGS = ("[a2a", "[m3-alert]", "connectivity", "directive", "selftest", "self-test")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def poll_inbox(inbox, key, limit=20):
    try:
        req = urllib.request.Request(
            f"https://api.agentmail.to/v0/inboxes/{inbox}/messages",
            headers={"Authorization": f"Bearer {key}"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return data.get("messages", [])
    except Exception as e:  # noqa: BLE001
        log(f"ERROR polling {inbox}: {e}")
        return []


def find_m3_keys(inbox, key, label, seen):
    """Return new external M3/compute keys detected in `msgs`."""
    msgs = poll_inbox(inbox, key)
    out = []
    for m in msgs:
        mid = m.get("message_id") or m.get("id") or ""
        subject = (m.get("subject") or "").lower()
        text = m.get("bodyText") or m.get("preview") or ""
        sender = (m.get("from") or "").lower()
        if any(tag in subject for tag in SELF_TAGS):
            continue
        if "agentmail" in sender or "jasonparser" in sender:
            continue
        hay = subject + " " + text.lower()
        if not any(k in hay for k in KW):
            continue
        found = KEY_RE.findall(text)
        if found:
            for k in found:
                if k not in seen:
                    seen.add(k)
                    out.append(f"[{label}] inbox={inbox} subject={m.get('subject')!r} key={k}")
        elif mid and mid not in seen:
            seen.add(mid)
            out.append(f"[{label}] inbox={inbox} subject={m.get('subject')!r} key=KEY-PENDING(see email)")
    return out


def forward(alert_inbox, owner_inbox, key, lines):
    body = ("NEW Minimax M3 / BGI compute key(s) detected. Forward into a coding lane "
            "or the BGI agent. Lines:\n" + "\n".join(lines))
    payload = json.dumps({
        "from": alert_inbox, "to": [owner_inbox],
        "subject": f"[m3-alert] {len(lines)} new key(s)",
        "messageText": body,
    }).encode()
    req = urllib.request.Request(
        f"https://api.agentmail.to/v0/inboxes/{alert_inbox}/messages/send",
        data=payload, headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20)
        for l in lines:
            log(f"  KEY AUTO-FORWARDED: {l}")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"  forward FAILED ({key[:12]}...): {e}")
        return False


def run_cycle(seen, inboxes, alert_inbox, owner_inbox, key):
    log("=" * 50)
    any_new = []
    for inbox, label in inboxes:
        new = find_m3_keys(inbox, key, label, seen)
        if new:
            any_new.extend(new)
    if any_new:
        forward(alert_inbox, owner_inbox, key, any_new)
    log(f"cycle done ({len(any_new)} new findings)")


def main():
    key = os.environ.get("A2A_AGENTMAIL_API_KEY", "")
    if not key:
        raise SystemExit("set A2A_AGENTMAIL_API_KEY")
    alert_inbox = os.environ.get("A2A_ALERT_INBOX", "")
    owner_inbox = os.environ.get("A2A_OWNER_INBOX", "")
    if not (alert_inbox and owner_inbox):
        raise SystemExit("set A2A_ALERT_INBOX and A2A_OWNER_INBOX (owner inbox that receives [m3-alert] forwards)")
    raw = os.environ.get("A2A_INBOXES", "")
    inboxes = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        # label optional after a '/'
        if "/" in part:
            inbox, label = [x.strip() for x in part.split("/", 1)]
        else:
            inbox, label = part, part.split("@")[0]
        inboxes.append((inbox, label))
    if not inboxes:
        raise SystemExit("set A2A_INBOXES (comma list)")
    interval = int(os.environ.get("A2A_POLL_INTERVAL_SEC", "300"))
    seen = set()

    log(f"A2A poller started: {len(inboxes)} inboxes every {interval}s, "
        f"alert->{alert_inbox}, owner->{owner_inbox}")
    run_cycle(seen, inboxes, alert_inbox, owner_inbox, key)
    while True:
        time.sleep(interval)
        run_cycle(seen, inboxes, alert_inbox, owner_inbox, key)


if __name__ == "__main__":
    main()
