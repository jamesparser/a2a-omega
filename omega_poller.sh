#!/bin/sh
set -a
. /workspace/notes/omega_answer.env
export A2A_AGENTVERSE_ENV=/workspace/notes/agentverse.env
export A2A_OWN_AGENTS=agent-two
export A2A_POLL_SEC=5
set +a
exec python3 /workspace/omega_poller.py
