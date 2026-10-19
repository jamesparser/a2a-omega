# After the HackerNoon article is live

Hold everything that spends credits until the post is public. Round 1 closes
31 Oct 2026, so there is time.

## Demo rules (from the contest FAQ)

There is **no required length** and no required shot list. The FAQ only says the
entry needs verifiable evidence: working code, a deployment URL, or reproducible
metrics. Mockups, prototypes without demos, and coming-soon projects are not
eligible.

What that means for us: show the thing working. A short clip is enough. The
BGI pitch at about three minutes is also fine. Title and end cards are a
branding choice, not a contest rule.

Recommended shape, matching the BGI branding brief:

1. **Title screen** (4 to 6 seconds)
   - Team: JasonParser Security / Jason Parser
   - GitHub: https://github.com/jamesparser/a2a-omega-mesh
   - X: https://x.com/jasonparsersec
   - Contest: Decentralize AI Hackathon
2. **Body** (30 to 60 seconds)
   - Two agents exchange a task and a reply through the hub
   - Daily transcript digest arriving in the owner inbox
   - Optional: hub `GET /healthz` on the VPS so the box is visible
3. **End screen** (4 to 6 seconds)
   - Same GitHub and X links
   - Repo name and "store-and-forward A2A hub"
   - No secrets on screen

Hub stays private. It binds to 127.0.0.1 or Tailscale and the HTTP endpoint has
no authentication by design. Record on the box or over Tailscale. Do not open
it to the public internet for a prettier shot.

## Should the VPS be on camera?

Useful, not required. A short clip of the VPS terminal showing `GET /healthz`
returning peers and protocol version, then a `mesh_test.py` run finishing,
answers "is this real?" without a public URL. Keep it local. Redact anything
key-shaped before the recording leaves the machine.

If time is tight: Hermes already using the hub on the gaming laptop is live
proof. The committed `results/mesh-2026-09-25.json` is the countable metric.
A VPS shot is polish, not a gate.

## Checklist, in order, once the post is public

1. **Grab the real URL.** The slug may not be
   `your-ai-agents-dont-need-you-anymore`. Use whatever the live post has.
2. **Tweet from @JasonParserSec only.** Text is in `docs/TWEET.md`. Do not use
   @the brand account. Do not cross-post. If this machine cannot post from the
   owner's X account, hand him the URL and the text.
3. **Host the demo.** Upload `assets/a2a-omega-demo-46s.mp4` (or the polished
   version with title and end cards) somewhere with a stable link: GitHub
   release on `jamesparser/a2a-omega-mesh`, or a gist/release asset. Judges
   need a link they can open.
4. **Submit the project entry at decentralizeai.tech.** Put in:
   - Article URL
   - Repo: https://github.com/jamesparser/a2a-omega-mesh
   - Demo link
   - Metrics: 60/60 deliveries, `results/mesh-2026-09-25.json`
   - Keep the Nosana wording as intent, not a build.
5. **Optional but strong, same day if the VPS is free:**
   - Re-run `python mesh_test.py --json results/mesh-$(date +%F).json`
   - Commit the raw JSON
   - Record the short VPS terminal clip and attach it to the demo if useful
6. **Optional, only if editing after publish is cheap and safe:**
   - Swap in `docs/ARTICLE-post-publish.md` (zero em dashes) once the story is
     public and edits no longer risk restarting review.

## Not now, for Round 2

- Hash the mesh run and transcript digest to Arweave (sponsor "provenance
  records" language, Permanent Storage tag).
- Follow-up post about actually spending Nosana credits, for the extra $450
  pool. Only after credits arrive and are used.
- Real GLM 5.3 versus rented GPU cost numbers.

## Do not spend credits on

- Rebuilding the demo from scratch.
- Polishing the article before publication.
- Anything that only matters if the post is rejected. Re-check status first.
