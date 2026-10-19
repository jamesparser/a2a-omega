# Peer registry

`config/peers.json` (copied from `peers.example.json`) is the hub's address book.
**It holds credentials and is gitignored — never commit the real one.**

Only the fields a peer actually needs are read:

| Transport | Required per peer | Also needs |
|---|---|---|
| AgentVerse | `agentverse_address` | `uagents` SDK + API key in env |
| e2a.dev | `e2a_email` | an e2a account key in env |
| AgentMail | `inbox`, `agent_mail_key` | nothing else |

Anything missing is skipped and the next transport in the chain is tried, so a
peer can be registered with one transport only.

Optional per-peer overrides: `e2a_from`, `agentverse_seed`, and the dormant
MailSlurp path (`mailslurp_inbox`, `mailslurp_email`, `mailslurp_api_key_site`).

`note` is free text for you. The hub ignores it.

Treat this file like a password store: the addresses are real inboxes and the keys
let anyone who has them send mail as your agents.

## Adding a peer

1. Choose the peer key other agents will type, for example `betterclaw`.
2. Add that key as a new object in `peers.json`.
3. Set one working transport first. You can add the others later.
4. No hub restart: `load_peers()` runs on each routing call.

```json
"betterclaw": {
  "inbox": "betterclaw@example.com",
  "agent_mail_key": "am_us_...",
  "note": "diff review peer"
}
```
