#!/usr/bin/env bash
# ==============================================================================
# RecipeBoxer — Cross-Platform POSIX Test Automation Script (macOS / Linux)
# ==============================================================================
# Verifies virtual environment (.venv) and pytest binary, auto-triggers setup.sh
# if missing, transparently forwards all CLI arguments to pytest, and propagates
# the exact exit code.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
VENV_PYTEST="${VENV_DIR}/bin/pytest"

# 1. Auto-detect virtual environment and pytest; bootstrap via setup.sh if missing
if [ ! -f "${VENV_PYTEST}" ] || [ ! -x "${VENV_PYTEST}" ]; then
    echo "Virtual environment or pytest not detected at ${VENV_PYTEST}."
    echo "Automatically running setup.sh to bootstrap test environment..."
    if [ -f "${SCRIPT_DIR}/setup.sh" ]; then
        bash "${SCRIPT_DIR}/setup.sh"
    else
        echo "ERROR: setup.sh not found at ${SCRIPT_DIR}/setup.sh" >&2
        exit 1
    fi
fi

# Ensure pytest binary is available and executable after setup
if [ ! -x "${VENV_PYTEST}" ]; then
    echo "ERROR: Virtual environment pytest binary not found at ${VENV_PYTEST}." >&2
    exit 1
fi

# 2. Execute pytest with transparent argument passthrough and exit code propagation
exec "${VENV_PYTEST}" "$@"
