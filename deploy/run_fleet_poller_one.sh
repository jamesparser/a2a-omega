#!/bin/sh
# One UNIFIED fleet poller actor for a SINGLE fleet agent (10-02 cutover).
# Replaces omega_poller.py (agentverse-only) + a2a_e2a_poller.py (e2a-only):
# one process reads ALL THREE lanes and replies on the two reliable ones
# (AgentMail primary + Agentverse best-effort; e2a is read-only - send capped).
#
# usage: run_fleet_poller_one.sh <agent-name>
agent="$1"
[ -n "$agent" ] || { echo "usage: run_fleet_poller_one.sh <agent>"; exit 2; }

export A2A_OWN_AGENTS="$agent"
export A2A_AGENTVERSE_BASE="${A2A_AGENTVERSE_BASE:-https://agentverse.ai}"
export A2A_AGENTVERSE_ENV="${A2A_AGENTVERSE_ENV:-./notes/agentverse.env}"
export A2A_AGENTMAIL_KEYS="${A2A_AGENTMAIL_KEYS:-./notes/agentmail_keys.json}"
export A2A_E2A_KEY_FILES="${A2A_E2A_KEY_FILES:-./notes/e2a.env,./notes/e2a2.env}"
export A2A_POLL_SEC="${A2A_POLL_SEC:-5}"
export A2A_AM_POLL_SEC="${A2A_AM_POLL_SEC:-30}"
export A2A_MAX_ATTEMPTS="${A2A_MAX_ATTEMPTS:-3}"

# Brain env - identical to the old agentverse poller so answers are as real.
if [ -f ./notes/omega_answer.env ]; then
    set -a
    . ./notes/omega_answer.env
    set +a
fi

exec python3 a2a_fleet_poller.py "__actor=$agent" \
    >> "a2a_fleet_poller_$agent.log" 2>&1
