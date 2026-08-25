#!/usr/bin/env bash
# ==============================================================================
# RecipeBoxer — Cross-Platform POSIX Execution Script (macOS / Linux)
# ==============================================================================
# Verifies virtual environment (.venv), auto-triggers setup.sh if missing,
# sets default environment variables, and launches the FastAPI backend /
# Antigravity orchestration server with graceful shutdown handling.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"

# 1. Auto-detect virtual environment; bootstrap via setup.sh if missing
if [ ! -f "${VENV_PYTHON}" ] || [ ! -x "${VENV_PYTHON}" ]; then
    echo "Virtual environment not detected at ${VENV_DIR}."
    echo "Automatically running setup.sh to bootstrap environment..."
    if [ -f "${SCRIPT_DIR}/setup.sh" ]; then
        bash "${SCRIPT_DIR}/setup.sh"
    else
        echo "ERROR: setup.sh not found at ${SCRIPT_DIR}/setup.sh" >&2
        exit 1
    fi
fi

# Ensure virtual environment python binary is available after setup
if [ ! -x "${VENV_PYTHON}" ]; then
    echo "ERROR: Virtual environment Python binary not found at ${VENV_PYTHON}." >&2
    exit 1
fi

# 2. Configure default environment variables if not already set
export RECIPEBOXER_API_HOST="${RECIPEBOXER_API_HOST:-127.0.0.1}"
export RECIPEBOXER_API_PORT="${RECIPEBOXER_API_PORT:-8000}"
export RECIPEBOXER_DEBUG="${RECIPEBOXER_DEBUG:-false}"
export RECIPEBOXER_CACHE_DIR="${RECIPEBOXER_CACHE_DIR:-.cache/recipeboxer}"
export RECIPEBOXER_EXPORT_DIR="${RECIPEBOXER_EXPORT_DIR:-exports}"

# 3. Trap SIGINT and SIGTERM for graceful exit
trap 'exit 0' INT TERM

# 4. Launch RecipeBoxer application runner
exec "${VENV_PYTHON}" -m recipeboxer "$@"
