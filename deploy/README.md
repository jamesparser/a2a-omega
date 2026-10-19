# deploy — keeping every agent polling and answering

Runtime files for the fleet's answering loop. These live **outside** git on the
boxes that run them, which is how the hub and the keeper drifted from the repo
in the first place; they are version-controlled here so a redeploy from git
cannot silently regress them.

Since the 2026-10-04 cutover the answering loop is a single program,
`a2a_fleet_poller.py`, supervised by one keeper. The per-transport pollers it
replaced (AgentMail-only `poller.py`, the Agentverse-only `omega_poller.py`
actor, the e2a-only poller, and the legacy `lcb_responder.py` fallback lane)
are preserved under `legacy/` for reference only — none of them run.

| File | Runs on | Purpose |
|---|---|---|
| `omega_fleet_poller_keeper.sh` | host (`/root/`) | Ensures exactly one unified fleet-poller actor per fleet agent. Idempotent, restarts a dead actor within ~10s, kills duplicate and legacy catch-all pollers, self-heals the uagents SDK after a container recreate. |
| `omega-fleet-poller.service` | host (`/etc/systemd/system/`) | systemd unit; runs the keeper every 10s. |
| `run_fleet_poller_one.sh` | inside the poller container (`/workspace/`) | Launches one actor for one agent with that agent's brain slot, persona and ledger. |

## Topology

```
              three inbound lanes per agent (Agentverse mailbox,
              AgentMail inbox, e2a inbox)  ^
                                           | poll, answer for real
   +--------- host: omega container + systemd -------------+
   |  run_fleet_poller_one.sh <agent>   x N agents          |
   |        ^ kept alive by omega-fleet-poller.service      |
   +--------------------------------------------------------+

   +--------- laptop / hub box ----------------------------+
   |  a2a_hub.py on 127.0.0.1:8787 (or a Tailscale IP)     |
   |  routes tasks; signs outbound as a registered identity|
   +--------------------------------------------------------+
```

**One process per agent, always.** `__actor=<name>` narrows a process to a
single agent. Running one process with a multi-name `A2A_OWN_AGENTS`
*alongside* per-actor processes puts two pollers on every mailbox, racing on
the same `.av_seen_<agent>.json`, which duplicates answers and can resurrect
an envelope the other process already handled. The keeper kills any poller
that lacks `__actor=`.

## Inbound lanes, and where replies go

Each actor reads three lanes in parallel:

* **Agentverse mailbox** — persistent retry through upstream 503 / DNS flaps.
  Inbound is seeded from `.av_seen_<agent>.json` so a restart never
  re-answers a seen envelope.
* **AgentMail inbox** — unlimited, cross-account, the guaranteed reply lane.
* **e2a inbox** — read-only on the free tier (~20 msgs/day/account,
  cross-account send disabled), so it is read but never sent from.

Outbound replies go **AgentMail primary, Agentverse best-effort**. e2a is
never a send lane.

Reading what the fleet answered: every inbound reply is transcribed to the
actor log (`a2a_fleet_poller_<agent>.log`) as a one-line `INBOUND from
<address>: <body>` entry before being swept, so answers are never silently
lost.

## Answering, not acknowledging

Every `[a2a]` message gets a real answer:

* **status-shaped** questions (`working on`, `queue`, `need work`, `fleet
  status`) are answered from that agent's own ledger file
  (`A2A_LEDGER_DIR/<agent>.json`), so they cannot confabulate;
* **everything else** goes to that agent's own brain slot
  (`A2A_ANSWER_BASE` / `_KEY` / `_MODEL`, overridable per agent via
  `A2A_ANSWER_PROFILES`), with its persona and live ledger injected as
  context, so it answers as itself;
* if no brain is reachable the reply **says so** instead of inventing an
  answer;
* a `start your top task` directive pulls the top of that agent's own queue
  into `active` and confirms it (closed loop);
* if the sender address is not a registered agent (reply 404s), the answer is
  redirected to `A2A_REPLY_FALLBACK` (default empty = disabled) rather than
  dropped.

## Install / update

```bash
# host
install -m 755 deploy/omega_fleet_poller_keeper.sh /root/omega_fleet_poller_keeper.sh
install -m 644 deploy/omega-fleet-poller.service /etc/systemd/system/omega-fleet-poller.service
docker cp a2a_fleet_poller.py omega:/workspace/a2a_fleet_poller.py
docker cp omega_poller.py        omega:/workspace/omega_poller.py
docker cp deploy/run_fleet_poller_one.sh omega:/workspace/run_fleet_poller_one.sh
systemctl daemon-reload && systemctl restart omega-fleet-poller.service

# verify: exactly one actor per agent, no catch-all
ps aux | grep '[a]2a_fleet_poller.py'
tail -f /var/log/omega-fleet-poller.log
```

## Checks

```bash
python3 mesh_omega_check.py       # did a reply to a given agent land in its inbox
python3 verify_subjroute.py       # reply subject routing end to end
python3 mesh_test.py              # NxN delivery matrix across transports
```
