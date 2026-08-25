# Original User Request

## Initial Request — 2026-08-24T21:58:03Z

Create cross-platform setup, build, binary packaging, and execution scripts for RecipeBoxer supporting macOS, Linux, and Windows environments.

Working directory: /Users/rrjefferson/.gemini/antigravity-insiders/worktrees/recipeboxer/init_recipeboxer_backend_architecture
Integrity mode: development

## Requirements

### R1. Cross-Platform Environment Setup Scripts
Provide automated setup scripts (`setup.sh` for macOS/Linux and `setup.ps1` / `setup.bat` for Windows) that:
- Detect or verify Python 3.10+ installation.
- Create and manage a local isolated virtual environment (`.venv`).
- Upgrade package managers (`pip`, `wheel`, `setuptools`) and install all production and development dependencies (`requirements.txt`, `requirements-dev.txt`, editable project install).

### R2. Python Binary Pre-Building & Packaging
Provide build scripts (`build.sh` / `build.ps1`) that:
- Compile bytecode and package RecipeBoxer into standalone deployable binary artifacts (e.g., via PyInstaller / standalone distribution executable) for macOS (Darwin), Linux (ELF), and Windows (`.exe`).
- Build standard distribution wheels and sdist packages into `dist/`.
- Ensure non-code assets, schemas, prompts, and default configs are properly bundled without runtime path breakages.

### R3. Cross-Platform Execution / Run Scripts
Provide one-step execution scripts (`run.sh` for macOS/Linux and `run.ps1` / `run.bat` for Windows) that:
- Check for existing virtual environment / build artifacts (triggering setup/build if missing).
- Set default environment variables (e.g., host `127.0.0.1`, port `8000`, log levels, app data dirs).
- Launch the RecipeBoxer FastAPI backend / Antigravity orchestration server with graceful shutdown handling (SIGINT/SIGTERM on POSIX, Ctrl+C on Windows).

### R4. Automated Testing & Verification Suite
- Include a test script (`test.sh` / `test.ps1`) that runs the full test suite (`pytest`) inside the isolated virtual environment across platforms.
- Ensure all scripts have executable permissions on POSIX systems (`chmod +x`) and proper syntax validation for PowerShell / cmd.exe on Windows.

## Acceptance Criteria

### Environment Setup
- [ ] Running `setup.sh` on macOS/Linux and `setup.ps1` / `setup.bat` on Windows successfully bootstraps `.venv`, installs dependencies, and verifies Python compatibility.

### Build & Binary Packaging
- [ ] Running `build.sh` on macOS/Linux and `build.ps1` on Windows creates standalone executable binaries in `dist/` and compiles package distributions without packaging errors.
- [ ] Packaged binaries execute and expose help/version flags and start the server correctly without missing module errors.

### Server Execution
- [ ] Running `run.sh` / `run.ps1` starts the FastAPI backend server on the specified port, serves health/status endpoints, and terminates cleanly upon interrupt.
- [ ] Cross-platform path handling (e.g. path separators, environment variables, virtualenv activation paths) functions identically on POSIX and Windows.

### Test Automation
- [ ] `test.sh` / `test.ps1` executes the 395+ test suite cleanly inside the configured environment.
- [ ] Documentation (`README.md` or `SCRIPTS.md`) clearly details prerequisites and usage for macOS, Linux, and Windows users.
