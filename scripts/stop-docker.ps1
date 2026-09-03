param(
    [string]$EnvironmentFile = '.runtime\docker.env',
    [switch]$RemoveData
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentPath = Join-Path $projectRoot $EnvironmentFile
if (-not (Test-Path -LiteralPath $environmentPath)) {
    Write-Host "AgentBI standalone Docker environment was not started; env file is absent: $environmentPath" -ForegroundColor Yellow
    Write-Host "If the source demo is running, use scripts\stop-demo.ps1 instead." -ForegroundColor Cyan
    exit 0
}
$arguments = @('compose', '--env-file', $environmentPath, '-f', 'compose.agentbi.yml', 'down')
if ($RemoveData) { $arguments += '--volumes' }

Push-Location $projectRoot
try {
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { throw "docker compose down failed with exit code $LASTEXITCODE" }
}
finally {
    Pop-Location
}
