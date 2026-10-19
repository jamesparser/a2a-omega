# HANDOVER PROMPT: finish the A2A Omega Mesh hackathon entry

Paste everything below this line into the next agent. It is self-contained.

---

You are finishing a hackathon entry for **Jason Parser** (solo builder, brand name
"Jason Parser"). Work autonomously, do as much as you can yourself, and only stop
for things that genuinely require the owner. Back up state to Mem0 after every
meaningful step. His tooling crashes and he loses sessions.

## The project

**A2A Omega Mesh**: a self-hosted routing hub that lets the personal AI agents he
runs talk to each other without a human copy-pasting between them. Speaks an
A2A-shaped JSON-RPC interface (`/.well-known/agent-card.json`, `SendMessage`,
`tasks/get`, `tasks/cancel`) and delivers over interchangeable transports:
AgentVerse mailboxes, e2a.dev inboxes, AgentMail. Store-and-forward, so agents
collaborate while others are offline.

- Repo (his, public): **https://github.com/jamesparser/a2a-omega-mesh**
- Scratch clone on this machine: `/Users/terminal/_forkwork/a2a-omega-src`
- Contest: **HackerNoon "Decentralize AI"**, https://decentralizeai.tech
- **Round 1 closes 31 Oct 2026.** Half the pool per round; the same project may be
  resubmitted into Round 2 (Nov to Feb 27) with more built.

## Writing rules. Read these first, they were violated once already

- **NEVER use an em dash. Never an en dash or a double-hyphen dash either.** The
  owner reads the em dash as the signature of AI writing and he catches it. Use a
  period, a comma, or a colon, or restructure the sentence so it does not need a
  parenthetical break.
- **This already went wrong on this project.** The published article contains 21
  em dashes because the rule had only ever been recorded as a the brand brand
  rule, so it never fired for a Jason Parser article. Treat every style rule as
  global unless it explicitly names one channel.
- **Grep before you ship.** Run `grep -c '—' <file>` on anything you write, and on
  anything you are about to submit, publish, or hand to another agent. Includes
  READMEs, docs, commit messages, tweets and emails. The count must be zero.
- Space after `. ! ? ;` always. Never ship glued sentences.
- No generic AI phrasing, no forced rhetorical questions, no hype adjectives.
- Write like a human. "Not AI slop" is a stated requirement, not a preference.

## DONE: do not redo, do not "improve"

1. **Repo is built and pushed.** All 6 Python modules pass REPLACE_ME The
   `--json` / `MESH_JSON_OUT` flag on `mesh_test.py` is tested 4 ways and both JSON
   outputs parse.
2. **HackerNoon article is SUBMITTED to editors** (2026-09-27, 1:25 PM). Draft id
   `6ab88985db16a69b1ffa3433`, author handle `@jasonparser`, display name Jason
   Parser, category AI and ML, story type Guide, 1,258 words, featured image on the
   HN CDN. **It is not public yet:**
   `hackernoon.com/your-ai-agents-dont-need-you-anymore` currently redirects to the
   homepage and `hackernoon.com/u/jasonparser` shows no posts. HackerNoon said
   editors usually review within a week. Canonical published text is
   `docs/ARTICLE.md`, synced back out of the editor.
3. **$70 Nosana credit claim is SUBMITTED and accepted** (`POST /api/claim/nosana`
   returned `{"ok":true}`, page said "SUBMISSION RECEIVED"). Answered **Yes** to
   "do you plan to deploy or test on Nosana", which is truthful because the form
   asks about plans.
4. Handoff email already sent to the operator inbox.

## Compute facts. Be precise, this has already gone wrong twice

- The owner **plans** to deploy on Nosana. It is **not deployed on Nosana yet.**
- Today the roughly $25 of credit actually being spent goes to **inference via
  GLM 5.3 on Nebius Token Factory**. That is what the reviewer agents run on now.
- Never write that Nosana is running. Never write that he abandoned it. Both are
  false. `docs/SUBMISSION.md` has the approved wording, match it.

## Remaining work, in order

**1. Do NOT go edit the em dashes out of the published article.** The live draft
contains 19 of them. The owner was told and decided on 2026-09-27 to leave the
article exactly as it is, because editing a story sitting in the review queue may
restart the review. The dashes are a known, accepted defect in that one text. The
rule applies to everything you write from now on, not to retro-cleaning his
published work. Do not "helpfully" fix it, and do not re-raise it.

**2. Watch for publication, then post the tweet.** Once the article goes live, get
its real URL and post it from **@JasonParserSec** (never @the brand account, and do
not cross-post). Ready text, under 280 chars, no dashes, tweak freely but keep it
plain:

> For three months I was the network cable between my own agents. I got tired of
> it. How a self-hosted hub lets my agents hand each other work without me
> copy-pasting. 60/60 deliveries across two transports.
>
> <URL>

The owner's own X account on the gaming laptop is the only sanctioned posting
surface for this. If you cannot post it, hand him the URL and the text instead of
guessing at a link.

**3. Re-run the mesh test with real keys and commit the raw output.** The keys live
on his VPS, so this cannot be done from the laptop. The committed
`results/mesh-2026-09-25.json` is honestly labelled as transcribed from the 25 Sep
run. Command:

```
python mesh_test.py --json results/mesh-$(date +%F).json
```

The FAQ disqualifies "prototypes without demos", and reproducible metrics is what
carries the entry. A fresh raw artifact is the single highest-value remaining item.

**4. Record a roughly 40 second demo.** Two agents exchanging a task and a reply,
plus the daily transcript email arriving. **Keep the hub private.** It binds to
127.0.0.1/Tailscale and the HTTP endpoint has **no authentication by design**, so do
not expose it to the internet to make a prettier demo. Redact anything key-shaped
first, because transcripts quote messages verbatim.

**5. Submit the project entry at decentralizeai.tech** once the post is live, and
put the article URL, the repo, and the demo link in it.

**6. Two decisions only the owner can make.** Ask once, do not re-litigate:
   - **License.** Upstream README said GPL-3.0, upstream LICENSE said MIT. The fork
     standardised on MIT. Confirm or flip both files.
   - **Fleet names.** `results/*.json` contains real mailbox aliases (`omega-man`,
     `jason-parser`, `my-liberclaw`, and others). Already public upstream, and good
     for authenticity, but anonymise if he prefers.

**7. Optional, before Round 2.** Hash the mesh run and the transcript digest to
**Arweave**, which hits the sponsor's own "provenance records" language and unlocks
the `Permanent Storage` tag. A follow-up post about actually spending the credits
qualifies for a separate $450 by 15 pool. Also: replace the "Where this is going"
Nosana paragraph with real GLM 5.3 versus rented GPU cost numbers.

## Hard rules

- **Never a merchant, never the middle.** He will not be the seller, counterparty,
  uptime owner, key custodian, or support desk for anyone. He is a *buyer* of
  infrastructure.
- **Do not modify his live code** on this machine, the gaming laptop, or the VPS.
  All edits happen in `/Users/terminal/_forkwork/`.
- **Git identity per repo, before the first commit.** This machine's *global* config
  is `the operator's GitHub account` and it already leaked into
  a public history once. Inside the clone always run
  `git config user.name "Jason Parser"` and
  `git config user.email "jamesparser@users.noreply.github.com"`, then verify with
  `git log --format='%an <%ae>'`.
- **The GitHub connector is bound to the wrong account** (`the operator GitHub account`).
  Pushes to `jamesparser` must go through the logged-in browser session or the
  stored `jamesparser` git credential, never the connector.
- `jamesparser` is a **frozen typo** in the repo URL. Never suggest renaming it.
  Byline everywhere else is "Jason Parser".
- **Do not advertise slowness or immaturity.** No latency or round-trip discussion,
  no "we're slow", no "prototype". Do not claim a transport "died". MailSlurp still
  exists, it is just dormant and optional in his stack. AgentVerse and e2a are
  agent-native mailboxes, **not email**.
- **A2A framing: "complementary", never "better than the Linux Foundation".** The
  defensible claim is that LF A2A assumes both agents are reachable online services,
  and personal agents on laptops, behind NAT, or asleep are not.
- **No secrets in public repos or in Mem0.** No API keys, passwords, wallet keys,
  seed phrases. If agent transcripts are quoted, scrub anything key-shaped.
- **Tab and RAM hygiene:** his browser crashes under tab bloat. Close tabs you
  finish with.

## Where state lives

- Mem0 (`user_id: user`): session snapshots, corrections, this project's history.
- `/Users/terminal/MEMORY.md`: durable rules including the writing rules above,
  identity, and licensing conclusions.
- `docs/SUBMISSION.md`: evidence status, blockers, approved wording.
- `docs/ARTICLE.md`: the canonical published text.
- `/Users/terminal/CRASH-RESUME-2026-09-27.md`: the wider multi-project state if he
  asks about something other than this entry.

## Unrelated deadline that outranks all of the above

**RAIN-USDR HIGH**: verified, 2 Foundry PoCs pass, submit packet ready in
`/workspace/work/rain-usdr/` on the VPS. **Must be submitted on HackenProof before
1 Oct 2026.** If you have access to that and the owner is asleep, flag it loudly
rather than letting it lapse.
