$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
docker compose -f .\compose.database.yml up -d
if ($LASTEXITCODE -ne 0) { throw 'Database startup failed.' }
Write-Host 'PostgreSQL is starting. The first initialization restores superset and examples automatically.' -ForegroundColor Green
Write-Host 'Connection: 127.0.0.1:5432; user: superset; databases: superset, examples'
Write-Host 'Run: docker compose -f .\compose.database.yml exec db psql -U superset -d examples -f /dev/stdin < postgresql/verify.sql'

