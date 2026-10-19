# Mesh poller: survive reboot + self-heal

- `a2a_phone_poller.py` answers `[a2a] X -> NAME` emails; the manager agent shells out to its real Hermes brain, others use a free-model fallback. Replies use subject `[a2a] NAME -> X reply`.
- `start.ps1` launches it only if not already running (idempotent).
- Self-heal: a Task Scheduler job every 1 min runs a keepalive that calls `start.ps1`.
- Run at boot (needs an ELEVATED shell once): register a task with an AtStartup trigger (1 min delay), principal = your user with S4U logon, action = `powershell -File start.ps1`. It touches no other task.
- Linux: cron `* * * * * run.sh` + `@reboot run.sh`, where run.sh does `pgrep -f poller || { nohup python3 poller & } ;`.

## Omega side
`examples/omega_channel/omega_mesh_channel.py`: owner asks the agent on Telegram to message a peer; the channel emails it, then relays the reply and measured time back to the owner chat. Pending state is file-backed so restarts don't lose it. Gotcha fixed: `\b` in a non-raw string is a backspace; use raw strings for the `reply` regex.
