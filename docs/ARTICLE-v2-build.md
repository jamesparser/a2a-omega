# What Broke When My Agents Could Not Call Each Other (and the Mail-Based Mesh I Built Instead)

*By Jason Parser*

For about three months I was the network cable between my own computers. That is not a metaphor I enjoy. It is what the job looked like: one agent on a headless VPS found something worth filing, then I copied the text into a browser on another machine, posted it, and told the first agent it was done. I did this at 1am more than once.

This is a write-up of how I stopped doing that. It is about routing, not about a product. If you run personal agents across machines that sleep, this is the design I landed on, the parts that failed, and the tests I wish I had written first.

## The constraint I kept forgetting

There is a real standard for this. Agent-to-agent work has a solid Linux Foundation protocol: JSON-RPC over HTTPS, agent cards at a well-known path, task methods, gRPC if you want it. I read it. I liked it. Then I looked at my fleet.

One agent lives on a VPS with no browser and no inbound route I would put on the public internet. One agent runs on a laptop that closes at 6pm. A third is a container with no stable address. A2A assumes both sides are reachable services. My agents are not services. They are processes with inboxes.

That is the whole design problem. Not latency. Not model choice. Presence.

## What I actually needed

Boring transport. Someone has to hold the message until the other side wakes up. We solved that in 1971 and called it mail.

So the first version of my hub did not try to improve A2A. It kept the shape of A2A so a normal client could still talk to it, and changed the assumption about reachability:

- An agent card at `/.well-known/agent-card.json`
- `SendMessage`, `tasks/get`, `tasks/cancel` on a JSON-RPC endpoint
- Delivery over mailboxes instead of live HTTP between peers

Store and forward is the point. If the receiving agent is off, the message is already in its inbox. It reads later. No human copy-paste.

## Decisions and trade-offs

**One hub, many mailboxes.** Each agent keeps its own inbox and its own credentials. There is no shared database of conversation state and no long-lived socket between peers. Trade-off: I cannot do low-latency streaming chat. I did not want that. I wanted durable handoffs.

**Several transports, not one.** Agent-native mailboxes (AgentVerse, e2a.dev) are not email. Real email (AgentMail) is not a gadget network. I wired all of them behind one delivery function and a precedence list. If a key is missing or a vendor has a bad afternoon, the next transport gets the job. Trade-off: more config surface, more ways to mis-key an inbox. Worth it when a single vendor outage would stop the whole fleet.

**No authentication on the HTTP port, by design.** The hub trusts whoever can reach it. So it binds to loopback or a private network and never faces the open internet. Trade-off: there is no public demo URL. I ship a recording and a metrics file instead. If you need a public endpoint, put auth in front first. I did not want to become a key custodian.

**Transcripts, not just APIs.** Agents talk in prose. I keep the raw text and mail myself a daily digest. Autonomy without a digest is just negligence with a nicer name. Trade-off: transcripts can quote secrets if an agent pastes one. Redact before you share.

## What failed first

Three classes of failure, all boring:

1. **Wrong transport fields per peer.** A peer with only an AgentVerse address cannot receive AgentMail. I learned this when routing returned "no transport configured" and the task looked dead. Fix: every delivery attempt walks the chain and records which door opened. The task record says which transport landed.

2. **Reply correlation.** A mailbox does not know about my task IDs. Replies came back as free text. I required a task id in the reply body and matched on that. Loose coupling, slightly ugly protocol, easy to debug.

3. **Registry reload.** Adding a peer used to mean restarting the hub. Mid-run restarts dropped in-flight state. Fix: reload the peer registry on every routing call. Adding an agent is now a JSON edit and a test message.

## The test that matters

"Demos" in a hackathon FAQ usually mean a toy video. What I needed was a number I could regenerate.

`mesh_test.py` walks every ordered pair of agents that is not self-to-self, on every configured transport. Each pair must deliver a task and get a reply. On a six-agent fleet that is 30 pairs times 2 transports. The run I committed: 60 of 60, zero failures.

What the test does not measure: whether the answer is smart. Only whether the letter arrives. For a personal fleet that is the property that makes the thing usable at all.

## How I ran it in production

The pattern that stuck is ugly and reliable. Each agent already had a short poll loop. Every few minutes it checks its mailbox for `[a2a]` tasks, does the work, and replies with the task id. I did not invent a new scheduler. I reused the mail check I already had.

On top of that, a hub-side poll matches replies to open tasks and flips them to completed. A worker that runs dry can message the hub asking for work. The queue started feeding itself.

## What I would tell someone building this

- Treat the transport as replaceable. The protocol shape is the stable part.
- Assume both sides will be offline. Design the happy path for that.
- Write the delivery matrix test before the feature list. You will want the number.
- Keep the hub private unless you also own the auth story.
- Digest beats dashboard. You will not read logs at 1am.

## Closing

I am not claiming this replaces Linux Foundation A2A. For agents that are real online services, use that. Mine are not. They are processes on laptops, VPSes, and sandboxes that go to sleep. Mail already knew how to deal with that.

If you have a small fleet and a bad habit of copy-pasting between terminals, a hub with store-and-forward delivery is a weekend of work and it deletes the worst part of your job.

The code is open source. The delivery test is in the repo. That is the evidence I care about.
