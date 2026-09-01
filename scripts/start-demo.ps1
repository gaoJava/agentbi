param(
    [switch]$UseBundledDemoCredentials,
    [switch]$ReinitializeSuperset,
    [switch]$RestartAgentBI,
    [switch]$RestartSuperSonic,
    [string]$SuperSonicUser = $env:SUPERSONIC_USER,
    [string]$SuperSonicPassword = $env:SUPERSONIC_PASSWORD,
    [string]$SupersetRoot = '',
    [string]$SuperSonicRoot = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $projectRoot
if (-not $SupersetRoot) { $SupersetRoot = Join-Path $workspaceRoot 'superset-main' }
if (-not $SuperSonicRoot) { $SuperSonicRoot = Join-Path $workspaceRoot 'supersonic-stable' }
if (-not (Test-Path -LiteralPath (Join-Path $SupersetRoot 'docker-compose.yml'))) {
    throw "Superset source was not found beside AgentBI: $SupersetRoot"
}
if (-not (Test-Path -LiteralPath $SuperSonicRoot)) {
    throw "SuperSonic source was not found beside AgentBI: $SuperSonicRoot"
}
$runtimeDir = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$supersetInitMarker = Join-Path $runtimeDir 'superset.initialized'

if ($RestartAgentBI) {
    $agentbiPidPath = Join-Path $runtimeDir 'agentbi.pid'
    if (Test-Path -LiteralPath $agentbiPidPath) {
        $agentbiPid = [int](Get-Content -LiteralPath $agentbiPidPath -Raw).Trim()
        $agentbiProcess = Get-Process -Id $agentbiPid -ErrorAction SilentlyContinue
        if ($agentbiProcess) {
            $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $agentbiPid" -ErrorAction SilentlyContinue).CommandLine
            if ($commandLine -and $commandLine -notmatch 'uvicorn\s+agentbi\.main:app') {
                throw "Refusing to stop PID $agentbiPid because it is not the recorded AgentBI uvicorn process."
            }
            Stop-Process -Id $agentbiPid -Force
            $agentbiProcess.WaitForExit()
        }
    }
}
if ($RestartSuperSonic) {
    $sonicPidPath = Join-Path $runtimeDir 'supersonic.pid'
    if (Test-Path -LiteralPath $sonicPidPath) {
        $sonicPid = [int](Get-Content -LiteralPath $sonicPidPath -Raw).Trim()
        $sonicProcess = Get-Process -Id $sonicPid -ErrorAction SilentlyContinue
        if ($sonicProcess) {
            $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $sonicPid" -ErrorAction SilentlyContinue).CommandLine
            if ($commandLine -and $commandLine -notmatch 'com\.tencent\.supersonic\.StandaloneLauncher') {
                throw "Refusing to stop PID $sonicPid because it is not the recorded SuperSonic process."
            }
            Stop-Process -Id $sonicPid -Force
            $sonicProcess.WaitForExit()
        }
    }
}

function Test-HttpOk {
    param([Parameter(Mandatory)] [string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Wait-HttpOk {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string]$Url,
        [int]$TimeoutSeconds = 180
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk $Url) {
            Write-Host "[AgentBI] $Name is ready." -ForegroundColor Green
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "$Name did not become ready within $TimeoutSeconds seconds."
}

function New-SessionApiKey {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

function Show-DemoUrls {
    Write-Host ''
    Write-Host 'AgentBI demo is ready:' -ForegroundColor Cyan
    Write-Host '  Superset dashboard:  http://127.0.0.1:8088'
    Write-Host '  InsightPilot login:  http://127.0.0.1:8090/app'
    Write-Host '  AgentBI API docs:    http://127.0.0.1:8090/docs'
    Write-Host '  SuperSonic backend:  http://127.0.0.1:9080'
    Write-Host 'Open a Superset dashboard and use the AI Insight panel in the lower-right corner.'
}

if (
    (Test-HttpOk 'http://127.0.0.1:9080/api/auth/user/getCurrentUser') -and
    (Test-HttpOk 'http://127.0.0.1:8090/health') -and
    (Test-HttpOk 'http://127.0.0.1:8088/health')
) {
    Show-DemoUrls
    exit 0
}

if ($UseBundledDemoCredentials) {
    $SuperSonicUser = 'admin'
    $SuperSonicPassword = 'admin'
    $env:AGENTBI_ENABLE_DEMO_LOGIN = 'true'
    $env:AGENTBI_DEMO_USER_PASSWORD = 'user'
    $env:AGENTBI_DEMO_ADMIN_PASSWORD = 'admin'
    Write-Warning 'Using bundled admin/admin credentials. Keep ports bound to this workstation only.'
}
if (-not $SuperSonicUser -or -not $SuperSonicPassword) {
    throw 'Set SUPERSONIC_USER and SUPERSONIC_PASSWORD, or pass -UseBundledDemoCredentials for the local demo only.'
}

if (-not (Test-HttpOk 'http://127.0.0.1:9080/api/auth/user/getCurrentUser')) {
    $runtimeRoot = Join-Path $env:TEMP 'agentbi-supersonic-runtime'
    $distributionDir = Join-Path $runtimeRoot 'launchers-standalone-0.8.6-SNAPSHOT'
    if (-not (Test-Path -LiteralPath (Join-Path $distributionDir 'conf\application.yaml'))) {
        $archive = Join-Path $SuperSonicRoot 'launchers\standalone\target\launchers-standalone-0.8.6-SNAPSHOT-bin.tar.gz'
        if (-not (Test-Path -LiteralPath $archive)) {
            throw "SuperSonic distribution was not found. Build it first: $archive"
        }
        New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
        & tar -xf $archive -C $runtimeRoot
        if ($LASTEXITCODE -ne 0) { throw 'Could not extract the SuperSonic distribution.' }
    }
    $java = (Get-Command java -ErrorAction Stop).Source
    $process = Start-Process -FilePath $java `
        -ArgumentList '-Dserver.address=127.0.0.1', '-cp', 'conf;lib/*', 'com.tencent.supersonic.StandaloneLauncher' `
        -WorkingDirectory $distributionDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtimeDir 'supersonic.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeDir 'supersonic.stderr.log') `
        -PassThru
    Set-Content -LiteralPath (Join-Path $runtimeDir 'supersonic.pid') -Value $process.Id
}
Wait-HttpOk -Name 'SuperSonic' -Url 'http://127.0.0.1:9080/api/auth/user/getCurrentUser'

$loginBody = @{ name = $SuperSonicUser; password = $SuperSonicPassword } | ConvertTo-Json
$login = Invoke-RestMethod -Uri 'http://127.0.0.1:9080/api/auth/user/login' `
    -Method Post -ContentType 'application/json' -Body $loginBody
if ($login.code -ne 200 -or -not $login.data) {
    throw 'SuperSonic login failed.'
}

$agentbiAlreadyRunning = Test-HttpOk 'http://127.0.0.1:8090/health'
if ($agentbiAlreadyRunning -and $env:AGENTBI_API_KEY.Length -lt 32) {
    throw 'AGENTBI_API_KEY is required to reuse a running orchestrator while starting Superset.'
}
$env:AGENTBI_API_KEY = if ($env:AGENTBI_API_KEY.Length -ge 32) {
    $env:AGENTBI_API_KEY
} else {
    New-SessionApiKey
}
$env:SUPERSONIC_BASE_URL = 'http://127.0.0.1:9080'
$env:SUPERSONIC_TOKEN = "Bearer $($login.data)"
$env:SUPERSET_BASE_URL = 'http://127.0.0.1:8088'
$env:SUPERSET_DASHBOARD_PATH = '/superset/dashboard/1/'
$env:SUPERSET_USER = 'admin'
$env:SUPERSET_PASSWORD = 'admin'
$env:AGENTBI_DATABASE_URL = 'sqlite:///./data/agentbi.db'
$dataDir = Join-Path $projectRoot 'data'
$sessionSecretPath = Join-Path $dataDir '.agentbi-session-secret'
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
if (Test-Path -LiteralPath $sessionSecretPath) {
    $sessionSecret = (Get-Content -LiteralPath $sessionSecretPath -Raw).Trim()
    if ($sessionSecret.Length -lt 32) { throw 'Persisted AgentBI session secret is invalid.' }
}
else {
    $sessionSecret = New-SessionApiKey
    Set-Content -LiteralPath $sessionSecretPath -Value $sessionSecret -NoNewline
}
# Keep the provider-key encryption key stable across demo restarts. The API key remains ephemeral.
$env:AGENTBI_SESSION_SECRET = $sessionSecret
$env:AGENTBI_ALLOWED_ORIGINS = 'http://localhost:8088,http://127.0.0.1:8088,http://127.0.0.1:8090'
$env:PYTHONPATH = Join-Path $projectRoot 'src'

if (-not (Test-HttpOk 'http://127.0.0.1:8090/health')) {
    $python = (Get-Command python -ErrorAction Stop).Source
    $process = Start-Process -FilePath $python `
        -ArgumentList '-m', 'uvicorn', 'agentbi.main:app', '--host', '127.0.0.1', '--port', '8090' `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtimeDir 'agentbi.stdout.log') `
        -RedirectStandardError (Join-Path $runtimeDir 'agentbi.stderr.log') `
        -PassThru
    Set-Content -LiteralPath (Join-Path $runtimeDir 'agentbi.pid') -Value $process.Id
}
Wait-HttpOk -Name 'AgentBI Orchestrator' -Url 'http://127.0.0.1:8090/health' -TimeoutSeconds 30

$env:DOCKER_CONFIG = Join-Path $projectRoot 'deploy\docker-anonymous'
$env:DOCKER_HOST = 'npipe:////./pipe/dockerDesktopLinuxEngine'
$env:AGENTBI_PROJECT_ROOT = $projectRoot.Replace('\', '/')
$env:SUPERSET_PORT = '127.0.0.1:8088'
$env:CYPRESS_PORT = '127.0.0.1:8081'
& (Join-Path $PSScriptRoot 'build-superset-extension.ps1')
if ($LASTEXITCODE -ne 0) { throw 'AgentBI Superset extension packaging failed.' }
$composeOverride = Join-Path $projectRoot 'deploy\superset\compose.agentbi.yml'
$composeBase = Join-Path $SupersetRoot 'docker-compose.yml'
# Start only the services used by the local competition demo. Once initialization
# has succeeded, bypass the upstream init dependency on later runs; otherwise it
# reinstalls the editable Superset package twice even though the database is ready.
if ((Test-Path -LiteralPath $supersetInitMarker) -and -not $ReinitializeSuperset) {
    Write-Host '[AgentBI] Reusing the initialized Superset database.' -ForegroundColor Cyan
    & docker compose -f $composeBase -f $composeOverride up -d --no-build `
        db redis superset-node
    if ($LASTEXITCODE -ne 0) { throw 'Superset dependency startup failed.' }
    & docker compose -f $composeBase -f $composeOverride up -d --no-build --no-deps superset
} else {
    & docker compose -f $composeBase -f $composeOverride up -d --no-build `
        db redis superset-init superset-node superset
}
if ($LASTEXITCODE -ne 0) { throw 'Superset Docker Compose startup failed.' }
Wait-HttpOk -Name 'Apache Superset' -Url 'http://127.0.0.1:8088/health'
Set-Content -LiteralPath $supersetInitMarker -Value (Get-Date).ToString('o')

Show-DemoUrls
