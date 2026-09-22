# A2A Hub — Self-Hosted Agent-to-Agent Routing over Email

A small, dependency-free (Python stdlib only) routing hub that lets **autonomous AI agents
talk to each other**. It bridges a fleet of agents over **AgentMail inboxes** while exposing
an **A2A-protocol-shaped JSON-RPC interface**, so any agent runtime (Hermes, OpenClaw,
OmegaClaw, plain HTTP) can plug in.

Built for the BGI / SingularityNET hackathon (Omega agents & applications track): agents from
different systems message each other autonomously, with an opt-in **daily transcript** to the
hub owner's email so a human can follow along.

## Why it's different
- **No shared database, no persistent connection between agents.** Every agent already has an
  email inbox; the hub turns "agent ↔ agent messages" into inbox-to-inbox email, so it works
  across VPSes, home machines, and cloud sandboxes with zero networking setup.
- **A2A-spec.** The endpoint mirrors the Agent2Agent protocol (Google `a2aproject/a2a`, Linux
  Foundation): a spec-shaped `agent-card` (`protocolVersion`, `capabilities`, `skills[]`),
  JSON-RPC `SendMessage` / `tasks/get` / `tasks/cancel`, task state transitions
  (`submitted → working → completed/failed/canceled` with per-task history), plus optional
  **webhook push** on terminal state — so any standard A2A client (OpenClaw SDK, plain
  JSON-RPC) can interoperate, and poll-only stays the default.
- **Owner observability.** Set `A2A_TRANSCRIPT_EMAIL` and you get a digest of every agent↔agent
  exchange each day — the "input your email to receive a daily transcript" feature.

## Components
| File | Role |
|------|------|
| `a2a_hub.py` | The routing server: A2A-spec agent-card, JSON-RPC `SendMessage`/`tasks/get`/`tasks/cancel`, task state machine (with transition history), AgentMail reply polling, optional webhook push, daily transcript. |
| `a2a_client.py` | One-agent client: `poll` my inbox for `[a2a]` tasks, `send` a task to a peer. |
| `poller.py` | Optional: watch N inboxes for external API-key / verify-link drops, auto-forward to the owner. |
| `config/peers.example.json` | Peer registry template (agent → inbox + API key). |
| `.env.example` | Every configuration knob, no secrets. |

## Quick start
```bash
cp .env.example .env        # fill in your inboxes + AgentMail API keys
cp config/peers.example.json config/peers.json   # your real fleet
python a2a_hub.py           # hub listens on 127.0.0.1:8787
```
From another machine, an agent does:
```bash
export A2A_ME_INBOX=alpha-agent@agentmail.to A2A_AGENTMAIL_API_KEY=am_us_... A2A_HUB=http://YOUR-TAILSCALE-IP:8787
python a2a_client.py send beta-agent "ping from alpha"
python a2a_client.py poll   # read my new [a2a] tasks
```

### Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/.well-known/agent-card.json` | A2A-spec agent card (`protocolVersion`, `capabilities`, `skills[]`) |
| `POST` | `/a2a/v1` | JSON-RPC: `SendMessage` / `message/send` / `tasks/send` (async; result by email), `tasks/get`, `tasks/cancel` |
| `GET`  | `/tasks/<peer>` | task history for a peer (each task carries its state-transition `history`) |
| `GET`  | `/healthz` | liveness (`ok`, peer count, `protocolVersion`) |

### Task lifecycle & push
Task states: `submitted → working → completed | failed | canceled` (every transition is
recorded in `task["history"]`). Set `A2A_PUSH_WEBHOOK` to an HTTPS URL and the hub POSTs a JSON
event `{task:{id, peer, status, result, history}}` when a task reaches a terminal state.
Default is poll-only (`tasks/get` or the AgentMail inbox), which matches A2A's
`pushNotifications: false` capability.

## Security
- **Bind to a private interface.** Default is `127.0.0.1`; a private agent fleet typically
  binds a Tailscale address. Do **not** expose this on `0.0.0.0` publicly.
- **Keys live in `.env` / your vault** — nothing secret is committed (see `.gitignore`).
- **The hub only moves text between agent inboxes.** It is not a general proxy.
- **AgentMail gotcha (learned the hard way):** when sending, use the `text` (or `html`) field.
  The `content`/`body` fields are silently dropped (HTTP 200, empty mail). This repo sends `text`.

## Honest scope
This is a working internal tool (used by a 6-agent security-research fleet), not a hardened
product: no auth on the JSON-RPC endpoint, in-memory task store, single-writer log files. It's
intended for a trusted private network. Add auth before ever running it on an untrusted one.

## License
MIT — see `LICENSE`.
