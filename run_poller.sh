#!/bin/sh
# omega-container launcher: agentverse key + asi1 brain + grounded ledger dir.
export A2A_AGENTVERSE_ENV=/workspace/notes/agentverse.env
export A2A_OWN_AGENTS="agent-two,agent-three,agent-four,agent-five,agent-six"
export A2A_LEDGER_DIR=/workspace/notes/ledger
export A2A_POLL_SEC=2
if [ -f /workspace/notes/omega_answer.env ]; then
  set -a
  . /workspace/notes/omega_answer.env
  set +a
fi
exec python3 /workspace/omega_poller.py "$@"
