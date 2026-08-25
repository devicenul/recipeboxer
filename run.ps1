<#
.SYNOPSIS
    RecipeBoxer — Cross-Platform Windows PowerShell Execution Script
.DESCRIPTION
    Checks for virtual environment (.venv), auto-triggers setup.ps1 if missing,
    configures default environment variables, and launches the FastAPI backend /
    Antigravity orchestration server with graceful interrupt handling.
.PARAMETER Host
    Host address to bind the FastAPI server (default: 127.0.0.1 or $env:RECIPEBOXER_API_HOST)
.PARAMETER Port
    Port to bind the FastAPI server (default: 8000 or $env:RECIPEBOXER_API_PORT)
.PARAMETER LogLevel
    Logging verbosity level (critical, error, warning, info, debug, trace)
.PARAMETER Reload
    Enable auto-reload for development (ignored in standalone frozen mode)
.PARAMETER Help
    Show application help and usage instructions
#>

[CmdletBinding(PositionalBinding = $false)]
param(
    [Alias("h")]
    [Parameter(Mandatory = $false)]
    [switch]$Help,

    [Alias("Host")]
    [Parameter(Mandatory = $false)]
    [string]$HostAddress,

    [Parameter(Mandatory = $false)]
    [Nullable[int]]$Port,

    [Alias("c")]
    [Parameter(Mandatory = $false)]
    [Nullable[int]]$Concurrency,

    [Parameter(Mandatory = $false)]
    [ValidateSet("critical", "error", "warning", "info", "debug", "trace")]
    [string]$LogLevel,

    [Parameter(Mandatory = $false)]
    [switch]$Reload,

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
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

# 1. Check for existing virtual environment; auto-trigger setup.ps1 if missing
if (-not (Test-Path $VenvPython)) {
    Write-Host "Virtual environment not detected at $VenvDir." -ForegroundColor Yellow
    Write-Host "Automatically running setup.ps1 to bootstrap environment..." -ForegroundColor Yellow
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

if (-not (Test-Path $VenvPython)) {
    Write-Error "ERROR: Virtual environment Python binary not found at $VenvPython."
    exit 1
}

# 2. Set default environment variables if not already defined
if (-not $env:RECIPEBOXER_API_HOST) { $env:RECIPEBOXER_API_HOST = "127.0.0.1" }
if (-not $env:RECIPEBOXER_API_PORT) { $env:RECIPEBOXER_API_PORT = "8000" }
if (-not $env:RECIPEBOXER_DEBUG) { $env:RECIPEBOXER_DEBUG = "false" }
if (-not $env:RECIPEBOXER_CACHE_DIR) { $env:RECIPEBOXER_CACHE_DIR = ".cache/recipeboxer" }
if (-not $env:RECIPEBOXER_EXPORT_DIR) { $env:RECIPEBOXER_EXPORT_DIR = "exports" }

# 3. Construct command-line arguments for Python CLI runner
$pyArgs = @("-m", "recipeboxer")
if ($Help) {
    $pyArgs += "--help"
}
if ($HostAddress) {
    $pyArgs += @("--host", $HostAddress)
}
if ($Port) {
    $pyArgs += @("--port", "$Port")
}
if ($Concurrency) {
    $pyArgs += @("--concurrency", "$Concurrency")
}
if ($LogLevel) {
    $pyArgs += @("--log-level", $LogLevel)
}
if ($Reload) {
    $pyArgs += "--reload"
}
if ($RemainingArgs) {
    $pyArgs += $RemainingArgs
}

# 4. Execute Python runner with try/finally graceful interrupt handling
$exitCode = 0
try {
    & $VenvPython $pyArgs
    $exitCode = $LASTEXITCODE
} catch [System.Management.Automation.PipelineStoppedException] {
    $exitCode = 0
} catch {
    Write-Host "RecipeBoxer runner interrupted." -ForegroundColor Yellow
    $exitCode = 0
} finally {
    # Cleanup complete
}

exit $exitCode
