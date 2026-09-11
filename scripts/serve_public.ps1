<#
Start the public demo: real model channel, key read from the environment only.

ASCII-only on purpose: Windows PowerShell 5.1 reads a .ps1 as ANSI unless it
carries a UTF-8 BOM, so non-ASCII text in this file breaks the parser on a
machine whose codepage is not UTF-8.  Chinese output belongs in the Python
services, which decode UTF-8 regardless of platform.

The key is never accepted as an argument, never printed and never written to
disk.  Put it in this shell first (piping from a file keeps it out of your shell
history, which a literal assignment would not):

  $env:GATEKEEPER_OPENAI_API_KEY = (Get-Content -Raw "$HOME\my-key.txt").Trim()
  powershell -ExecutionPolicy Bypass -File scripts/serve_public.ps1

Then expose it from a second window:

  cloudflared tunnel --url http://127.0.0.1:8766

Before pointing a reviewer at it, run the leak probe against the live URL:

  python scripts/leak_check.py --url https://<your-tunnel>.trycloudflare.com
#>
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not $env:GATEKEEPER_OPENAI_API_KEY) {
  Write-Error 'GATEKEEPER_OPENAI_API_KEY is not set. Set it in this shell first; do not put the key in this file or anywhere inside the repository.'
  exit 1
}

# Pin the channel.  `auto` would fall through to `anthropic` or `claude -p` when
# the OpenAI call fails, which would spend the author's own Claude credentials on
# a stranger's request - and shell out to a CLI on a public endpoint.
$env:GATEKEEPER_PROVIDER = 'openai'
if (-not $env:GATEKEEPER_OPENAI_BASE_URL) { $env:GATEKEEPER_OPENAI_BASE_URL = 'https://token-plan-cn.xiaomimimo.com/v1' }
if (-not $env:GATEKEEPER_OPENAI_MODEL)    { $env:GATEKEEPER_OPENAI_MODEL    = 'mimo-v2.5-pro' }

# Visitor text is cached to a scratch directory, so it never lands in the cache
# that ships with the repository.  The shipped cache is still read as a fallback,
# which keeps the bundled sample free to replay no matter who walks it.
$env:GATEKEEPER_CACHE_DIR = 'data/cache_demo'
$env:GATEKEEPER_CACHE_FALLBACK_DIR = 'data/cache'

# Budget brakes (app/guard.py).  Everything a visitor's own manuscript costs is
# metered; the bundled sample never reaches the model at all.
if (-not $env:GATEKEEPER_IP_LIMIT)          { $env:GATEKEEPER_IP_LIMIT          = '20' }
if (-not $env:GATEKEEPER_IP_WINDOW_SECONDS) { $env:GATEKEEPER_IP_WINDOW_SECONDS = '3600' }
if (-not $env:GATEKEEPER_DAILY_LIMIT)       { $env:GATEKEEPER_DAILY_LIMIT       = '150' }
if (-not $env:GATEKEEPER_MAX_CONCURRENCY)   { $env:GATEKEEPER_MAX_CONCURRENCY   = '1' }

# The console is the server log.  To keep a copy, redirect outside the script:
#   powershell -File scripts/serve_public.ps1 *> data/server_demo.log
Write-Host "provider   $env:GATEKEEPER_PROVIDER"
Write-Host "model      $env:GATEKEEPER_OPENAI_MODEL"
Write-Host "key        read from the environment (never echoed, never stored)"
Write-Host "cache      $env:GATEKEEPER_CACHE_DIR (read-only fallback $env:GATEKEEPER_CACHE_FALLBACK_DIR)"
$minutes = [int]$env:GATEKEEPER_IP_WINDOW_SECONDS / 60
Write-Host "budget     $env:GATEKEEPER_IP_LIMIT calls per visitor per $minutes min, $env:GATEKEEPER_DAILY_LIMIT per day, concurrency $env:GATEKEEPER_MAX_CONCURRENCY"
Write-Host ''
Write-Host 'Binding 127.0.0.1:8766 (reachable only through the tunnel).'
Write-Host 'Expose with: cloudflared tunnel --url http://127.0.0.1:8766'
Write-Host ''

python -m uvicorn app.server:app --host 127.0.0.1 --port 8766
