param(
    [switch]$Build,
    [switch]$Recreate,
    [string]$EnvironmentFile = '.runtime\docker.env'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentPath = Join-Path $projectRoot $EnvironmentFile

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

if (-not (Test-Path -LiteralPath $environmentPath)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $environmentPath) -Force | Out-Null
    $apiKey = New-RandomSecret
    $sessionSecret = New-RandomSecret
    $content = @"
AGENTBI_API_KEY=$apiKey
AGENTBI_SESSION_SECRET=$sessionSecret
AGENTBI_IMAGE=insightpilot-agentbi:0.1.0
AGENTBI_BIND_ADDRESS=127.0.0.1
AGENTBI_PORT=8090
AGENTBI_ENABLE_DEMO_LOGIN=true
AGENTBI_DEMO_USER_PASSWORD=user
AGENTBI_DEMO_ADMIN_PASSWORD=admin
SUPERSET_BASE_URL=http://host.docker.internal:8088
SUPERSONIC_BASE_URL=http://host.docker.internal:9080
"@
    [System.IO.File]::WriteAllText(
        $environmentPath,
        $content,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host "Generated local Docker secrets: $environmentPath" -ForegroundColor Cyan
}

$arguments = @('compose', '--env-file', $environmentPath, '-f', 'compose.agentbi.yml', 'up', '-d')
if ($Build) { $arguments += '--build' }
if ($Recreate) { $arguments += '--force-recreate' }

Push-Location $projectRoot
try {
    & docker @arguments
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed with exit code $LASTEXITCODE" }
    & docker compose --env-file $environmentPath -f compose.agentbi.yml ps
    Write-Host 'AgentBI: http://127.0.0.1:8090/app' -ForegroundColor Green
}
finally {
    Pop-Location
}
