param([int]$Port = 8090)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

try {
    $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    if ($health.StatusCode -eq 200) {
        Write-Host "InsightPilot is already running: http://127.0.0.1:$Port/app" -ForegroundColor Cyan
        exit 0
    }
}
catch {
    # A failed probe is the expected state before the local workbench starts.
}

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

$env:AGENTBI_API_KEY = New-RandomSecret
$env:AGENTBI_SESSION_SECRET = New-RandomSecret
$env:AGENTBI_ENABLE_DEMO_LOGIN = 'true'
$env:AGENTBI_DEMO_USER_PASSWORD = 'user'
$env:AGENTBI_DEMO_ADMIN_PASSWORD = 'admin'
$env:AGENTBI_DATABASE_URL = 'sqlite:///./data/agentbi.db'
$env:SUPERSONIC_BASE_URL = 'http://127.0.0.1:9080'
$env:AGENTBI_ALLOWED_ORIGINS = "http://127.0.0.1:$Port"
$env:PYTHONPATH = Join-Path $projectRoot 'src'

$python = (Get-Command python -ErrorAction Stop).Source
$process = Start-Process -FilePath $python `
    -ArgumentList '-m', 'uvicorn', 'agentbi.main:app', '--host', '127.0.0.1', '--port', $Port `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $runtimeDir 'agentbi.stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDir 'agentbi.stderr.log') `
    -PassThru
Set-Content -LiteralPath (Join-Path $runtimeDir 'agentbi.pid') -Value $process.Id

$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($health.StatusCode -eq 200) {
            Write-Host "InsightPilot workbench is ready: http://127.0.0.1:$Port/app" -ForegroundColor Green
            Write-Host '  Normal user: user / user'
            Write-Host '  Administrator: admin / admin'
            exit 0
        }
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
}

throw 'InsightPilot workbench did not become ready within 30 seconds.'
