# A2A Omega — Multi-Identity Agent Routing Hub

**Six AI agents that talk to each other over Agentverse mailboxes and e2a email, with an opt-in daily transcript to the hub owner.** Built for BGI HyperSprint (team 58, JasonParser Security) and real-world security-research coordination.

## Status (2026-09-25)

| Piece | State |
|---|---|
| **Agentverse** | **Primary.** 6 mailbox agents + signed-envelope submit. Mesh 30/30 pong (p50 ~9s). |
| **e2a.dev** | **Primary.** 6 fleet inboxes (`*@agents.e2a.dev`). Mesh 30/30 pong (p50 ~8s). Free plan = 20 msgs/day. |
| Hub | JSON-RPC `SendMessage` / `tasks` / `agent-card` |
| AgentMail / MailSlurp | Retired as fallbacks. Not used in the current chain. |

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

| Name | e2a | Agentverse mailbox |
|---|---|---|
| jason-parser | jason-parser@agents.e2a.dev | `a2a-omega-e2a-fleet-jason-parser` |
| omega-man | omega-man@agents.e2a.dev | `a2a-omega-e2a-fleet-omega-man` |
| my-liberclaw | my-liberclaw@agents.e2a.dev | `a2a-omega-e2a-fleet-my-liberclaw` |
| omega-liberclaw | omega-liberclaw@agents.e2a.dev | `a2a-omega-e2a-fleet-omega-liberclaw` |
| my-betterclaw | my-betterclaw@agents.e2a.dev | `a2a-omega-e2a-fleet-my-betterclaw` |
| omega-betterclaw | omega-betterclaw@agents.e2a.dev | `a2a-omega-e2a-fleet-omega-betterclaw` |

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

`A2A_TRANSPORT` picks the primary. Fallback chain:

1. **Agentverse** — `POST https://agentverse.ai/v2/agents/mailbox/submit` (signed Envelope, Bearer JWT)
2. **e2a** — `POST https://api.e2a.dev/v1/agents/{from}/messages` with `{"to":[...],"subject","text"}`

e2a needs a browser-like `User-Agent` (Cloudflare 1010 otherwise). Peers carry `e2a_email` + `agentverse_address` in `config/peers.json`.

## Quick start

```bash
git clone https://github.com/jamesparser/a2a-omega
cd a2a-omega && cp .env.example .env && nano .env
cp config/peers.example.json config/peers.json
python a2a_hub.py   # default port 8787

curl -X POST http://localhost:8787/a2a/v1 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","peer":"omega-man",
       "params":{"message":{"parts":[{"text":"[a2a] hello"}],"sender":"jasonparser"}}}'
```

## Add another agent

1. Create an inbox for the agent (AgentVerse, e2a.dev, or AgentMail).
2. Add a block for it in `config/peers.json` under the name other agents will use.
3. Fill only the fields that transport needs. Missing fields fall through to the next transport.
4. The hub reloads `peers.json` on every routing call, so no restart is required.
5. Send one test task or run `mesh_test.py` to confirm delivery.

## Keep agents picking up work

Messages are store-and-forward. Something has to read the mailbox on a timer.

Recommended for installers: poll about every 5 minutes on each agent machine.

```bash
*/5 * * * *  cd /path/to/a2a-omega && A2A_ME_INBOX=you@example.com A2A_HUB=http://127.0.0.1:8787 python a2a_client.py poll >> /tmp/a2a-poll.log 2>&1
```

If the agent already has its own mail-check loop, keep that. Do not add a second
one on top.

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
