# RecipeBoxer

**Intelligent recipe card digitization, visual AI pairing engine, and HITL (Human-in-the-Loop) correlation platform for vintage, handwritten, and multi-sided recipe collections.**

---

## Overview & Key Features

RecipeBoxer transforms disorganized shoe boxes, albums, and binders of vintage handwritten, typewritten, and clipping recipe cards into structured digital recipes, heirloom family cookbooks, and culinary manager formats.

### Core Capabilities

- **Multi-Modal Substrate Fingerprinting**: Extracts paper color centroids in CIELAB space ($\Delta E$), paper stock texture, ruled lines, ink characteristics, and horizontally mirrored stain bleed-through using Gemini 3.7 / 2.5 multimodal models.
- **Open-Ended N-Part Sequence Correlation Engine**:
  - *Stage 1 (Visual & Physical Screening)*: High-speed heuristic pruning based on CIELAB $\Delta E$, scan proximity decay, paper ruling, and stain symmetry.
  - *Stage 2 (Directed Semantic Reasoning & Sequence Chaining)*: Evaluates narrative continuity across card transitions and constructs a directed transition graph with maximal DAG path chaining to automatically correlate multi-card recipes ($N \ge 1$).
- **Asynchronous HITL (Human-in-the-Loop) Review**: Real-time Server-Sent Events (SSE) telemetry paired with a keyboard-driven Rapid Review Queue (`Space`, `S`, `B`, `X`, `Tab`), slide-out alternate candidate ranking drawer, and unified "Part 1 of N" sequence reordering, splitting, and merging.
- **Split-Screen Recipe Editor & Image Inspection**: Side-by-side verification with 50/50 split-screen layout, synchronized pan and zoom (up to 400%), 90° lossless rotation, and adaptive image enhancement filters (brightness, contrast, negative inversion) for faded pencil and aged cursive.
- **Pluggable Ingestion Sources**: Ingest from local filesystem folders, drag-and-drop multipart uploads, Google Photos albums with OAuth2 auto-refresh, and Cloud Drive / Object Storage (Google Drive, AWS S3, Google Cloud Storage).
- **9-Format Recipe Export Matrix**:
  1. **Plan To Eat CSV**: RFC 4180 compliant 9-column format with strict CRLF line terminators.
  2. **Schema.org Recipe JSON-LD**: Web standard structured recipe metadata (`@graph`, `HowToStep`, ISO 8601 durations).
  3. **Google Docs Archival Exporter**: Structured Google Docs API batch updates and downloadable HTML archives with embedded card scans.
  4. **Heirloom DOCX**: Formatted Microsoft Word cookbook document with title pages, metadata tables, and embedded scan photos.
  5. **Printable PDF**: High-resolution print-ready cookbook with custom typography, headers, and footer page numbering.
  6. **Obsidian Markdown**: YAML frontmatter and wikilink image embeds formatted for Obsidian Recipe View plugins.
  7. **Cooklang (`.cook`)**: Plain-text culinary markup with `@ingredient{}`, `#cookware{}`, and `~timer{}` step annotations.
  8. **Paprika (`.paprikarecipes`)**: Compressed gzip archives containing recipe JSON and base64-encoded card photos for 1-click Paprika app import.
  9. **Raw JSON**: Complete lossless `RecipeSchema` JSON array backup.
- **Portable Project State Persistence (`.recipeboxer` Archives)**: Portable, compressed project archive container storing full session manifests (card sequences, fingerprints, recipes, HITL review decisions, marginalia notes) alongside original card scans with content-addressed SHA-256 digests. Supports export-view "Save Project As..." and "Merge into Existing Project...".
- **Incremental Ingestion with Duplicate-Skipping**: Baseline archive selection during ingestion; streaming SHA-256 card hashing automatically skips previously processed cards during OCR, fingerprinting, and correlation, preserving API quotas while seamlessly merging baseline recipes.
- **Configurable Multi-Format Recipe Ordering & Grouping**: Interactive drag-and-drop recipe reordering with 5 categorical grouping presets (Course, Contributor, Tags, Alphabetical, Custom) generating formatted section divider pages and styled Tables of Contents with right-aligned dot leaders across DOCX, PDF, Google Docs, and Markdown cookbook exports.
- **"First Run" Interactive UI Guided Tour**: Accessible 6-step onboarding walkthrough overlay built directly into the React frontend with keyboard controls and localStorage persistence.
- **Cross-Platform Tooling & Standalone Distribution**: Native bootstrap, execution, testing, and PyInstaller packaging scripts for macOS, Linux, and Windows.

---

## Documentation

Complete documentation for RecipeBoxer is organized under the [`docs/`](docs/) directory:

- [**System Architecture** (`docs/ARCHITECTURE.md`)](docs/ARCHITECTURE.md): Complete pipeline design, LLM orchestration, `DeckBackground` likelihood ratios, path-cover optimization, failure models, Schema v2, and frontend UI contracts.
- [**Evaluation Framework & Honesty Rules** (`docs/EVALS.md`)](docs/EVALS.md): Anti-cheat invariants, benchmark tiers T0–T4, black-box runner, encrypted ground-truth vault, scoring protocols, verified pricing, and certified baselines.
- [**Test Infrastructure** (`docs/TEST_INFRA.md`)](docs/TEST_INFRA.md): Anti-cheat canaries, static AST invariant checks, hermetic API key isolation, `ScriptedLLMClient` test doubles, execution tiers, and test triage catalog.
- [**User Guide & Settings** (`docs/USER_GUIDE.md`)](docs/USER_GUIDE.md): Getting a Gemini API key, free vs. paid tier recommendations (paid key recommended for family photo privacy), quality profiles, spend caps, legibility markers, and 9-format recipe exporting.
- [**Strategic Roadmap** (`docs/ROADMAP.md`)](docs/ROADMAP.md): Milestone tracking, Gate B certification criteria, Tier T4 real photo intake plan, d04 homogeneous scaling gap closure, and CardForge split.
- [**CI & Evaluation Security** (`docs/CI.md`)](docs/CI.md): Two-workflow GitHub Actions split isolating untrusted PR execution from trusted ground-truth decryption.

> [!IMPORTANT]
> **Evaluation Honesty Note**: Earlier benchmark results were produced by code and tests that could read answers instead of deriving them (manifest/hash lookups, filename heuristics, canned fallback recipes). An October 2026 audit removed these, added anti-cheat invariants (INV-AC1..INV-AC6) with tests, and moved evaluation to a black-box harness scored against encrypted ground truth. See [`docs/EVALS.md`](docs/EVALS.md) for the current honest baselines.

### Developer & Agent Context Hub

- [**`_agents/README.md`**](_agents/README.md): Fast-orientation index and onboarding guide for AI agents and human developers.
- [**`_agents/ARCHITECTURE.md`**](_agents/ARCHITECTURE.md): Comprehensive breakdown of the Python backend (Google Antigravity SDK orchestrator, SQLite WAL persistence, two-stage correlation engine, FastAPI/SSE) and React 18+ SPA.
- [**`_agents/INVARIANTS.md`**](_agents/INVARIANTS.md): Non-negotiable architectural guardrails (SQLite WAL concurrency, Plan To Eat RFC 4180 CSV escaping, HITL async barrier semantics, Vite SPA static bundle contracts).
- [**`_agents/TESTING_GUIDELINES.md`**](_agents/TESTING_GUIDELINES.md): Testing philosophy, 4-tier test pyramid, pytest & Vitest testing guides, fixtures, and regression checklists.
- [**`_agents/EXTENDING_RECIPEBOXER.md`**](_agents/EXTENDING_RECIPEBOXER.md): Step-by-step developer extension guides with code examples for adding source providers, custom prompt branches, and export format serializers.
- [**`ROADMAP.md`**](ROADMAP.md): Canonical project roadmap tracking completed milestones M1–M16 and prioritized future backlog items (Phase 10).
- [**`SCRIPTS.md`**](SCRIPTS.md): Comprehensive script usage and troubleshooting guide for all POSIX and Windows scripts.


---

## Prerequisites

- **Python 3.10+** (Python 3.10, 3.11, 3.12, 3.13, 3.14 supported).
- **Node.js 18+** & **npm** (for building and testing the React frontend).
- *(Optional)* **Google Gemini API Key** for live multimodal AI processing (`GEMINI_API_KEY`). All test suites, synthetic card generators, and offline modes operate hermetically with mock fixtures when no key is supplied.

---

## Multi-Platform Quickstart

### macOS & Linux (POSIX)

```bash
# 1. Bootstrap environment and install backend + frontend dependencies
./setup.sh

# 2. Run the automated test suites (2,205+ tests: 1,848 Python pytest + 357 Frontend Vitest)
./test.sh
cd frontend && npm test -- --run && cd ..

# 3. Start the RecipeBoxer API server and embedded React SPA
./run.sh

# 4. (Optional) Build standalone executable binary and wheel packages
./build.sh --all
```

### Windows (PowerShell)

```powershell
# 1. Bootstrap environment and install backend + frontend dependencies
.\setup.ps1

# 2. Run the automated test suites (2,205+ tests: 1,848 Python pytest + 357 Frontend Vitest)
.\test.ps1
cd frontend; npm test -- --run; cd ..

# 3. Start the RecipeBoxer API server and embedded React SPA
.\run.ps1

# 4. (Optional) Build standalone executable and distribution packages
.\build.ps1 -All
```

### Windows (Command Prompt)

```cmd
:: 1. Bootstrap environment
setup.bat

:: 2. Start API server and launch application
run.bat
```

Once started, open your web browser to **`http://127.0.0.1:8000`** to access the RecipeBoxer interface.

---

## End-to-End Workflow Guide

RecipeBoxer provides an intuitive 4-stage pipeline for digitizing physical card collections:

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────────┐     ┌─────────────────────┐
│  1. Ingestion   │ ──► │  2. HITL Queue   │ ──► │   3. Recipe Editor   │ ──► │      4. Export      │
│  (Multi-Source) │     │ (Rapid Keyboard) │     │ (Split-Screen & CV)  │     │ (9 Formats / Print) │
└─────────────────┘     └──────────────────┘     └──────────────────────┘     └─────────────────────┘
```

### 1. Ingestion Setup & Throttling
- **Select Source**: Choose between **Local Folder / Uploads**, **Google Photos**, **Google Drive**, or **Cloud Storage (S3 / GCS)**.
- **Configure Throttling**:
  - **Parallel Mode**: High-throughput parallel processing with configurable concurrency (`max_concurrency: 5`).
  - **Slow-Batch Mode**: Paces API calls in small batches (e.g., 2–4 cards per batch with 2.0s delays) to prevent API rate limits on free-tier quotas.
- **Dynamic Thinking Budget**: Enable extended reasoning tokens (1,024–4,096 tokens) for degraded cursive and faint pencil cards.

### 2. Rapid HITL Queue & Hotkey Review
When card sequences have ambiguous visual or semantic confidence scores, the pipeline pauses for human review:
- **`Space`**: **Approve Sequence** — confirms the proposed "Part 1 ... Part N" sequence and advances the queue.
- **`S`**: **Standalone Card** — marks the card as an independent single-part recipe.
- **`B`**: **Invert Sequence** — reverses the order of the card sequence.
- **`X`**: **Reject Scan** — discards non-recipe cards, blank backs, or blurry scans.
- **`Tab`**: **Candidate Drawer** — opens the ranked candidate drawer to inspect or select alternate sequence matches.
- **Sequence Controls ("Part 1 ... N")**: Drag-and-drop cards to reorder sequence positions, split cards into separate recipes, or merge adjacent proposals into multi-card sets.

### 3. Split-Screen Recipe Editor & Image Inspection
- **Side-by-Side Verification**: 50/50 view displaying the original card scan carousel on the left and the editable recipe form on the right.
- **Image Inspection Tools**:
  - Synchronized zoom & pan up to 400%.
  - 90° Clockwise and Counter-Clockwise rotation.
  - Image Enhancement: Brightness, Contrast adjustment, and Negative Color Inversion for faint pencil text.
- **Structured Fields**: Verify and adjust recipe title, yield, prep/cook times, structured ingredients with fractions (`1/8`, `1/4`, `1/2`, `3/4`), step-by-step directions, and categorization tags with automatic SQLite WAL persistence.

### 4. Multi-Format Recipe Export
- Click **Export** to access the 9-format publishing dialog.
- Switch tabs to inspect live syntax previews for Plan To Eat CSV, Schema.org JSON-LD, Obsidian Markdown, Cooklang, and Google Docs HTML.
- Download individual files, heirloom `.docx` Word cookbooks, printable styled PDF documents, or `.paprikarecipes` archives.

### 5. "First Run" Interactive UI Guided Tutorial
First-time visitors are automatically greeted with an interactive 6-step guided walkthrough overlay. Returning users can restart the tour at any time by clicking the persistent **Tour** button in the header navigation.
- Use `ArrowRight` / `ArrowLeft` or on-screen buttons to step through the walkthrough.
- Press `Escape` or click **Skip Tour** to close the walkthrough and save state to `localStorage`.

---

## Command Line Interface

RecipeBoxer provides a comprehensive CLI runner:

```bash
# Display version information
recipeboxer --version

# Display help and usage instructions
recipeboxer --help

# Start server on custom host, port, and default concurrency
recipeboxer --host 0.0.0.0 --port 8080 --concurrency 8 --log-level info

# Enable auto-reload in development
recipeboxer --reload
```

---

## Configuration & Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | *(optional)* | Google Gemini API key for live multimodal processing |
| `RECIPEBOXER_API_HOST` | `127.0.0.1` | FastAPI server bind address |
| `RECIPEBOXER_API_PORT` | `8000` | FastAPI server bind port |
| `RECIPEBOXER_MAX_CONCURRENCY` | `5` | Maximum concurrent asynchronous ingestion tasks |
| `RECIPEBOXER_CACHE_DIR` | `.cache/recipeboxer` | Persistent SQLite cache and thumbnail storage directory |
| `RECIPEBOXER_EXPORT_DIR` | `exports` | Default destination directory for exported recipe archives |
| `RECIPEBOXER_DEBUG` | `false` | Enable verbose debug logging and traceback output |
| `RECIPEBOXER_MODEL_FAST` | `gemini-3.7-flash` | Model identifier for visual fingerprinting and screening |
| `RECIPEBOXER_MODEL_REASONING` | `gemini-3.1-pro` | Model identifier for semantic correlation and extraction |

---

## Configuration & Troubleshooting FAQ

### 1. Pacing API Quotas with Slow-Batch Mode
**Problem**: Running large collections of recipe cards triggers Google Gemini API rate limits (`429 RESOURCE_EXHAUSTED`).  
**Solution**: Enable **Slow-Batch Mode** in the Ingestion configuration panel or pass `slow_batch_size: 2` and `slow_batch_interval_seconds: 2.0`. This paces API requests into manageable chunks with pauses between batches.

### 2. Custom Port Binding & Network Access
**Problem**: Port 8000 is occupied by another service, or you want to access RecipeBoxer from other devices on your local network.  
**Solution**: Start RecipeBoxer with custom `--host` and `--port` arguments:
```bash
./run.sh --host 0.0.0.0 --port 8888
# Or via CLI directly:
recipeboxer --host 0.0.0.0 --port 8888
```

### 3. Resetting SQLite Cache & Session Database
**Problem**: You want to clear previously cached card scans and session history for a clean run.  
**Solution**: Remove the `.cache/recipeboxer` directory:
```bash
rm -rf .cache/recipeboxer/fingerprints.db*
# Or on Windows PowerShell:
Remove-Item -Recurse -Force .cache\recipeboxer\fingerprints.db*
```

### 4. Windows PowerShell Script Execution Policy
**Problem**: PowerShell blocks running `./setup.ps1` or `./run.ps1` with an execution policy error.  
**Solution**: Set execution policy for the current process:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\setup.ps1
```

---

## Automated Testing & Quality Assurance
 
RecipeBoxer maintains a comprehensive multi-tier test pyramid:
 
```bash
# Run full backend pytest suite (1,530 tests across Tiers 1–5 and challenger suites)
./test.sh
 
# Run frontend Vitest suite (258 tests across 23 test suites)
cd frontend && npm test -- --run && cd ..
 
# Run end-to-end platform integration test suite
pytest tests/test_e2e_platform.py -v
 
# Run Tier 5 adversarial hardening and concurrency tests
pytest tests/test_tier5_adversarial_hardening.py -v
 
# Run empirical challenger sequence correlation tests
pytest tests/test_challenger_1_full_empirical.py -v
```

For complete documentation on testing conventions, mock harnesses, and writing new tests, see [**`_agents/TESTING_GUIDELINES.md`**](_agents/TESTING_GUIDELINES.md).

---

## Standalone Binary Packaging

RecipeBoxer packages into self-contained standalone binaries using PyInstaller:

```bash
# Build standalone binary on macOS / Linux
./build.sh --binary

# Build standalone executable on Windows
.\build.ps1 -Binary
```

The compiled executable is placed in `dist/recipeboxer` (or `dist\recipeboxer.exe`), bundling all backend dependencies, schemas, and pre-compiled React SPA static assets for zero-dependency execution.

---

## License

This project is licensed under the terms described in the [LICENSE](LICENSE) file.
