#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Smoke-test the deployed Action Guardrail on Hugging Face Spaces.

.PARAMETER Endpoint
    The HF Space URL, e.g. https://username-action-guardrail.hf.space

.PARAMETER ApiKey
    The API key set in HF Space secrets as API_KEY.

.EXAMPLE
    .\deploy\smoke_test_hf.ps1 -Endpoint "https://myuser-action-guardrail.hf.space" -ApiKey "your-key"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Endpoint,

    [Parameter(Mandatory = $true)]
    [string]$ApiKey
)

$ErrorActionPreference = "Stop"

$Endpoint = $Endpoint.TrimEnd("/")
$Headers = @{
    "X-API-Key"   = $ApiKey
    "Content-Type" = "application/json"
}

$PassCount = 0
$FailCount = 0

function Test-Step {
    param([string]$Name, [scriptblock]$Script)
    try {
        & $Script
        Write-Host "  [PASS] $Name" -ForegroundColor Green
        $script:PassCount++
    } catch {
        Write-Host "  [FAIL] $Name : $_" -ForegroundColor Red
        $script:FailCount++
    }
}

Write-Host "=== Smoke Test (HF Spaces): $Endpoint ===" -ForegroundColor Cyan
Write-Host ""

# ── 1. Health ──────────────────────────────────────────────────────────────
Test-Step -Name "GET /health" -Script {
    $resp = Invoke-RestMethod -Uri "$Endpoint/health" -Method Get -Headers $Headers
    if ($resp.status -ne "ok") { throw "status is '$($resp.status)'" }
    if ($resp.policies_loaded -le 0) { throw "no policies loaded" }
    Write-Host "       database: $($resp.database)" -ForegroundColor Gray
}

# ── 2. Block ───────────────────────────────────────────────────────────────
Test-Step -Name "POST /evaluate -> block (delete >100 records)" -Script {
    $body = @{
        tool_call = @{
            tool       = "delete_records"
            parameters = @{ record_count = 500; table = "legacy_users" }
        }
        dry_run   = $false
    } | ConvertTo-Json

    $resp = Invoke-RestMethod -Uri "$Endpoint/evaluate" -Method Post -Body $body -Headers $Headers
    if ($resp.outcome -ne "block") { throw "expected block, got '$($resp.outcome)'" }
    if ($resp.matched_rule_id -ne "block-bulk-delete") { throw "expected rule block-bulk-delete" }
}

# ── 3. Require HITL ────────────────────────────────────────────────────────
Test-Step -Name "POST /evaluate -> require_hitl (external email)" -Script {
    $body = @{
        tool_call = @{
            tool       = "send_email"
            parameters = @{ recipient_domain = "gmail.com" }
        }
        dry_run   = $false
    } | ConvertTo-Json

    $resp = Invoke-RestMethod -Uri "$Endpoint/evaluate" -Method Post -Body $body -Headers $Headers
    if ($resp.outcome -ne "require_hitl") { throw "expected require_hitl, got '$($resp.outcome)'" }
    if ($resp.hitl_request_id -eq $null) { throw "hitl_request_id should not be null" }
}

# ── 4. Log and allow ───────────────────────────────────────────────────────
Test-Step -Name "POST /evaluate -> log_and_allow (confidential file)" -Script {
    $body = @{
        tool_call = @{
            tool       = "read_file"
            parameters = @{ path = "/data/confidential/report.pdf" }
        }
        dry_run   = $false
    } | ConvertTo-Json

    $resp = Invoke-RestMethod -Uri "$Endpoint/evaluate" -Method Post -Body $body -Headers $Headers
    if ($resp.outcome -ne "log_and_allow") { throw "expected log_and_allow, got '$($resp.outcome)'" }
}

# ── 5. Default allow ───────────────────────────────────────────────────────
Test-Step -Name "POST /evaluate -> allow (unknown tool)" -Script {
    $body = @{
        tool_call = @{
            tool       = "unknown_tool"
            parameters = @{}
        }
        dry_run   = $false
    } | ConvertTo-Json

    $resp = Invoke-RestMethod -Uri "$Endpoint/evaluate" -Method Post -Body $body -Headers $Headers
    if ($resp.outcome -ne "allow") { throw "expected allow, got '$($resp.outcome)'" }
}

# ── Summary ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "+-------------------+"
if ($FailCount -eq 0) {
    Write-Host "| ALL $PassCount PASSED  v |" -ForegroundColor Green
} else {
    Write-Host "| $PassCount PASSED, $FailCount FAILED x |" -ForegroundColor Red
}
Write-Host "+-------------------+"
Write-Host ""
Write-Host "To run the full harness against this endpoint:" -ForegroundColor Yellow
Write-Host "  `$env:GUARDRAIL_API_URL = '$Endpoint'"
Write-Host "  python harness\run_all.py"
