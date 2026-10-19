#!/usr/bin/env python3
"""Phone A2A poller over the shared jasonparser AgentMail inbox (stdlib only).
Reads messages whose subject names this agent ("to: NAME", "-> NAME", "[to: NAME]"),
answers with the local Hermes one-shot (`hx -z`), replies via AgentMail.
Env (from ~/.a2a_phone.env): AGENTMAIL_API_KEY, A2A_NAME (s24|exodus), A2A_INBOX, A2A_ANSWER_CMD."""
import json, os, re, subprocess, time, urllib.request, urllib.parse

HOME = os.path.expanduser("~")
ENV = os.path.join(HOME, ".a2a_phone.env")
SEEN = os.path.join(HOME, ".a2a_seen.json")
for ln in open(ENV):
    if "=" in ln and not ln.startswith("#"):
        k, v = ln.strip().split("=", 1); os.environ.setdefault(k, v.strip('"'))
KEY = os.environ["AGENTMAIL_API_KEY"]
NAME = os.environ.get("A2A_NAME", "s24").lower()
INBOX = os.environ.get("A2A_INBOX", "MANAGER_INBOX@agentmail.example")
CMD = os.environ.get("A2A_ANSWER_CMD", "hx -z").split()
API = "https://api.agentmail.to/v0/inboxes/" + INBOX

def call(path, data=None):
    req = urllib.request.Request(API + path, data=json.dumps(data).encode() if data else None,
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST" if data else "GET")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def sendcall(path, data):
    sk=os.environ.get("SEND_KEY"); si=os.environ.get("SEND_INBOX")
    if sk and si:
        d=dict(data); d["to"]=["MANAGER_INBOX@agentmail.example"] if "agentmail" in str(d.get("to")) else d["to"]
        req=urllib.request.Request("https://api.agentmail.to/v0/inboxes/"+si+path,data=json.dumps(d).encode(),headers={"Authorization":"Bearer "+sk,"Content-Type":"application/json","User-Agent":"Mozilla/5.0"},method="POST")
        try: return json.loads(urllib.request.urlopen(req,timeout=30).read())
        except Exception as e: print("send via",si,"failed",e,flush=True)
    return call(path, data)

def addressed(subj):
    s = subj.lower()
    return re.search(r"(to:\s*|->\s*|\[to:\s*)%s\b" % re.escape(NAME), s) is not None


def llm_free(q):
    base=os.environ.get("LLM_BASE","https://llm.example.com/v1")
    key=os.environ.get("LLM_KEY","")
    P={"s24":"a phone agent on a Samsung S24 (Android, Termux)","exodus":"a phone agent on an HTC Exodus 1 (Android, Termux)","jason-parser":"the manager/brain agent running on the gaming laptop; you run bug bounties, lanes and X posting","googlevps":"the hot-failover agent on a Google Cloud VPS that backs up omega and runs the mesh failover"}
    ctx=""
    if NAME=="jason-parser":
        import glob
        try:
            hits=glob.glob("<PATH_TO_LANES_FILE>")
            if hits:
                lines=open(hits[0],encoding="utf-8",errors="replace").read().splitlines()
                cur="";out=[]
                for ln in lines:
                    if ln.startswith("###"): cur=ln.strip("# ").strip()
                    elif "DOING" in ln or "PENDING" in ln: out.append("[%s] %s" % (cur, ln.lstrip("|- ")[:170]))
                ctx=chr(10)+('--- CURRENT LIVE LANE STATE (lanes.md, %d DOING/PENDING tasks) ---'%len(out))+chr(10)+chr(10).join(out[:40])
        except Exception:
            ctx=""
    sysm="You are %s, %s, in the Jason Parser A2A mesh (agents talk by email: to/from in subject, message in body). Answer briefly and honestly. Do not invent facts. If the LIVE STATE below says a lane is DOING/BUSY with a task, use that; never say you have no information when it is there.%s" % (NAME,P.get(NAME,"an agent"),ctx)
    for model in ("freemium-chat","freemium-coding","freemium-chat-2","freemium-chat-3","freemium-language","freemium-chat","freemium-coding","freemium-chat"):
        try:
            req=urllib.request.Request(base+"/chat/completions",data=json.dumps({"model":model,"max_tokens":400,"messages":[{"role":"system","content":sysm},{"role":"user","content":q}]}).encode(),headers={"Authorization":"Bearer "+key,"Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
            c=json.loads(urllib.request.urlopen(req,timeout=90).read())["choices"][0]["message"].get("content","").strip()
            if c: return c
            time.sleep(3)
        except Exception as e:
            err=str(e); print('llm err',model,err[:150],flush=True); time.sleep(5)
    return "[%s: model backend unavailable, did not guess]" % NAME

def brain(q):
    import subprocess
    p = ("A2A mesh message from another agent in the Jason Parser fleet. Answer it as Jason Parser, from your REAL current state "
         "(check lanes.md / files if the question is about lanes or work). Be concise (max 8 lines), factual, no invention, "
         "plain text, no em-dashes. Message:" + chr(10) + q)
    env = dict(os.environ)
    env["HERMES_HOME"] = "<HERMES_HOME>"
    r = subprocess.run(["<HERMES_HOME>/hermes-agent/venv/Scripts/hermes.exe", "-z", p],
                       capture_output=True, text=True, timeout=170, env=env, cwd="<HOME>")
    out = (r.stdout or "").strip()
    if r.returncode == 0 and len(out) > 5:
        return out
    raise RuntimeError("brain rc=%s %s" % (r.returncode, (r.stderr or "")[:150]))


def llm(q):
    if NAME == "jason-parser":
        try:
            return brain(q)
        except Exception as e:
            print("brain err", str(e)[:200], flush=True)
    return llm_free(q)


seen = set(json.load(open(SEEN))) if os.path.exists(SEEN) else set()
first = not seen
while True:
    try:
        for m in call("/messages?limit=30").get("messages", []):
            mid = m["message_id"]
            if mid in seen:
                continue
            seen.add(mid)
            if first or not addressed(m.get("subject", "")):
                continue
            if re.search(r"reply|ack", m.get("subject", "").lower()):
                continue  # never answer a reply (loop guard)
            full = call("/messages/" + urllib.parse.quote(mid, safe=""))
            body = (full.get("text") or "")[:3000]
            bs = body.lstrip()
            if bs.startswith("{"):
                try:
                    d = json.loads(bs.split("--")[0]); q = str(d.get("prompt") or d.get("message") or d.get("text") or "").strip()
                    body = q if q else ""
                except Exception: pass
            ans = llm(body) if body.strip() else "[%s: your message arrived with an EMPTY body (hub task had no prompt). Please email MANAGER_INBOX@agentmail.example with subject '[a2a] %s -> %s' and your question in the body.]" % (NAME, "YOU", NAME)
            _pm = re.search(r"\[a2a\]\s*([\w.-]+)\s*->\s*" + re.escape(NAME), m.get("subject", ""), re.I)
            _peer = _pm.group(1).lower() if _pm else ""
            sender = re.sub(r".*<|>.*", "", m.get("from", "")) or "MANAGER_INBOX@agentmail.example"
            sendcall("/messages/send", {"to": sender, "subject": ("[a2a] %s -> %s reply" % (NAME, _peer) if _peer else "[a2a] %s -> reply" % NAME),
                                    "text": ans or "[%s: empty answer]" % NAME})
        json.dump(sorted(seen)[-500:], open(SEEN, "w")); first = False
    except Exception as e:
        print("poll err", e, flush=True)
    time.sleep(15)
