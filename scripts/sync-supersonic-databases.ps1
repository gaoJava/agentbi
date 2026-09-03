param(
    [Parameter(Mandatory)] [string]$ConfigPath,
    [Parameter(Mandatory)] [string]$SuperSonicUser,
    [Parameter(Mandatory)] [string]$SuperSonicPassword,
    [string]$BaseUrl = 'http://127.0.0.1:9080'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "Configuration not found: $ConfigPath" }
$configuration = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$loginBody = @{ name = $SuperSonicUser; password = $SuperSonicPassword } | ConvertTo-Json
$login = Invoke-RestMethod -Uri "$BaseUrl/api/auth/user/login" -Method Post `
    -ContentType 'application/json' -Body $loginBody -TimeoutSec 15
if ($login.code -ne 200 -or -not $login.data) { throw 'SuperSonic login failed.' }
$headers = @{ Authorization = "Bearer $($login.data)" }
$inventoryResponse = Invoke-RestMethod -Uri "$BaseUrl/api/semantic/database/getDatabaseList" `
    -Headers $headers -TimeoutSec 15
$inventory = @($inventoryResponse.data)

foreach ($connection in @($configuration.connections)) {
    $existing = $inventory | Where-Object {
        $_.name -eq $connection.name -or (
            $_.type -eq $connection.type -and $_.host -eq $connection.host -and
            [string]$_.port -eq [string]$connection.port -and $_.database -eq $connection.database
        )
    } | Select-Object -First 1
    $payload = @{
        name = $connection.name; type = $connection.type; host = $connection.host
        port = [string]$connection.port; database = $connection.database
        username = $connection.username; password = $connection.password
        schema = $connection.schema; admins = @($SuperSonicUser); viewers = @($SuperSonicUser)
    }
    if ($existing) { $payload.id = $existing.id }
    $testBody = $payload | ConvertTo-Json -Depth 6
    $tested = Invoke-RestMethod -Uri "$BaseUrl/api/semantic/database/testConnect" `
        -Method Post -Headers $headers -ContentType 'application/json' -Body $testBody -TimeoutSec 20
    if ($tested.code -ne 200 -or $tested.data -ne $true) {
        throw "Connection test failed: $($connection.name)"
    }
    $saved = Invoke-RestMethod -Uri "$BaseUrl/api/semantic/database/createOrUpdateDatabase" `
        -Method Post -Headers $headers -ContentType 'application/json' -Body $testBody -TimeoutSec 20
    if ($saved.code -ne 200) { throw "Connection save failed: $($connection.name)" }
    Write-Host "[AgentBI] SuperSonic connection synchronized: $($connection.name)" -ForegroundColor Green
}
