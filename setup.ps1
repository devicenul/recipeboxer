<#
.SYNOPSIS
    RecipeBoxer — Cross-Platform Windows PowerShell Setup Script
.DESCRIPTION
    Detects Python 3.10+, bootstraps an isolated virtual environment (.venv),
    upgrades build packaging tools (pip, wheel, setuptools), installs production
    and development dependencies, and performs an editable project install.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent -Path $MyInvocation.MyCommand.Definition
}
if (-not $ScriptDir) {
    $ScriptDir = (Get-Location).Path
}

$VenvDir = Join-Path $ScriptDir ".venv"

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " RecipeBoxer — Environment Bootstrap & Setup (PowerShell)" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

# 1. Locate compatible Python 3.10+ interpreter
Write-Host "[1/5] Checking for Python 3.10+ in PATH..." -ForegroundColor Yellow

$PythonCommand = $null
$PythonArgs = @()

$Candidates = @(
    @{ Cmd = "py"; Args = @("-3.14") },
    @{ Cmd = "py"; Args = @("-3.13") },
    @{ Cmd = "py"; Args = @("-3.12") },
    @{ Cmd = "py"; Args = @("-3.11") },
    @{ Cmd = "py"; Args = @("-3.10") },
    @{ Cmd = "py"; Args = @("-3") },
    @{ Cmd = "python"; Args = @() },
    @{ Cmd = "python3"; Args = @() }
)

foreach ($cand in $Candidates) {
    $cmdName = $cand.Cmd
    $cmdArgs = $cand.Args

    $hasCmd = $false
    try {
        $check = Get-Command $cmdName -ErrorAction SilentlyContinue
        if ($check) { $hasCmd = $true }
    } catch {
        $hasCmd = $false
    }

    if ($hasCmd) {
        try {
            $testArgs = $cmdArgs + @("-c", "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)")
            $proc = Start-Process -FilePath $cmdName -ArgumentList $testArgs -NoNewWindow -Wait -PassThru -ErrorAction SilentlyContinue
            if ($proc.ExitCode -eq 0) {
                $PythonCommand = $cmdName
                $PythonArgs = $cmdArgs
                break
            }
        } catch {
            # Candidate failed, proceed to next
        }
    }
}

if (-not $PythonCommand) {
    Write-Error "ERROR: Python 3.10 or higher is required but not found in PATH or Windows py launcher."
    Write-Host "Please install Python 3.10+ from python.org and add it to your PATH." -ForegroundColor Red
    exit 1
}

$versionArgs = $PythonArgs + @("-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
$pyVersion = (& $PythonCommand $versionArgs)
Write-Host "Found compatible Python: $PythonCommand $($PythonArgs -join ' ') (v$pyVersion)" -ForegroundColor Green

# 2. Bootstrap or reuse isolated virtual environment (.venv)
Write-Host "[2/5] Setting up virtual environment at $VenvDir..." -ForegroundColor Yellow

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating new isolated virtual environment..." -ForegroundColor Gray
    $venvCreateArgs = $PythonArgs + @("-m", "venv", $VenvDir)
    & $PythonCommand $venvCreateArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ERROR: Failed to create virtual environment."
        exit 1
    }
} else {
    Write-Host "Existing virtual environment detected. Reusing $VenvDir." -ForegroundColor Gray
}

# 3. Upgrade core packaging tools (pip, wheel, setuptools)
Write-Host "[3/5] Upgrading build tools (pip, wheel, setuptools)..." -ForegroundColor Yellow
& $VenvPython -m pip install --upgrade pip wheel setuptools
if ($LASTEXITCODE -ne 0) {
    Write-Error "ERROR: Failed to upgrade packaging tools."
    exit 1
}

# 4. Install production, dev dependencies, and editable project
Write-Host "[4/5] Installing dependencies and package in editable mode..." -ForegroundColor Yellow

$ReqProd = Join-Path $ScriptDir "requirements.txt"
if (Test-Path $ReqProd) {
    Write-Host "Installing production dependencies from requirements.txt..." -ForegroundColor Gray
    & $VenvPython -m pip install -r $ReqProd
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ERROR: Failed to install production dependencies."
        exit 1
    }
}

$ReqDev = Join-Path $ScriptDir "requirements-dev.txt"
if (Test-Path $ReqDev) {
    Write-Host "Installing development dependencies from requirements-dev.txt..." -ForegroundColor Gray
    & $VenvPython -m pip install -r $ReqDev
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ERROR: Failed to install development dependencies."
        exit 1
    }
}

Write-Host "Installing RecipeBoxer in editable mode (-e .)..." -ForegroundColor Gray
& $VenvPython -m pip install -e $ScriptDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "ERROR: Failed to install package in editable mode."
    exit 1
}

# 5. Verify installation & display version banner
Write-Host "[5/5] Verifying installation..." -ForegroundColor Yellow
$InstalledVersion = (& $VenvPython -c "import recipeboxer; print(recipeboxer.__version__)")
if ($LASTEXITCODE -ne 0) {
    Write-Error "ERROR: Verification failed. Could not import recipeboxer."
    exit 1
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host " RecipeBoxer v$InstalledVersion setup completed successfully!" -ForegroundColor Green
Write-Host " Virtual environment: $VenvDir" -ForegroundColor Gray
Write-Host ""
Write-Host " Quick start:" -ForegroundColor Gray
Write-Host "   Activate: .\.venv\Scripts\Activate.ps1" -ForegroundColor Gray
Write-Host "   Run:      .\run.ps1  (or: recipeboxer)" -ForegroundColor Gray
Write-Host "   Test:     .\test.ps1 (or: pytest)" -ForegroundColor Gray
Write-Host "======================================================================" -ForegroundColor Cyan

exit 0
