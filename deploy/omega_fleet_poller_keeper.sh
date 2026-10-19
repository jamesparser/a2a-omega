#!/bin/bash
# Host-side keeper for the UNIFIED fleet poller (10-02). One a2a_fleet_poller.py
# actor per fleet agent, inside the omega container. This replaces BOTH the
# agentverse-only omega_poller keeper and the e2a-only keeper, so it must be
# the ONLY poller fleet running (the two old services are disabled at cutover).
#
# Runs every ~10s from the omega-fleet-poller systemd unit. Idempotent + quiet
# in steady state. Enforces: EXACTLY ONE fleet-poller actor per agent.
#
# Install (on the VPS host):
#   install -m 755 deploy/run_fleet_poller_one.sh    /workspace/run_fleet_poller_one.sh   (via docker cp)
#   install -m 755 deploy/omega_fleet_poller_keeper.sh /root/omega_fleet_poller_keeper.sh
#   install -m 644 deploy/omega-fleet-poller.service  /etc/systemd/system/
#   systemctl daemon-reload && systemctl enable --now omega-fleet-poller.service
set -u

AGENTS="${A2A_KEEPER_AGENTS:-agent-one agent-two agent-three agent-four agent-five agent-six}"
CONTAINER="${A2A_FLEET_KEEPER_CONTAINER:-omega}"
LOG=/var/log/omega-fleet-poller.log
STARTER=/workspace/run_fleet_poller_one.sh

ts() { date -u '+%Y-%m-%d %H:%M:%S'; }
say() { echo "$(ts) $*" >> "$LOG"; }

# pids of fleet-poller actors whose cmdline matches the anchored $1 pattern.
# Anchored on `python` so the keeper's own scan shell never matches itself.
pids_matching() {
  docker exec "$CONTAINER" sh -c 'for p in /proc/[0-9]*/cmdline; do
          [ -r "$p" ] || continue
          c=$(tr "\0" " " < "$p" 2>/dev/null) || continue
          case "$c" in
            sh\ -c*|*\ /proc/*) continue ;;
          esac
          case "$c" in
            '"$1"') echo "${p#/proc/}";;
          esac
        done' 2>/dev/null | sed 's#/cmdline##'
}
has_actor() { [ -n "$(pids_matching "python*a2a_fleet_poller.py*__actor=$1*")" ]; }

# container present?
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  exit 0
fi

# ensure the poller files are present in the container (idempotent; no-op if present)
if ! docker exec "$CONTAINER" test -f a2a_fleet_poller.py 2>/dev/null; then
  say "missing a2a_fleet_poller.py in $CONTAINER - expecting host mount"
fi

# collapse duplicate actors (keep lowest pid)
for agent in $AGENTS; do
  pids=$(pids_matching "python*a2a_fleet_poller.py*__actor=$agent*")
  n=$(printf '%s\n' "$pids" | grep -c . || true)
  if [ "${n:-0}" -gt 1 ]; then
    keep=$(printf '%s\n' "$pids" | sort -n | head -1)
    for pid in $pids; do
      [ "$pid" = "$keep" ] && continue
      say "killing duplicate fleet actor $agent pid=$pid (keeping $keep)"
      docker exec "$CONTAINER" kill "$pid" >/dev/null 2>&1
    done
  fi
done

# ensure one actor per agent
started=0
for agent in $AGENTS; do
  if has_actor "$agent"; then
    continue
  fi
  say "starting fleet actor $agent"
  docker exec -d "$CONTAINER" sh -c "nohup $STARTER $agent >/dev/null 2>&1 &" >/dev/null 2>&1
  started=$((started + 1))
done

# settle, then self-heal: restart only actors still genuinely absent.
if [ "$started" -gt 0 ]; then
  sleep 20
fi
for agent in $AGENTS; do
  if ! has_actor "$agent"; then
    say "restarting missing fleet actor $agent"
    docker exec -d "$CONTAINER" sh -c "nohup $STARTER $agent >/dev/null 2>&1 &" >/dev/null 2>&1
    sleep 20
    break
  fi
done
exit 0
