# RecipeBoxer System Architecture & Technical Specification

This document provides a comprehensive technical breakdown of the **RecipeBoxer** platform architecture, covering both the Python backend and the React 18+ TypeScript frontend Single Page Application (SPA).

---

## 1. High-Level System Architecture

RecipeBoxer is structured as a decoupled, multi-stage pipeline designed for physical card digitization, heuristic and semantic pairing, asynchronous human verification, and structured recipe extraction.

```
+-----------------------------------------------------------------------------------------------+
|                                INGESTION SOURCES & CONNECTORS                                 |
|   • Local Filesystem Directory Scans (.jpg, .jpeg, .png, .webp, .tiff, .bmp)                  |
|   • Google Photos Library API (OAuth2, auto-refresh token mutex, category filters)           |
|   • Cloud Storage & Drives (Google Drive, AWS S3 / GCS with pure-Python SigV4)               |
+-----------------------------------------------------------------------------------------------+
                                               │
                                               ▼
+-----------------------------------------------------------------------------------------------+
|                                     PYTHON BACKEND RUNTIME                                    |
|                                                                                               |
|  +─────────────────────────────────────────────────────────────────────────────────────────+  |
|  |                  Google Antigravity SDK Orchestrator (RecipeBoxerAgent)                 |  |
|  +─────────────────────────────────────────────────────────────────────────────────────────+  |
|                                              │                                                |
|       ┌───────────────────┬──────────────────┴────────────────────┬───────────────────┐       |
|       ▼                   ▼                                       ▼                   ▼       |
|  [ Stage 1 ]        [ Stage 2 ]                             [ Stage 3 ]         [ Stage 4 ]   |
|  INGESTION        FINGERPRINTING                            CORRELATION        HITL BARRIER   |
|  • BaseSource-    • CV Preprocessing (Deskewing,            • CIELAB ΔE        • Async Event  |
|    Provider         Border Cropping, CLAHE Contrast)        • Proximity Decay    Pause Barrier|
|  • SHA-256 Cache  • Content-Addressed SQLite Cache          • Mirrored Stains  • Rapid Queue  |
|  • ScannedCard    • Modality Router (95.7% accuracy)        • Semantic Verify  • Candidate    |
|                     (Cached in SQLite FingerprintCache)     • Greedy Match       Drawer       |
|                                                             • Multi-Card Group  • Visual Tools|
|                                                                                       │       |
|       ┌───────────────────────────────────────────────────────────────────────────────┘       |
|       ▼                                                                                       |
|  [ Stage 5 ]                                                [ Stage 6 ]                       |
|  RECIPE EXTRACTION                                          EXPORTERS (9 Formats)             |
|  • Antigravity Skills (Cursive, Clippings)                  • Plan To Eat RFC 4180 CSV (Prim) |
|  • Dynamic Thinking Budget Allocation (0-4096 tokens)       • Schema.org Recipe JSON-LD       |
|  • RecipeSchema (Ingredients, Steps, Times, Yield, Tags)    • Google Docs Archival with Scans |
|                                                             • OpenXML DOCX & Printable PDF    |
|                                                             • Obsidian MD / Cooklang / Paprika|
|                                                             • Standard JSON                   |
|                                                                                               |
|  +─────────────────────────────────────────────────────────────────────────────────────────+  |
|  |                     Persistence Layer (SQLite WAL Mode + RLock)                         |  |
|  |  • FingerprintCache & RemoteAssetCache: sha256_hash -> cached assets & fingerprints     |  |
|  |  • SessionStore: sessions, session_cards, session_proposals, session_recipes            |  |
|  +─────────────────────────────────────────────────────────────────────────────────────────+  |
|  |                     FastAPI ASGI Layer & Server-Sent Events (SSE)                       |  |
|  |  • REST Endpoints: /api/pipeline/*, /api/hitl/*, /api/recipes/*, /api/export/*,         |  |
|  |                    /api/sources/*                                                       |  |
|  |  • SSE Stream: /api/events (Real-time telemetry, state transitions, keep-alives)        |  |
|  |  • Static Asset Host: /assets -> recipeboxer/api/static/assets                          |  |
|  |  • SPA Catch-All: /{full_path} -> recipeboxer/api/static/index.html (with /api 404 guard)|
|  +─────────────────────────────────────────────────────────────────────────────────────────+  |
+-----------------------------------------------------------------------------------------------+
                                               │
                                  REST / SSE / Static Assets
                                               │
                                               ▼
+-----------------------------------------------------------------------------------------------+
|                                REACT 18+ TYPESCRIPT SPA FRONTEND                              |
|                                                                                               |
|  • Interactive Onboarding Tutorial: 6-step spotlight walkthrough, keyboard nav, persistent tour|
|  • Custom Hooks: useSSE, useKeyboard, useImageViewer, useRecipeForm, useTheme               |
|  • Ingestion & Source Switcher: Local Dropzone, Google Photos Album Picker, Cloud Drive Tabs   |
|  • Interactive Image Viewer: Pan, Zoom (0.25x-5x), 90° Stepped Rotation, CSS Filters          |
|  • Rapid HITL Workspace: Side-by-side comparison, Hotkeys, Candidate Drawer, Multi-Card Tray  |
|  • Split-Screen Recipe Review: Side-by-side card inspection and editable structured form      |
|  • Multi-Format Export Dialog: Monospace previews, live download buttons across 9 formats     |
+-----------------------------------------------------------------------------------------------+
```

---

## 2. Python Backend Architecture

### 2.1 Google Antigravity SDK Orchestration Layer

The execution runtime is managed by the Google Antigravity Agent pattern in `recipeboxer/agent/`:

- **`RecipeBoxerAgent` (`orchestrator.py`)**:
  - The central coordinator managing asynchronous session tasks (`run_in_background(session)` $\rightarrow$ `asyncio.Task`).
  - Manages dependency injection of vision extractors, semantic verifiers, pairing optimizers, storage stores, and exporters.
  - Implements the sequential 6-stage execution state machine with lifecycle error recovery.
- **`PipelineSession` (`session.py`)**:
  - Encapsulates mutable runtime state for an individual digitization run: session configuration, discovered cards, visual fingerprints, correlation proposals, human approvals, extracted recipes, token telemetry, and progress metrics.
  - Controls pipeline pausing and resumption through cooperative `asyncio.Event` primitives (`approval_event` and `cancellation_event`).
- **`SessionManager` (`session.py`)**:
  - Maintains in-memory active session instances and orchestrates SQLite rehydration (`create_session`, `get_session`, `list_sessions`, `remove_session`).

---

### 2.2 The 6-Stage Pipeline Lifecycle

```
[ INGESTING ] ──> [ FINGERPRINTING ] ──> [ CORRELATING ] ──> [ HITL_PENDING ] ──> [ EXTRACTING ] ──> [ EXPORTING ] ──> [ COMPLETED ]
                                                                     │
                                                             (User Approval)
```

#### Stage 1: Ingestion (`PipelineState.INGESTING`)
- Discovers card images via `BaseSourceProvider` (`recipeboxer/sources/`):
  - Local filesystem directory scans or multipart uploads (`LocalSourceProvider`).
  - Remote Google Photos albums with category filters (`GooglePhotosSourceProvider`).
  - Cloud storage buckets / drives (`GoogleDriveSourceProvider`, `S3GCSSourceProvider`).
- Streams remote assets into local content-addressable cache (`RemoteAssetCache`) with SHA-256 validation.
- Instantiates immutable `ScannedCard` domain dataclasses.

#### Stage 2: Deterministic CV Preprocessing & Visual Fingerprinting (`PipelineState.FINGERPRINTING`)
- **Computer Vision Preprocessing Engine** (`recipeboxer/vision/preprocessing.py`):
  - **Auto-Deskewing (`deskew_card_image`)**: Coarse-to-fine projection profile analysis detecting and correcting rotation skew within $\pm 1^\circ$ tolerance. Canvas is expanded with mode-aware fill to prevent text clipping.
  - **Card Border Auto-Cropping (`crop_card_borders`)**: Isolates card contour boundaries and removes dark scanner bed margins.
  - **Adaptive Contrast Enhancement (`enhance_card_readability`, `apply_clahe`)**: Tile-based CLAHE and white-balance normalization for faint pencil and yellowed cards.
  - **Substrate Colorimetric Clustering (`extract_paper_color_palette`)**: K-Means clustering in CIE $L^*a^*b^*$ space for paper and ink color centroids.
- **Document Modality Classifier & Dynamic Routing** (`recipeboxer/vision/router.py`):
  - Classifies card modality with 95.7% benchmark accuracy (`HANDWRITTEN_CURSIVE`, `HANDWRITTEN_PRINT`, `TYPEWRITTEN`, `PRINTED_CLIPPING_OR_LABEL`, `MIXED_MEDIA`).
  - Routes cards to specialized prompt branches and tailored preprocessing pipelines.
- **Visual Fingerprinting**:
  - Checks `FingerprintCache` by SHA-256 digest. If present, returns cached fingerprint immediately with zero API cost.
  - If missing, extracts paper color, ruling, ink medium, physical stains, boundary text, and card role.

#### Stage 3: Open-Ended N-Part Sequence Correlation (`PipelineState.CORRELATING`)
- **Stage 1 Heuristic Screening** (`vision/heuristics.py`): Evaluates pairwise directed transition compatibility using physical substrate distance metrics ($\Delta E$ color, paper ruling, scan proximity decay, horizontally mirrored stain bleed-through).
- **Stage 2 Semantic Continuity Verification** (`correlation/semantic.py`): Candidate card transitions are evaluated with `GeminiSemanticVerifier` for multimodal syntactic flow, recipe identity, and culinary logic.
- **Global Sequence Chaining** (`correlation/optimizer.py`): `PairingOptimizer` constructs a directed transition graph and finds maximal directed path chains subject to DAG constraints. This automatically proposes single-card ($N=1$), two-part ($N=2$), and multi-card ($N \ge 3$) recipe sequences without artificial front/back partitioning. Unpaired cards remain standalone single-part sequences.

#### Stage 4: Human-in-the-Loop Barrier (`PipelineState.HITL_PENDING`)
- The pipeline transitions to `HITL_PENDING` and calls `await session.pause_for_hitl()`.
- The background task pauses at an `asyncio.Event` barrier without holding locks or consuming CPU cycles.
- Telemetry broadcasts `hitl_ready` over Server-Sent Events.
- Operators review pairings in the React SPA (with the 6-step interactive onboarding tutorial guiding first-time users). Submitting approvals via `POST /api/hitl/approve` unblocks `approval_event` and resumes execution.

#### Stage 5: Structured Recipe Extraction & Antigravity Skills (`PipelineState.EXTRACTING`)
- Iterates over approved card pairings and standalone cards.
- **Antigravity Domain Skills** (`recipeboxer/agent/skills/`):
  - `vintage-handwriting-ocr`: Paleography prompts and normalization for archaic culinary units ("oleo", "saleratus", "butter size of an egg", "gills", "pecks", "drams") and fractions (`1/8`, `1/4`, `1/2`, `3/4`).
  - `print-clipping-extractor`: Multi-column newsprint reading order parsing and promotional noise filtering (`filter_advertisement_noise`).
- **Dynamic Thinking Budget Allocation** (`recipeboxer/extraction/extractor.py`):
  - `compute_adaptive_thinking_budget`: Automatically allocates 0 reasoning tokens for clean typed cards, and 2,048–4,096 tokens for degraded cursive, faded pencil, and complex clippings.
- `GeminiRecipeExtractor` transcribes multimodal card images into structured `RecipeSchema`.

#### Stage 6: Multi-Format Archival Export (`PipelineState.EXPORTING` $\rightarrow$ `PipelineState.COMPLETED`)
- Serializes extracted recipes to configured formats via `recipeboxer/exporter/`:
  1. **Plan To Eat RFC 4180 CSV** (`plan_to_eat.py` — Primary / Default)
  2. **Schema.org Recipe JSON-LD** (`json_ld.py`)
  3. **Google Docs Archival Exporter** (`google_docs.py` — with page breaks and embedded card scans)
  4. **Offline OpenXML DOCX** (`docx_export.py` — heirloom cookbook with photos)
  5. **Printable PDF Cookbook** (`pdf_export.py` — print-ready layout with running headers/footers)
  6. **Obsidian / Markdown** (`markdown_export.py` — YAML frontmatter and image embeds)
  7. **Cooklang** (`cooklang.py` — `.cook` plain-text markup)
  8. **Paprika Recipe Manager** (`paprika.py` — `.paprikarecipes` gzip archive with photos)
  9. **Raw JSON**
- Finalizes token telemetry, updates `SessionStore`, and transitions state to `COMPLETED`.

---

### 2.3 Dual SQLite WAL Persistence Architecture

RecipeBoxer uses two distinct SQLite databases configured with Write-Ahead Logging (WAL) for thread safety, high read concurrency, and resilience:

```
                          STORAGE LAYER
                                │
         ┌──────────────────────┴──────────────────────┐
         ▼                                             ▼
  FingerprintCache                              SessionStore
  (Content-Addressable Cache)                   (Relational State Engine)
  • Key: sha256_hash (TEXT PK)                  • sessions
  • Stores: VisualFingerprint JSON              • session_cards
  • Thread-safe RLock + WAL                     • session_proposals
  • Zero LLM cost on duplicate scans            • session_recipes
```

#### Connection PRAGMA Configuration
Both storage engines execute the following initialization commands:
```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -64000; -- 64MB memory page cache
```

#### Schema Definitions

##### 1. `FingerprintCache` (`recipeboxer/storage/cache.py`)
```sql
CREATE TABLE IF NOT EXISTS fingerprints (
    sha256_hash TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    image_path TEXT NOT NULL,
    role TEXT NOT NULL,
    json_data TEXT NOT NULL,
    extracted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fp_card_id ON fingerprints(card_id);
CREATE INDEX IF NOT EXISTS idx_fp_image_path ON fingerprints(image_path);
CREATE INDEX IF NOT EXISTS idx_fp_role ON fingerprints(role);
CREATE INDEX IF NOT EXISTS idx_fp_extracted_at ON fingerprints(extracted_at);
```

##### 2. `SessionStore` (`recipeboxer/storage/session_store.py`)
```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    mode TEXT,
    folder_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    config_json TEXT,
    metrics_json TEXT,
    token_usage_json TEXT,
    error_message TEXT,
    csv_output_path TEXT,
    card_count INTEGER DEFAULT 0,
    recipe_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_cards (
    session_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    image_path TEXT NOT NULL,
    sha256_hash TEXT,
    role TEXT,
    json_data TEXT,
    PRIMARY KEY (session_id, card_id)
);

CREATE TABLE IF NOT EXISTS session_proposals (
    session_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    ordered_card_ids TEXT NOT NULL,
    json_data TEXT,
    PRIMARY KEY (session_id, proposal_id)
);

CREATE TABLE IF NOT EXISTS session_recipes (
    session_id TEXT NOT NULL,
    recipe_id TEXT NOT NULL,
    json_data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, recipe_id)
);
```

---

### 2.4 Two-Stage Hybrid Correlation Engine & Mathematical Formulas

The correlation engine evaluates card pairs through a two-stage hybrid process:

#### Stage 1: Physical Heuristic Scoring Formulas (`vision/heuristics.py`)

1. **CIELAB Color Delta-E ($S_{\text{color}}$)**:
   RGB values are converted to CIE XYZ (D65 standard observer) and transformed into CIE $L^*a^*b^*$ coordinates. Euclidean color distance is computed as:
   $$\Delta E = \sqrt{(L_1 - L_2)^2 + (a_1 - a_2)^2 + (b_1 - b_2)^2}$$
   $$S_{\text{color}} = \max\left(0.0, 1.0 - \frac{\Delta E}{50.0}\right)$$

2. **Scan Proximity Exponential Decay ($S_{\text{prox}}$)**:
   Accounts for physical scanner sequence proximity:
   $$S_{\text{prox}} = \exp(-0.5 \cdot |\text{idx}_1 - \text{idx}_2|)$$

3. **Horizontally Mirrored Stain Bleed-Through ($S_{\text{stain}}$)**:
   Physical kitchen stains (grease, coffee, oil) bleed through from the front card to the horizontally mirrored position on the back:
   - `TOP_LEFT` $\leftrightarrow$ `TOP_RIGHT`
   - `BOTTOM_LEFT` $\leftrightarrow$ `BOTTOM_RIGHT`
   - `EDGE_LEFT` $\leftrightarrow$ `EDGE_RIGHT`
   - `CENTER` $\leftrightarrow$ `CENTER`
   - `EDGE_TOP` $\leftrightarrow$ `EDGE_TOP`, `EDGE_BOTTOM` $\leftrightarrow$ `EDGE_BOTTOM`
   - Matching mirrored stains yield $S_{\text{stain}} = 1.00$. Both cards pristine yields $0.60$. Stained/clean mismatch yields $0.30$. Uncorrelated stains yield $0.10$.

4. **Paper Ruling Match ($S_{\text{ruling}}$)**:
   Evaluates ruling pattern alignment and line spacing delta:
   $$\Delta_{\text{spacing}} \le 4\text{px} \implies 1.00; \quad \le 10\text{px} \implies 0.85; \quad > 10\text{px} \implies 0.70$$

5. **Ink Compatibility ($S_{\text{ink}}$)**:
   $$S_{\text{ink}} = 0.60 \cdot S_{\text{type}} + 0.40 \cdot S_{\text{color}}$$

6. **Role Compatibility Gating ($S_{\text{role}}$)**:
   - `(FRONT_ONLY, BACK_ONLY)` $\implies 1.00$
   - `(FRONT_ONLY, UNKNOWN)` or `(UNKNOWN, BACK_ONLY)` $\implies 0.80$
   - `(UNKNOWN, UNKNOWN)` $\implies 0.50$
   - Any pairing involving `STANDALONE` or incompatible roles (`FRONT_ONLY` + `FRONT_ONLY`, `BACK_ONLY` + `BACK_ONLY`, or reverse ordering `BACK_ONLY` + `FRONT_ONLY`) evaluates to $0.00$ and is immediately pruned.

7. **Stage 1 Composite Heuristic Score ($S_{\text{heur}}$)**:
   $$S_{\text{heur}} = 0.25 S_{\text{role}} + 0.25 S_{\text{prox}} + 0.15 S_{\text{color}} + 0.15 S_{\text{ruling}} + 0.10 S_{\text{ink}} + 0.05 S_{\text{stain}} + 0.05 S_{\text{style}}$$

#### Stage 2: Multimodal Semantic Verification & Composite Confidence
Screened pairs ($S_{\text{heur}} \ge 0.50$) undergo semantic analysis via `GeminiSemanticVerifier` ($S_{\text{sem}}$). The overall pair confidence is calculated as:
$$\text{Transition Score}(u \to v) = 0.30 \cdot S_{\text{heur}}(u \to v) + 0.70 \cdot S_{\text{sem}}(u \to v)$$

`PairingOptimizer` constructs a directed transition graph with cycle prevention and executes a maximal DAG path chaining algorithm, aggregating non-conflicting directed transitions into coherent ordered sequences ("Part 1 ... Part N") with confidence $\ge 0.50$. Standalone cards are preserved as single-part sequences.

---

### 2.5 FastAPI REST Endpoints & Server-Sent Events (SSE) Protocol

#### REST API Route Inventory

| Method | Path | Purpose | Behavior |
|---|---|---|---|
| `POST` | `/api/pipeline/start` | Launch Pipeline | Accepts `PipelineStartRequest`, initializes session in SQLite, launches async `run_pipeline` task. |
| `POST` | `/api/cards/upload` | Multipart Upload | Uploads image files, decodes with PIL, persists cards into SQLite. |
| `GET` | `/api/status` | Get Status | Returns real-time `PipelineStatus` with progress counters and token metrics. |
| `GET` | `/api/events` | SSE Stream | Broadcasts real-time events (`state_change`, `progress`, `hitl_ready`, `recipe_extracted`, `completed`, `failed`). |
| `GET` | `/api/cards/{id}/image`| Stream Image | Streams card image with traversal protection, MIME detection, and 24h cache headers. |
| `GET` | `/api/sessions` | List Sessions | Lists all historical and active sessions ordered by date. |
| `GET` | `/api/sessions/{id}` | Session Detail | Returns full session details (cards, proposals, recipes, telemetry). |
| `DELETE`| `/api/sessions/{id}` | Delete Session | Cancels active background tasks and purges database records. |
| `POST` | `/api/sessions/{id}/resume`| Resume Session | Rehydrates historical session from SQLite into active memory. |
| `GET` | `/api/hitl/pending` | HITL Proposals | Returns pending proposals and unassigned cards for active review. |
| `GET` | `/api/hitl/candidates` | Candidate Drawer | Returns ranked alternative cards with visual feature distance breakdowns. |
| `POST` | `/api/hitl/approve` | Submit Approvals | Submits approved pairings, unblocks `approval_event`, resumes extraction. |
| `GET` | `/api/recipes` | List Recipes | Returns list of extracted `RecipeSchema` objects. |
| `PUT` | `/api/recipes/{id}` | Update Recipe | Modifies structured recipe fields (ingredients, steps, metadata) in SQLite. |
| `GET` | `/api/export/csv` | Download CSV | Downloads Plan To Eat RFC 4180 CSV attachment. |
| `GET` | `/api/export/json-ld`| Download JSON-LD | Downloads Schema.org Recipe JSON-LD structured data. |
| `GET` | `/api/export/json` | Download JSON | Downloads standard RecipeSchema JSON array. |

#### SSE Event Stream Protocol (`/api/events`)
- Media Type: `text/event-stream`
- Connection Parameter: `?session_id=<session_id>`
- Events:
  - `event: state_change` — Fired on pipeline state transitions (triggers auto-tab navigation in UI).
  - `event: progress` — Fired on step completion with percentage and stage counters.
  - `event: hitl_ready` — Fired when Stage 3 correlation completes and proposals are ready.
  - `event: recipe_extracted` — Incremental streaming of individual extracted recipes.
  - `event: completed` / `event: failed` / `event: cancelled` — Terminal pipeline events.
  - `: ping\n\n` — Keepalive comments sent every 7.5 seconds (15 ticks) to maintain HTTP proxy connections.

#### Ingestion Concurrency Modes
- **`IngestionMode.PARALLEL`**: Executes card fingerprinting concurrently using an `asyncio.Semaphore(max_concurrency)` (configurable 1–50, default 5).
- **`IngestionMode.SLOW_BATCH`**: Chunks cards into discrete batches (`slow_batch_size`), pausing for `slow_batch_interval_seconds` between batches via `asyncio.wait_for(cancel_token.wait_cancelled(), timeout=interval)`. **Invariant**: Never sleeps after the final chunk.

---

## 3. Frontend Architecture (React 18+ TypeScript SPA)

The frontend Single Page Application is located in `frontend/` and built with React 18, TypeScript, Tailwind CSS, Lucide icons, and Vite.

```
frontend/src/
├── App.tsx                     # Root application container, view switcher & SSE listener
├── main.tsx                    # React DOM entry point
├── context/
│   └── ThemeContext.tsx        # Light/Dark/System theme state & DOM synchronization
├── hooks/
│   ├── useSSE.ts               # SSE client with exponential backoff auto-reconnect
│   ├── useKeyboard.ts          # Input-trapped global keyboard shortcuts
│   ├── useImageViewer.ts       # Pan, zoom (0.25x-5x), 90° rotation, and CSS filter controls
│   ├── useRecipeForm.ts        # Form validation, dirty tracking, and step/ingredient mutations
│   └── useTheme.ts             # Theme context consumer hook
├── pages/
│   ├── IngestionPage.tsx       # Directory launcher, drag-and-drop dropzone, throttle controls
│   ├── HitlReviewPage.tsx      # Rapid HITL review workspace wrapper
│   ├── RecipeReviewPage.tsx    # Split-screen card viewer and editable recipe form
│   └── ExportPage.tsx          # Multi-format export dialog with monospace previews
├── components/
│   ├── layout/                 # Header, Navigation tabs, telemetry StatusBar
│   ├── ingestion/              # SessionLauncher, Dropzone, ConfigPanel, ProgressDashboard
│   ├── hitl/                   # HitlWorkspace, RapidQueueCard, CandidateDrawer, MultiCardGrouping
│   ├── viewer/                 # CardViewerCanvas, VisualToolbar, FilterControls
│   ├── recipe/                 # RecipeEditorView, RecipeForm, Dynamic List Inputs, TagManager
│   ├── export/                 # ExportDialog, CsvPreview, JsonLdPreview, RawJsonPreview
│   └── common/                 # Button, Badge, HotkeyBadge, Modal, Slider, ThemeToggle
└── utils/
    ├── api.ts                  # Fetch API wrapper for all REST endpoints
    ├── exportPlanToEat.ts      # Client-side RFC 4180 CSV generator and blob downloader
    ├── exportJsonLd.ts         # Client-side Schema.org JSON-LD generator
    └── formatters.ts           # Time (ISO 8601), score, and date formatting helpers
```

---

### 3.1 Vite Build & Static Asset Mounting Contract

The Vite build system compiles frontend assets directly into the Python backend package:

```
frontend/dist/ (Not used)
       │
       ▼ (Configured in frontend/vite.config.ts)
recipeboxer/api/static/
├── index.html                  # Production SPA entry point
└── assets/                     # Compiled JS, CSS, and font bundles
    ├── index-*.js
    └── index-*.css
```

- **FastAPI Static Mount** (`recipeboxer/api/app.py`):
  - Mounts `/assets` directly to `recipeboxer/api/static/assets/`.
  - `GET /` serves `index.html` (or `FALLBACK_SPA_HTML` if unbuilt).
  - `GET /{full_path:path}` catches all client-side routes (`/ingest`, `/correlate`, `/recipes`, `/export`) and returns `index.html` for client-side HTML5 routing.
  - **Security Guard**: Any path matching `api` or starting with `api/` is strictly rejected with a `404 Not Found` JSON error.

---

### 3.2 Custom Hooks Architecture

1. **`useSSE(sessionId, options)` (`frontend/src/hooks/useSSE.ts`)**:
   - Establishes an `EventSource` connection to `/api/events?session_id=<id>`.
   - Dispatches incoming SSE events to React callbacks.
   - Automatically navigates the active tab when `state_change` transitions to `HITL_PENDING`.
   - Implements exponential backoff auto-reconnection (`Math.min(delay * 1.5, 10000)` starting at 1000ms).

2. **`useKeyboard(shortcuts, enabled)` (`frontend/src/hooks/useKeyboard.ts`)**:
   - Registers global keyboard listeners on `window` for rapid HITL review:
     - `Space` / `Enter` $\implies$ Approve pair
     - `S` $\implies$ Mark front card as standalone
     - `B` $\implies$ Reverse card order (swap front/back)
     - `X` $\implies$ Reject / discard pairing
     - `M` / `Tab` $\implies$ Open Candidate Drawer
     - `Escape` $\implies$ Close drawer or blur focus
   - **Input Focus Trapping**: Automatically suppresses all shortcut triggers when the user is focused inside an editable element (`INPUT`, `TEXTAREA`, `SELECT`, or `isContentEditable`).

3. **`useImageViewer(options)` (`frontend/src/hooks/useImageViewer.ts`)**:
   - Manages interactive canvas transformations:
     - Pan offset: $(x, y)$ coordinates updated via pointer drag.
     - Zoom scale: $0.25\times$ to $5.0\times$ via wheel scroll and toolbar buttons.
     - Rotation: Stepped $90^\circ$ increments ($0^\circ, 90^\circ, 180^\circ, 270^\circ$).
     - CSS Filters: Brightness ($0\text{--}200\%$), Contrast ($0\text{--}300\%$), Invert ($0\text{ or }100\%$).
     - Dual-Card Linked Zoom: Synchronizes pan and zoom coordinates across paired front/back viewports when link mode is enabled.

4. **`useRecipeForm(initialRecipe, onSave)` (`frontend/src/hooks/useRecipeForm.ts`)**:
   - Form state management with dirty state tracking.
   - Reordering, inserting, and deleting dynamic ingredient rows and numbered directions.
   - Tag chip management with instant deduplication.

5. **`useTheme()` (`frontend/src/hooks/useTheme.ts`)**:
   - Provides theme context (`light`, `dark`, `system`).
   - Syncs DOM root class (`class="dark"`) and `data-theme` attribute.
   - Persists user preferences to `localStorage` and listens to OS `prefers-color-scheme` media queries.

---

### 3.3 Key Frontend Workspaces

1. **Ingestion Page (`IngestionPage.tsx`)**:
   - `SourceSelector`: Tabbed switcher for **Local Files / Uploads**, **Google Photos** (with `AlbumPickerModal`), and **Cloud Drive**.
   - Concurrency and throttle configuration sliders (parallel workers, slow-batch size/interval, model selection).
   - Real-time progress dashboard with active stage indicators and elapsed time telemetry.
   - Historical session table with one-click session resumption and deletion.

2. **Rapid HITL Review Workspace (`HitlWorkspace.tsx`)**:
   - Dual-card side-by-side comparison with visual confidence score badges ($\ge 80\%$ green, $\ge 50\%$ amber, $< 50\%$ red).
   - Candidate Drawer: Slide-out panel presenting alternative ranked cards with visual feature distance score breakdowns.
   - Multi-Card Grouping: Interactive tray for constructing 3+ card multi-part recipe groups with drag-and-drop reordering.

3. **Recipe Review & Editor (`RecipeReviewPage.tsx`)**:
   - Split-screen workspace: Left panel features the pan/zoom/filter card viewer; right panel hosts the live editable recipe form.
   - Real-time PUT synchronization to `/api/recipes/{id}`.

4. **Multi-Format Export Dialog (`ExportDialog.tsx`, `ExportPage.tsx`)**:
   - Monospace preview tabs and live binary blob downloads for all **9 export formats**:
     1. Plan To Eat RFC 4180 CSV (Primary)
     2. Schema.org Recipe JSON-LD
     3. Google Docs Archival with embedded card scans
     4. Offline OpenXML DOCX Family Cookbook
     5. Printable PDF Cookbook
     6. Obsidian / Markdown (`.md` with YAML frontmatter)
     7. Cooklang (`.cook` markup)
     8. Paprika Recipe Manager (`.paprikarecipes` gzip archive)
     9. Raw JSON

5. **Interactive Guided UI Tutorial (`frontend/src/components/tutorial/`)**:
   - `TutorialOverlay.tsx` and `tutorialSteps.ts` providing 6 interactive walkthrough steps with spotlight highlight rings, keyboard navigation (`Escape`, Arrow keys), and header restart controls.

---

## 4. Evaluation & Quality Assurance Harness (`recipeboxer/evals/`)

RecipeBoxer includes a comprehensive evaluation and continuous regression harness:

- **Quantitative Metric Suite** (`evals/metrics.py`):
  - **Pairing F1 Score**: Evaluates proposal accuracy and multi-card clustering against ground truth.
  - **Levenshtein CER & WER**: Character and Word Error Rates across full recipe text, ingredients, fractions, and directions.
  - **Schema Conformity & Completeness**: Strict validator for Plan To Eat RFC 4180 rules and ISO 8601 durations.
- **LLM-as-a-Judge Rubric** (`evals/judge.py`):
  - Independent Gemini evaluation rubric assessing 4 qualitative dimensions (Semantic Fidelity, Preservation of Marginalia, Cross-Consistency, Step Ordering Logic) with offline mock mode.
- **Benchmark Runner CLI** (`evals/run_benchmarks.py`, `evals/runner.py`):
  - Benchmarks extraction and correlation against the 23-card historical corpus (`tests/fixtures/sample_cards/manifest.json`).

---

## 5. Summary of System Statistics & Testing Verification
 
- **Full-Stack Test Coverage**: **1,660 tests passing (100% pass rate)**
  - **1,442 backend Pytest unit, boundary, integration, E2E, and adversarial stress tests** (including 5-tier testing pyramid and challenger suites).
  - **218 frontend Vitest component and workflow tests** across 20 test suites.
- **Supported Export Formats**: 9 Formats (Plan To Eat CSV, Schema.org JSON-LD, Google Docs, DOCX, PDF, Obsidian MD, Cooklang, Paprika, Raw JSON).
- **Supported Source Providers**: 4 Providers (Local Directory/Uploads, Google Photos API, Google Drive, S3/GCS Object Storage).
- **Embedded Static Bundle**: Compiled directly into `recipeboxer/api/static/` for zero-dependency standalone execution.
