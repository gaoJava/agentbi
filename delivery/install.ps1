param(
    [switch]$UseBundledDemoCredentials,
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    foreach ($command in @('python', 'node', 'java', 'docker')) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            throw "Required command not found: $command"
        }
    }
    if (-not (Test-Path -LiteralPath '.venv')) {
        python -m venv .venv
    }
    $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
    & $python -m pip install --upgrade pip
    & $python -m pip install -e '.[dev]'
    & $python delivery\database\init_database.py

    if (-not $SkipFrontend) {
        Push-Location integrations\superset-extension\frontend
        try { npm ci } finally { Pop-Location }
    }

    Write-Host '[AgentBI] Installation completed.' -ForegroundColor Green
    Write-Host 'Run .\scripts\verify.ps1, then .\scripts\start-demo.ps1.'
    if ($UseBundledDemoCredentials) {
        & .\scripts\start-demo.ps1 -UseBundledDemoCredentials
    }
}
finally {
    Pop-Location
}
