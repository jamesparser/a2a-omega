#!/bin/sh
# restart all fleet-poller actors so they load new code + env
for a in /proc/[0-9]*/cmdline; do
  c=$(tr "\0" " " < "$a" 2>/dev/null) || continue
  case "$c" in *a2a_fleet_poller.py*python*) kill "${a#/proc/}" 2>/dev/null;; esac
done
sleep 25
for x in agent-one agent-two agent-three agent-four agent-five agent-six; do
  n=0
  for a in /proc/[0-9]*/cmdline; do
    c=$(tr "\0" " " < "$a" 2>/dev/null) || continue
    case "$c" in *"__actor=$x"*) n=$((n+1));; esac
  done
  echo "$x $n"
done
