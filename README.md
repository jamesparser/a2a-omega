# A2A Omega Hub — Self-Hosted Agent-to-Agent Routing over Email

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
- **Agentverse transport (documented default).** Every agent can be given an
  **Agentverse mailbox** (Fetch.ai / SingNET ecosystem) and still run its
  execution locally; incoming messages are delivered through the Agentverse
  Mailbox. `A2A_TRANSPORT=agentverse` makes Agentverse the primary outbound
  transport; the hub then falls back AgentMail → MailSlurp so a missing
  key/SDK never blocks routing.
- **MailSlurp fallback.** When AgentMail delivery to a peer fails (HTTP 403 or
  error key), the hub retries via MailSlurp if the peer carries
  `mailslurp_inbox` + `mailslurp_api_key_site`.
- **Owner observability.** Set `A2A_TRANSCRIPT_EMAIL` and you get a digest of
  every agent↔agent exchange each day — the "input your email to receive a
  daily transcript" feature.

## Agent roster
| Peer | AgentMail inbox | Role |
|------|-----------------|------|
| jasonparser | jasonparser@agentmail.to | Hub owner (direct transport) |
| omega-man | omega-man@agentmail.to | Omega org (primary) |
| my-liberclaw | confusedseat117@agentmail.to | LC/BC peer 1/4 — LiberClaw |
| my-betterclaw | delightfulart204@agentmail.to | LC/BC peer 2/4 — BetterClaw |
| omega-liberclaw | smilingbag599@agentmail.to | LC/BC peer 3/4 — LiberClaw |
| omega-betterclaw | brainystreet989@agentmail.to | LC/BC peer 4/4 — BetterClaw |

**LC/BC = the 2 commercial LiberClaw + 2 BetterClaw agents.** Each carries optional
`mailslurp_inbox` fields for anti-censorship fallback.

## Components
| File | Role |
|------|------|
| `a2a_hub.py` | The routing server: A2A-spec agent-card, JSON-RPC `SendMessage`/`tasks/get`/`tasks/cancel`, task state machine (with transition history), multi-transport delivery (Agentverse → AgentMail → MailSlurp), AgentMail reply polling, optional webhook push, daily transcript. |
| `a2a_agentverse.py` | Optional Agentverse (Fetch.ai / SingNET) mailbox transport: builds + signs a uAgents envelope and POSTs it to the Agentverse mailbox-submit API with the operator's Agentverse API key. Lazy-imported; degrades to an error (so the hub falls back to AgentMail) if the `uagents` SDK or key is missing. |
| `a2a_client.py` | One-agent client: `poll` my inbox for `[a2a]` tasks, `send` a task to a peer. |
| `poller.py` | Optional: watch N inboxes for external API-key / verify-link drops, auto-forward to the owner. |
| `config/peers.example.json` | Peer registry template (agent → inbox + API key + optional Agentverse mailbox + MailSlurp fallback). |
| `.env.example` | Every configuration knob, no secrets. |

## Mail transport precedence (Agentverse → AgentMail → MailSlurp)
The hub delivers each task through the **first transport that succeeds**, in this
order (controlled by `A2A_TRANSPORT`, which just rotates the preferred transport
to the front while keeping the fallbacks):

1. **Agentverse** (documented default when available) — the Omega-ecosystem
   mailbox. Requires the `uagents` SDK (`pip install "uagents>=0.25.3"`), an
   `A2A_AGENTVERSE_API_KEY`, and the peer's `agentverse_address` in
   `peers.json`. Execution stays local; only signed envelopes cross the wire.
2. **AgentMail** — the legacy primary; `agent_mail_key` + `inbox` per peer.
3. **MailSlurp** — anti-censorship fallback; `mailslurp_inbox` +
   `mailslurp_email` + `mailslurp_api_key_site` per peer.

If the preferred transport is not usable for a peer (missing key / SDK /
address), it is skipped and the next one is tried, so routing is never blocked.
Without any Agentverse config, the chain is exactly `[agentmail, mailslurp]`
(the original behavior).

### Agentverse mailbox (the Omega-ecosystem mailbox)
Give every Omega agent an Agentverse mailbox with `mailbox=True` and it "keeps
execution local" while incoming messages are delivered through the Agentverse
Mailbox. Each peer entry may therefore carry:
- `agentverse_address`: the peer's Agentverse mailbox address (`agent...` string)
- `agentverse_seed`: the peer's seed phrase (single-node / test only; keep the
  real per-agent seed in the vault, not in this file)

The hub-side key lives in env / vault (`A2A_AGENTVERSE_API_KEY` /
`A2A_AGENTVERSE_KEY_SITE`), never in the repo.

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

### MailSlurp fallback
Each peer entry in `config/peers.json` may carry two optional fields:
- `mailslurp_inbox`: the MailSlurp inbox id (UUID string)
- `mailslurp_api_key_site`: vault site name for the shared MailSlurp key (default: `MailSlurp`)

When AgentMail delivery to that peer fails (HTTP 403 or an error key), the hub retries
by POSTing to `https://api.mailslurp.com/inboxes/{inbox_id}` authenticating with the
shared MailSlurp key. Set `A2A_MAILSLURP_KEY_SITE` in `.env` to override the default
vault site. Legacy behavior (no mailslurp fields = no fallback) is preserved.

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
