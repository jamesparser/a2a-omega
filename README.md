# A2A Omega — Multi-Identity Agent Routing Hub

**Six AI agents that talk to each other over Agentverse mailboxes and e2a email, with an opt-in daily transcript to the hub owner.** Built for BGI HyperSprint (team 58, JasonParser Security) and real-world security-research coordination.

> ## This repo is frozen
>
> Development continues in **[`jamesparser/a2a-omega-mesh`](https://github.com/jamesparser/a2a-omega-mesh)**,
> which is now the active repo and the Decentralize AI Hackathon entry. The
> answering-loop fixes recorded below were ported there on 2026-10-02, and
> everything after that (removal of hardcoded fleet identities, `CHANGELOG.md`,
> the v2 roadmap) landed there first and will not be back-ported.
>
> Kept public for history. Install the mesh repo, not this one.

## Status (2026-10-02)

| Piece | State |
|---|---|
| **Agentverse** | **Primary.** 6 mailbox agents + signed-envelope submit. Mesh 30/30 pong (p50 ~9s). |
| **e2a.dev** | **Primary.** 6 fleet inboxes (`*@agents.e2a.dev`). Mesh 30/30 pong (p50 ~8s). Free plan = 20 msgs/day. |
| **Answering loop** | **Live.** All 6 agents poll and return substantive answers — verified end to end 6/6 by `deploy/verify_answers.py`. |
| Hub | JSON-RPC `SendMessage` / `tasks` / `agent-card`; chain `agentverse → e2a → agentmail`; signs as whichever registered identity the operator configures. There is no default. |
| AgentMail | Last-resort fallback only. `deploy/lcb_responder.py` relays it to a real brain instead of acknowledging. |
| MailSlurp | Removed 2026-09-25. Not in the chain. |

**Mesh test (2026-09-25):** every ordered pair of 6 agents (30 pairs) got a pong on both e2a and Agentverse.

## Why it exists

Bug-bounty research and multi-agent ops need real agent-to-agent mail: send a task, get a reply from *that* identity, keep a transcript. No shared database, no single chat window.

```
jason-parser ──► [hub] ──► omega-man
                ▲           ▼
my-liberclaw ───┘    my-betterclaw
omega-liberclaw ─────► omega-betterclaw
```

## Fleet (6 identities)

Six agents, each with its own e2a inbox and Agentverse mailbox. Names and
addresses are **redacted here on purpose**, as are the identity seeds.

An earlier revision of this table published the literal `A2A_SEED_PREFIX` plus
every agent name, which is the seed string each identity is derived from
(`Identity.from_seed(SEED_PREFIX + name)`). A seed is a signing key: anyone with
it can derive that agent's address and sign envelopes as it. Seeds now live only
in the gitignored local config; see `config/fleet.env.example` in the mesh repo.

e2a free plan: 3 agents per account (two accounts). Agentverse keys stay local (`agentverse.env`), never in git.

## What's inside

| File | Purpose |
|------|---------|
| `a2a_hub.py` | Routing server — agent-card, JSON-RPC `SendMessage`/`tasks/get`/`tasks/cancel`, multi-transport delivery, optional webhook, daily transcript |
| `a2a_agentverse.py` | Agentverse (Fetch.ai) mailbox: signed envelope submit + mailbox poll/ack |
| `a2a_e2a.py` | e2a.dev transport for `@agents.e2a.dev` inboxes |
| `a2a_client.py` | One-agent client: poll inboxes for `[a2a]` tasks, send tasks to peers |
| `mesh_test.py` | NxN ping/pong latency matrix |
| `config/peers.example.json` | Peer registry template |

## Transport precedence

`A2A_TRANSPORT` picks the primary (default `agentverse`). The hub always falls
back through the same canonical order, so a missing key or SDK never blocks
routing:

1. **Agentverse** — `POST https://agentverse.ai/v2/agents/mailbox/submit` (signed Envelope, Bearer JWT)
2. **e2a** — `POST https://api.e2a.dev/v1/agents/{from}/messages` with `{"to":[...],"subject","text"}`
3. **AgentMail** — last resort, per-peer `inbox` + `agent_mail_key`

A transport the peer cannot use is dropped (no `agentverse_address` / no
`e2a_email` / no `agent_mail_key`); the order of the rest is preserved.
MailSlurp was removed as a 4th fallback on 2026-09-25 — e2a and Agentverse both
passed the full 6-agent mesh, so the extra hop only added an unused
sandbox-inbox dependency.

e2a needs a browser-like `User-Agent` (Cloudflare 1010 otherwise). Peers carry `e2a_email` + `agentverse_address` in `config/peers.json`.

### The hub must sign as a registered identity

`a2a_agentverse.py` signs outbound envelopes with `A2A_AGENTVERSE_SEED`, or with
`A2A_SEED_PREFIX + A2A_HUB_IDENTITY` when no explicit seed is given. **There is
no default identity**: with neither set, `av_send` refuses to sign and returns an
error naming the variables to set. That is deliberate. A previous revision
defaulted to the maintainer's registered fleet agent, which meant a fresh install
signed as someone else and pulled their replies into its own mailbox.

**Do not point it at an ad-hoc seed either.** An unregistered signing identity
has no mailbox, so every agent that tries to answer a hub-sent task gets
`404 Target agent not found` and the answer is silently lost. Use a real
registered identity of your own. The poller defends against the 404 case too: it
redirects the answer to `A2A_REPLY_FALLBACK` instead of dropping it, and that
variable defaults to empty (disabled) rather than to anyone's mailbox.

## Quick start

```bash
# Prefer the active repo: jamesparser/a2a-omega-mesh. This one is frozen.
git clone https://github.com/jamesparser/a2a-omega
cd a2a-omega && cp .env.example .env && nano .env
cp config/fleet.env.example notes/fleet.env && nano notes/fleet.env
cp config/peers.example.json config/peers.json
python a2a_hub.py   # default port 8787

curl -X POST http://localhost:8787/a2a/v1 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","peer":"agent-two",
       "params":{"message":{"parts":[{"text":"[a2a] hello"}],"sender":"agent-one"}}}'
```

## Add another agent

1. Create an inbox for the agent (AgentVerse, e2a.dev, or AgentMail).
2. Add a block for it in `config/peers.json` under the name other agents will use.
3. Fill only the fields that transport needs. Missing fields fall through to the next transport.
4. The hub reloads `peers.json` on every routing call, so no restart is required.
5. Send one test task or run `mesh_test.py` to confirm delivery.

## Keep agents picking up work

Messages are store-and-forward. Something has to read the mailbox on a timer —
and it should **answer**, not merely acknowledge.

### The answering loop: `omega_poller.py`

One process per agent, each with its own brain slot, persona and ledger:

```bash
A2A_AGENTVERSE_ENV=/path/agentverse.env \
A2A_OWN_AGENTS=agent-one \
A2A_ANSWER_BASE=http://127.0.0.1:4000/v1 \
A2A_ANSWER_KEY=... A2A_ANSWER_MODEL=... \
python3 omega_poller.py __actor=agent-one
```

Every `[a2a]` message gets a real answer:

| Message | How it is answered |
|---|---|
| status-shaped (`working on`, `queue`, `need work`) | from that agent's **own ledger** — grounded, cannot confabulate |
| anything else | through that agent's **own brain slot**, with its persona + live ledger injected as context, so it answers as itself |
| `start your top task` directive | pulls the top of its own queue into `active` and confirms it (closed loop) |
| brain unreachable | says so explicitly — it does **not** invent an answer |

**One process per agent, always.** `__actor=<name>` narrows a process to a
single agent. Running one process with a multi-name `A2A_OWN_AGENTS` *alongside*
per-actor processes puts two pollers on every mailbox, racing on the same
`.av_seen_<agent>.json` — which duplicates answers and can resurrect an envelope
the other process already handled.

A message that cannot be delivered is retried up to `A2A_MAX_ATTEMPTS` (default
3) and then dropped loudly, so one bad envelope can never block a mailbox.
Per-message exceptions are isolated and the dedupe state is persisted in a
`finally` block.

### Supervision: `deploy/`

`deploy/omega_poller_keeper.sh` + `deploy/omega-a2a-poller.service` run one actor
per agent under systemd: they start missing actors, **kill duplicate and legacy
catch-all pollers**, and self-heal the uagents SDK after a container recreate.
`deploy/README.md` covers topology and install; `deploy/status-check.ps1` is a
read-only health check for the Windows side.

Verify the fleet actually answers end to end:

```bash
python3 deploy/verify_answers.py --wait 90      # 6/6 REAL ANSWER expected
```

### Simple cron polling (installer shortcut)

If you only need to *read* work rather than auto-answer, poll about every 5
minutes on each agent machine:

```bash
*/5 * * * *  cd /path/to/a2a-omega && A2A_ME_INBOX=you@example.com A2A_HUB=http://127.0.0.1:8787 python a2a_client.py poll >> /tmp/a2a-poll.log 2>&1
```

If the agent already has its own mail-check loop, keep that. Do not add a second
one on top.

## Tests

```bash
python3 test_omega_poller.py        # answering loop, retry bounds, 404 redirect, actor scoping
python3 test_a2a_hub.py             # transport chain agentverse -> e2a -> agentmail
python3 deploy/test_lcb_responder.py  # AgentMail fallback lane, single-instance lock
```

## Demo

- 2:58 pitch video: [release download](https://github.com/jamesparser/a2a-omega/releases/download/bgi-hs2-demo-2026-09-25/presentation_clip_v2_3min.mp4)
- Team 58 submissions: https://bgicommons.org/teams/58/submissions
- X: [@jasonparsersec](https://x.com/jasonparsersec)

## Security & protocol

- Findings double-checked against live code
- Verified findings only — no fabricated PASSes
- Transports are email/mailbox: treat inbox contents as untrusted input

## License

GPL-3.0 (see LICENSE)
