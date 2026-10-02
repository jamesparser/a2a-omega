#!/bin/sh
# One poller actor for a SINGLE fleet agent.
#
# Each agent gets its own process so it answers with its OWN brain slot, persona
# and ledger, and so no two processes ever share a .av_seen_<agent>.json file.
#
# usage: run_poller_one.sh <agent-name>
agent="$1"
[ -n "$agent" ] || { echo 'usage: run_poller_one.sh <agent>'; exit 2; }

# Operator-local fleet config (gitignored). Holds the real roster, seed prefix
# and reply-redirect target, so none of them live in the public repo.
[ -f "${A2A_FLEET_ENV:-/workspace/notes/fleet.env}" ] && . "${A2A_FLEET_ENV:-/workspace/notes/fleet.env}"

export A2A_AGENTVERSE_ENV="${A2A_AGENTVERSE_ENV:-/workspace/notes/agentverse.env}"
export A2A_OWN_AGENTS="$agent"
export A2A_LEDGER_DIR="${A2A_LEDGER_DIR:-/workspace/notes/ledger}"
export A2A_POLL_SEC="${A2A_POLL_SEC:-2}"

# Never lose an answer: if the envelope's sender is not a registered Agentverse
# agent the reply would 404, so redirect it to the hub owner's mailbox.
export A2A_REPLY_FALLBACK="${A2A_REPLY_FALLBACK:-}"
# Bound retries so one undeliverable message cannot block the mailbox forever.
export A2A_MAX_ATTEMPTS="${A2A_MAX_ATTEMPTS:-3}"

# Per-agent brain (A2A_ANSWER_BASE / _KEY / _MODEL). Without these the agent can
# only acknowledge, which is exactly the failure this fleet is meant to avoid.
if [ -f /workspace/notes/omega_answer.env ]; then
    set -a
    . /workspace/notes/omega_answer.env
    set +a
fi

exec python3 /workspace/omega_poller.py "__actor=$agent" \
    >> "/workspace/omega_poller_$agent.log" 2>&1
