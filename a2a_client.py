"""A2A client: one agent polls its own inbox for [a2a] tasks and sends to peers.

Usage:
  python a2a_client.py poll          # read + print new [a2a] tasks for ME
  python a2a_client.py check         # cron mode: exit 1 if new tasks, else 0
  python a2a_client.py send <peer> "message"   # send a task via the hub
  python a2a_client.py broadcast "message"     # fan out to every other peer

All configuration comes from environment variables (see .env.example):
  A2A_ME_INBOX    your AgentMail inbox, e.g. myagent@example.com
  A2A_AGENTMAIL_API_KEY   API key for your inbox (or A2A_VAULT_SITE if you use a vault)
  A2A_HUB         base URL of the hub, e.g. http://100.x.y.z:8787

Optional: `check` is for operators who want a shell exit code. Existing agent
pings already cover the usual 5 minute poll; this does not replace them.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime

ME_INBOX = os.environ.get("A2A_ME_INBOX", "")
HUB = os.environ.get("A2A_HUB", "http://127.0.0.1:8787")
A2A_TAG = "[a2a]"


def get_key():
    key = os.environ.get("A2A_AGENTMAIL_API_KEY", "")
    if not key:
        site = os.environ.get("A2A_VAULT_SITE", "")
        if site:
            import subprocess
            vault_py = os.environ["A2A_VAULT_PY"]
            vault = os.environ["A2A_VAULT"]
            mp = os.environ["VAULT_MASTER_PASSWORD"]
            r = subprocess.run(
                f'"{vault_py}" "{vault}" get {site} --master-password "{mp}"',
                shell=True, text=True, capture_output=True)
            for line in r.stdout.strip().splitlines():
                if line.startswith("password:"):
                    return line.split(":", 1)[1].strip()
            key = r.stdout.strip()
    if key.startswith("am_us_") or key.startswith("sk_"):
        return key
    for line in key.splitlines():
        if line.startswith("password:"):
            return line.split(":", 1)[1].strip()
    raise SystemExit("no AgentMail key: set A2A_AGENTMAIL_API_KEY or A2A_VAULT_SITE")


def am_get(inbox, key):
    req = urllib.request.Request(
        f"https://api.agentmail.to/v0/inboxes/{inbox}/messages",
        headers={"Authorization": f"Bearer {key}"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read()).get("messages", [])
    except Exception as e:  # noqa: BLE001
        return [{"ERROR": str(e)}]


def a2a_send(peer, text):
    payload = {
        "jsonrpc": "2.0",
        "id": f"client-{peer}-{datetime.now().strftime('%H%M%S')}",
        "method": "SendMessage",
        "peer": peer,
        "params": {"message": {
            "messageId": f"client-{peer}",
            "role": "ROLE_USER",
            "parts": [{"text": text}],
            "sender": ME_INBOX.split("@")[0] if ME_INBOX else "unknown-agent",
        }},
    }
    req = urllib.request.Request(
        HUB + "/a2a/v1",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception as e:  # noqa: BLE001
        return {"ERROR": str(e)}


def a2a_broadcast(text):
    payload = {
        "jsonrpc": "2.0",
        "id": f"bc-{datetime.now().strftime('%H%M%S')}",
        "method": "Broadcast",
        "peer": "*",
        "params": {"message": {
            "messageId": f"bc-{datetime.now().strftime('%H%M%S')}",
            "role": "ROLE_USER",
            "parts": [{"text": text}],
            "sender": ME_INBOX.split("@")[0] if ME_INBOX else "unknown-agent",
        }},
    }
    req = urllib.request.Request(
        HUB + "/a2a/v1",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:  # noqa: BLE001
        return {"ERROR": str(e)}


def cmd_poll(seen_file=None):
    """Read my inbox, print new [a2a] tasks I haven't seen yet."""
    seen = set()
    if seen_file and os.path.exists(seen_file):
        with open(seen_file) as f:
            seen = set(l.strip() for l in f if l.strip())
    if not ME_INBOX:
        raise SystemExit("set A2A_ME_INBOX")
    msgs = am_get(ME_INBOX, get_key())
    new = []
    for m in msgs:
        if "ERROR" in m:
            print(f"READ ERROR: {m['ERROR']}")
            continue
        subj = m.get("subject", "")
        if A2A_TAG in subj.lower() and m.get("message_id") not in seen:
            body = m.get("bodyText") or m.get("preview") or ""
            new.append(m.get("message_id"))
            print(f"\n== {A2A_TAG} from {m.get('from')} :: {subj}\n{body[:2000]}")
    if new and seen_file:
        with open(seen_file, "a") as f:
            for mid in new:
                f.write(mid + "\n")
    if not new:
        print(f"No new {A2A_TAG} tasks in {ME_INBOX}")
    return new


def cmd_check(seen_file=None):
    """Cron-friendly: print a one-line count. Exit 1 if there is new work, else 0."""
    new = cmd_poll(seen_file=seen_file)
    n = len(new or [])
    if n:
        print(f"NEW_TASKS={n}")
        return 1
    print("NEW_TASKS=0")
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    seen = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".a2a_seen")
    if sys.argv[1] == "poll":
        cmd_poll(seen_file=seen)
    elif sys.argv[1] == "check":
        raise SystemExit(cmd_check(seen_file=seen))
    elif sys.argv[1] == "send":
        peer, text = sys.argv[2], " ".join(sys.argv[3:])
        r = a2a_send(peer, text)
        print("sent OK" if ("task" in r or "result" in r) else f"ERROR: {r}")
    elif sys.argv[1] == "broadcast":
        text = " ".join(sys.argv[2:])
        r = a2a_broadcast(text)
        bc = (r.get("result") or {}).get("broadcast") or r
        if "ERROR" in r:
            print(f"ERROR: {r}")
        else:
            print(f"broadcast OK id={bc.get('id')} targets={bc.get('target_count')}")
            for t in bc.get("targets") or []:
                print(f"  {t.get('peer')}: {t.get('id')}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
