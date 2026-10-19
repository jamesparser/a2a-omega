# Your AI Agents Don't Need You Anymore

*By Jason Parser — entry for the [Decentralize AI Hackathon](https://decentralizeai.tech) by HackerNoon, Nosana, Arweave and MEXC.*

> **Status:** submitted to HackerNoon editors 2026-09-27 (draft id
> `6ab88985db16a69b1ffa3433`, author handle `@jasonparser`). This file is the
> **published** text, synced back from the live editor, so it differs from the
> first draft on purpose. Three edits were made in the editor and are reflected
> below: the "Fork of a2a-omega, which I built for BGI Commons HyperSprint #2"
> sentence was dropped from the closing paragraph, "skip my middlebox, you'll be
> glad you did" was softened to "skip my solution", and the inline-code styling
> inside the results link was removed. Word count as published: 1,258.
>
> **KNOWN DEFECT, not yet fixed:** the body below uses 19 em dashes, which
> violates the owner's global writing rule. This file is a record of what is
> live, so the dashes were deliberately left in place rather than quietly
> diverging from the published version. Fix them in the HackerNoon draft first,
> then re-sync this file. See docs/HANDOVER-PROMPT.md task 1.

---

For about three months I was the network cable between my own computers.

I run a handful of agents. Omega is named after SingularityNET's agentic harness, and it lives on a VPS where it does research headless. Hermes, Liberclaw, Betterclaw and a couple of others do different jobs on different boxes — one can see images, one can drive a browser, one is patient with diffs. Each of them was reasonably good at its own thing.

None of them could talk to each other. So every message passed through me.

The one that finally made me quit was at 1am. Omega had found something worth publishing, but posting it needed a logged-in browser, and Omega sits on a headless server with no browser and no way to get one. It wrote out the exact instructions, I read them, I opened the browser on another machine, I pasted, I posted, I told Omega it was done.

Nothing in that chain required a human except me. I was doing store-and-forward by hand, badly, on a schedule that included my sleep.

## What an agent actually needs from a network

There's a decent standard for this now. A2A — Agent-to-Agent — came out of Google in April 2025, went to the Linux Foundation in June, and is now JSON-RPC 2.0, gRPC and REST over HTTPS with an "agent card" at a well-known URL describing what an agent can do and how to reach it.

It's a good protocol. It is built for a specific kind of agent: one that runs as a **service**. It has a port. It has TLS. Somebody answers when you call it.

My agents are not that. One runs on a laptop that closes at 6pm. One is in a container with no inbound route anybody could dial. None of them have a stable public address someone can call, so "send it a request" has no implementation for most of my fleet. Not inconvenient — impossible.

What they need is the boring thing: somewhere to leave a message that survives the recipient being turned off. We solved that in 1971 and called it mail. Store it now, deliver it later, nobody has to be listening when the letter arrives.

So I built [A2A Omega Mesh](https://github.com/jamesparser/a2a-omega-mesh): a small hub you run yourself that speaks an A2A-shaped interface on the outside — `/.well-known/agent-card.json` at protocol version 0.3.0, `SendMessage`, `tasks/get`, `tasks/cancel` — and on the inside delivers over whatever transports your agents can actually reach.

## Not just email, and not one vendor either

Three transports, in a chain:

- **AgentVerse** — Fetch.ai's agent network. This is not email. Agents have
  mailbox addresses and you submit signed envelopes; the SDK is `uagents`.

- **e2a.dev** — agent-native inboxes at `@agents.e2a.dev`, REST API, again not
  email.

- **AgentMail** — real email inboxes and an API for them.

Pick a preferred one per run; the hub rotates it to the front of the chain and keeps the rest as fallbacks. A peer that only has an AgentVerse address gets AgentVerse. A peer with only an inbox gets email. If a key is missing or a vendor has a bad afternoon, routing doesn't stop — the message goes out the next door.

That's the part I care about, and it's the reason this is a decentralisation argument rather than a convenience argument. A fleet whose messages depend on one company's API being up is not autonomous; it's a customer. I would rather my agents keep talking when a provider disappears, or prices change, or I get banned by mistake and can't appeal until Thursday.

Every identity is also its own. Each agent gets its own mailbox and its own credentials. There's no shared database, no persistent connection between peers, and no third party holding the whole social graph of my fleet.

## What it looks like when it works

**A task that needs different hardware.** Omega, headless on the VPS, needs something posted from a logged-in browser. It sends a task to the agent that has eyes and a browser. The task sits in that agent's mailbox until it polls. It does the thing, replies, and I read about it in the morning.

**A second opinion before anything goes out.** Omega finds a bug. Before a report leaves the building it messages another agent the draft and the proof of concept and asks for a check. The reply comes back with the edge case that breaks the PoC. Two agents, one false positive deleted, zero copy-paste by me.

**Chasing a job.** I want to know whether Liberclaw finished. I ask the hub, which has a task record for that peer, and the peer answers on its own schedule. I don't open a terminal and I don't SSH anywhere.

**A worker asking for work.** This one still surprises me. Betterclaw runs dry and sends a message saying it has nothing to do and needs tasks. The hub treats a message *from* a peer as a task like any other. The queue started feeding itself.

I keep the transcripts, and I get a summary email once a day listing what the agents said to each other — which turns out to be the minimum viable amount of supervision for letting them run unsupervised. Autonomy without a daily digest is just negligence with a nicer name.

## Does it work, measurably

Six agents. Every ordered pair that isn't an agent messaging itself — 30 pairs — run on both e2a and AgentVerse. Each pair has to receive a task and reply to it.

**60 out of 60 deliveries, no failures.** The run is in the repo as [results/mesh-2026-09-25.json](https://github.com/jamesparser/a2a-omega-mesh/blob/main/results/mesh-2026-09-25.json), and `mesh_test.py --json` regenerates it on your own fleet.

The property being tested is delivery: can agent A get work to agent B when B isn't listening. That's the thing that makes a personal fleet usable at all.

## Where this is going

The obvious next step is for the fleet to stop depending on closed APIs for its brains too, which is why I entered this hackathon: I want the reviewer agents — the ones doing the second audit pass, reading a 900-line diff, checking a PoC — running open weights on rented GPUs rather than on a per-token meter. Nosana's marketplace is hourly GPU rental, so a fleet that owns its transports should own its compute budget too, and I have opinions about how badly that goes if you let a job run unattended.

The second step is provenance. Right now a transcript is text on my disk. If the hash of a run — what task went out, what came back, who signed it — lands on permanent storage, then "agent X verified agent Y's finding" stops being my assertion and becomes something checkable after the fact. That matters a great deal in security work, where the interesting moment is the one where somebody claims they double-checked.

## What it isn't

A replacement for A2A. It deliberately mimics A2A's shape so an A2A client can talk to it, and if your agents are honest services with reachable endpoints, use A2A directly and skip my solution.

A security boundary either. There's no authentication on the HTTP endpoint because it was never meant to face the internet: bind it to `127.0.0.1` or a private network. And mail is not a secret channel. It gives you delivery and provenance, not confidentiality; encrypt what matters. The hub also trusts anyone who can reach the port, which is exactly as scary as it reads and why it lives behind a VPN.

Code's here: [jamesparser/a2a-omega-mesh](https://github.com/jamesparser/a2a-omega-mesh) — Python 3.9+, standard library for the email path, MIT. If you have agents sitting on machines that can't call each other, run a hub for an afternoon and see how much of your day was actually you pretending to be a mailbox.

---

## Tags used on HackerNoon

`Decentralize AI Hackathon` · `Decentralized AI` · `Agentic AI` · `LLMs` ·
`Open Source` · `Artificial Intelligence`

Category: AI and ML · Story type: Guide · Original: Yes
Featured image: `docs/featured.png` (agents -> local hub -> transports)
