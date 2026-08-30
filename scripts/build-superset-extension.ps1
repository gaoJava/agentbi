param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$extensionRoot = Join-Path $projectRoot 'integrations\superset-extension'
$frontendRoot = Join-Path $extensionRoot 'frontend'
$frontendDist = Join-Path $frontendRoot 'dist'
$packageDist = Join-Path $extensionRoot 'dist'
$packageFrontendDist = Join-Path $packageDist 'frontend\dist'
$packageBackend = Join-Path $packageDist 'backend'

Push-Location $frontendRoot
try {
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Superset extension frontend build failed.' }
}
finally {
    Pop-Location
}

$remoteEntry = @(Get-ChildItem -LiteralPath $frontendDist -Filter 'remoteEntry.*.js')
if ($remoteEntry.Count -ne 1) {
    throw "Expected exactly one remoteEntry build, found $($remoteEntry.Count)."
}

New-Item -ItemType Directory -Path $packageFrontendDist -Force | Out-Null
New-Item -ItemType Directory -Path $packageBackend -Force | Out-Null
Copy-Item -Path (Join-Path $frontendDist '*') -Destination $packageFrontendDist -Recurse -Force
Copy-Item -Path (Join-Path $extensionRoot 'backend\src') -Destination $packageBackend -Recurse -Force

$extension = Get-Content -LiteralPath (Join-Path $extensionRoot 'extension.json') -Raw | ConvertFrom-Json
$manifest = [ordered]@{
    publisher = $extension.publisher
    name = $extension.name
    displayName = $extension.displayName
    version = $extension.version
    license = $extension.license
    description = $extension.description
    dependencies = @($extension.dependencies)
    permissions = @($extension.permissions)
    id = "$($extension.publisher).$($extension.name)"
    frontend = [ordered]@{
        remoteEntry = $remoteEntry[0].Name
        moduleFederationName = 'agentbi_insightPilot'
    }
    backend = [ordered]@{ entrypoint = 'agentbi.insight_pilot.entrypoint' }
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $packageDist 'manifest.json') -Encoding utf8
Write-Host "Superset extension package ready: $($extension.version) / $($remoteEntry[0].Name)" -ForegroundColor Green
