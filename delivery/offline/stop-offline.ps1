$ErrorActionPreference = 'Stop'
docker compose --env-file (Join-Path $PSScriptRoot '.env.runtime') -f (Join-Path $PSScriptRoot 'compose.offline.yml') down
