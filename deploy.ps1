#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Deploy the Action Guardrail stack to AWS via SAM.

.DESCRIPTION
    First run:  .\deploy.ps1
    Subsequent: .\deploy.ps1 -DeployOnly

    The first run prompts for:
      - Stack name (default: action-guardrail)
      - AWS Region (default: us-east-1)
      - Confirm IAM role creation (Y)
      - ApiKeyParamValue: choose a strong, random string

    After the first guided deploy, samconfig.toml is created and subsequent
    deploys use `sam deploy` without the --guided flag.
#>

param(
    [switch]$DeployOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

Set-Location -LiteralPath $ProjectRoot

if (-not (Get-Command sam -ErrorAction SilentlyContinue)) {
    Write-Error "SAM CLI not found. Install it from https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
    exit 1
}

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Write-Error "AWS CLI not found. Install it from https://aws.amazon.com/cli/"
    exit 1
}

Write-Host "=== SAM Build ===" -ForegroundColor Cyan
sam build

if ($LASTEXITCODE -ne 0) {
    Write-Error "sam build failed"
    exit 1
}

if (-not $DeployOnly) {
    Write-Host ""
    Write-Host "=== SAM Deploy (guided — first time) ===" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Expected prompts:"
    Write-Host "  Stack Name                     : action-guardrail"
    Write-Host "  AWS Region                     : us-east-1"
    Write-Host "  Parameter ApiKeyParamValue     : <pick a random string>"
    Write-Host "  Confirm changes before deploy  : N"
    Write-Host "  Allow SAM CLI IAM role creation : Y"
    Write-Host "  Disable rollback                : N"
    Write-Host "  Save arguments to samconfig.toml: Y"
    Write-Host ""
    sam deploy --guided
} else {
    Write-Host ""
    Write-Host "=== SAM Deploy (using samconfig.toml) ===" -ForegroundColor Cyan
    Write-Host ""
    sam deploy
}

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=== Deploy complete ===" -ForegroundColor Green

    $outputs = sam list stack-outputs --stack-name action-guardrail 2>$null
    if (-not $outputs) {
        # fallback: read from CloudFormation describe-stacks
        $outputs = aws cloudformation describe-stacks --stack-name action-guardrail `
            --query "Stacks[0].Outputs" --output json | ConvertFrom-Json
    }

    if ($outputs) {
        $endpoint = ($outputs | Where-Object { $_.OutputKey -eq "GuardrailApiEndpoint" }).OutputValue
        if ($endpoint) {
            Write-Host "API Gateway URL: $endpoint" -ForegroundColor Yellow
            Write-Host ""
            Write-Host "To run the smoke test:" -ForegroundColor Yellow
            Write-Host "  .\deploy\smoke_test.ps1 -Endpoint $endpoint -ApiKey <your-api-key>"
            Write-Host ""
            Write-Host "To point the harness at the cloud endpoint:" -ForegroundColor Yellow
            Write-Host "  set GUARDRAIL_API_URL=$endpoint"
        }
    }
}
