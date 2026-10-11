# Windows: install (first run only), then start the Verity agent and web app in two windows and open the browser.
#   powershell -ExecutionPolicy Bypass -File .\run.ps1
# Keys come from .env.local (created on first run for you to fill in). Close both windows to stop.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# 1. uv (installs Python 3.12 and the packages)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing uv...'
    powershell -ExecutionPolicy ByPass -c 'irm https://astral.sh/uv/install.ps1 | iex'
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# 2. Python environment with just what the Verity agent and web app need
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'Creating the Python environment (first run only)...'
    uv venv --python 3.12 .venv
    uv pip install --python $py livekit livekit-api 'livekit-agents==1.8.5' 'livekit-plugins-google==1.8.5' 'livekit-plugins-fishaudio==1.8.5' aiohttp numpy opencv-python-headless
    if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force .venv; throw 'Package install failed. Fix the error above and run again.' }
}
uv pip install -q --python $py 'livekit-plugins-fishaudio==1.8.5'   # added later (Verity's Fish Audio voice); a no-op once installed

# 3. Keys live in .env.local, which git ignores
if (-not (Test-Path .env.local)) {
    @(
        'LIVEKIT_URL=wss://<your-project>.livekit.cloud'
        'LIVEKIT_API_KEY=<key>'
        'LIVEKIT_API_SECRET=<secret>'
        'AGENT_NAME=team-verity-agent'
        'GEMINI_API_KEY=<team token>'
        'GEMINI_BASE_URL=https://james-72765--rtcoach-gemini.modal.run'
    ) | Set-Content -Encoding ascii .env.local
    Write-Host 'Created .env.local.'
}
Get-Content .env.local | ForEach-Object {
    if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*?)\s*(#.*)?$') { Set-Item "env:$($matches[1])" $matches[2] }
}
if (-not $env:AGENT_NAME) { $env:AGENT_NAME = 'team-verity-agent' }
$missing = 'LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET', 'GEMINI_API_KEY' |
    Where-Object { -not (Get-Item "env:$_" -ErrorAction SilentlyContinue).Value -or (Get-Item "env:$_").Value -match '<' }
if ($missing) {
    Write-Host "Fill in these lines in .env.local, save, then run this again: $($missing -join ', ')" -ForegroundColor Red
    notepad .env.local
    exit 1
}

# 4. Two windows (they inherit the keys loaded above), then the browser.
# A web app left over from an earlier run would keep port 8765 and serve the old page, so stop it first.
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList '-NoExit', '-Command', "`$Host.UI.RawUI.WindowTitle='Verity agent'; & '$py' -m verity.agent dev"
Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList '-NoExit', '-Command', "`$Host.UI.RawUI.WindowTitle='Verity web app'; & '$py' -m verity.server"
Write-Host 'Starting... the app opens in your browser as soon as it is ready.'
foreach ($i in 1..60) {   # the first start on Windows can take a while
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8765/api/health' | Out-Null; break } catch { Start-Sleep -Seconds 1 }
}
Start-Process 'http://127.0.0.1:8765'
