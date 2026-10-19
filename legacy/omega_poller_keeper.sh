#!/bin/bash
# Host-side keeper for the omega A2A poller fleet.
#
# Runs every 10s from the omega-a2a-poller.service systemd unit. Idempotent and
# quiet in steady state.
#
# Invariant enforced: EXACTLY ONE poller process PER fleet agent.
#
# Why per-agent and not one catch-all: a single process started with a five-name
# A2A_OWN_AGENTS polls all five mailboxes, and if per-actor processes also run
# then every mailbox has two pollers racing on the same .av_seen_<agent>.json.
# The race produced duplicate answers and could resurrect an envelope that the
# other process had already answered. So this keeper starts one actor per agent
# via run_poller_one.sh and kills any legacy catch-all it finds.
#
# Install (on the VPS host):
#   install -m 755 deploy/omega_poller_keeper.sh /root/omega_poller_keeper.sh
#   install -m 755 deploy/omega-a2a-poller.service /etc/systemd/system/
#   systemctl daemon-reload && systemctl enable --now omega-a2a-poller.service
set -u

# Fleet roster. Defaults to generic placeholders so a fresh install cannot
# start pollers for someone else's agents. Set A2A_KEEPER_AGENTS (or put it in
# /workspace/notes/fleet.env, which is gitignored) for your own fleet.
FLEET_ENV="${A2A_FLEET_ENV:-/workspace/notes/fleet.env}"
[ -f "$FLEET_ENV" ] && . "$FLEET_ENV"
AGENTS="${A2A_KEEPER_AGENTS:-agent-one agent-two agent-three}"
CONTAINER="${A2A_KEEPER_CONTAINER:-omega}"
LOG=/var/log/omega-a2a-poller.log
STARTER=/workspace/run_poller_one.sh

ts() { date -u '+%Y-%m-%d %H:%M:%S'; }
say() { echo "$(ts) $*" >> "$LOG"; }

dx() { docker exec "$CONTAINER" sh -c "$1" 2>/dev/null; }

# ---------------------------------------------------------------- helpers
# pids of POLLER processes whose cmdline matches $1.
#
# Two guards matter here, both learned the hard way:
#   * the pattern must be anchored on `python` - an unanchored
#     "*omega_poller.py*__actor=X*" also matches THIS scanning shell's own
#     cmdline (the pattern text is literally in it), which makes every actor
#     look alive and the keeper silently start nothing;
#   * /proc entries that vanish mid-scan are skipped.
pids_matching() {
    dx 'for p in /proc/[0-9]*/cmdline; do
          [ -r "$p" ] || continue
          c=$(tr "\0" " " < "$p" 2>/dev/null) || continue
          case "$c" in
            sh\ -c*|*\ /proc/*) continue ;;   # our own scan / any scanner shell
          esac
          case "$c" in '"$1"') echo "${p#/proc/}";; esac
        done' | sed 's#/cmdline##'
}

# every real poller process (anchored: starts with the python interpreter)
poller_pids() { pids_matching "python*omega_poller.py*"; }

has_actor() {  # has_actor <agent>
    [ -n "$(pids_matching "python*omega_poller.py*__actor=$1*")" ]
}

# ------------------------------------------------------- container present?
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
    # container down/restarting: nothing to do, do not spam the log
    exit 0
fi

# ------------------------------------------- kill legacy catch-all pollers
# A poller with no __actor= label serves the whole A2A_OWN_AGENTS list and
# therefore duplicates every per-actor poller.
for pid in $(poller_pids); do
    cmd=$(dx "tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null")
    case "$cmd" in
        *__actor=*) : ;;                       # per-actor: correct, keep
        *omega_poller.py*)
            say "killing legacy catch-all poller pid=$pid ($cmd)"
            dx "kill $pid" >/dev/null 2>&1
            ;;
    esac
done

# ------------------------------------- collapse duplicate actors per agent
# Enforce the invariant positively: if a slow start or a manual launch left two
# processes on one agent, keep the lowest pid and kill the rest. Without this a
# duplicate would survive forever, since has_actor only asks "is there one?".
for agent in $AGENTS; do
    pids=$(pids_matching "python*omega_poller.py*__actor=$agent*")
    n=$(printf '%s\n' "$pids" | grep -c . || true)
    if [ "${n:-0}" -gt 1 ]; then
        keep=$(printf '%s\n' "$pids" | sort -n | head -1)
        for pid in $pids; do
            [ "$pid" = "$keep" ] && continue
            say "killing duplicate actor $agent pid=$pid (keeping $keep)"
            dx "kill $pid" >/dev/null 2>&1
        done
    fi
done

# --------------------------------------------- ensure one actor per agent
started=0
for agent in $AGENTS; do
    if has_actor "$agent"; then
        continue
    fi
    say "starting actor $agent"
    docker exec -d "$CONTAINER" sh -c "nohup $STARTER $agent >/dev/null 2>&1 &" >/dev/null 2>&1
    started=$((started + 1))
done

if [ "$started" -gt 0 ]; then
    # Give the interpreter time to appear in /proc before judging success.
    # Too short a settle makes the keeper declare a healthy actor "down" and
    # start a second one.
    sleep 25
fi

# ------------------------------------------------- self-heal a crash loop
# Only actors genuinely still absent are retried, and only after one more
# settle, so a merely slow start cannot trigger an SDK reinstall or a
# duplicate launch.
missing=""
for agent in $AGENTS; do
    has_actor "$agent" || missing="$missing $agent"
done

if [ -n "$missing" ]; then
    sleep 15
    still=""
    for agent in $missing; do
        has_actor "$agent" || still="$still $agent"
    done
    missing="$still"
fi

if [ -n "$missing" ]; then
    say "actors still down:$missing - reinstalling uagents SDK and retrying"
    dx 'pip3 install -q uagents uagents_core 2>/dev/null || python3 -m pip install -q uagents uagents_core' >/dev/null 2>&1
    for agent in $missing; do
        has_actor "$agent" && continue      # came up meanwhile; do not double-start
        say "retrying actor $agent after SDK reinstall"
        docker exec -d "$CONTAINER" sh -c "nohup $STARTER $agent >/dev/null 2>&1 &" >/dev/null 2>&1
    done
    sleep 25
    still=""
    for agent in $AGENTS; do
        has_actor "$agent" || still="$still $agent"
    done
    if [ -n "$still" ]; then
        say "STILL DOWN after SDK reinstall:$still (needs operator attention)"
    else
        say "recovered after SDK reinstall:$missing"
    fi
fi
