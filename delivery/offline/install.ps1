param([switch]$SkipImageLoad)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host '[InsightPilot] Windows offline installer' -ForegroundColor Cyan

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker Desktop is not installed or docker.exe is not in PATH. Install Docker Desktop and rerun this script.'
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Desktop is installed but its engine is not running. Start Docker Desktop and rerun this script.'
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Compose v2 is required. Upgrade Docker Desktop and rerun this script.'
}

$archive = Join-Path $PSScriptRoot 'images\insightpilot-offline-images.tar'
if (-not (Test-Path -LiteralPath $archive)) {
    throw "Prebuilt image archive is missing: $archive"
}

Write-Host '[InsightPilot] Environment check passed.' -ForegroundColor Green
& (Join-Path $PSScriptRoot 'start-offline.ps1') -SkipImageLoad:$SkipImageLoad
if ($LASTEXITCODE -ne 0) {
    throw "Offline installation failed with exit code $LASTEXITCODE"
}

