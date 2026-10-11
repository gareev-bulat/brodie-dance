# Windows: install (first run only), then start one Verity character on Brodie's worker (facetime.worker) and the web app in two
# windows and open the browser. The app can call the running character only.
#   powershell -ExecutionPolicy Bypass -File .\run.ps1 [verity|dancity|gravity|polarity|intensity] [-Agent]
# -Agent: run the standalone verity/agent.py instead of Brodie's worker (every character, Cruelty; no Dancity lesson).
param([string]$Character = 'verity', [switch]$Agent)
# Keys come from .env.local (created on first run for you to fill in). Close both windows to stop.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # PowerShell 5's download progress bar makes Invoke-WebRequest many times slower
Set-Location $PSScriptRoot

# 1. uv (installs Python 3.12 and the packages)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing uv...'
    powershell -ExecutionPolicy ByPass -c 'irm https://astral.sh/uv/install.ps1 | iex'
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# 2. Python environment: Brodie's requirements (CPU torch, no 3 GB of CUDA wheels) plus the web app's, and the pose models
$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'Creating the Python environment (first run only, several minutes)...'
    uv venv --python 3.12 .venv
    uv pip install --python $py torch torchvision --index-url https://download.pytorch.org/whl/cpu
    uv pip install --python $py -r requirements.txt aiohttp pillow
    if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force .venv; throw 'Package install failed. Fix the error above and run again.' }
}
uv pip install -q --python $py -r requirements.txt aiohttp pillow   # an environment made by an older run.ps1 lacks Brodie's packages; a no-op once installed
New-Item -ItemType Directory -Force models | Out-Null
foreach ($m in 'lite', 'full', 'heavy') {   # MediaPipe pose (Dancity), as in the README's install step
    $f = "models\pose_landmarker_$m.task"
    if (-not (Test-Path $f)) { Invoke-WebRequest -UseBasicParsing -OutFile $f "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_$m/float16/latest/pose_landmarker_$m.task" }
}

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
    # an empty line never overrides a value set on another line; quotes around a value are dropped
    if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*?)\s*(#.*)?$' -and $matches[2]) {
        $name, $v = $matches[1], $matches[2].Trim('"', "'")
        $placeholder = $v -in '<key>', '<secret>', '<team token>' -or $v -match '<your-project>'
        if (-not $placeholder -and $v -match '^<(.+)>$') { $v = $matches[1] }   # a pasted key left inside its <angle brackets>
        Set-Item "env:$name" $v
    }
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
$voice = & $py -c "from verity.variants import VARIANTS as V; k='$Character'; assert k in V and not V[k].get('secret'), 'unknown character: ' + k; print(V[k]['voice'])"
if ($LASTEXITCODE -ne 0) { throw "Unknown character '$Character'." }
$env:VERITY_CHARACTER = $Character
$args_ = "--plan-file none --voice gemini --voice-name $voice --persona starter/verity/$Character.md --avatar $Character --text --log-caller"
# Same flags as scripts/run_verity.sh: --vision shows the character the caller, sharp (--video-quality high), one frame every 4 s for
# Dancity (it looks between parts) or 2 s for a conversation (1 fps bloats the model's context and slows its replies); VISION_FPS and
# DANCE_PASS (the score that passes a part; 70% was too strict for a learner) can be set in .env.local; DANCE_VIDEO=0 = the face alone.
$fps = if ($env:VISION_FPS) { $env:VISION_FPS } else { if ($Character -eq 'dancity') { '0.25' } else { '0.5' } }
if ($Character -eq 'dancity' -and -not $env:DANCE_PASS) { $env:DANCE_PASS = '0.5' }
$args_ += if ($Character -eq 'dancity') { " --dance --leave-grace-s -1 --vision --vision-fps $fps --video-quality high" } else { " --perception off --vision --vision-fps $fps --video-quality high --barge-in --handlers starter/verity/handlers.py" }
# start --log-level info, not dev: dev logs every debug line, and a Windows console is slow enough to stall the audio
if ($Agent) {
    Remove-Item env:VERITY_CHARACTER -ErrorAction SilentlyContinue   # the web app may call any character; the agent picks it from the room name
    Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList '-NoExit', '-Command', "`$Host.UI.RawUI.WindowTitle='Verity agent'; & '$py' -m verity.agent start --log-level info"
} else {
    Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList '-NoExit', '-Command', "`$Host.UI.RawUI.WindowTitle='Verity on Brodie ($Character)'; & '$py' -m facetime.worker $args_ start --log-level info"
}
Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList '-NoExit', '-Command', "`$Host.UI.RawUI.WindowTitle='Verity web app'; & '$py' -m verity.server"
Write-Host 'Starting... the app opens in your browser as soon as it is ready.'
foreach ($i in 1..60) {   # the first start on Windows can take a while
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 'http://127.0.0.1:8765/api/health' | Out-Null; break } catch { Start-Sleep -Seconds 1 }
}
Start-Process 'http://127.0.0.1:8765'
