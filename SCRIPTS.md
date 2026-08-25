# RecipeBoxer Cross-Platform Scripts Guide

RecipeBoxer provides an automated, cross-platform suite of bootstrapping, execution, packaging, and test scripts supporting macOS (Darwin), Linux (POSIX), and Windows environments.

---

## Script Matrix & Platform Support

| Workflow | macOS / Linux (POSIX Bash) | Windows (PowerShell) | Windows (Command Prompt) | Purpose |
|---|---|---|---|---|
| **Environment Setup** | `./setup.sh` | `.\setup.ps1` | `setup.bat` | Detects Python 3.10+, bootstraps `.venv`, upgrades pip/wheel/setuptools, installs dependencies, and registers package in editable mode. |
| **Server Execution** | `./run.sh` | `.\run.ps1` | `run.bat` | Auto-detects `.venv`, configures default environment variables, and launches the FastAPI / Antigravity orchestration server with graceful shutdown. |
| **Build & Packaging** | `./build.sh` | `.\build.ps1` | N/A | Builds standalone deployable binaries via PyInstaller and standard distribution wheels/sdist into `dist/`. |
| **Test Automation** | `./test.sh` | `.\test.ps1` | N/A | Runs `pytest` inside the isolated virtual environment with transparent argument passthrough and exit code propagation. |

---

## 1. Environment Setup

The setup scripts automate virtual environment creation and dependency management.

### macOS & Linux (`setup.sh`)

```bash
# Ensure script is executable
chmod +x setup.sh

# Run setup
./setup.sh
```

**Workflow executed by `setup.sh`**:
1. Scans `PATH` for Python 3.10+ candidates (`python3`, `python`, `python3.14` down to `python3.10`).
2. Creates isolated virtual environment in `.venv/` if not present.
3. Upgrades core packaging tools: `pip`, `wheel`, and `setuptools`.
4. Installs production requirements from `requirements.txt`.
5. Installs development and testing requirements from `requirements-dev.txt`.
6. Performs editable project install (`pip install -e .`).
7. Verifies installation by importing `recipeboxer` and printing the version banner.

### Windows PowerShell (`setup.ps1`)

```powershell
# If script execution is restricted by PowerShell execution policy:
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# Run setup
.\setup.ps1
```

**Workflow executed by `setup.ps1`**:
1. Checks for Python 3.10+ using the Windows `py` launcher (`py -3.14` ... `py -3.10`, `py -3`) and `python`/`python3`.
2. Creates `.venv\` using `python -m venv .venv`.
3. Upgrades `pip`, `wheel`, and `setuptools` via `.venv\Scripts\python.exe`.
4. Installs `requirements.txt`, `requirements-dev.txt`, and editable package (`pip install -e .`).
5. Verifies package import and outputs completion banner.

### Windows Command Prompt (`setup.bat`)

```cmd
setup.bat
```

Designed for traditional Windows `cmd.exe` environments, using delayed environment variable expansion (`!ERRORLEVEL!`) and `%~dp0` directory resolution.

---

## 2. Server Execution / Run Scripts

The execution scripts provide single-command startup of the RecipeBoxer FastAPI application with automatic environment bootstrapping, configurable defaults, and clean signal handling.

### macOS & Linux (`run.sh`)

```bash
chmod +x run.sh

# Launch with defaults (127.0.0.1:8000)
./run.sh

# Launch with custom host, port, and log level
./run.sh --host 0.0.0.0 --port 9000 --log-level debug

# Launch in hot-reload development mode
./run.sh --reload

# Show CLI help
./run.sh --help
```

### Windows PowerShell (`run.ps1`)

```powershell
# Launch with defaults
.\run.ps1

# Launch with named parameters
.\run.ps1 -HostAddress 0.0.0.0 -Port 9000 -LogLevel debug

# Launch in hot-reload mode
.\run.ps1 -Reload

# Display help
.\run.ps1 -Help
```

### Windows Command Prompt (`run.bat`)

```cmd
run.bat --host 0.0.0.0 --port 9000 --log-level info
```

### Supported CLI Options

| Option | Short | Type | Default | Description |
|---|---|---|---|---|
| `--host` | | `str` | `127.0.0.1` | Bind address for FastAPI ASGI server. |
| `--port` | | `int` | `8000` | Port number to bind. |
| `--log-level` | | `str` | `info` | Logging verbosity (`critical`, `error`, `warning`, `info`, `debug`, `trace`). |
| `--reload` | | `flag` | `False` | Enable auto-reload on code change (ignored in frozen binary). |
| `--version` | `-V`, `-v` | `flag` | | Output `RecipeBoxer <version>` and exit 0. |
| `--help` | `-h` | `flag` | | Output help message and exit 0. |

### Environment Variables

The execution scripts honor and propagate the following environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `RECIPEBOXER_API_HOST` | `127.0.0.1` | Server host IP. |
| `RECIPEBOXER_API_PORT` | `8000` | Server TCP port. |
| `RECIPEBOXER_DEBUG` | `false` | Enable verbose debug output. |
| `RECIPEBOXER_CACHE_DIR` | `.cache/recipeboxer` | Directory for SQLite WAL cache and temporary artifacts. |
| `RECIPEBOXER_EXPORT_DIR` | `exports` | Directory for exported Plan To Eat CSV files. |
| `GEMINI_API_KEY` | *(optional)* | Google Gemini API key for live multimodal extraction. |
| `RECIPEBOXER_MODEL_FAST` | `gemini-2.5-flash` | Multimodal model for visual fingerprinting and screening. |
| `RECIPEBOXER_MODEL_REASONING` | `gemini-2.5-pro` | Model for semantic correlation and complex recipe extraction. |

### Graceful Termination

- **POSIX (macOS/Linux)**: Traps `SIGINT` (Ctrl+C) and `SIGTERM` to allow Uvicorn lifespan shutdown handlers to flush SQLite caches and close background threads.
- **Windows (PowerShell / CMD)**: Catches `PipelineStoppedException` and keyboard interrupt events to ensure clean termination with exit code 0.

---

## 3. Build & Packaging

Build scripts compile RecipeBoxer into standalone single-file executables (via PyInstaller) and build standard PEP 517/621 wheel and sdist distributions.

### macOS & Linux (`build.sh`)

```bash
chmod +x build.sh

# Build all artifacts (wheels + standalone binary)
./build.sh --all

# Build only wheels and source distribution
./build.sh --wheel

# Build only standalone PyInstaller binary
./build.sh --binary

# Clean previous build/ and dist/ artifacts before building
./build.sh --clean --all

# View build help
./build.sh --help
```

### Windows PowerShell (`build.ps1`)

```powershell
# Build all artifacts
.\build.ps1 -All

# Build only wheels
.\build.ps1 -Wheel

# Build only standalone .exe binary
.\build.ps1 -Binary

# Clean and build
.\build.ps1 -Clean -All

# View help
.\build.ps1 -Help
```

### Output Artifacts in `dist/`

- **Standalone Executable**:
  - `dist/recipeboxer` (macOS Darwin / Linux ELF)
  - `dist/recipeboxer.exe` (Windows PE)
- **Standard Distributions**:
  - `dist/recipeboxer-0.1.0-py3-none-any.whl` (Python Wheel)
  - `dist/recipeboxer-0.1.0.tar.gz` (Source distribution)

### Standalone Binary Features

The standalone binary packaged via `recipeboxer.spec`:
- Contains all required hidden imports (`uvicorn`, `fastapi`, `starlette`, `pydantic`, `google.genai`, `google.antigravity`, `multipart`, `PIL`, `sqlite3`).
- Bundles internal package metadata and default assets.
- Supports `multiprocessing.freeze_support()`.
- Runs without requiring a pre-installed Python interpreter or virtual environment.
- Supports CLI flags: `./dist/recipeboxer --version`, `./dist/recipeboxer --help`, `./dist/recipeboxer --port 8080`.

---

## 4. Test Automation

The test automation scripts run the full pytest test suite in the isolated `.venv` environment across platforms.

### macOS & Linux (`test.sh`)

```bash
chmod +x test.sh

# Run entire test suite (395+ tests)
./test.sh

# Pass arguments directly to pytest
./test.sh -v
./test.sh tests/test_cli.py
./test.sh -k "test_setup"
./test.sh -m "not e2e"
./test.sh --maxfail=1 -q
```

**Key behaviors of `test.sh`**:
- Pre-flight check: Verifies `.venv/bin/pytest` exists. If missing, automatically runs `./setup.sh`.
- Transparent argument passthrough: Forwards all CLI arguments (`"$@"`) to pytest.
- Exit code propagation: Preserves exact pytest return code (0 for pass, non-zero for test failures or errors).

### Windows PowerShell (`test.ps1`)

```powershell
# Run full test suite
.\test.ps1

# Pass arguments to pytest
.\test.ps1 -v
.\test.ps1 tests/test_cli.py
.\test.ps1 -k "test_setup"
```

**Key behaviors of `test.ps1`**:
- Pre-flight check: Verifies `.venv\Scripts\pytest.exe` (or `.venv/bin/pytest` in pwsh). If missing, automatically runs `.\setup.ps1`.
- Transparent argument forwarding: Uses `$RemainingArgs` to pass flags directly to pytest.
- Exit code propagation: Propagates `$LASTEXITCODE` cleanly to caller.

---

## 5. Troubleshooting & Platform Notes

### 1. Python Version Incompatibility
- **Issue**: `ERROR: Python 3.10 or higher is required but not found in PATH.`
- **Solution**: Install Python 3.10, 3.11, 3.12, 3.13, or 3.14 from [python.org](https://www.python.org/downloads/). On Windows, ensure the `py` launcher or `python` is added to your system `PATH`. On macOS, use `brew install python@3.12`.

### 2. POSIX Permission Denied
- **Issue**: `bash: ./setup.sh: Permission denied`
- **Solution**: Make scripts executable with `chmod +x setup.sh run.sh build.sh test.sh`.

### 3. Windows PowerShell Script Execution Policy
- **Issue**: `File ...\setup.ps1 cannot be loaded because running scripts is disabled on this system.`
- **Solution**: Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in your PowerShell session before running `.ps1` scripts.

### 4. Line Ending Issues (CRLF vs LF)
- **Issue**: `\r: command not found` on Linux/macOS.
- **Solution**: All shell scripts (`*.sh`) use Unix LF line endings. Configure git with `git config core.autocrlf input` or use `.gitattributes`.

### 5. Running in Offline Mock Mode vs Live Mode
- By default, all unit and E2E tests run in offline simulated mock mode without making external network calls.
- To enable live Google Gemini API calls during extraction, set the environment variable:
  ```bash
  export GEMINI_API_KEY="your-api-key-here"
  ```
