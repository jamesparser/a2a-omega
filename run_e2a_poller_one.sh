#!/bin/sh
# One e2a poller actor for a SINGLE fleet agent (companion to run_poller_one.sh).
# Answers the agent's e2a.dev inbox with its OWN brain + ledger (reuses
# omega_poller's answer pipeline). Own log + own seen/attempt files.
#
# usage: run_e2a_poller_one.sh <agent-name>
agent="$1"
[ -n "$agent" ] || { echo "usage: run_e2a_poller_one.sh <agent>"; exit 2; }

export A2A_OWN_AGENTS="$agent"
export A2A_E2A_BASE="${A2A_E2A_BASE:-https://api.e2a.dev}"
export A2A_POLL_SEC="${A2A_POLL_SEC:-5}"
export A2A_MAX_ATTEMPTS="${A2A_MAX_ATTEMPTS:-3}"

# Brain env - identical to the agentverse poller so answers are as real.
if [ -f /workspace/notes/omega_answer.env ]; then
    set -a
    . /workspace/notes/omega_answer.env
    set +a
fi

exec python3 /workspace/a2a_e2a_poller.py "__actor=$agent" \
    >> "/workspace/a2a_e2a_poller_$agent.log" 2>&1
