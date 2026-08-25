@echo off
rem ==============================================================================
rem RecipeBoxer — Cross-Platform Windows Batch Execution Script (cmd.exe)
rem ==============================================================================
rem Verifies virtual environment (.venv), auto-triggers setup.bat if missing,
rem sets default environment variables, and launches the FastAPI backend /
rem Antigravity orchestration server with clean argument forwarding.
rem ==============================================================================

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

rem 1. Auto-detect virtual environment; bootstrap via setup.bat if missing
if not exist "%VENV_PYTHON%" (
    echo Virtual environment not detected at %VENV_DIR%.
    echo Automatically running setup.bat to bootstrap environment...
    if exist "%SCRIPT_DIR%setup.bat" (
        call "%SCRIPT_DIR%setup.bat"
        if !ERRORLEVEL! NEQ 0 (
            echo ERROR: setup.bat failed with exit code !ERRORLEVEL!. >&2
            exit /b !ERRORLEVEL!
        )
    ) else (
        echo ERROR: setup.bat not found at %SCRIPT_DIR%setup.bat >&2
        exit /b 1
    )
)

if not exist "%VENV_PYTHON%" (
    echo ERROR: Virtual environment Python binary not found at %VENV_PYTHON%. >&2
    exit /b 1
)

rem 2. Configure default environment variables if not already set
if not defined RECIPEBOXER_API_HOST set "RECIPEBOXER_API_HOST=127.0.0.1"
if not defined RECIPEBOXER_API_PORT set "RECIPEBOXER_API_PORT=8000"
if not defined RECIPEBOXER_DEBUG set "RECIPEBOXER_DEBUG=false"
if not defined RECIPEBOXER_CACHE_DIR set "RECIPEBOXER_CACHE_DIR=.cache/recipeboxer"
if not defined RECIPEBOXER_EXPORT_DIR set "RECIPEBOXER_EXPORT_DIR=exports"

rem 3. Launch RecipeBoxer application runner with forwarded arguments
"%VENV_PYTHON%" -m recipeboxer %*
set "RUN_EXIT_CODE=%ERRORLEVEL%"

exit /b %RUN_EXIT_CODE%
