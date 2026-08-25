#!/usr/bin/env bash
# ==============================================================================
# RecipeBoxer — Cross-Platform POSIX Setup Script (macOS / Linux)
# ==============================================================================
# Detects Python 3.10+, bootstraps an isolated virtual environment (.venv),
# upgrades build packaging tools (pip, wheel, setuptools), installs production
# and development dependencies, and performs an editable project install.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

echo "======================================================================"
echo " RecipeBoxer — Environment Bootstrap & Setup"
echo "======================================================================"

# 1. Locate compatible Python 3.10+ interpreter from PATH candidates
echo "[1/5] Checking for Python 3.10+ in PATH..."

PYTHON_CANDIDATES=(
    "python3"
    "python"
    "python3.14"
    "python3.13"
    "python3.12"
    "python3.11"
    "python3.10"
)

PYTHON_BIN=""

for cand in "${PYTHON_CANDIDATES[@]}"; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            PYTHON_BIN="$(command -v "$cand")"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: Python 3.10 or higher is required but not found in PATH." >&2
    echo "Please install Python 3.10+ and ensure it is available in your PATH." >&2
    exit 1
fi

PY_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
echo "Found compatible Python: ${PYTHON_BIN} (v${PY_VERSION})"

# 2. Bootstrap or reuse isolated virtual environment (.venv)
echo "[2/5] Setting up virtual environment at ${VENV_DIR}..."

if [ ! -d "${VENV_DIR}" ] || [ ! -f "${VENV_DIR}/bin/python" ]; then
    echo "Creating new isolated virtual environment..."
    "$PYTHON_BIN" -m venv "${VENV_DIR}"
else
    echo "Existing virtual environment detected. Reusing ${VENV_DIR}."
fi

VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

if [ ! -x "${VENV_PYTHON}" ]; then
    echo "ERROR: Virtual environment Python binary not found at ${VENV_PYTHON}." >&2
    exit 1
fi

# 3. Upgrade core packaging tools (pip, wheel, setuptools)
echo "[3/5] Upgrading build tools (pip, wheel, setuptools)..."
"${VENV_PYTHON}" -m pip install --upgrade pip wheel setuptools

# 4. Install production, dev dependencies, and editable project
echo "[4/5] Installing dependencies and package in editable mode..."

if [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
    echo "Installing production dependencies from requirements.txt..."
    "${VENV_PYTHON}" -m pip install -r "${SCRIPT_DIR}/requirements.txt"
fi

if [ -f "${SCRIPT_DIR}/requirements-dev.txt" ]; then
    echo "Installing development dependencies from requirements-dev.txt..."
    "${VENV_PYTHON}" -m pip install -r "${SCRIPT_DIR}/requirements-dev.txt"
fi

echo "Installing RecipeBoxer in editable mode (-e .)..."
"${VENV_PYTHON}" -m pip install -e "${SCRIPT_DIR}"

# 5. Verify installation & display version banner
echo "[5/5] Verifying installation..."
RECIPEBOXER_VERSION="$("${VENV_PYTHON}" -c "import recipeboxer; print(recipeboxer.__version__)")"

echo "======================================================================"
echo " RecipeBoxer v${RECIPEBOXER_VERSION} setup completed successfully!"
echo " Virtual environment: ${VENV_DIR}"
echo ""
echo " Quick start:"
echo "   Activate: source .venv/bin/activate"
echo "   Run:      ./run.sh  (or: recipeboxer)"
echo "   Test:     ./test.sh (or: pytest)"
echo "======================================================================"
exit 0
