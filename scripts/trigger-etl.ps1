<#
.SYNOPSIS
    Trigger the SiteDocs ETL remotely via the Function App HTTP endpoint.

.DESCRIPTION
    Fetches the API_KEY from the Function App's App Settings using `az`, then
    POSTs to /api/etl/trigger. The ETL runs in the background on the Function
    App host — this script returns immediately. Watch progress in the portal
    under Function App → Monitoring → Log stream, or query /api/etl/status.

.PARAMETER Only
    Run a single ETL stage instead of the full sync.
    Valid values: lookups, companies, workers, equipment, certifications,
                  forms, incidents, attachments, time_tickets

.PARAMETER Status
    Skip triggering and just fetch /api/etl/status.

.PARAMETER FunctionApp
    Function App name. Defaults to 'sitedocs-api'.

.PARAMETER ResourceGroup
    Resource group name. Defaults to 'rg-sitedocs'.

.EXAMPLE
    .\scripts\trigger-etl.ps1
    Runs a full ETL sync.

.EXAMPLE
    .\scripts\trigger-etl.ps1 -Only time_tickets
    Runs only the time_tickets stage.

.EXAMPLE
    .\scripts\trigger-etl.ps1 -Status
    Prints the last sync state and record counts.

.NOTES
    Requires Azure CLI (`az login` first). You need 'list' permission on the
    Function App's App Settings to fetch the API key.
#>

[CmdletBinding()]
param(
    [ValidateSet('lookups','companies','workers','equipment','certifications',
                 'forms','incidents','attachments','time_tickets')]
    [string]$Only,

    [switch]$Status,

    [string]$FunctionApp  = 'sitedocs-api',
    [string]$ResourceGroup = 'rg-sitedocs'
)

$ErrorActionPreference = 'Stop'

# --- Fetch API key from Function App settings -------------------------------
Write-Host "Fetching API_KEY from $FunctionApp..." -ForegroundColor Cyan
$key = az functionapp config appsettings list `
    -n $FunctionApp -g $ResourceGroup `
    --query "[?name=='API_KEY'].value | [0]" -o tsv

if (-not $key) {
    Write-Error "Could not retrieve API_KEY. Check `az login`, subscription, and that $FunctionApp exists in $ResourceGroup."
    exit 1
}

$baseUrl = "https://$FunctionApp.azurewebsites.net/api"
$headers = @{ 'X-API-Key' = $key }

# --- Status check only ------------------------------------------------------
if ($Status) {
    Write-Host "GET $baseUrl/etl/status" -ForegroundColor Cyan
    Invoke-RestMethod -Method Get -Uri "$baseUrl/etl/status" -Headers $headers |
        ConvertTo-Json -Depth 5
    exit 0
}

# --- Trigger ETL ------------------------------------------------------------
$body = if ($Only) { "{`"only`":`"$Only`"}" } else { '{}' }
$stageLabel = if ($Only) { $Only } else { 'all stages' }

Write-Host "POST $baseUrl/etl/trigger  ($stageLabel)" -ForegroundColor Cyan
$resp = Invoke-RestMethod -Method Post `
    -Uri "$baseUrl/etl/trigger" `
    -Headers $headers `
    -ContentType 'application/json' `
    -Body $body

$resp | ConvertTo-Json
Write-Host ""
Write-Host "ETL running in background on $FunctionApp." -ForegroundColor Green
Write-Host "Monitor: portal.azure.com -> $FunctionApp -> Log stream" -ForegroundColor DarkGray
Write-Host "Or run: .\scripts\trigger-etl.ps1 -Status" -ForegroundColor DarkGray
