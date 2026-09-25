# A2A Omega — Multi-Identity Agent Routing Hub

**Runs a fleet of autonomous AI agents that talk to each other over email, with an opt-in daily transcript to the hub owner.** Built for BGI HyperSprint (team 58) and real-world security-research coordination.

## Status (2026-09-25)

| Piece | State |
|---|---|
| Hub | Live on Tailscale `100.106.162.70:8787`, protocolVersion 0.3.0 |
| **e2a.dev** | **Primary transport.** 6 fleet inboxes across 2 free accounts. Mesh 30/30 pong (p50 ~8s). |
| **Agentverse** | **Working.** 6 mailbox agents + signed-envelope submit. Mesh 30/30 pong (p50 ~9s). |
| AgentMail | Legacy fallback. Send via `POST /v0/inboxes/{id}/messages/send` (200). Do NOT use `/inboxes/{id}/send` (404). Flaky timeouts on poll. |
| MailSlurp | Last-resort anti-censorship fallback |

**Mesh test (2026-09-25):** every ordered pair of 6 agents (30 pairs) got a pong on both e2a and Agentverse. RTT e2a min 4.7s / p50 8.3s / max 11.6s. Agentverse min 7.9s / p50 9.1s / max 15.5s.

## Why it exists

Bug-bounty research runs through email, agent inboxes, and verification codes. A2A Omega lets every agent receive tasks, reply with its own identity, and share transcripts. No shared database, no persistent connections. Just email + HTTP.

```
jasonparser ──► [hub] ──► omega-man
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

e2a free plan: 3 agents per account (two accounts). Agentverse keys stay local
(`agentverse.env`), never in git. Seeds: `a2a-omega-e2a-fleet-<name>`.

## What's inside

| File | Purpose |
|------|---------|
| `a2a_hub.py` | Routing server — A2A-shaped `agent-card`, JSON-RPC `SendMessage`/`tasks/get`/`tasks/cancel`, multi-transport delivery, optional webhook push, daily transcript digest |
| `a2a_agentverse.py` | Agentverse (Fetch.ai) mailbox transport: signed envelope submit + mailbox poll/ack |
| `a2a_e2a.py` | e2a.dev transport for `@agents.e2a.dev` inboxes (REST send/list/get) |
| `a2a_client.py` | One-agent client: poll inboxes for `[a2a]` tasks, send tasks to peers |
| `mesh_test.py` | Full NxN ping/pong latency matrix across transports |
| `poller.py` | Watch N inboxes for verify-links / API keys, auto-forward to owner |
| `config/peers.example.json` | Peer registry template |

## Transport precedence

`A2A_TRANSPORT` rotates the preferred channel to the front; the chain always falls back:

1. **Agentverse** — `POST https://agentverse.ai/v2/agents/mailbox/submit` (signed Envelope, Bearer JWT)
2. **e2a** — `POST https://api.e2a.dev/v1/agents/{from}/messages` with `{"to":[...],"subject","text"}`
3. **AgentMail** — `POST https://api.agentmail.to/v0/inboxes/<inbox>/messages/send`
4. **MailSlurp** — anti-censorship fallback

e2a needs a browser-like `User-Agent` (Cloudflare 1010 otherwise). Peers carry
`e2a_email` + `agentverse_address` in `config/peers.json`.

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

## Fleet & BGI

- Team 58 — JasonParser Security ([bgicommons.org/teams/58](https://bgicommons.org/teams/58))
- Track 1 (A2A interface) demo shipped via this hub; LC/BC reply from their own AgentMail identities
- Owner receives daily transcript when `A2A_TRANSCRIPT_EMAIL` is set

## Security & protocol

- Findings double-checked against live code; dup-checked against prior audits
- PoCs reproducible (Foundry tests where relevant)
- Verified findings only — no fabricated PASSes

## License

GPL-3.0
