param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory)] [string]$Label,
        [Parameter(Mandatory)] [scriptblock]$Command
    )
    Write-Host "[AgentBI] $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Push-Location $projectRoot
try {
    Invoke-Checked 'Running Python tests' { python -m pytest -q -p no:cacheprovider }
    Invoke-Checked 'Running Python static checks' {
        python -m ruff check src tests integrations\superset-extension\backend\src
    }

    if (-not $SkipFrontend) {
        Push-Location (Join-Path $projectRoot 'integrations\superset-extension\frontend')
        try {
            Invoke-Checked 'Checking extension TypeScript' { npm run typecheck }
            Invoke-Checked 'Building extension production assets' { npm run build }
        }
        finally {
            Pop-Location
        }
    }

    Write-Host '[AgentBI] Verification completed successfully.' -ForegroundColor Green
}
finally {
    Pop-Location
}
