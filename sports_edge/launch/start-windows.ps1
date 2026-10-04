# Started by "Start True Edge (Windows).bat". Research and paper tracking only.
# Heavy installs stay on this computer, not the drive.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $here "Anthropic-number-one\sports_edge"
if (-not (Test-Path $app)) { $app = Split-Path -Parent $here }   # when run from sports_edge\launch
Set-Location $app

$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "==> Installing uv (Python tool, one time, into your user folder)"
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:USERPROFILE ".true-edge\venv"

if ((Get-Command git -ErrorAction SilentlyContinue) -and (Test-Path "..\.git")) {
  Write-Host "==> Getting the latest saved version from GitHub"
  git -C .. config core.fileMode false
  git -C .. pull --ff-only
  if ($LASTEXITCODE -ne 0) { Write-Host "   (could not update; starting the copy on this drive)" }
}

Write-Host "==> Preparing Python (first run downloads a few hundred MB, later runs are quick)"
uv sync --frozen --no-dev
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

if (-not (Test-Path "web\dist\index.html")) {
  if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location web; npm ci --no-audit --no-fund; npm run build; Pop-Location
  } else { throw "The web app is not built and Node.js is not installed. Install Node 20+ from https://nodejs.org/" }
}

uv run --no-dev python -c "from sports_edge.config import settings; from sports_edge.api.auth import Auth; Auth(settings().runs_dir)" | Out-Null
$token = if ($env:SPORTS_EDGE_API_TOKEN) { $env:SPORTS_EDGE_API_TOKEN } else { (Get-Content runs\api_token -Raw).Trim() }
$port = if ($env:PORT) { $env:PORT } else { "8000" }
$url = "http://127.0.0.1:$port"
Write-Host ""
Write-Host "============================================================"
Write-Host " True Edge is starting:  $url"
Write-Host " Operator token:         $token"
Write-Host " Keep this window open. Close it to stop."
Write-Host "============================================================"
Start-Job { Start-Sleep 5; Start-Process $using:url } | Out-Null
uv run --no-dev uvicorn sports_edge.api.app:app_factory --factory --host 127.0.0.1 --port $port
