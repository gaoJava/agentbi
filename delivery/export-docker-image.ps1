param(
    [string]$Image = 'insightpilot-agentbi:0.1.0',
    [string]$OutputDirectory = 'delivery\docker'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$outputPath = Join-Path $projectRoot $OutputDirectory
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$archivePath = Join-Path $outputPath 'insightpilot-agentbi-0.1.0.tar'
$checksumPath = "$archivePath.sha256"

Push-Location $projectRoot
try {
    & docker build --pull=false --tag $Image .
    if ($LASTEXITCODE -ne 0) { throw "docker build failed with exit code $LASTEXITCODE" }
    & docker image save --output $archivePath $Image
    if ($LASTEXITCODE -ne 0) { throw "docker image save failed with exit code $LASTEXITCODE" }
    $hash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([System.IO.Path]::GetFileName($archivePath))" |
        Set-Content -LiteralPath $checksumPath -Encoding ascii
    Write-Host "Offline image: $archivePath" -ForegroundColor Green
    Write-Host "SHA256: $hash"
}
finally {
    Pop-Location
}
