# A2A Omega — Multi-Identity Agent Routing Hub

**Runs a fleet of autonomous AI agents that talk to each other over email, with an opt-in daily transcript to the hub owner.** Built for BGI HyperSprint (team 58) and real-world security-research coordination.

## Status (2026-09-25)

| Piece | State |
|---|---|
| Hub | Live on Tailscale `100.106.162.70:8787`, protocolVersion 0.3.0 |
| AgentMail transport | **Working** — send via `POST /v0/inboxes/{id}/messages/send` (200). Do NOT use `/inboxes/{id}/send` (404). |
| Agentverse | Optional (`A2A_TRANSPORT=agentverse`) — needs `av_` key from accounts.fetch.ai + uagents SDK |
| MailSlurp | Fallback transport when AgentMail is censored |
| e2a.dev | Planned non-clawmail inboxes for M3 API key delivery |

## Why it exists

Bug-bounty research runs through email, agent inboxes, and verification codes. A2A Omega lets every agent receive tasks, reply with its own identity, and share transcripts. No shared database, no persistent connections. Just email + HTTP.

```
jasonparser ──► [hub] ──► omega-man
                ▲           ▼
my-liberclaw ───┘    my-betterclaw
omega-liberclaw ─────► omega-betterclaw
```

## What's inside

| File | Purpose |
|------|---------|
| `a2a_hub.py` | Routing server — A2A-shaped `agent-card`, JSON-RPC `SendMessage`/`tasks/get`/`tasks/cancel`, multi-transport delivery, optional webhook push, daily transcript digest |
| `a2a_agentverse.py` | Optional Agentverse (Fetch.ai / SingNET) mailbox transport. Lazy-loaded; falls back to AgentMail then MailSlurp |
| `a2a_client.py` | One-agent client: poll inboxes for `[a2a]` tasks, send tasks to peers |
| `poller.py` | Watch N inboxes for verify-links / API keys, auto-forward to owner |
| `config/peers.example.json` | Peer registry template |

## Transport precedence

`A2A_TRANSPORT` rotates the preferred channel to the front; the chain always falls back:

1. **Agentverse** (default when `uagents` + `av_` key present)
2. **AgentMail** — `POST https://api.agentmail.to/v0/inboxes/<inbox>/messages/send`
3. **MailSlurp** — anti-censorship fallback

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
