param(
    [string]$OutputPath = '',
    [string]$TeamName = 'TEAM-NAME'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$timestamp = Get-Date -Format 'yyyyMMdd-HHmm'
if (-not $OutputPath) {
    $OutputPath = Join-Path $projectRoot "delivery\AgentBI-$TeamName-$timestamp.zip"
}

$include = @(
    'src', 'tests', 'scripts', 'docs', 'deploy', 'delivery', 'integrations',
    'config', 'README.md', 'pyproject.toml', 'Dockerfile', '.dockerignore',
    '.gitignore', '.env.example', 'compose.agentbi.yml'
)
$blockedNames = @('.env', '.env.runtime', 'agentbi.db', 'semantic.mv.db',
                  '.agentbi-session-secret', 'supersonic-databases.json')
$blockedPath = '[\\/](node_modules|dist|\.venv|\.runtime|\.tmp|__pycache__|\.pytest_cache|\.ruff_cache)[\\/]'
$blockedDeliveryPath = '[\\/]delivery[\\/](offline[\\/](images|data)|docker)[\\/]'

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$outputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $outputFullPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
if (Test-Path -LiteralPath $outputFullPath) {
    [System.IO.File]::Delete($outputFullPath)
}

$stream = [System.IO.File]::Open($outputFullPath, [System.IO.FileMode]::CreateNew)
$archive = [System.IO.Compression.ZipArchive]::new(
    $stream,
    [System.IO.Compression.ZipArchiveMode]::Create
)
try {
    foreach ($item in $include) {
        $source = Join-Path $projectRoot $item
        $files = if (Test-Path -LiteralPath $source -PathType Leaf) {
            @(Get-Item -LiteralPath $source)
        } else {
            @(Get-ChildItem -LiteralPath $source -File -Recurse -Force)
        }
        foreach ($file in $files) {
            if (
                $file.Name -in $blockedNames -or
                $file.FullName -match $blockedPath -or
                $file.FullName -match $blockedDeliveryPath -or
                $file.Extension -in @('.log', '.pyc', '.zip', '.tar', '.gz', '.docx')
            ) {
                continue
            }
            $relative = $file.FullName.Substring($projectRoot.Length).TrimStart('\', '/')
            $entryName = ('agentbi/' + $relative.Replace('\', '/'))
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive,
                $file.FullName,
                $entryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    }
}
finally {
    $archive.Dispose()
    $stream.Dispose()
}
Write-Host "Submission package created: $outputFullPath" -ForegroundColor Green
