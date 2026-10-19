## 2026-10-14
- Omega channel: owner-relay of replies with measured timing, file-backed pending state, fixed reply regex (backspace bug).
- Poller: reboot/self-heal guide, googlevps poller updated to the reply-subject format.

# Revision timeline

Dated record of what broke, what fixed it, and how the fix was verified.
Commit times are +07 (Bangkok). SHAs are resolvable in this repo's history; the
answering-loop work landed upstream in `jamesparser/a2a-omega` and was ported
here on 2026-10-02.

The interesting entries are the failures. A routing hub either delivers and gets
a real answer, or it is decoration, and for part of 2026-10-01 it was decoration
while every process looked healthy.

## 2026-10-04: unified fleet poller (the answering loop is now one program)

The per-transport pollers (`poller.py` AgentMail-only, the `omega_poller.py`
per-actor Agentverse loop, the e2a poller, and the `lcb_responder.py`
fallback lane) were replaced by a single `a2a_fleet_poller.py`: one actor
process per agent, each reading **all three inbound lanes** (Agentverse
mailbox with persistent retry through 503 / DNS flaps, the AgentMail inbox,
the e2a inbox) and replying **AgentMail primary, Agentverse best-effort** —
e2a is read-only on the free tier and is never a send lane. The retired
pollers are preserved under `legacy/` for reference; none of them run.

Supervision is one keeper, `deploy/omega_fleet_poller_keeper.sh` under
`deploy/omega-fleet-poller.service`: it starts a missing actor within ~10s,
kills duplicate and catch-all pollers (any process without `__actor=`), and
self-heals the uagents SDK after a container recreate. `run_fleet_poller_one.sh`
is the per-agent launcher. The Agentverse brain/ledger pipeline
(`omega_poller.py`) is unchanged and still the answer engine: status-shaped
questions are answered from the agent's own ledger, everything else goes to
the agent's own brain slot with its persona and live ledger as context.

Verified 6/6: all six actors running under the unified keeper, a jason ->
omega-man question answered in ~8s and the four LC/BC each in ~11s, replies
confirmed landed in the right inboxes with `mesh_omega_check.py` and
`verify_subjroute.py`.

## 2026-10-02: v2 priorities reordered

The paid-jobs plan decided this order. Reply-aware escalation, a durable queue
and proof-of-work receipts now lead the roadmap, ahead of the MCP server,
because settlement needs proof that a task was received, worked on and
answered, and today a delivered task can sit at `completed` with nothing behind
it. Reply-aware escalation now carries the hub-side task record work too:
inbound replies attach to the task id they answer, task state gains
`delivered -> answered`, and a fleet-wide tracker checklist shows asked,
delivered, answered and the answer text per task. The escalation rule now includes receipt: an agent must confirm it got the
message and return a reply, including a plain working-on-it status while the
work runs, before the task can count as answered. The `lcb_responder.py`
honesty, replying `not_answered` when its forward back into Agentverse fails,
is kept as a guarantee, and the durable queue is what turns that honesty into
durability. The harness bullet and a new "why not a Telegram group chat" section record
the cross-ecosystem pitch: agents locked inside WeChat (MaxClaw, QClaw,
KimiClaw, Xiaowei) or bound to Slack, WhatsApp or Telegram join by getting an
Agentverse, e2a or AgentMail account, no chat-app integration anywhere, so
agents across ecosystems reach each other with no human relay. The roadmap
also names HVFR, a workload layer on top of the tracker checklist, with the
acronym left unexpanded on purpose.

## 2026-10-02: identity is input, never a default

**Problem.** Every fleet identity was a hardcoded default in the source. A fresh
clone signed Agentverse envelopes with the maintainer's registered seed, so its
tasks arrived looking like they came from someone else's agent and the replies
landed in the maintainer's mailbox. It also redirected undeliverable answers and
fallback-lane status mail to the maintainer, and `A2A_OWN_AGENTS` defaulted to a
real agent name, so an installer's poller competed for a mailbox it did not own.

**Fix.** All real identities removed from defaults. `av_send` now refuses to sign
without an explicit identity; `omega_poller.py` exits 2 without `A2A_OWN_AGENTS`;
`A2A_REPLY_FALLBACK` and `LCB_REPLY_TO` default to empty (disabled, log-only).
Operator config moved to a gitignored `notes/fleet.env`, templated by
`config/fleet.env.example` and sourced automatically by both deploy scripts.
Hardcoded `C:\Users\...` operator paths in `status-check.ps1` and
`lcb_responder.py` replaced with env-driven, repo-relative defaults. A real
Agentverse address and a real AgentMail inbox in test fixtures were replaced with
the same placeholders `config/peers.example.json` already used.

**Verified.** 110/110 checks green after the change; `DEFAULT_HUB_SEED` resolves
to `""`; grep for real identities across all `*.py`, `*.sh`, `*.ps1`, `*.env*`
and `*.json` returns nothing outside `docs/` and `results/`.

## 2026-10-02: e2a fallback lane re-proven

**Why.** Agentverse is primary; e2a and AgentMail are fallbacks behind it. A
fallback that quietly rots is worse than no fallback, because you believe you
have redundancy.

**Result.** Probe from the fleet host: 2 keys loaded, sender resolved to the
correct owning account, `{'ok': True, 'message_id': 'msg_df419b…', 'method':
'smtp'}`, then read back in the recipient's inbox listing. Lane is live. Test
used an inert subject so the poller swept it instead of answering, costing no
fleet traffic and no inference tokens.

## 2026-10-02 02:47, `70eeb18`: transcript stays on one line

**Problem.** Long markdown answers were split across log lines, so the daily
transcript and any grep of an actor log lost the boundary between messages, and
verification could not reliably attribute an answer to a task.

**Fix.** Inbound transcript entries are whitespace-flattened and capped by
`A2A_INBOUND_LOG_CHARS` (default 1200). The verifier now reads each agent's own
actor log for a race-proof answer record and reports which source produced the
verdict, instead of relying on mailbox timing alone.

**Verified.** `deploy/verify_answers.py` reported **6/6 REAL ANSWER**, 93 to 812
characters, zero cycle errors since the restart.

## 2026-10-02 02:30, `98cbc46`: the fleet stopped answering silently

This is the outage that motivated the whole test suite. **Every process was
running.** The systemd unit was active, six actors were alive, no crash, no
alert, no error in the hub. The fleet simply answered nothing at all.

Four independent root causes:

1. **A shadowed function definition wedged every mailbox.** `omega_poller.py`
   defined `llm_answer` twice. The second, prompt-only definition shadowed the
   agent-aware one, so every non-status question raised `TypeError: llm_answer()
   got an unexpected keyword argument 'agent'` out of `serve_once` **before**
   `save_seen` ran. Because the envelope was never marked seen, it was retried on
   every poll forever, and it blocked every later message in that agent's
   mailbox. Stale definition removed.
2. **Answers were acknowledgements.** Status questions are now answered from the
   agent's own ledger, so they are grounded and cannot be confabulated.
   Everything else goes through that agent's own brain slot with its persona and
   live ledger injected. An unreachable brain says so explicitly instead of
   inventing an answer. Inbound replies are logged before being swept, retries
   are bounded by `A2A_MAX_ATTEMPTS` (default 3) so one bad envelope can never
   block a mailbox, per-message exceptions are isolated, and dedupe state is
   persisted in a `finally` block.
3. **Transport chain was wrong.** Corrected to the canonical
   `agentverse -> e2a -> agentmail`. The dead MailSlurp dispatch branch was
   removed; e2a and AgentMail remain fallbacks behind Agentverse, never
   replacements for it.
4. **Two pollers per mailbox.** `__actor=` was not functional, so a catch-all
   process served the whole `A2A_OWN_AGENTS` list alongside per-actor processes.
   Both raced on the same `.av_seen_<agent>.json`, duplicating answers and
   resurrecting envelopes the other had already handled. `__actor=` now narrows a
   process to one agent and the keeper enforces exactly one actor per agent.

Also fixed: the hub must sign as a **registered** fleet identity. The old default
derived an address with no mailbox, so agent replies to hub-sent tasks came back
`404 Target agent not found` and the answer was silently lost. The poller now
redirects those to `A2A_REPLY_FALLBACK` instead of dropping them.

**Verified.** `test_omega_poller.py` (48 checks at this commit),
`test_a2a_hub.py` (28) and `deploy/test_lcb_responder.py` (29) added as
regression guards, each reproducing the original failure before asserting the
fix. `deploy/verify_answers.py` then reported 6/6 REAL ANSWER across the fleet,
18 answered events, zero cycle errors.

## 2026-10-01 14:17, `b585fbc`: per-agent brains

Each agent got its own brain slot, persona and ledger instead of one shared
reply generator, plus a directive loop: `start your top task` pulls the top of
that agent's own queue into `active` and confirms it, closing the loop between
"what do you have queued" and "now do it".

## 2026-09-30, `d4349de`: fleet broadcast

`Broadcast`, `message/broadcast`, or `SendMessage` with peer `*`/`all` fans one
message to every other peer. Each target gets its own task id under a shared
broadcast id, so a fan-out is still individually trackable and individually
retryable. Preserved through the 2026-10-02 port.

## 2026-09-27, `67afae0`: transports re-prioritised

MailSlurp dropped as a documented fallback after e2a and Agentverse both passed
the full six-agent mesh. Fewer hops, one less sandbox-inbox dependency.

## 2026-09-26, `d5f271f`: packaged for Decentralize AI

Split from the working repo into a submission package: sanitised
`.env.example` placeholders, submission notes, owner-only blocker list, then the
HackerNoon write-up (`docs/ARTICLE.md`, `452bcb6`).

## 2026-09-25, `5a72caa`: multi-transport mesh

e2a and Agentverse transports added alongside AgentMail, hub hardened, and
`mesh_test.py` written to walk every ordered pair of a six-agent fleet.

**Result.** 60 of 60 deliveries succeeded (30 ordered pairs, each on both
transports), zero failures. Machine-readable output in
[`results/mesh-2026-09-25.json`](results/mesh-2026-09-25.json). Note the property
under test is **delivery**, not responsiveness: can A get a task to B when B is
not listening. Answer quality became testable only with the 2026-10-02 work.

## Verifying any of this yourself

```bash
python3 test_omega_poller.py           # 53 checks: answering loop, retry bounds, 404 redirect, actor scoping
python3 test_a2a_hub.py                # 28 checks: transport chain agentverse -> e2a -> agentmail
python3 deploy/test_lcb_responder.py   # 29 checks: AgentMail fallback lane, single-instance lock
```

110 checks, stdlib only, no network, no API keys. The uagents SDK is stubbed and
all transport calls are monkeypatched, so they run anywhere in a couple of
seconds. Each of the 2026-10-02 tests reproduces its original failure first.

`deploy/verify_answers.py` is the end-to-end check: it sends real questions to
real agents and requires a substantive answer from each, rejecting bare
acknowledgements. It needs a live fleet and credentials, so it is not part of the
110.

## Known gaps

Recorded here rather than hidden, because two of them are exactly what v2 has to
solve before this can carry paid work. See `## Roadmap: v2` in the README.

- **Fallback triggers on send failure, not on silence.** `_route` walks the chain
  and returns on the first transport that *accepts* the message. A task marked
  `completed` means delivered, not answered. If Agentverse accepts a message and
  the agent never replies, nothing escalates to e2a or AgentMail and nothing
  times the task out.
- **Replies never land on the task record.** Agent answers come back on the
  sender's own mailbox, so the hub closes a task at delivery and never learns
  whether it was answered. Verified live on 2026-10-02: an inbound reply to a
  hub-sent task appears nowhere on `/tasks/<peer>`. The v2 reply tracker fixes
  this by attaching each reply to the task id it answers.
- **The last-resort lane points back at the primary.** `deploy/lcb_responder.py`
  receives on AgentMail and forwards into Agentverse, because that is where the
  brain slot lives. If Agentverse itself is down, the forward fails and the
  responder replies `not_answered` honestly. The message is never falsely
  claimed as done, but it is not durably queued either.
- **No durable outbound queue.** A lane outage reports failure instead of parking
  the message and draining it on recovery.
- **Hub endpoint has no authentication.** Bind `127.0.0.1` or a private network.
  Documented in `## Security notes`; unchanged since the first revision.
