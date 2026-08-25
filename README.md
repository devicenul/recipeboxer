# RecipeBoxer

Intelligent recipe card digitization and HITL (Human-in-the-Loop) correlation engine for converting vintage, handwritten, and multi-sided recipe card collections into structured digital recipes and Plan To Eat CSV exports.

---

## Key Features

- **Multi-Modal Visual Fingerprinting**: Captures physical card features (paper hue, ruling lines, card stock, ink color, stain bleed-through) using Gemini 2.5 Flash.
- **Two-Stage Card Correlation**: Combines color Delta-E and physical heuristic screening with deep semantic continuity reasoning to pair two-sided cards.
- **Asynchronous HITL Review**: Pauses on low-confidence pairings for human operator inspection, manual re-pairing, or card rejection via FastAPI endpoints.
- **Structured Recipe Extraction & RFC 4180 CSV Export**: Extracts ingredients, directions, prep/cook times, yields, and tags into structured JSON and standard Plan To Eat CSV format.
- **Cross-Platform Tooling**: Native bootstrap, execution, packaging, and testing scripts for macOS, Linux, and Windows.

---

## Prerequisites

- **Python 3.10+** (Python 3.10, 3.11, 3.12, 3.13, 3.14 supported).
- *(Optional)* **Google Gemini API Key** for live multimodal AI processing (`GEMINI_API_KEY`). All tests and offline operations run with simulated mock fixtures when no key is supplied.

---

## Quick Start

### macOS & Linux (POSIX)

```bash
# 1. Bootstrap environment and install dependencies
./setup.sh

# 2. Run the full test suite (395+ tests)
./test.sh

# 3. Start the RecipeBoxer API server
./run.sh

# 4. (Optional) Build standalone binary and wheel packages
./build.sh --all
```

### Windows (PowerShell)

```powershell
# 1. Bootstrap environment and install dependencies
.\setup.ps1

# 2. Run the full test suite
.\test.ps1

# 3. Start the RecipeBoxer API server
.\run.ps1

# 4. (Optional) Build standalone executable and wheels
.\build.ps1 -All
```

### Windows (Command Prompt)

```cmd
# Bootstrap environment
setup.bat

# Start API server
run.bat
```

For complete documentation on all scripts, flags, and platform-specific configurations, see [SCRIPTS.md](SCRIPTS.md).

---

## Command Line Interface

Once installed or running via `run.sh` / `run.ps1`:

```bash
# Display version
recipeboxer --version

# Display help and options
recipeboxer --help

# Start server on custom host and port
recipeboxer --host 0.0.0.0 --port 8080 --log-level info

# Enable auto-reload in development
recipeboxer --reload
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `RECIPEBOXER_API_HOST` | `127.0.0.1` | Server bind host address |
| `RECIPEBOXER_API_PORT` | `8000` | Server bind port |
| `RECIPEBOXER_DEBUG` | `false` | Enable debug logging |
| `RECIPEBOXER_CACHE_DIR` | `.cache/recipeboxer` | SQLite cache & thumbnail storage directory |
| `RECIPEBOXER_EXPORT_DIR` | `exports` | Destination directory for exported CSV files |
| `GEMINI_API_KEY` | *(optional)* | Google Gemini API key for live multimodal processing |
| `RECIPEBOXER_MODEL_FAST` | `gemini-2.5-flash` | Fast model for screening and visual fingerprinting |
| `RECIPEBOXER_MODEL_REASONING` | `gemini-2.5-pro` | Reasoning model for semantic pairing and extraction |

---

## Testing

Run the automated test suite across all modules and E2E tiers:

```bash
# Run via POSIX test script
./test.sh

# Run via Windows PowerShell test script
.\test.ps1

# Or run pytest directly with specific flags
pytest -v tests/
pytest -v tests/e2e/test_e2e_pipeline.py
pytest -v tests/test_scripts.py
```

---

## Project Structure

```
.
├── pyproject.toml              # PEP 517/621 package metadata & build config
├── recipeboxer.spec            # PyInstaller standalone binary specification
├── setup.sh / setup.ps1 / .bat # Cross-platform environment bootstrap scripts
├── run.sh / run.ps1 / .bat     # Cross-platform application execution scripts
├── build.sh / build.ps1        # Cross-platform packaging & binary build scripts
├── test.sh / test.ps1          # Cross-platform test execution scripts
├── SCRIPTS.md                  # Comprehensive script usage and troubleshooting guide
├── recipeboxer/                # Core application package
│   ├── cli.py                  # CLI entry point
│   ├── api/                    # FastAPI routers and app factory
│   ├── extraction/             # Multimodal recipe extraction
│   ├── correlation/            # Visual & semantic card correlation
│   ├── ingestion/              # Parallel & batch card image ingestion
│   ├── exporter/               # Plan To Eat CSV export engine
│   ├── models/                 # Pydantic data schemas
│   └── storage/                # SQLite WAL cache & state management
└── tests/                      # Unit, integration, E2E, and script tests
```

---

## License

This project is licensed under the terms described in the [LICENSE](LICENSE) file.
