<#
.SYNOPSIS
  Read-only health check for the a2a fleet on the gaming laptop + reachable VPS.

.NOTES
  Run from the laptop:  powershell -NoProfile -ExecutionPolicy Bypass -File status-check.ps1

  Two bugs in the original draft are fixed here:
    * it probed http://127.0.0.1:8787/healthz, but the hub binds ONLY to the
      Tailscale address, so a perfectly healthy hub reported as DOWN. The bind
      address is now resolved from A2A_HUB_HOST / the live listener.
    * Get-CimInstance -Filter "name like 'python%'" was mangled by nested
      quoting through the SSH layer; process discovery now uses Where-Object.
#>
$ErrorActionPreference = "Continue"

# Set these for your own machine/fleet, e.g.
#   $env:A2A_DIR = "C:\work\a2a"; $env:A2A_FLEET = "agent-one,agent-two"
$dir = if ($env:A2A_DIR) { $env:A2A_DIR } else { $PSScriptRoot }
$FLEET = if ($env:A2A_FLEET) { $env:A2A_FLEET.Split(",") } else { @("agent-one","agent-two","agent-three") }

function Sec([string]$t) { Write-Output ""; Write-Output "=== $t ===" }

Sec "HOST"
hostname
Get-Date -Format o

# ---------------------------------------------------------- resolve hub bind
Sec "HUB BIND ADDRESS"
$bindHost = $null
$listen = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalPort -eq 8787 })
if ($listen.Count -ge 1) {
  $bindHost = $listen[0].LocalAddress
  foreach ($l in $listen) { Write-Output ("  listener {0}:{1} pid={2}" -f $l.LocalAddress, $l.LocalPort, $l.OwningProcess) }
}
$envHost = Select-String -Path (Join-Path $dir ".env") -Pattern "^A2A_HUB_HOST=" -ErrorAction SilentlyContinue
if ($envHost) { Write-Output ("  .env A2A_HUB_HOST = " + ($envHost.Line -replace '^A2A_HUB_HOST=','')) }
if (-not $bindHost -or $bindHost -eq "0.0.0.0") { $bindHost = "127.0.0.1" }
# A Tailscale CGNAT address is what the fleet actually dials; 0.0.0.0 would answer
# on any of them, so prefer a concrete non-loopback listener when one exists.
$ts = $listen | Where-Object { $_.LocalAddress -like "100.*" } | Select-Object -First 1
if ($ts) { $bindHost = $ts.LocalAddress }
Write-Output "  probing -> $bindHost"

Sec "HUB HEALTH"
try {
  $r = Invoke-WebRequest -Uri "http://${bindHost}:8787/healthz" -UseBasicParsing -TimeoutSec 12
  Write-Output ("  healthz HTTP {0}: {1}" -f $r.StatusCode, $r.Content)
} catch { Write-Output ("  healthz FAIL: " + $_.Exception.Message) }

Sec "HUB PROCESS"
$hub = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like "python*" -and $_.CommandLine -like "*a2a_hub.py*" })
Write-Output ("  a2a_hub.py instances = " + $hub.Count + $(if ($hub.Count -eq 1) { " (expected 1)" } else { "  <-- EXPECTED EXACTLY 1" }))
$hub | ForEach-Object { Write-Output ("    pid=" + $_.ProcessId) }

Sec "RESPONDER PROCESS (single-instance lock enforced)"
$lcb = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like "python*" -and $_.CommandLine -like "*lcb_responder*" })
Write-Output ("  lcb_responder instances = " + $lcb.Count + $(if ($lcb.Count -le 1) { " (expected <=1)" } else { "  <-- DUPLICATE" }))
$lcb | ForEach-Object { Write-Output ("    pid=" + $_.ProcessId) }

Sec "CREDENTIAL EXTERNALISATION (no secrets printed)"
foreach ($f in @("lcb_responder.py","poll_daemon.py","a2a_client.py")) {
  $p = Join-Path $dir $f
  if (-not (Test-Path $p)) { continue }
  $hits = @(Select-String -Path $p -Pattern "am_us_[0-9a-f]{16,}" -AllMatches -ErrorAction SilentlyContinue)
  if ($hits.Count -gt 0) {
    Write-Output ("  LEAK  {0}: {1} hardcoded AgentMail key(s) on line(s) {2}" -f $f, $hits.Count, (($hits | ForEach-Object { $_.LineNumber }) -join ","))
  } else {
    Write-Output ("  clean {0}: no hardcoded AgentMail keys" -f $f)
  }
}
if (Test-Path (Join-Path $dir "lcb_agents.json")) { Write-Output "  ok    lcb_agents.json present (externalised credentials)" }
else { Write-Output "  MISS  lcb_agents.json absent - responder cannot authenticate" }

Sec "SCHEDULED TASKS"
Get-ScheduledTask -ErrorAction SilentlyContinue |
  Where-Object { $_.TaskName -match "a2a|lcb|hub|omega|poll" } |
  ForEach-Object { Write-Output ("  {0} | {1}{2}" -f $_.TaskName, $_.State, $_.TaskPath) }

Sec "LOG TAILS"
foreach ($lg in @(@("hub.err.log",6), @("hub_watchdog.log",5), @("lcb_watchdog.log",5))) {
  $p = Join-Path $dir $lg[0]
  Write-Output ("  --- " + $lg[0] + " ---")
  if (Test-Path $p) { Get-Content $p -Tail $lg[1] -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ("    " + $_) } }
  else { Write-Output "    (absent)" }
}
$rl = if ($env:LCB_LOG) { $env:LCB_LOG } else { Join-Path $dir "lcb_a2a_replies.log" }
Write-Output "  --- lcb_a2a_replies.log ---"
if (Test-Path $rl) { Get-Content $rl -Tail 6 -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ("    " + $_) } } else { Write-Output "    (absent)" }

Sec "FLEET ROSTER (from config/peers.json)"
$pf = Join-Path $dir "config\peers.json"
if (Test-Path $pf) {
  try {
    $j = Get-Content $pf -Raw | ConvertFrom-Json
    foreach ($n in $FLEET) {
      $e = $j.$n
      if ($null -eq $e) { Write-Output ("  {0,-18} MISSING from peers.json" -f $n); continue }
      Write-Output ("  {0,-18} agentverse={1} e2a={2} agentmail={3}" -f $n,
        [bool]$e.agentverse_address, [bool]$e.e2a_email, [bool]$e.agent_mail_key)
    }
    Write-Output ("  total peers = " + ($j.PSObject.Properties.Name).Count)
  } catch { Write-Output ("  peers.json parse FAIL: " + $_.Exception.Message) }
} else { Write-Output "  config/peers.json absent" }

Sec "TRANSPORT PRECEDENCE (must be agentverse -> e2a -> agentmail)"
$t = Select-String -Path (Join-Path $dir ".env") -Pattern "^A2A_TRANSPORT=" -ErrorAction SilentlyContinue
if ($t) { Write-Output ("  " + $t.Line) } else { Write-Output "  A2A_TRANSPORT unset -> defaults to agentverse" }
$chain = Select-String -Path (Join-Path $dir "a2a_hub.py") -Pattern 'full = \["agentverse", "e2a", "agentmail"\]' -ErrorAction SilentlyContinue
if ($chain) { Write-Output "  chain literal OK (mailslurp removed)" } else { Write-Output "  CHAIN LITERAL NOT FOUND - hub may be an old build" }

Write-Output ""
Write-Output "=== DONE-STATUS ==="
