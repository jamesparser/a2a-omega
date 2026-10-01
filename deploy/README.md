# deploy — keeping every agent polling and answering

Runtime files for the fleet's answering loop. These live **outside** git on the
boxes that run them, which is how the hub and the keeper drifted from the repo
in the first place; they are version-controlled here so a redeploy from git
cannot silently regress them.

| File | Runs on | Purpose |
|---|---|---|
| `omega_poller_keeper.sh` | VPS host (`/root/`) | Ensures exactly one poller actor per fleet agent inside the `omega` container. Idempotent, self-heals the uagents SDK, kills legacy catch-all pollers. |
| `omega-a2a-poller.service` | VPS host (`/etc/systemd/system/`) | systemd unit; runs the keeper every 10s. |
| `run_poller_one.sh` | inside `omega` container (`/workspace/`) | Launches one actor for one agent with that agent's brain slot, persona and ledger. |

## Topology

```
                      Agentverse mailboxes (one per fleet agent)
                                     ^
                                     | poll every 2s, answer for real
   +--------------- VPS (omega container) ----------------+
   |  run_poller_one.sh jason-parser     (hub owner)       |
   |  run_poller_one.sh omega-man                          |
   |  run_poller_one.sh my-liberclaw                       |
   |  run_poller_one.sh my-betterclaw                       |
   |  run_poller_one.sh omega-liberclaw                     |
   |  run_poller_one.sh omega-betterclaw                    |
   |        ^ kept alive by omega-a2a-poller.service        |
   +-------------------------------------------------------+

   +--------------- gaming laptop (DESKTOP-IATAR1H) --------+
   |  a2a_hub.py  on 100.106.162.70:8787  (Tailscale)       |
   |  routes tasks; signs outbound as the jason-parser      |
   |  fleet identity so agent replies are deliverable       |
   +--------------------------------------------------------+
```

All six actors run on the VPS: that is where the Agentverse key, the shared
brain endpoint and this keeper already live, so one supervisor covers the whole
fleet. The hub stays on the laptop because it owns the Tailscale address the
fleet is configured to reach. jason-parser does **not** need to be co-located
with the hub - the hub routes, the poller answers.

**One process per agent, always.** A single poller started with a six-name
`A2A_OWN_AGENTS` serves the whole fleet. If per-actor processes also run, every
mailbox has two pollers racing on the same `.av_seen_<agent>.json`, which
duplicates answers and can resurrect an envelope the other process already
answered. `__actor=<name>` narrows a process to one agent, and the keeper kills
any poller that lacks it.

Reading what the fleet answered: jason-parser's actor transcribes every inbound
reply to `/workspace/omega_poller_jason-parser.log` as `INBOUND from <address>:
<body>` before sweeping it from the mailbox, so answers are never silently lost.

## Answering, not acknowledging

Every `[a2a]` message gets a real answer:

* **status-shaped** questions (`working on`, `queue`, `need work`, `fleet status`)
  are answered from that agent's own ledger file, so they cannot confabulate;
* **everything else** goes to that agent's own brain slot
  (`A2A_ANSWER_BASE` / `_KEY` / `_MODEL`, overridable per agent via
  `answer_profiles.json`), with its persona and live ledger injected as context,
  so it answers as itself;
* if no brain is reachable the reply **says so** instead of inventing an answer;
* a `start your top task` directive pulls the top of that agent's own queue into
  `active` and confirms it (closed loop);
* if the sender address is not a registered agent (reply 404s), the answer is
  redirected to `A2A_REPLY_FALLBACK` (default `jason-parser`) rather than dropped.

## Install / update

```bash
# VPS host
install -m 755 deploy/omega_poller_keeper.sh   /root/omega_poller_keeper.sh
install -m 644 deploy/omega-a2a-poller.service /etc/systemd/system/omega-a2a-poller.service
docker cp deploy/run_poller_one.sh omega:/workspace/run_poller_one.sh
docker cp omega_poller.py          omega:/workspace/omega_poller.py
systemctl daemon-reload && systemctl restart omega-a2a-poller.service

# verify: exactly one actor per agent, no catch-all
docker exec omega sh -c 'for p in /proc/[0-9]*/cmdline; do
  tr "\0" " " < "$p" 2>/dev/null; echo; done' | grep omega_poller
tail -f /var/log/omega-a2a-poller.log
```

## Regression tests

```bash
python3 test_omega_poller.py   # answering loop, retry bounds, 404 redirect
python3 test_a2a_hub.py        # transport chain agentverse -> e2a -> agentmail
```
