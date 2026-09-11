<#
Keep the demo alive: the FastAPI server plus a cloudflared quick tunnel, each
restarted when it dies, with the assigned public URL printed and kept in
data/tunnel.log.

Run it from a terminal you leave open:

  # offline cache channel, no key needed
  powershell -ExecutionPolicy Bypass -File scripts/start_demo.ps1 -Cache

  # real model channel; set the key in this shell first, it is never echoed
  $env:GATEKEEPER_OPENAI_API_KEY = (Get-Content -Raw "$HOME\my-key.txt").Trim()
  powershell -ExecutionPolicy Bypass -File scripts/start_demo.ps1

ASCII-only on purpose: Windows PowerShell 5.1 reads a .ps1 as ANSI unless it
carries a UTF-8 BOM, so non-ASCII text here breaks the parser.

A quick tunnel still is not durable: Cloudflare can drop it (observed:
"accept stream listener encountered a failure" then "no more connections active
and exiting"), and each restart hands out a NEW random hostname, so the printed
URL changes and any link you published goes stale. This script keeps the service
up and tells you the current address; it cannot make the address permanent. For
that, see the hosting section in README.md.
#>
param(
  [switch]$Cache,
  [int]$Port = 8766,
  [int]$IntervalSeconds = 5
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not $Cache -and -not $env:GATEKEEPER_OPENAI_API_KEY) {
  Write-Error 'GATEKEEPER_OPENAI_API_KEY is not set. Set it in this shell, or run with -Cache for the keyless offline channel.'
  exit 1
}

if ($Cache) {
  $env:GATEKEEPER_PROVIDER = 'cache'
  Write-Host 'channel    cache (no model, no key)'
} else {
  # Pinned, never `auto`: a fallback would spend the author's own credentials on
  # a stranger's request.
  $env:GATEKEEPER_PROVIDER = 'openai'
  if (-not $env:GATEKEEPER_OPENAI_BASE_URL) { $env:GATEKEEPER_OPENAI_BASE_URL = 'https://token-plan-cn.xiaomimimo.com/v1' }
  if (-not $env:GATEKEEPER_OPENAI_MODEL)    { $env:GATEKEEPER_OPENAI_MODEL    = 'mimo-v2.5-pro' }
  Write-Host "channel    openai / $env:GATEKEEPER_OPENAI_MODEL (key read from the environment)"
}
# Sample replays from the shipped cache; anything a visitor writes goes to a
# scratch directory so it never lands in the tracked tree.
$env:GATEKEEPER_CACHE_DIR = 'data/cache_demo'
$env:GATEKEEPER_CACHE_FALLBACK_DIR = 'data/cache'
New-Item -ItemType Directory -Force -Path data | Out-Null

$tunnelLog = Join-Path $PWD 'data/tunnel.log'

function Start-Server {
  Write-Host "[server] starting uvicorn on 127.0.0.1:$Port"
  Start-Process python -PassThru -NoNewWindow -ArgumentList @(
    '-m', 'uvicorn', 'app.server:app', '--host', '127.0.0.1', '--port', "$Port"
  )
}

function Start-Tunnel {
  Write-Host '[tunnel] starting cloudflared quick tunnel'
  Start-Process cloudflared -PassThru -NoNewWindow -ArgumentList @(
    'tunnel', '--url', "http://127.0.0.1:$Port", '--logfile', $tunnelLog
  )
}

function Show-TunnelUrl {
  if (-not (Test-Path $tunnelLog)) { return }
  $line = Select-String -Path $tunnelLog -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches |
    Select-Object -Last 1
  if ($line) {
    $url = [regex]::Match($line.Line, 'https://[a-z0-9-]+\.trycloudflare\.com').Value
    Write-Host ''
    Write-Host "PUBLIC URL  $url"
    Write-Host ''
  }
}

$server = Start-Server
Start-Sleep -Seconds 3
$tunnel = Start-Tunnel
Start-Sleep -Seconds 5
Show-TunnelUrl

# Watchdog: the tunnel is the flaky half, but a dead server makes it useless too.
while ($true) {
  Start-Sleep -Seconds $IntervalSeconds
  if ($server.HasExited) {
    Write-Host "[server] exited (code $($server.ExitCode)) - restarting"
    $server = Start-Server
  }
  if ($tunnel.HasExited) {
    Write-Host "[tunnel] exited (code $($tunnel.ExitCode)) - restarting; the public URL will change"
    $tunnel = Start-Tunnel
    Start-Sleep -Seconds 8
    Show-TunnelUrl
  }
}
