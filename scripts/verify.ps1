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

function Invoke-NodeChecked {
    param(
        [Parameter(Mandatory)] [string]$Label,
        [Parameter(Mandatory)] [string[]]$Arguments
    )
    Write-Host "[AgentBI] $Label"
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    & $node @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Push-Location $projectRoot
try {
    # Keep pytest artifacts on D:; the Windows profile temp directory may be locked
    # and is intentionally not part of the portable workbench.
    Invoke-Checked 'Running Python tests' {
        python -m pytest -q -p no:cacheprovider --basetemp .runtime/pytest
    }
    Invoke-Checked 'Running Python static checks' {
        python -m ruff check src tests integrations\superset-extension\backend\src
    }

    if (-not $SkipFrontend) {
        Push-Location (Join-Path $projectRoot 'integrations\superset-extension\frontend')
        try {
            Invoke-NodeChecked 'Checking extension TypeScript' @(
                'node_modules/typescript/bin/tsc', '--noEmit'
            )
            Invoke-NodeChecked 'Checking workbench TypeScript' @(
                'node_modules/typescript/bin/tsc', '-p',
                '../../../src/agentbi/web-src/tsconfig.json', '--noEmit'
            )
            Invoke-NodeChecked 'Building workbench TypeScript' @(
                'node_modules/typescript/bin/tsc', '-p',
                '../../../src/agentbi/web-src/tsconfig.json'
            )
            Invoke-NodeChecked 'Building extension production assets' @(
                'node_modules/webpack/bin/webpack.js', '--stats-error-details', '--mode', 'production'
            )
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
