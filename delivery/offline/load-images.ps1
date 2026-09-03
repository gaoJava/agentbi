param([string]$Archive = (Join-Path $PSScriptRoot 'images\insightpilot-offline-images.tar'))
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Archive)) { throw "Offline image archive was not found: $Archive" }
$checksumFile = "$Archive.sha256"
if (Test-Path -LiteralPath $checksumFile) {
    $expected = ((Get-Content -LiteralPath $checksumFile -Raw).Trim() -split '\s+')[0]
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
    if ($actual -ne $expected.ToLowerInvariant()) { throw 'Offline image archive SHA256 verification failed.' }
}
docker load --input $Archive
if ($LASTEXITCODE -ne 0) { throw 'Docker image import failed.' }
Write-Host 'Offline Docker images imported.' -ForegroundColor Green
