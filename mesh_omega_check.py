#!/usr/bin/env python3
"""VPS-side check: did AGENT's reply to agent-two land in agent-two's inbox
after SINCE (ISO Z)? Uses the banked key pool (no key printed).
usage: python3 mesh_omega_check.py <agent-name> [since_iso_z]"""
import json, os, sys, urllib.request
os.chdir("/workspace"); sys.path.insert(0, "/workspace")
import importlib.util
spec = importlib.util.spec_from_file_location("P", "/workspace/a2a_fleet_poller.py")
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
P.log = lambda m: None

agent = sys.argv[1] if len(sys.argv) > 1 else "agent-one"
since = sys.argv[2].strip() if len(sys.argv) > 2 else ""
INBOX = "agent-two@agentmail.to"

k = (P.am_keys().get(INBOX) or {}).get("key", "")
if not k:
    print("NO-KEY"); print("FAIL"); sys.exit(1)
try:
    req = urllib.request.Request(
        f"https://api.agentmail.to/v0/inboxes/{INBOX}/messages?limit=60",
        headers={"Authorization": "Bearer " + k})
    d = json.loads(urllib.request.urlopen(req, timeout=25).read())
except Exception as e:
    print(f"READ-ERR {type(e).__name__}"); print("FAIL"); sys.exit(1)
items = d if isinstance(d, list) else (d.get("messages") or d.get("items") or [])

hits = []
for m in items:
    c = str(m.get("createdAt") or m.get("created_at") or "")
    if since and c and c < since:
        continue
    subj = str(m.get("subject") or "")
    blob = subj + " " + str(m.get("bodyText") or m.get("text") or "")
    # new subject form: "[a2a] <agent> -> agent-two reply <task>"
    # legacy      form: "[a2a] <agent> reply <task>"
    if ("[a2a] " + agent in subj and "agent-two" in subj and "reply" in subj.lower()) or \
       ("[a2a] " + agent + " reply" in subj):
        hits.append((c, subj))
hits.sort()
for c, s in hits:
    print(f"  {c}  {s}")
print("PASS" if hits else "FAIL")
