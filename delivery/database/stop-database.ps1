$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
docker compose -f .\compose.database.yml down
if ($LASTEXITCODE -ne 0) { throw 'Database shutdown failed.' }
Write-Host 'Database stopped. The Docker volume and all imported data were preserved.' -ForegroundColor Green

