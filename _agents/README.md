# RecipeBoxer Agent Onboarding & Context Index

Welcome to the **RecipeBoxer** developer and AI agent guidance repository. This directory (`_agents/`) provides authoritative, comprehensive documentation on system architecture, non-negotiable invariants, testing standards, and extension mechanisms for both autonomous AI agents and human software engineers.

---

## Mission & System Overview

**RecipeBoxer** is an intelligent recipe card digitization and Human-in-the-Loop (HITL) correlation platform designed to convert physical, vintage, handwritten, and multi-sided recipe card collections into structured digital recipes and Plan To Eat CSV exports.

The platform combines:
1. **Multi-Modal Visual Fingerprinting**: Physical substrate analysis (CIELAB color, ruling patterns, card stock, ink medium, stain bleed-through) using Google Gemini 2.5 Flash.
2. **Open-Ended N-Part Sequence Correlation**: Fast physical heuristic screening ($\Delta E$ color distance, scan proximity decay, ruling, horizontally mirrored stain bleed-through) followed by Gemini multimodal semantic continuity reasoning and maximal DAG sequence path chaining for recipes of arbitrary length ($N \ge 1$).
3. **Asynchronous HITL Review Pipeline**: A non-blocking cooperative async barrier where ambiguous card sequences pause for human verification, candidate replacement, sequence reordering ("Part 1 ... N"), splitting, or rejection.
4. **Structured Recipe Transcription**: Multimodal transcription into validated Pydantic `RecipeSchema` objects with standardized numbering, clean ingredients, and tag deduplication.
5. **Multi-Format Exporters**: RFC 4180-compliant 9-column CSV for Plan To Eat, Schema.org Recipe JSON-LD with ISO 8601 duration strings, and standard JSON formats.
6. **Embedded React 18+ TypeScript SPA**: A modern single-page application built with Vite and Tailwind CSS, featuring real-time Server-Sent Events (SSE), split-screen recipe editing, an interactive pan/zoom/filter card canvas, and static distribution directly inside FastAPI.

---

## Core Documentation Index

| Document | Purpose & Key Topics |
|---|---|
| [**`ARCHITECTURE.md`**](ARCHITECTURE.md) | **System Architecture & Deep Dive**: Google Antigravity SDK orchestrator (`RecipeBoxerAgent`, `PipelineSession`), 6-stage lifecycle, dual SQLite WAL cache (`FingerprintCache` & `SessionStore`), 2-stage correlation engine mathematics, FastAPI REST + SSE protocol, React 18+ SPA architecture, custom hooks (`useSSE`, `useKeyboard`, `useImageViewer`, `useRecipeForm`, `useTheme`), and component hierarchy. |
| [**`INVARIANTS.md`**](INVARIANTS.md) | **Non-Negotiable Guardrails**: SQLite WAL concurrency & `threading.RLock()`, Plan To Eat RFC 4180 CSV escaping (9 columns, CRLF `\r\n`, `""` quoting), HITL async barrier semantics, keyboard focus trapping, Vite SPA static asset path contracts (`recipeboxer/api/static`), secure card image streaming, and ingestion throttling invariants. |
| [**`TESTING_GUIDELINES.md`**](TESTING_GUIDELINES.md) | **Testing Standards & QA**: The 4-tier testing architecture (Tier 1 Feature Coverage, Tier 2 Boundary Cases, Tier 3 Pairwise Combinations, Tier 4 Real-World Scenarios) + Tier 5 Adversarial Hardening. Pytest test suites, core fixtures (`MockGeminiClient`, `synthetic_card_deck`), Vitest frontend test suites, setup mocks, and test execution commands. |
| [**`EXTENDING_RECIPEBOXER.md`**](EXTENDING_RECIPEBOXER.md) | **Developer Extension Playbooks**: Step-by-step guides with complete code examples for adding external source providers (Google Photos, S3), implementing custom prompt branches / LLM customization, and creating new export format serializers (Markdown, Cooklang). |
| [**`../ROADMAP.md`**](../ROADMAP.md) | **Canonical Milestone Roadmap**: Shared roadmap tracking milestones M1 to M15, standardized status tags (`[x] Completed`, `[~] In Progress`, `[ ] Planned`), future enhancement backlog (Phase 9), and roadmap update conventions. |
| [**`../README.md`**](../README.md) | **Repository Root Overview**: High-level project summary, prerequisites, CLI usage, environment variables, test commands, and quick-start instructions. |

---

## Repository Directory Map

```
.
├── pyproject.toml              # Python PEP 517/621 package metadata, dependencies & pytest config
├── recipeboxer.spec            # PyInstaller standalone binary packaging specification
├── setup.sh / setup.ps1 / .bat # Cross-platform environment bootstrap scripts
├── run.sh / run.ps1 / .bat     # Cross-platform application launch scripts
├── build.sh / build.ps1        # Cross-platform production build and packaging scripts
├── test.sh / test.ps1          # Cross-platform automated test runners (1,130+ Python tests)
├── README.md                   # User-facing project overview and quick start
├── ROADMAP.md                  # Canonical project milestone and enhancement roadmap
├── SCRIPTS.md                  # Comprehensive script usage and troubleshooting guide
├── _agents/                    # Authoritative AI agent & contributor guidance directory
│   ├── README.md               # This index and onboarding guide
│   ├── ARCHITECTURE.md         # Full backend & frontend architectural breakdown
│   ├── INVARIANTS.md           # Critical system invariants and guardrails
│   ├── TESTING_GUIDELINES.md   # 4-tier testing pyramid, pytest & Vitest guides
│   └── EXTENDING_RECIPEBOXER.md# Playbooks for source providers, prompts, and exporters
├── recipeboxer/                # Core Python backend package
│   ├── __init__.py             # Package version (__version__ = "0.1.0")
│   ├── cli.py                  # CLI parser, uvicorn startup, freeze_support
│   ├── config.py               # Pydantic Settings V2 configuration management
│   ├── models/                 # Domain schemas (ScannedCard, Fingerprint, RecipeSchema, etc.)
│   ├── storage/                # SQLite WAL caches (FingerprintCache, SessionStore)
│   ├── vision/                 # Visual fingerprinting, heuristics, and CIELAB color math
│   ├── ingestion/              # Card scanning, validation, and parallel/batch runners
│   ├── correlation/            # Directed sequence transition verifier & path chaining optimizer
│   ├── extraction/             # Multimodal recipe transcription into RecipeSchema
│   ├── exporter/               # Plan To Eat CSV, Schema.org JSON-LD, and JSON serializers
│   └── api/                    # FastAPI REST routes, SSE events, and SPA static hosting
│       └── static/             # Vite production build distribution (index.html, assets/)
├── frontend/                   # React 18+ TypeScript SPA source
│   ├── package.json            # NPM dependencies (React 18, TypeScript, Tailwind, Lucide, Vitest)
│   ├── vite.config.ts          # Vite build config with outDir: recipeboxer/api/static
│   ├── src/
│   │   ├── App.tsx             # Root container, SSE listener, view router
│   │   ├── components/         # Ingestion, HITL Workspace, Viewer, Recipe Editor, Export
│   │   ├── hooks/              # Custom hooks (useSSE, useKeyboard, useImageViewer, etc.)
│   │   ├── utils/              # API fetch client and client-side formatters/exporters
│   │   └── types/              # TypeScript interfaces mirroring backend domain models
│   └── tests/                  # Vitest frontend unit and component test suites (102 tests)
└── tests/                      # Python pytest test suites (1,130 tests across 29 modules)
    ├── conftest.py             # Test harness, fixtures, mock Gemini client, synthetic decks
    ├── test_e2e_4tier_suite.py # 165 comprehensive tests covering all 15 project features
    ├── test_tier5_adversarial_coverage.py # Adversarial stress, concurrency, and fuzzing tests
    └── e2e/                    # End-to-end integration and pipeline lifecycle tests
```

---

## Getting Started: Daily Agent Commands

### 1. Environment Bootstrap
```bash
# POSIX (macOS / Linux)
./setup.sh

# Windows PowerShell
.\setup.ps1
```

### 2. Running Automated Tests (1,788 Passing Tests)
```bash
# Run all Python backend tests (1,530 tests across Tiers 1–5 and challenger suites)
./test.sh

# Run all Frontend Vitest tests (258 tests across 23 suites)
cd frontend && npm test -- --run

# Run both in sequence
./test.sh && cd frontend && npm test -- --run
```

### 3. Launching the Local Application Server
```bash
# Start FastAPI backend and serve static SPA on http://127.0.0.1:8000
./run.sh

# Start backend in development mode with auto-reload
recipeboxer --reload --host 127.0.0.1 --port 8000
```

### 4. Compiling Frontend Assets for Production
```bash
# Builds React SPA directly into recipeboxer/api/static/
cd frontend && npm run build
```

---

## Agent Rules of Engagement

When operating in this codebase as an autonomous AI agent or contributor, you must strictly observe the following rules:

1. **Integrity Mandate — Zero Hardcoding**:
   - All code modifications must implement genuine business logic.
   - Never fabricate test results, mock outputs within production code paths, or bypass validation checks.
   - Offline tests must use dedicated fixtures from `tests/conftest.py` (such as `MockGeminiClient` or `synthetic_card_deck`), never hardcoded bypasses in `recipeboxer/`.

2. **Regression Gate — 100% Pass Rate**:
   - The repository maintains **1,232 passing tests** (1,130 Python pytest tests + 102 Frontend Vitest tests).
   - Zero test failures or regressions are permitted. After every code modification, run `./test.sh` and `cd frontend && npm test -- --run` to confirm all tests pass.

3. **Respect System Invariants**:
   - Read and adhere to [**`_agents/INVARIANTS.md`**](INVARIANTS.md) before modifying storage, API routing, CSV serialization, HITL barrier synchronization, or frontend event handling.

4. **Minimal Change Principle**:
   - Modify only the files strictly required for your assignment.
   - Do not perform unrelated refactoring or rewrite untouched logic.
   - Always inspect files (`view_file`) prior to making edits.

5. **Roadmap Discipline**:
   - When completing features or milestones, update [**`ROADMAP.md`**](../ROADMAP.md) following the status tagging conventions (`[x] Completed`, `[~] In Progress`, `[ ] Planned`).
