param(
    [string]$SupersonicRoot = 'D:\project\ai-coding\supersonic-stable',
    [string]$PostgresContainer = 'superset-main-db-1'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$offlineRoot = Join-Path $PSScriptRoot 'offline'
$imageDir = Join-Path $offlineRoot 'images'
$postgresDir = Join-Path $offlineRoot 'data\postgres'
$agentbiDir = Join-Path $offlineRoot 'data\agentbi'
New-Item -ItemType Directory -Path $imageDir, $postgresDir, $agentbiDir -Force | Out-Null

docker build -f (Join-Path $projectRoot 'Dockerfile') -t insightpilot-agentbi:0.1.0 $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'AgentBI image build failed.' }
docker build -f (Join-Path $projectRoot 'deploy\offline\superset\Dockerfile') -t insightpilot-superset:0.1.0 $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Superset image build failed.' }
docker build -f (Join-Path $projectRoot 'deploy\offline\supersonic\Dockerfile') -t insightpilot-supersonic:0.8.6 $SupersonicRoot
if ($LASTEXITCODE -ne 0) { throw 'SuperSonic image build failed.' }

python (Join-Path $projectRoot 'scripts\create-offline-agentbi-seed.py') `
    (Join-Path $projectRoot 'data\agentbi.db') (Join-Path $agentbiDir 'agentbi.db')
if ($LASTEXITCODE -ne 0) { throw 'AgentBI seed creation failed.' }

docker exec $PostgresContainer pg_dump -U superset -d superset -Fc -f /tmp/superset.dump
docker exec $PostgresContainer pg_dump -U superset -d examples -Fc -f /tmp/examples.dump
docker cp "${PostgresContainer}:/tmp/superset.dump" (Join-Path $postgresDir 'superset.dump')
docker cp "${PostgresContainer}:/tmp/examples.dump" (Join-Path $postgresDir 'examples.dump')
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL snapshot export failed.' }

$archive = Join-Path $imageDir 'insightpilot-offline-images.tar'
docker save --output $archive `
    insightpilot-agentbi:0.1.0 insightpilot-superset:0.1.0 `
    insightpilot-supersonic:0.8.6 postgres:17 redis:7
if ($LASTEXITCODE -ne 0) { throw 'Offline image export failed.' }
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$archive.sha256" -Value "$hash  insightpilot-offline-images.tar" -Encoding ascii
Write-Host "Offline bundle ready: $offlineRoot" -ForegroundColor Green
