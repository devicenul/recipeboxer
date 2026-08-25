@echo off
rem ==============================================================================
rem RecipeBoxer — Cross-Platform Windows Batch Setup Script (cmd.exe)
rem ==============================================================================
rem Detects Python 3.10+, bootstraps an isolated virtual environment (.venv),
rem upgrades build packaging tools (pip, wheel, setuptools), installs production
rem and development dependencies, and performs an editable project install.
rem ==============================================================================

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

echo ======================================================================
echo  RecipeBoxer — Environment Bootstrap ^& Setup (Batch)
echo ======================================================================

echo [1/5] Checking for Python 3.10+ in PATH...

set "PYTHON_CMD="

for %%C in ("py -3.14" "py -3.13" "py -3.12" "py -3.11" "py -3.10" "py -3" "python" "python3") do (
    if not defined PYTHON_CMD (
        %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            set "PYTHON_CMD=%%~C"
        )
    )
)

if not defined PYTHON_CMD (
    echo ERROR: Python 3.10 or higher is required but not found in PATH. >&2
    echo Please install Python 3.10+ and ensure it is available in your PATH. >&2
    exit /b 1
)

for /f "delims=" %%V in ('!PYTHON_CMD! -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"') do (
    set "PY_VERSION=%%V"
)
echo Found compatible Python: !PYTHON_CMD! (v!PY_VERSION!)

echo [2/5] Setting up virtual environment at %VENV_DIR%...

if not exist "%VENV_PYTHON%" (
    echo Creating new isolated virtual environment...
    !PYTHON_CMD! -m venv "%VENV_DIR%"
    if !ERRORLEVEL! NEQ 0 (
        echo ERROR: Failed to create virtual environment. >&2
        exit /b 1
    )
) else (
    echo Existing virtual environment detected. Reusing %VENV_DIR%.
)

echo [3/5] Upgrading build tools (pip, wheel, setuptools)...
"%VENV_PYTHON%" -m pip install --upgrade pip wheel setuptools
if !ERRORLEVEL! NEQ 0 (
    echo ERROR: Failed to upgrade packaging tools. >&2
    exit /b 1
)

echo [4/5] Installing dependencies and package in editable mode...

if exist "%SCRIPT_DIR%requirements.txt" (
    echo Installing production dependencies from requirements.txt...
    "%VENV_PYTHON%" -m pip install -r "%SCRIPT_DIR%requirements.txt"
    if !ERRORLEVEL! NEQ 0 (
        echo ERROR: Failed to install production dependencies. >&2
        exit /b 1
    )
)

if exist "%SCRIPT_DIR%requirements-dev.txt" (
    echo Installing development dependencies from requirements-dev.txt...
    "%VENV_PYTHON%" -m pip install -r "%SCRIPT_DIR%requirements-dev.txt"
    if !ERRORLEVEL! NEQ 0 (
        echo ERROR: Failed to install development dependencies. >&2
        exit /b 1
    )
)

echo Installing RecipeBoxer in editable mode (-e .)...
"%VENV_PYTHON%" -m pip install -e "%SCRIPT_DIR%."
if !ERRORLEVEL! NEQ 0 (
    echo ERROR: Failed to install package in editable mode. >&2
    exit /b 1
)

echo [5/5] Verifying installation...
for /f "delims=" %%R in ('"%VENV_PYTHON%" -c "import recipeboxer; print(recipeboxer.__version__)"') do (
    set "RECIPEBOXER_VERSION=%%R"
)
if !ERRORLEVEL! NEQ 0 (
    echo ERROR: Verification failed. Could not import recipeboxer. >&2
    exit /b 1
)

echo ======================================================================
echo  RecipeBoxer v!RECIPEBOXER_VERSION! setup completed successfully!
echo  Virtual environment: %VENV_DIR%
echo.
echo  Quick start:
echo    Activate: .venv\Scripts\activate.bat
echo    Run:      run.bat  (or: recipeboxer)
echo    Test:     pytest
echo ======================================================================

exit /b 0
