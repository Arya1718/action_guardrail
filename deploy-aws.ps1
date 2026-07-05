<# 
.SYNOPSIS
    Deploy Action Guardrail to AWS Lambda + API Gateway (always-free tier).

.DESCRIPTION
    First run:     .\deploy-aws.ps1
    This runs 'sam build' then 'sam deploy --guided'.

    Subsequent:    .\deploy-aws.ps1 -DeployOnly
    This re-runs 'sam deploy' using the saved samconfig.toml.

    The deployment uses a container image (Dockerfile.lambda) pushed to ECR,
    API Gateway HTTP API, and SSM Parameter Store for secrets.
    No paid AWS services are used.
#>

param(
    [switch]$DeployOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSCommandPath
Set-Location $ProjectRoot

Write-Host "=== Action Guardrail: AWS Deployment ===" -ForegroundColor Cyan

# ── Step 1: SAM Build ────────────────────────────────────────────────────────
# Builds the container image locally (using Dockerfile.lambda) and prepares
# the CloudFormation template.
Write-Host "`n[1/2] Running sam build..." -ForegroundColor Yellow
sam build --template template.yaml
if ($LASTEXITCODE -ne 0) {
    Write-Host "sam build FAILED with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "sam build succeeded." -ForegroundColor Green

# ── Step 2: SAM Deploy ───────────────────────────────────────────────────────
if ($DeployOnly) {
    Write-Host "`n[2/2] Running sam deploy (with saved config)..." -ForegroundColor Yellow
    sam deploy
} else {
    Write-Host "`n[2/2] Running sam deploy --guided..." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "You will be prompted for the following:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Stack Name:           action-guardrail-aws  (suggested)" -ForegroundColor White
    Write-Host "  AWS Region:           us-east-1             (free-tier safe)" -ForegroundColor White
    Write-Host "  Parameter ApiKeyParamValue:       <your-api-key>     (e.g. a random 32-char string)" -ForegroundColor White
    Write-Host "  Parameter MongoUriParamValue:     <mongo-uri>         (from MongoDB Atlas)" -ForegroundColor White
    Write-Host "  Parameter MongoDbNameParamValue:  guardrail_aws      (different from HF deployment)" -ForegroundColor White
    Write-Host "  Parameter GroqApiKeyParamValue:   <groq-key>          (optional, free from console.groq.com)" -ForegroundColor White
    Write-Host "  Confirm changes before deploy:    Y                   (recommended for first deploy)" -ForegroundColor White
    Write-Host "  Allow SAM CLI IAM role creation:  Y                   (required)" -ForegroundColor White
    Write-Host "  Disable rollback:                 N" -ForegroundColor White
    Write-Host "  Save arguments to samconfig.toml: Y                   (so future 'sam deploy' works without prompts)" -ForegroundColor White
    Write-Host ""
    sam deploy --guided
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "sam deploy FAILED with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "`nDeployment complete!" -ForegroundColor Green
Write-Host ""
Write-Host "To get the API Gateway URL, run:" -ForegroundColor Cyan
Write-Host "  sam list stack-outputs --stack-name action-guardrail-aws" -ForegroundColor White
Write-Host ""
Write-Host "Or check the AWS Console: CloudFormation > action-guardrail-aws > Outputs" -ForegroundColor White
