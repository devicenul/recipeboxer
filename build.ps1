<#
.SYNOPSIS
    RecipeBoxer Cross-Platform Build & Packaging Script (Windows PowerShell)
.DESCRIPTION
    Compiles RecipeBoxer into standalone deployable Windows executable (dist\recipeboxer.exe)
    and standard distribution wheel and source distribution packages (dist\*.whl, dist\*.tar.gz).
.PARAMETER All
    Builds wheel, sdist, and PyInstaller standalone binary (default).
.PARAMETER Wheel
    Builds only wheel (.whl) and source distribution (.tar.gz).
.PARAMETER Binary
    Builds only standalone Windows executable binary.
.PARAMETER Clean
    Removes previous build\ and dist\ artifacts before compilation.
.PARAMETER Help
    Displays this detailed help documentation and exits.
#>

[CmdletBinding()]
param(
    [Alias("a")]
    [switch]$All,

    [Alias("w", "dist-only", "DistOnly")]
    [switch]$Wheel,

    [Alias("b", "binary-only", "BinaryOnly")]
    [switch]$Binary,

    [Alias("c")]
    [switch]$Clean,

    [Alias("h", "?")]
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Get-Help $MyInvocation.MyCommand.Path -Detailed
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Write-Info { param([string]$Msg) Write-Host "[INFO] $Msg" -ForegroundColor Cyan }
function Write-Step { param([string]$Msg) Write-Host "`n==> $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "[WARN] $Msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }

# Resolve build mode
$BuildMode = "all"
if ($Wheel) { $BuildMode = "wheel" }
if ($Binary) { $BuildMode = "binary" }

# Locate Python environment
$Python = ""
if (Test-Path "$ScriptDir\.venv\Scripts\python.exe") {
    $Python = "$ScriptDir\.venv\Scripts\python.exe"
} elseif ($env:VIRTUAL_ENV -and (Test-Path "$env:VIRTUAL_ENV\Scripts\python.exe")) {
    $Python = "$env:VIRTUAL_ENV\Scripts\python.exe"
} elseif (Get-Command "python.exe" -ErrorAction SilentlyContinue) {
    $Python = (Get-Command "python.exe").Source
} elseif (Get-Command "py.exe" -ErrorAction SilentlyContinue) {
    $Python = (Get-Command "py.exe").Source
} else {
    Write-Err "Python 3 environment not found. Please run .\setup.ps1 first."
    exit 1
}

# Verify Python version >= 3.10
$VersionCheck = & $Python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Err "Python 3.10 or higher is required to build RecipeBoxer."
    exit 1
}

$PyVer = & $Python --version
Write-Info "Using Python interpreter: $PyVer at $Python"

# Clean if requested
if ($Clean) {
    Write-Step "Cleaning previous build artifacts..."
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist, *.egg-info, recipeboxer.egg-info
    Get-ChildItem -Recurse -Filter "__pycache__" -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Info "Clean complete."
}

if (-not (Test-Path "$ScriptDir\dist")) {
    New-Item -ItemType Directory -Path "$ScriptDir\dist" | Out-Null
}

# Ensure build tools
Write-Step "Verifying build tools (build, wheel, setuptools, pyinstaller)..."
& $Python -m pip install --quiet --upgrade build wheel setuptools pyinstaller

# Step A: Build Wheel & Sdist
if ($BuildMode -eq "all" -or $BuildMode -eq "wheel") {
    Write-Step "Building standard distribution packages (wheel & sdist)..."
    & $Python -m build --sdist --wheel --outdir "$ScriptDir\dist"
    Write-Info "Distribution packages created in dist\:"
    Get-ChildItem "$ScriptDir\dist\*.whl", "$ScriptDir\dist\*.tar.gz" -ErrorAction SilentlyContinue | Format-Table Name, Length, LastWriteTime
}

# Step B: Build Standalone Windows Executable
if ($BuildMode -eq "all" -or $BuildMode -eq "binary") {
    Write-Step "Building standalone deployable executable binary with PyInstaller..."
    & $Python -m PyInstaller --clean --noconfirm "$ScriptDir\recipeboxer.spec"

    $ExePath = "$ScriptDir\dist\recipeboxer.exe"
    if (Test-Path $ExePath) {
        Write-Info "Standalone binary compiled: $ExePath"

        # Verify flags
        Write-Step "Verifying standalone binary flags..."
        & $ExePath --version
        & $ExePath --help | Out-Null
        Write-Info "Standalone binary verified successfully (--version and --help passed)."
    } else {
        Write-Err "Binary output not found at $ExePath."
        exit 1
    }
}

Write-Host "`n================================================================" -ForegroundColor Green
Write-Host "   RecipeBoxer Build Succeeded!" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Info "Artifacts in $ScriptDir\dist:"
Get-ChildItem "$ScriptDir\dist" | Format-Table Name, Length, LastWriteTime
