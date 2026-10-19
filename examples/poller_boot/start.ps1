if (-not (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'a2a_phone_poller' -and $_.Name -match 'python' })) {
  $env:HOME='<POLLER_DIR>\lap'; $env:USERPROFILE=$env:HOME
  Start-Process -WindowStyle Hidden -FilePath python -ArgumentList '<POLLER_DIR>\a2a_phone_poller.py' -RedirectStandardOutput '<POLLER_DIR>\lap\poller.log' -RedirectStandardError '<POLLER_DIR>\lap\poller.err'
}
