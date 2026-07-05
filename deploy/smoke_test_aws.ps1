<#
.SYNOPSIS
    Smoke-test the AWS-deployed Action Guardrail API.

.DESCRIPTION
    Hits /health, /, /docs, and runs the three canonical evaluate scenarios
    against a live API Gateway endpoint. Prints PASS/FAIL for each check.

.PARAMETER Endpoint
    The API Gateway URL (e.g. https://abc123.execute-api.us-east-1.amazonaws.com)

.PARAMETER ApiKey
    The API key value to use in the X-API-Key header.

.EXAMPLE
    .\deploy\smoke_test_aws.ps1 -Endpoint "https://abc123.execute-api.us-east-1.amazonaws.com" -ApiKey "my-secret-key"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Endpoint,

    [Parameter(Mandatory = $true)]
    [string]$ApiKey
)

$ErrorActionPreference = "Stop"
$pass = 0
$fail = 0
$total = 0

function Check-Test {
    param([string]$Name, [scriptblock]$Script)
    $global:total++
    try {
        $result = & $Script
        if ($result) {
            $global:pass++
            Write-Host "  [PASS] $Name" -ForegroundColor Green
        } else {
            $global:fail++
            Write-Host "  [FAIL] $Name" -ForegroundColor Red
        }
    } catch {
        $global:fail++
        Write-Host "  [FAIL] $Name — $_" -ForegroundColor Red
    }
}

function Invoke-Api {
    param([string]$Method, [string]$Path, [object]$Body = $null)
    $params = @{
        Uri = "$Endpoint$Path"
        Method = $Method
        Headers = @{
            "X-API-Key" = $ApiKey
        }
        ContentType = "application/json"
        UseBasicParsing = $true
    }
    if ($Body) {
        $params["Body"] = ($Body | ConvertTo-Json -Compress)
    }
    return Invoke-RestMethod @params
}

Write-Host "=== Smoke Test: $Endpoint ===" -ForegroundColor Cyan

# -- Health check (public, no API key needed) ------------------------------
Check-Test -Name "GET /health" -Script {
    $r = Invoke-RestMethod -Uri "$Endpoint/health" -UseBasicParsing
    return ($r.status -eq "ok")
}

# -- Landing page ----------------------------------------------------------
Check-Test -Name "GET / (landing)" -Script {
    # Just check it returns 200 - no API key needed
    $r = Invoke-WebRequest -Uri "$Endpoint/" -UseBasicParsing
    return ($r.StatusCode -eq 200)
}

# -- Evaluate: block (delete >100 records) ---------------------------------
Check-Test -Name "POST /evaluate -> block (bulk delete)" -Script {
    $r = Invoke-Api -Method POST -Path "/evaluate" -Body @{
        tool_call = @{
            tool = "delete_records"
            parameters = @{ record_count = 500 }
        }
        dry_run = $false
    }
    return ($r.outcome -eq "block" -and $r.matched_rule_id -eq "block-bulk-delete")
}

# -- Evaluate: require_hitl (external email) -----------------------------
Check-Test -Name "POST /evaluate -> require_hitl (external email)" -Script {
    $r = Invoke-Api -Method POST -Path "/evaluate" -Body @{
        tool_call = @{
            tool = "send_email"
            parameters = @{ recipient_domain = "gmail.com" }
        }
        dry_run = $false
    }
    return ($r.outcome -eq "require_hitl" -and $r.hitl_request_id -ne $null)
}

# -- Evaluate: log_and_allow (confidential file) --------------------------
Check-Test -Name "POST /evaluate -> log_and_allow (confidential file)" -Script {
    $r = Invoke-Api -Method POST -Path "/evaluate" -Body @{
        tool_call = @{
            tool = "read_file"
            parameters = @{ path = "/data/confidential/report.pdf" }
        }
        dry_run = $false
    }
    return ($r.outcome -eq "log_and_allow" -and $r.matched_rule_id -eq "log-confidential-read")
}

# -- Evaluate: allow (no rule) ---------------------------------------------
Check-Test -Name "POST /evaluate -> allow (no matching rule)" -Script {
    $r = Invoke-Api -Method POST -Path "/evaluate" -Body @{
        tool_call = @{
            tool = "unknown_tool"
            parameters = @{}
        }
        dry_run = $false
    }
    return ($r.outcome -eq "allow")
}

# -- Summary --------------------------------------------------------------
Write-Host ""
$border = "+" + ("-" * 19) + "+"
Write-Host $border
if ($fail -eq 0) {
    Write-Host "| ALL $pass PASSED $([char]0x2713) |" -ForegroundColor Green
} else {
    Write-Host "| $pass passed, $fail failed |" -ForegroundColor Yellow
}
Write-Host $border

exit $fail
