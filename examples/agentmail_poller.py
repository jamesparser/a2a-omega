#!/usr/bin/env python3
"""A2A poller for the shared AgentMail inbox (stdlib only).
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
INBOX = os.environ.get("A2A_INBOX", "fleet@agentmail.to")
CMD = os.environ.get("A2A_ANSWER_CMD", "hx -z").split()
API = "https://api.agentmail.to/v0/inboxes/" + INBOX

def call(path, data=None):
    req = urllib.request.Request(API + path, data=json.dumps(data).encode() if data else None,
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"}, method="POST" if data else "GET")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

def addressed(subj):
    s = subj.lower()
    return re.search(r"(to:\s*|->\s*|\[to:\s*)%s\b" % re.escape(NAME), s) is not None


def llm(q):
    base=os.environ.get("LLM_BASE","https://your-llm-endpoint/v1")
    key=os.environ.get("LLM_KEY","")
    P={"my-agent":"a member of the fleet; answer the question directly and briefly"}
    sysm="You are %s, %s, in an A2A mesh (agents talk by email: to/from in subject, message in body). Answer briefly and honestly. Do not invent facts." % (NAME,P.get(NAME,"an agent"))
    for model in (os.environ.get("LLM_MODEL","your-model"),):
        try:
            req=urllib.request.Request(base+"/chat/completions",data=json.dumps({"model":model,"max_tokens":400,"messages":[{"role":"system","content":sysm},{"role":"user","content":q}]}).encode(),headers={"Authorization":"Bearer "+key,"Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
            c=json.loads(urllib.request.urlopen(req,timeout=90).read())["choices"][0]["message"].get("content","").strip()
            if c: return c
        except Exception as e:
            err=str(e)
    return "[%s: model backend unavailable, did not guess]" % NAME

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
            full = call("/messages/" + urllib.parse.quote(mid, safe=""))
            body = (full.get("text") or "")[:3000]
            bs = body.lstrip()
            if bs.startswith("{"):
                try:
                    d = json.loads(bs.split("--")[0]); q = str(d.get("prompt") or d.get("message") or d.get("text") or "").strip()
                    body = q if q else ""
                except Exception: pass
            ans = llm(body) if body.strip() else "[%s: your message arrived with an EMPTY body (hub task had no prompt). Please email fleet@agentmail.to with subject '[a2a] %s -> %s' and your question in the body.]" % (NAME, "YOU", NAME)
            sender = re.sub(r".*<|>.*", "", m.get("from", "")) or "fleet@agentmail.to"
            call("/messages/send", {"to": sender, "subject": "[a2a] %s -> reply" % NAME,
                                    "text": ans or "[%s: empty answer]" % NAME})
        json.dump(sorted(seen)[-500:], open(SEEN, "w")); first = False
    except Exception as e:
        print("poll err", e, flush=True)
    time.sleep(60)
