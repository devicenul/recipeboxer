# RecipeBoxer

Intelligent recipe card digitization and HITL (Human-in-the-Loop) correlation engine for converting vintage, handwritten, and multi-sided recipe card collections into structured digital recipes and Plan To Eat CSV exports.

---

## Key Features

- **Multi-Modal Visual Fingerprinting**: Captures physical card features (paper hue via CIELAB $\Delta E$, ruling lines, card stock, ink color, stain bleed-through) using Gemini 2.5 Flash.
- **Two-Stage Card Correlation**: Combines color Delta-E and physical heuristic screening with deep semantic continuity reasoning and greedy bipartite assignment to pair two-sided cards.
- **Asynchronous HITL Review**: Pauses on low-confidence pairings for human operator inspection, candidate replacement via slide-out drawer, 3+ card multi-part grouping, or card rejection.
- **Structured Recipe Extraction & RFC 4180 CSV Export**: Extracts ingredients, directions, prep/cook times, yields, and tags into structured JSON, Schema.org Recipe JSON-LD, and standard Plan To Eat CSV format.
- **Embedded React 18+ TypeScript SPA**: Single Page Application built with Vite and Tailwind CSS, featuring an interactive pan/zoom/filter card canvas and real-time Server-Sent Events (SSE) telemetry.
- **Cross-Platform Tooling**: Native bootstrap, execution, packaging, and testing scripts for macOS, Linux, and Windows.

---

## Developer & Agent Documentation

For comprehensive system documentation, architecture specifications, non-negotiable invariants, and developer extension guides, visit the **`_agents/`** context hub:

- [**`_agents/README.md`**](_agents/README.md): Fast-orientation index and onboarding guide for AI agents and human contributors.
- [**`_agents/ARCHITECTURE.md`**](_agents/ARCHITECTURE.md): Comprehensive breakdown of the Python backend (Antigravity SDK orchestrator, SQLite WAL persistence, 2-stage correlation engine, FastAPI/SSE) and React 18+ SPA.
- [**`_agents/INVARIANTS.md`**](_agents/INVARIANTS.md): Non-negotiable architectural guardrails (SQLite WAL concurrency, Plan To Eat RFC 4180 CSV escaping, HITL async barrier semantics, Vite SPA static bundle contracts).
- [**`_agents/TESTING_GUIDELINES.md`**](_agents/TESTING_GUIDELINES.md): Testing philosophy, 4-tier test pyramid, pytest & Vitest testing guides, fixtures, and regression checklists.
- [**`_agents/EXTENDING_RECIPEBOXER.md`**](_agents/EXTENDING_RECIPEBOXER.md): Step-by-step developer extension guides with code examples for adding source providers, custom prompt branches, and export format serializers.
- [**`ROADMAP.md`**](ROADMAP.md): Canonical project roadmap tracking milestones M1 to M6 and future feature backlog.

---

## Prerequisites

- **Python 3.10+** (Python 3.10, 3.11, 3.12, 3.13, 3.14 supported).
- **Node.js 18+** & **npm** (for building and testing the React frontend).
- *(Optional)* **Google Gemini API Key** for live multimodal AI processing (`GEMINI_API_KEY`). All tests and offline operations run with simulated mock fixtures when no key is supplied.

---

## Quick Start

### macOS & Linux (POSIX)

```bash
# 1. Bootstrap environment and install backend + frontend dependencies
./setup.sh

# 2. Run the full test suite (910 passing tests: 836 Python + 74 Frontend)
./test.sh && cd frontend && npm test -- --run

# 3. Start the RecipeBoxer API server and embedded SPA
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

RecipeBoxer maintains **910 automated tests** (836 Python pytest tests + 74 Frontend Vitest tests) with a 100% pass rate:

```bash
# Run Python backend test suite (836 tests)
./test.sh

# Run Frontend Vitest test suite (74 tests)
cd frontend && npm test -- --run

# Run 4-Tier E2E Integration Suite directly
pytest tests/test_e2e_4tier_suite.py -v

# Run Tier 5 Adversarial Hardening Suite directly
pytest tests/test_tier5_adversarial_coverage.py -v
```

For detailed instructions on testing tiers, mock isolation, and authoring new tests, see [**`_agents/TESTING_GUIDELINES.md`**](_agents/TESTING_GUIDELINES.md).

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
├── README.md                   # Project overview and quick start
├── ROADMAP.md                  # Milestone tracking and enhancement backlog
├── SCRIPTS.md                  # Comprehensive script usage and troubleshooting guide
├── _agents/                    # Developer & AI agent guidance repository
│   ├── README.md               # Onboarding hub & directory index
│   ├── ARCHITECTURE.md         # System architecture & technical specification
│   ├── INVARIANTS.md           # Non-negotiable architectural guardrails
│   ├── TESTING_GUIDELINES.md   # Multi-tier testing guide & QA checklist
│   └── EXTENDING_RECIPEBOXER.md# Developer extension playbooks
├── recipeboxer/                # Core Python backend package
│   ├── cli.py                  # CLI entry point
│   ├── api/                    # FastAPI routers, SSE, and static SPA hosting
│   │   └── static/             # Vite production build distribution (index.html, assets/)
│   ├── agent/                  # Google Antigravity SDK orchestrator & session manager
│   ├── extraction/             # Multimodal recipe extraction
│   ├── correlation/            # Visual & semantic card correlation and optimizer
│   ├── ingestion/              # Parallel & batch card image ingestion
│   ├── exporter/               # Plan To Eat CSV, Schema.org JSON-LD, JSON exporters
│   ├── models/                 # Pydantic data schemas
│   ├── storage/                # SQLite WAL cache & session store
│   └── vision/                 # Visual fingerprinting, heuristics, and CIELAB color math
├── frontend/                   # React 18+ TypeScript Single Page Application
│   ├── src/                    # Components, custom hooks, pages, and utilities
│   └── tests/                  # Vitest component and unit test suites (74 tests)
└── tests/                      # Python pytest test suites (836 tests across 28 modules)
    ├── conftest.py             # Shared fixtures, MockGeminiClient, synthetic card decks
    ├── test_e2e_4tier_suite.py # 165 4-tier E2E feature tests
    └── test_tier5_adversarial_coverage.py # Adversarial hardening and concurrency tests
```

---

## License

This project is licensed under the terms described in the [LICENSE](LICENSE) file.
