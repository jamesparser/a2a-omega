# Submission notes — Decentralize AI Hackathon (HackerNoon / Nosana / Arweave / MEXC)

Entry: **A2A Omega Mesh** — self-hosted A2A routing hub for personal agents.
Repo: https://github.com/jamesparser/a2a-omega-mesh
Byline: **Jason Parser** (the GitHub handle `jamesparser` is a legacy typo; leave it).

## Which round

Round 1 closes **31 Oct 2026**. Half the pool is awarded per round, and the same
project can be resubmitted into Round 2 (Nov-Feb) with more built.

## Credit claim — DONE 2026-09-27

Submitted and accepted (`POST /api/claim/nosana` -> `{"ok":true}`, "SUBMISSION RECEIVED").
Deploy-on-Nosana answered **Yes** (the form asks about plans; the plan is real, the
deployment is not yet done). Credit access instructions arrive by email after review.

Original paste text, kept for the record:

## Claim the credits (done)

https://decentralizeai.tech/claim/nosana - $70 Nosana compute, first 500, and the
form doubles as the project proposal. **One claim per team.** Text to paste:

> A2A Omega Mesh: a self-hosted routing hub that lets personal AI agents send each
> other tasks over interchangeable transports (AgentVerse mailboxes, e2a.dev inboxes,
> AgentMail). Delivery is store-and-forward, so agents collaborate while others are
> offline. Nosana GPUs would run the inference-heavy peers locally instead of renting
> closed APIs - a fleet that owns its transports should also own its compute.
> Planned workload: local open-weight inference for the reviewer / second-opinion
> agent, measured against hosted-API cost.

## Evidence status

| Required by FAQ | Status |
|---|---|
| Working code | done - public repo, full history, MIT |
| Mesh-specific feature | **Broadcast.** `Broadcast` / `SendMessage` with peer `*` fans one message to every other peer, each with its own task id. `python a2a_client.py broadcast "text"`. Optional `check` for shell exit codes. Does not touch production crons. |
| Reproducible metrics | done - `results/mesh-2026-09-25.json`, 60/60 deliveries (30 pairs x 2 transports) |
| Deployment URL **or** reproducible metrics | metrics satisfied; a live URL is still missing |
| Featured image | `docs/featured.png` (uploaded to HackerNoon CDN as `images/img-5p03rto.png`) |
| Demo | **ready** - `assets/a2a-omega-demo-narrated.mp4` (30s, live Tailscale hub session + voiceover) and `assets/a2a-omega-demo-full.mp4` (55s, title/body/end). Hub stays private (127.0.0.1/Tailscale). |
| HackerNoon post | **still in editorial review as of 2026-10-02.** Writer tracker: "Stories in Editorial", submitted 5 days ago, not published, not rejected. Project submit at decentralizeai.tech waits on the live URL. |
| Live fleet check | **passed 2026-10-02.** Hub <hub-host>:8787 over Tailscale: healthz ok, 9 peers, protocol 0.3.0. SendMessage delivered. Agents answering real audit questions. |

## Blockers - owner only

1. **Re-run the mesh test** and commit raw output:
   `python mesh_test.py --json results/mesh-$(date +%F).json`
   Keys live on the VPS, so the committed file is transcribed from the 25 Sep run.
2. **Demo clip is cut** (`assets/a2a-omega-demo-40s.mp4`) from the existing BGI
   video. Lowest-credit path. Optional add-on: a still of the daily transcript
   email if the entry form wants that beat on camera. Redact anything key-shaped
   if you shoot new material.
3. **Decide on a public endpoint.** Recommended: keep the hub private, use the
   recording. A public hub has **no authentication** by design.
4. **Confirm the license.** Upstream README said GPL-3.0 while LICENSE said MIT.
   This fork standardised on MIT. Flip both if GPL was intended.
5. **Fleet names are real mailbox aliases** (`omega-man`, `jason-parser`, ...) and
   already public upstream. Anonymise `results/*.json` if you would rather not.
6. **The Nosana part is intent, not a build.** Do not overstate it in the article.

## Optional, before Round 2

- Hash the mesh run + transcript digest to **Arweave** (sponsor language:
  "provenance records"), and tag `Permanent Storage` for the follow-up post that
  qualifies for the extra $450 pool.
- One paragraph on running a peer's model on Nosana with real cost numbers.


## Compute status — stated precisely (2026-09-27)

The owner **plans** to deploy on Nosana. It is **not deployed on Nosana yet**.
Nothing in the article, the proposal, or any submission form should claim
otherwise, and nothing should claim the opposite either.

**Credit delivery status (checked 2026-09-28):** The $70 claim was submitted
2026-09-27 (`POST /api/claim/nosana` -> `{"ok":true}`). A Nosana Deploy account
exists (Google sign-in to deploy.nosana.com, 2026-09-28 06:44 BKK). Inbox has
only the generic "Welcome to Nosana" mail from `social@nosana.io`, which tells
you to top up and has **no credit code**. The hackathon credit email has not
arrived. Current inference spend is still **GLM 5.3 on Nebius Token Factory**,
not Nosana GPUs. Do not write that Nosana credits are in use, and do not write
that the Nosana path was abandoned.

What is true today:

- The roughly $25 of credit currently being spent goes to **AI inference via
  GLM 5.3 (Nebius Token Factory)**. That is what the reviewer agents run on now.
- Nosana is the intended next step, not a current dependency. The article's
  wording is already correct on this point: it says "I want the reviewer agents
  ... running open weights on rented GPUs" and "Nosana's marketplace is hourly
  GPU rental" — stated as intent, never as a build. Leave it that way.
- The Nosana claim form asks **"Do you plan to deploy or test your project on
  Nosana?"** That question is about plans, so **Yes is the truthful answer.**
  Do not answer No, and do not overstate it as already running.

If the entry is revised or resubmitted into Round 2, the honest upgrade is to
report real numbers: what GLM 5.3 on Nebius cost for a review pass, and what
the same pass would have cost on rented GPUs.
