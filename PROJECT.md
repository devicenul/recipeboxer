# Project: RecipeBoxer Cross-Platform Tooling & Packaging

## Architecture
RecipeBoxer provides an intelligent recipe card digitization and HITL correlation engine. To enable seamless execution across macOS (Darwin), Linux (POSIX), and Windows environments, the cross-platform architecture comprises:
1. **Core Package & CLI Entry Points**:
   - PEP 517/621 `pyproject.toml` with `setuptools` build backend, entry point `recipeboxer = recipeboxer.cli:main`, and explicit package discovery (`recipeboxer*`).
   - Unified CLI in `recipeboxer/cli.py` and `recipeboxer/__main__.py` with argument parsing (`--help`, `--version`, `--host`, `--port`, `--log-level`, `--reload`), `multiprocessing.freeze_support()`, and direct ASGI app runner.
2. **Environment Setup Subsystem (R1)**:
   - `setup.sh` (POSIX macOS/Linux) and `setup.ps1` / `setup.bat` (Windows) detecting Python 3.10+, bootstrapping isolated `.venv`, upgrading build tools (`pip`, `wheel`, `setuptools`), installing production and dev dependencies, and editable project install (`pip install -e .`).
3. **Execution Subsystem (R3)**:
   - `run.sh` (POSIX) and `run.ps1` / `run.bat` (Windows) ensuring `.venv` presence, configuring default/override environment variables, launching uvicorn ASGI server with `create_app()`, and trapping SIGINT/SIGTERM/Ctrl+C for graceful shutdown.
4. **Binary & Distribution Packaging Subsystem (R2)**:
   - PyInstaller specification `recipeboxer.spec` with complete hidden imports (Uvicorn, Starlette, FastAPI, Pydantic V2, Google GenAI/Antigravity, Multipart), package data bundling, single-file binary configuration.
   - `build.sh` (POSIX) and `build.ps1` (Windows) compiling standalone binaries (`dist/recipeboxer` / `dist/recipeboxer.exe`) and standard wheels/sdist (`dist/*.whl`, `dist/*.tar.gz`), with clean, flag verification, and permission handling.
5. **Testing & Verification Subsystem (R4)**:
   - `test.sh` (POSIX) and `test.ps1` (Windows) with argument passthrough to pytest running 395+ tests in `.venv`.
   - Comprehensive documentation in `SCRIPTS.md` and updated `README.md`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | `pyproject-metadata` | PEP 517/621 build system, project metadata, package discovery, and dependencies | M1 | Survey (Exp 1, Exp 3, Miner 2) |
| 2 | `cli-entrypoint` | `recipeboxer/cli.py` and `recipeboxer/__main__.py` with `--version`, `--help`, `--host`, `--port`, freeze support | M1 | Survey (Exp 1, Exp 3) |
| 3 | `posix-setup-script` | `setup.sh` Python 3.10+ probe, `.venv` bootstrap, pip/wheel/setuptools upgrade, reqs install, editable install | M2 | Survey (Miner 2 §R1) |
| 4 | `windows-setup-powershell` | `setup.ps1` Python 3.10+ check, `.venv` creation, dependency installation, editable install | M2 | Survey (Miner 2 §R1) |
| 5 | `windows-setup-cmd` | `setup.bat` batch file launcher for Windows Command Prompt environments | M2 | Survey (Miner 2 §R1) |
| 6 | `posix-run-script` | `run.sh` with venv auto-bootstrap, env vars defaults, uvicorn startup, and SIGINT/SIGTERM trap | M3 | Survey (Miner 2 §R3) |
| 7 | `windows-run-powershell` | `run.ps1` with venv check, env vars defaults, server launch, and Ctrl+C interrupt handling | M3 | Survey (Miner 2 §R3) |
| 8 | `windows-run-cmd` | `run.bat` batch wrapper for Windows server execution | M3 | Survey (Miner 2 §R3) |
| 9 | `pyinstaller-spec` | `recipeboxer.spec` with all uvicorn/fastapi hidden imports, datas, and single-file executable config | M4 | Survey (Exp 3 §R2) |
| 10 | `posix-build-script` | `build.sh` supporting `--all`, `--wheel`, `--binary`, `--clean`, `--help`, artifact verification | M4 | Survey (Exp 3 §R2) |
| 11 | `windows-build-script` | `build.ps1` supporting `--All`, `--Wheel`, `--Binary`, `--Clean`, `--Help`, artifact verification | M4 | Survey (Exp 3 §R2) |
| 12 | `posix-test-script` | `test.sh` executing pytest inside `.venv` with CLI argument passthrough and exit code propagation | M5 | Survey (Miner 2 §R4) |
| 13 | `windows-test-script` | `test.ps1` PowerShell pytest runner with argument passthrough | M5 | Survey (Miner 2 §R4) |
| 14 | `scripts-documentation` | `SCRIPTS.md` & `README.md` detailing cross-platform setup, build, run, test workflows | M5 | Survey (Exp 1, Miner 2 §AC) |
| 15 | `e2e-crossplatform-verification` | Full E2E test execution (Tiers 1-4) and adversarial verification (Tier 5) on all scripts/binaries | M6 | Survey & Project Pattern |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Package Metadata & CLI Entry Points | `pyproject.toml`, `recipeboxer/cli.py`, `recipeboxer/__main__.py`, CLI unit tests | none | DONE |
| M2 | Cross-Platform Setup Scripts | `setup.sh`, `setup.ps1`, `setup.bat`, executable permissions | M1 | DONE |
| M3 | Cross-Platform Run Scripts | `run.sh`, `run.ps1`, `run.bat`, graceful shutdown, env var resolution | M1, M2 | DONE |
| M4 | Standalone Binary & Wheel Packaging | `recipeboxer.spec`, `build.sh`, `build.ps1`, dist verification | M1, M2 | DONE |
| M5 | Automated Test Scripts & Documentation | `test.sh`, `test.ps1`, `SCRIPTS.md`, `README.md` updates | M1, M2, M3, M4 | DONE |
| M6 | Final Milestone: Full Verification & Hardening | Pass 100% E2E tests across all scripts/binaries + Adversarial Tier 5 | M1-M5 | DONE |

## Interface Contracts

### CLI Interface (`recipeboxer.cli:main`)
- Binary / Command: `recipeboxer` (or `python -m recipeboxer`)
- Arguments:
  - `-V`, `--version`: Prints `RecipeBoxer <version>` and exits 0.
  - `-h`, `--help`: Prints command line help with descriptions and exits 0.
  - `--host <str>`: Server bind address (default: `127.0.0.1` or `RECIPEBOXER_API_HOST`).
  - `--port <int>`: Server bind port (default: `8000` or `RECIPEBOXER_API_PORT`).
  - `--log-level <str>`: `critical` | `error` | `warning` | `info` | `debug` | `trace` (default: `info`).
  - `--reload`: Enable development hot-reload (ignored in frozen PyInstaller binary).
- Exit Codes: 0 on normal exit / signal interrupt, 1 on unhandled exception, 2 on invalid CLI syntax.

### Setup Scripts (`setup.sh`, `setup.ps1`, `setup.bat`)
- Invocation: `./setup.sh`, `.\setup.ps1`, `setup.bat`
- Output: `.venv/` fully populated with upgraded `pip`, `wheel`, `setuptools`, dependencies from `requirements.txt`, `requirements-dev.txt`, and `recipeboxer` installed in editable mode (`-e .`).
- Exit Code: 0 on success, non-zero on failure (e.g. Python < 3.10 or pip installation failure).

### Run Scripts (`run.sh`, `run.ps1`, `run.bat`)
- Invocation: `./run.sh [OPTIONS]`, `.\run.ps1 [OPTIONS]`, `run.bat [OPTIONS]`
- Environment: Respects `RECIPEBOXER_API_HOST`, `RECIPEBOXER_API_PORT`, `RECIPEBOXER_DEBUG`, `RECIPEBOXER_CACHE_DIR`, `RECIPEBOXER_EXPORT_DIR`.
- Exit Behavior: Exits 0 on SIGINT/SIGTERM / Ctrl+C after uvicorn lifespan cleanup.

### Build Scripts (`build.sh`, `build.ps1`)
- Invocation: `./build.sh [--all | --wheel | --binary | --clean | --help]`, `.\build.ps1 [-All | -Wheel | -Binary | -Clean | -Help]`
- Artifacts:
  - `dist/recipeboxer` (macOS/Linux) or `dist/recipeboxer.exe` (Windows)
  - `dist/recipeboxer-0.1.0-py3-none-any.whl`
  - `dist/recipeboxer-0.1.0.tar.gz`
- Exit Code: 0 on successful compilation and verification.

### Test Scripts (`test.sh`, `test.ps1`)
- Invocation: `./test.sh [PYTEST_ARGS]`, `.\test.ps1 [PYTEST_ARGS]`
- Behavior: Passes all CLI arguments directly to `.venv/bin/pytest` or `.venv\Scripts\pytest.exe`.
- Exit Code: Returns exact pytest return code (0 for pass).

## Code Layout
- `pyproject.toml`: Root package and build metadata
- `recipeboxer.spec`: PyInstaller standalone specification
- `recipeboxer/cli.py`: Unified CLI entry point
- `recipeboxer/__main__.py`: Module entry point (`python -m recipeboxer`)
- `setup.sh`, `setup.ps1`, `setup.bat`: Setup bootstrap scripts at root
- `run.sh`, `run.ps1`, `run.bat`: Execution scripts at root
- `build.sh`, `build.ps1`: Build & packaging scripts at root
- `test.sh`, `test.ps1`: Test execution scripts at root
- `tests/test_cli.py`: Unit and integration tests for CLI entry points
- `tests/test_scripts.py`: Cross-platform script validation tests
- `SCRIPTS.md`: Detailed cross-platform usage guide
