param(
    [string]$HostName = '127.0.0.1',
    [int]$Port = 5432,
    [string]$User = 'superset',
    [string]$Database = 'examples',
    [string]$Password = ''
)

$ErrorActionPreference = 'Stop'
$databaseDir = Join-Path $PSScriptRoot 'postgresql'
$schema = Join-Path $databaseDir '001_schema.sql'
$loader = Join-Path $databaseDir '002_load_data.sql'
$csv = Join-Path $databaseDir 'video_game_sales.csv'

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
    throw 'psql was not found. Install the PostgreSQL 17 client or use delivery/offline/install.sh.'
}
if (-not (Test-Path -LiteralPath $csv)) {
    throw "Seed file missing: $csv"
}
if ($Password) { $env:PGPASSWORD = $Password }
try {
    & psql -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $User -d $Database -f $schema
    if ($LASTEXITCODE -ne 0) { throw 'Schema initialization failed.' }
    $psqlCsv = $csv.Replace('\', '/')
    & psql -v ON_ERROR_STOP=1 -v "data_file=$psqlCsv" -h $HostName -p $Port -U $User -d $Database -f $loader
    if ($LASTEXITCODE -ne 0) { throw 'Seed import failed.' }
    & psql -v ON_ERROR_STOP=1 -h $HostName -p $Port -U $User -d $Database -f (Join-Path $databaseDir 'verify.sql')
    if ($LASTEXITCODE -ne 0) { throw 'Database verification failed.' }
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

