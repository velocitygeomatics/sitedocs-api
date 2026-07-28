# deploy.ps1 — Deploy sitedocs-api to Azure Functions
# Usage: .\deploy.ps1

$ErrorActionPreference = "Stop"

$APP_NAME       = "sitedocs-api"

Write-Host "=== Deploying $APP_NAME ===" -ForegroundColor Cyan

# 1. Deploy using Azure Functions Core Tools
# This handles the Python v2 programming model better than the standard 'az' zip deploy.
# It automatically respects .funcignore and performs a remote build.
Write-Host "Deploying to Azure using Functions Core Tools..." -ForegroundColor Yellow
func azure functionapp publish $APP_NAME --python

if ($LASTEXITCODE -eq 0) {
    Write-Host "Deploy successful!" -ForegroundColor Green
} else {
    Write-Host "Deploy FAILED — check output above" -ForegroundColor Red
    exit 1
}
