import json, os, sys, urllib.request
os.chdir("/workspace"); sys.path.insert(0,"/workspace")
import importlib.util
spec=importlib.util.spec_from_file_location("P","/workspace/a2a_fleet_poller.py")
P=importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
P.log=lambda m:None
MARK=os.environ.get("PROOF_MARKER","")
keys=P.am_keys()
def read(inbox):
    k=(keys.get(inbox) or {}).get("key","")
    if not k: return []
    try:
        req=urllib.request.Request(f"https://api.agentmail.to/v0/inboxes/{inbox}/messages?limit=12",
            headers={"Authorization":"Bearer "+k})
        d=json.loads(urllib.request.urlopen(req,timeout=20).read())
        return d if isinstance(d,list) else (d.get("messages") or d.get("items") or [])
    except Exception as e:
        print(f"  read {inbox} failed: {type(e).__name__} {str(e)[:80]}"); return []

print(f"### agent-two inbox - looking for agent-one reply / marker {MARK!r}")
om=read("agent-two@agentmail.to")
hits=0
for m in om:
    subj=str(m.get("subject") or ""); blob=subj+" "+str(m.get("bodyText") or m.get("text") or "")
    tag=""
    if MARK and MARK in blob: tag=" **MARKER-HIT**"
    if ("agent-one" in subj and ("reply" in subj.lower() or "REPLY from jason" in blob)):
        hits+=1
        print(f"  subj={subj!r}  from={m.get('from')!r}{tag}")
        print(f"     {blob[:200]}")
print(f"  jason->omega reply hits in omega inbox: {hits}")
print("VERDICT:", "PASS - jason's reply reached agent-two's inbox" if (hits or any(MARK and MARK in str(m.get('bodyText') or '') for m in om)) else "NOT YET - reply not in omega inbox (hub may still be in agentverse retry)")
