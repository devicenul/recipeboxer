#!/usr/bin/env bash
# ==============================================================================
# RecipeBoxer Cross-Platform Build & Packaging Script (POSIX / macOS / Linux)
# ==============================================================================
set -euo pipefail

# ANSI color codes
BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_step() { echo -e "${BOLD}${GREEN}==>${NC} ${BOLD}$*${NC}"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

show_help() {
    cat <<EOF
Usage: ./build.sh [OPTIONS]

Build RecipeBoxer package distributions and standalone executable binaries.

Options:
    --all           Build wheel, sdist, and PyInstaller standalone binary (default)
    --wheel         Build only wheel (.whl) and source distribution (.tar.gz)
    --dist-only     Alias for --wheel
    --binary        Build only standalone PyInstaller executable binary
    --binary-only   Alias for --binary
    --clean         Remove previous build/ and dist/ artifacts before building
    -h, --help      Show this help message and exit
EOF
}

# Parse CLI options
BUILD_MODE="all"
DO_CLEAN=false

for arg in "$@"; do
    case "$arg" in
        --all)
            BUILD_MODE="all"
            ;;
        --wheel|--dist-only)
            BUILD_MODE="wheel"
            ;;
        --binary|--binary-only)
            BUILD_MODE="binary"
            ;;
        --clean)
            DO_CLEAN=true
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            log_error "Unknown option: $arg"
            show_help
            exit 1
            ;;
    esac
done

# Locate Python environment
if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    PYTHON="${VIRTUAL_ENV}/bin/python"
elif [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
    PYTHON="${SCRIPT_DIR}/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
elif command -v python &>/dev/null; then
    PYTHON="$(command -v python)"
else
    log_error "Python 3 environment not found. Please run ./setup.sh first."
    exit 1
fi

# Verify Python version >= 3.10
if ! "${PYTHON}" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
    log_error "Python 3.10 or higher is required to build RecipeBoxer."
    exit 1
fi

log_info "Using Python interpreter: $(${PYTHON} --version) at ${PYTHON}"

# Clean artifacts if requested
if [[ "${DO_CLEAN}" == true ]]; then
    log_step "Cleaning previous build artifacts..."
    rm -rf build/ dist/ *.egg-info recipeboxer.egg-info/
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    log_info "Clean complete."
fi

mkdir -p "${SCRIPT_DIR}/dist"

# Ensure build tools are installed
log_step "Verifying build tools (build, wheel, setuptools, pyinstaller)..."
${PYTHON} -m pip install --quiet --upgrade build wheel setuptools pyinstaller

# Step A: Build Wheels & Sdist
if [[ "${BUILD_MODE}" == "all" || "${BUILD_MODE}" == "wheel" ]]; then
    log_step "Building standard distribution packages (wheel & sdist)..."
    ${PYTHON} -m build --sdist --wheel --outdir "${SCRIPT_DIR}/dist"
    log_info "Distribution packages created in dist/:"
    ls -lh "${SCRIPT_DIR}/dist"/*.whl "${SCRIPT_DIR}/dist"/*.tar.gz 2>/dev/null || true
fi

# Step B: Build Standalone PyInstaller Binary
if [[ "${BUILD_MODE}" == "all" || "${BUILD_MODE}" == "binary" ]]; then
    log_step "Building standalone deployable executable binary with PyInstaller..."
    ${PYTHON} -m PyInstaller --clean --noconfirm "${SCRIPT_DIR}/recipeboxer.spec"

    BINARY_PATH="${SCRIPT_DIR}/dist/recipeboxer"
    if [[ -f "${BINARY_PATH}" ]]; then
        chmod +x "${BINARY_PATH}"
        log_info "Standalone binary compiled: ${BINARY_PATH}"

        # Verify binary execution
        log_step "Verifying standalone binary flags..."
        "${BINARY_PATH}" --version
        "${BINARY_PATH}" --help > /dev/null
        log_info "Standalone binary verified successfully (--version and --help passed)."
    else
        log_error "Binary output not found at ${BINARY_PATH}."
        exit 1
    fi
fi

echo ""
echo -e "${BOLD}${GREEN}================================================================${NC}"
echo -e "${BOLD}${GREEN}   RecipeBoxer Build Succeeded!${NC}"
echo -e "${BOLD}${GREEN}================================================================${NC}"
log_info "Artifacts in ${SCRIPT_DIR}/dist:"
ls -lh "${SCRIPT_DIR}/dist"
echo ""
