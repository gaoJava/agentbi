param(
    [string]$SupersetRoot = 'D:\project\ai-coding\superset-main'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot '.runtime'

foreach ($service in @(
    @{ File = 'agentbi.pid'; Process = 'python' },
    @{ File = 'supersonic.pid'; Process = 'java' }
)) {
    $pidFile = Join-Path $runtimeDir $service.File
    if (-not (Test-Path -LiteralPath $pidFile)) { continue }
    $savedPid = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($savedPid -notmatch '^\d+$') { continue }
    $process = Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -eq $service.Process) {
        Stop-Process -Id $process.Id
        Write-Host "[AgentBI] Stopped $($service.Process) process $savedPid."
    }
}

$env:DOCKER_CONFIG = Join-Path $projectRoot 'deploy\docker-anonymous'
$env:DOCKER_HOST = 'npipe:////./pipe/dockerDesktopLinuxEngine'
# Compose validates required variables even for ``stop``. This placeholder is
# used only to render the configuration and is never started or persisted.
if ($env:AGENTBI_API_KEY.Length -lt 32) {
    $env:AGENTBI_API_KEY = 'stop-only-placeholder-not-a-runtime-key'
}
$composeBase = Join-Path $SupersetRoot 'docker-compose.yml'
$composeOverride = Join-Path $projectRoot 'deploy\superset\compose.agentbi.yml'
& docker compose -f $composeBase -f $composeOverride stop
if ($LASTEXITCODE -ne 0) { throw 'Superset Docker Compose stop failed.' }

Write-Host '[AgentBI] Demo services stopped. Volumes and data were preserved.' -ForegroundColor Green
