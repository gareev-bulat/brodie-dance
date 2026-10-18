# Windows: install (first run only), then start Brodie the dance teacher (facetime.worker in dance mode) and the website in two
# windows and open the browser.   powershell -ExecutionPolicy Bypass -File .\run.ps1
# Keys come from .env.local (created on first run for you to fill in). Close both windows to stop.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing uv...'
    powershell -ExecutionPolicy ByPass -c 'irm https://astral.sh/uv/install.ps1 | iex'
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'Creating the Python environment (first run only, several minutes)...'
    uv venv --python 3.12 .venv
    uv pip install --python $py torch torchvision --index-url https://download.pytorch.org/whl/cpu
    uv pip install --python $py -r requirements.txt aiohttp
    if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force .venv; throw 'Package install failed. Fix the error above and run again.' }
}
New-Item -ItemType Directory -Force models | Out-Null
foreach ($m in 'lite', 'full', 'heavy') {
    $f = "models\pose_landmarker_$m.task"
    if (-not (Test-Path $f)) { Invoke-WebRequest -UseBasicParsing -OutFile $f "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_$m/float16/latest/pose_landmarker_$m.task" }
}
if (-not (Test-Path .env.local)) {
    @('LIVEKIT_URL=wss://<your-project>.livekit.cloud', 'LIVEKIT_API_KEY=<key>', 'LIVEKIT_API_SECRET=<secret>', 'AGENT_NAME=brodie-dance',
      'GEMINI_API_KEY=<team token>', 'GEMINI_BASE_URL=https://james-72765--rtcoach-gemini.modal.run', 'DANCE=sbbyj') | Set-Content -Encoding ascii .env.local
    Write-Host 'Created .env.local.'
}
Get-Content .env.local | ForEach-Object { if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*?)\s*(#.*)?$') { Set-Item "env:$($matches[1])" $matches[2] } }
if (-not $env:AGENT_NAME) { $env:AGENT_NAME = 'brodie-dance' }
$missing = 'LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET', 'GEMINI_API_KEY' |
    Where-Object { -not (Get-Item "env:$_" -ErrorAction SilentlyContinue).Value -or (Get-Item "env:$_").Value -match '<' }
if ($missing) { Write-Host "Fill in these lines in .env.local, save, then run this again: $($missing -join ', ')" -ForegroundColor Red; notepad .env.local; exit 1 }
Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
$persona = if ($env:DANCE_ROAST -eq '0') { 'starter/persona_friendly.md' } else { 'starter/persona.md' }   # DANCE_ROAST=0: the friendly coach
$args_ = "--plan-file none --voice gemini --voice-name Puck --persona $persona --text --log-caller --dance --leave-grace-s -1"
Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList '-NoExit', '-Command', "`$Host.UI.RawUI.WindowTitle='Brodie dance teacher'; & '$py' -m facetime.worker $args_ dev"
Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList '-NoExit', '-Command', "`$Host.UI.RawUI.WindowTitle='Brodie Dance website'; & '$py' -m brodie.server"
Write-Host 'Starting... the site opens in your browser as soon as it is ready.'
foreach ($i in 1..60) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8765/api/health' | Out-Null; break } catch { Start-Sleep -Seconds 1 } }
Start-Process 'http://127.0.0.1:8765'
