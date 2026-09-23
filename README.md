# A2A Omega — Multi‑Identity Agent Routing Hub

**Runs a fleet of autonomous AI agents that talk to each other over email, with an opt‑in daily transcript to the hub owner.** Built for BGI HyperSprint (team 58) and real‑world security‑research coordination.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![A2A spec](https://img.shields.io/badge/A2A-spec%20shaped-blueviolet.svg)](https://github.com/a2aproject/a2a)
[![OpenRouter](https://img.shields.io/badge/OpenRouter-union lane-ff69b4.svg)](https://openrouter.ai)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)

## Why it exists

Bug‑bounty research runs through email, agent inboxes, and verification codes. A2A Omega lets every agent in the fleet **receive tasks, reply with its own identity, and share transcripts** — no shared database, no persistent connections, no Docker compose required. Just email + HTTP.

```
jasonparser ──► [hub] ──► omega-man
                ▲           ▼
my-liberclaw ───┘    my-betterclaw
omega-liberclaw ─────► omega-betterclaw
```

## What's inside

| File | Purpose |
|------|---------|
| `a2a_hub.py` | Routing server — A2A-shaped `agent-card`, JSON-RPC `SendMessage`/`tasks/get`/`tasks/cancel`, task lifecycle, multi‑transport delivery, optional webhook push, daily transcript digest. |
| `a2a_agentverse.py` | Optional Agentverse (Fetch.ai / SingNET) mailbox transport. Lazy‑loaded; degrades to fallback if the `uagents` SDK or key is missing. |
| `a2a_client.py` | One-agent client: poll inboxes for `[a2a]` tasks, send tasks to peers. |
| `poller.py` | Watch N inboxes for verify‑links / API keys, auto‑forward to the owner. |
| `config/peers.example.json` | Peer registry template (inbox + keys + optional Agentverse address + MailSlurp fallback). |
| `.env.example` | All configuration knobs — no secrets. |

## Transport precedence

`A2A_TRANSPORT` rotates the preferred channel to the front; the chain always falls back:

1. **Agentverse** (documented default when available) — Omega/SingNET mailbox. `uagents>=0.25.3` SDK + key required.
2. **AgentMail** — legacy primary; per‑peer `agent_mail_key` + `inbox`.
3. **MailSlurp** — anti‑censorship fallback; per‑peer `mailslurp_inbox` + `mailslurp_email`.

If a peer lacks a given transport, it's skipped silently — routing never blocks.

## Quick start

```bash
# 1. clone & configure
git clone https://github.com/jamesparser/a2a-omega
cd a2a-omega && cp .env.example .env && nano .env   # fill AGENTMAIL_API_KEY etc.
cp config/peers.example.json config/peers.json       # add peers, optional mailslurp_inbox fields

# 2. run the hub (default port 8787)
python a2a_hub.py

# 3. speak to it
curl -X POST http://localhost:8787/a2a/v1      -H "Content-Type: application/json"      -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage",
          "peer":"omega-man",
          "params":{"message":{"parts":[{"text":"[a2a] hello"}],"sender":"jasonparser"}}}'
```

## Fleet & BGI

- **Team 58 — JasonParser Security** ([bgicommons.org/teams/58](https://bgicommons.org/teams/58)).
- Track 1 (A2A interface) demo shipped via this hub; LC/BC reply from their own AgentMail identities.
- Owner receives a daily transcript when `A2A_TRANSCRIPT_EMAIL=you@example.com`.

## Security & protocol

- Findings are **double‑checked against live code** and **dup‑checked against prior audits**.
- PoCs are reproducible (Foundry tests included wherever relevant).
- Verified findings only — no fabricated PASSes.

## License

GPL-3.0 — see [LICENSE](LICENSE).
