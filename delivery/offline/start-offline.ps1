param([switch]$SkipImageLoad)
$ErrorActionPreference = 'Stop'
$compose = Join-Path $PSScriptRoot 'compose.offline.yml'
$envFile = Join-Path $PSScriptRoot '.env.runtime'

function New-Secret([int]$Bytes = 32) {
    $buffer = New-Object byte[] $Bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($buffer)).Replace('-', '').ToLowerInvariant()
}

if (-not $SkipImageLoad) { & (Join-Path $PSScriptRoot 'load-images.ps1') }
if (-not (Test-Path -LiteralPath $envFile)) {
    $initialEnvironment = @(
        "POSTGRES_PASSWORD=$(New-Secret)"
        "SUPERSET_SECRET_KEY=$(New-Secret 48)"
        "AGENTBI_API_KEY=$(New-Secret)"
        "AGENTBI_SESSION_SECRET=$(New-Secret 48)"
        'SUPERSONIC_TOKEN=pending'
        'BIND_ADDRESS=127.0.0.1'
        'SUPERSET_PORT=8088'
        'AGENTBI_PORT=8090'
        'SUPERSONIC_PORT=9080'
    )
    [IO.File]::WriteAllLines($envFile, $initialEnvironment, [Text.Encoding]::ASCII)
}

docker compose --env-file $envFile -f $compose up -d db redis supersonic
if ($LASTEXITCODE -ne 0) { throw 'Base services failed to start.' }
$deadline = (Get-Date).AddMinutes(4)
$login = $null
while ((Get-Date) -lt $deadline) {
    try {
        $body = @{name='admin'; password='admin'} | ConvertTo-Json
        $login = Invoke-RestMethod -Uri 'http://127.0.0.1:9080/api/auth/user/login' -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 5
        if ($login.code -eq 200 -and $login.data) { break }
    } catch { Start-Sleep -Seconds 3 }
}
if (-not $login -or $login.code -ne 200 -or -not $login.data) { throw 'SuperSonic did not become ready or the default admin login failed.' }
$lines = Get-Content -LiteralPath $envFile | Where-Object { $_ -notmatch '^SUPERSONIC_TOKEN=' }
$lines += "SUPERSONIC_TOKEN=Bearer $($login.data)"
[IO.File]::WriteAllLines($envFile, [string[]]$lines, [Text.Encoding]::ASCII)

docker compose --env-file $envFile -f $compose up -d superset agentbi
if ($LASTEXITCODE -ne 0) { throw 'InsightPilot services failed to start.' }
$deadline = (Get-Date).AddMinutes(4)
$agentbiReady = $false
while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8090/health' -TimeoutSec 5
        if ($health.StatusCode -eq 200) { $agentbiReady = $true; break }
    } catch { Start-Sleep -Seconds 3 }
}
if (-not $agentbiReady) { throw 'InsightPilot did not become healthy in time.' }
Write-Host 'InsightPilot offline environment is ready:' -ForegroundColor Green
Write-Host '  InsightPilot: http://127.0.0.1:8090/app'
Write-Host '  Superset:     http://127.0.0.1:8088'
Write-Host '  SuperSonic:   http://127.0.0.1:9080'
Write-Host 'Demo accounts: admin / admin, user / user.'
Write-Host "Stop command: docker compose --env-file `"$envFile`" -f `"$compose`" down"
