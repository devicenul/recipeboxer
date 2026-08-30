<#
.SYNOPSIS
    RecipeBoxer - Cross-Platform Windows PowerShell Test Automation Script
.DESCRIPTION
    Checks for virtual environment (.venv) and pytest executable, auto-triggers
    setup.ps1 if missing, forwards all command-line arguments to pytest, and
    propagates the exact exit code.
.PARAMETER RemainingArgs
    Command-line arguments transparently passed through to pytest.
#>

[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent -Path $MyInvocation.MyCommand.Definition
}
if (-not $ScriptDir) {
    $ScriptDir = (Get-Location).Path
}

$VenvDir = Join-Path $ScriptDir ".venv"
$VenvPytest = Join-Path $VenvDir "Scripts\pytest.exe"

# Support cross-platform PowerShell Core environments where .venv uses bin/
if (-not (Test-Path $VenvPytest)) {
    $PosixPytest = Join-Path $VenvDir "bin/pytest"
    if (Test-Path $PosixPytest) {
        $VenvPytest = $PosixPytest
    }
}

# 1. Check for existing virtual environment and pytest; auto-trigger setup.ps1 if missing
if (-not (Test-Path $VenvPytest)) {
    Write-Host "Virtual environment or pytest not detected at $VenvDir." -ForegroundColor Yellow
    Write-Host "Automatically running setup.ps1 to bootstrap test environment..." -ForegroundColor Yellow
    $SetupPs1 = Join-Path $ScriptDir "setup.ps1"
    if (Test-Path $SetupPs1) {
        & $SetupPs1
        if ($LASTEXITCODE -ne 0) {
            Write-Error "ERROR: setup.ps1 failed with exit code $LASTEXITCODE."
            exit $LASTEXITCODE
        }
    } else {
        Write-Error "ERROR: setup.ps1 not found at $SetupPs1"
        exit 1
    }
}

if (-not (Test-Path $VenvPytest)) {
    Write-Error "ERROR: Virtual environment pytest binary not found at $VenvPytest."
    exit 1
}

# 2. Execute pytest with argument passthrough and exit code propagation
$exitCode = 0
try {
    if ($RemainingArgs -and $RemainingArgs.Count -gt 0) {
        & $VenvPytest @RemainingArgs
    } else {
        & $VenvPytest
    }
    $exitCode = $LASTEXITCODE
} catch {
    Write-Error "ERROR: An error occurred while executing pytest: $_"
    $exitCode = if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) { $LASTEXITCODE } else { 1 }
}

exit $exitCode
