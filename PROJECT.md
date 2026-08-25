# Project: RecipeBoxer Embedded React SPA & FastAPI Integration

## Architecture
RecipeBoxer integrates a modern React 18+ TypeScript Single-Page Application (SPA) directly into a FastAPI backend.
- **Frontend**: React 18+, TypeScript, Vite, Tailwind CSS located in `frontend/`. Compiled into static assets located at `recipeboxer/api/static/`.
- **Backend API**: FastAPI application in `recipeboxer/api/app.py` mounting static assets at `/assets` and providing an SPA fallback route for all non-API GET requests to `index.html`.
- **Real-Time Telemetry**: Server-Sent Events (`/api/events`) broadcasting pipeline status, card ingestion counts, correlation updates, and extraction completion.
- **Session & Storage**: SQLite-backed session persistence in `recipeboxer/storage/` supporting historical session listings and resumption.
- **HITL Correlation**: Rapid Queue keyboard shortcuts (`Space`/`Enter`, `S`, `B`, `X`, `M`/`Tab`), slide-out Candidate Drawer with ranked alternatives, Multi-Card Grouping (3+ cards), and Visual Inspection Toolbar (pan, zoom, 90° rotation, brightness/contrast/invert filters).
- **Recipe Management & Export**: Split-screen transcription review and editor (`recipeboxer/models/recipe.py`, `recipeboxer/api/routes.py`), with multi-format exports (Plan To Eat CSV, Schema.org Recipe JSON-LD, standard JSON).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | SPA Static Hosting & Catch-All Routing | FastAPI static file mounting at `/assets` and SPA HTML5 history catch-all route returning `index.html` | M1, M2 | ORIGINAL_REQUEST §R1 |
| 2 | Theme Subsystem (Light/Dark Mode) | Persistent theme toggle with localStorage persistence and system theme detection | M2 | ORIGINAL_REQUEST §R1 |
| 3 | SSE Real-Time Telemetry | Server-Sent Events endpoint `/api/events` broadcasting ingestion, pairing, and extraction progress | M1, M2 | ORIGINAL_REQUEST §R1 |
| 4 | Session Manager & Resumption | SQLite session persistence, listing historical sessions, and resumption API | M1, M2 | ORIGINAL_REQUEST §R2 |
| 5 | Ingestion Controller & Throttling | Folder path selection, drag-and-drop dropzone, concurrency throttle, slow-batch controls, model selector | M1, M2 | ORIGINAL_REQUEST §R2 |
| 6 | Card Image Streaming Service | `/api/cards/{card_id}/image` endpoint serving scanned card image files securely | M1 | ORIGINAL_REQUEST §R3 |
| 7 | Rapid Queue Keyboard Shortcuts | High-speed keyboard navigation (`Space`/`Enter`, `S`, `B`, `X`, `M`/`Tab`) with input focus trapping | M2 | ORIGINAL_REQUEST §R3 |
| 8 | Candidate Drawer & Multi-Card Grouping | Slide-out candidate drawer with ranked scores/snippets and 3+ card grouping tray | M1, M2 | ORIGINAL_REQUEST §R3 |
| 9 | Visual Inspection Toolbar | Synchronized & independent pan/zoom, 90° rotation, brightness/contrast/invert CSS filters | M2 | ORIGINAL_REQUEST §R3 |
| 10 | Post-Extraction Split-Screen Editor | Side-by-side card viewing (left) and rich editable recipe form (right) | M2 | ORIGINAL_REQUEST §R4 |
| 11 | Inline Recipe CRUD & Validation | Real-time schema validation and saving recipe edits directly to SQLite via `/api/recipes` | M1, M2 | ORIGINAL_REQUEST §R4 |
| 12 | Plan To Eat RFC 4180 CSV Export | 9-column CSV export with CRLF line terminators and double-quote escaping | M1, M2 | ORIGINAL_REQUEST §R5 |
| 13 | Schema.org Recipe JSON-LD Export | Valid JSON-LD Schema.org recipe export with `HowToStep` instructions and ISO 8601 durations | M1, M2 | ORIGINAL_REQUEST §R5 |
| 14 | Standard Recipe JSON Export | Direct raw RecipeSchema JSON export download | M1, M2 | ORIGINAL_REQUEST §R5 |
| 15 | Full-Stack 4-Tier Test Suite | Comprehensive automated unit, component, API, and E2E test suites (Tiers 1-4) | M3, M4 | ORIGINAL_REQUEST Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Backend API & Storage Extensions | Static mounting & catch-all routing, session persistence, card image streaming, multipart upload, candidate drawer endpoints, multi-card approval schemas, recipe CRUD, JSON-LD and raw JSON exporters, SSE streaming | none | DONE |
| M2 | React + TypeScript SPA Frontend | Vite setup, Tailwind styling, Theme toggle, Ingestion Controller, Rapid Queue, Candidate Drawer, Multi-Card Grouping tray, Pan/Zoom/Filter viewer, Recipe Editor, Export modal, SSE integration, build pipeline to `recipeboxer/api/static/` | M1 | DONE |
| M3 | E2E Testing Track Suite (Tiers 1-4) | Pytest API & SSE suite, Vitest component suite, Playwright / E2E integration test suite covering 75+ Tier 1, 75+ Tier 2, 10 Tier 3, and 5 Tier 4 test cases, publishing TEST_READY.md | M1, M2 | DONE |
| M4 | Final Integration & Adversarial Hardening | Verify 100% pass on all test tiers, run Tier 5 adversarial testing with Challenger, Forensic Integrity Audit, and final verification | M1, M2, M3 | DONE |

## Interface Contracts

### 1. SPA Static File & Catch-All Routing
- `GET /assets/{file_path}`: Serves compiled static JS/CSS/image assets from `recipeboxer/api/static/assets/`.
- `GET /{full_path:path}`: Returns `recipeboxer/api/static/index.html` for any client-side routes (e.g. `/`, `/ingest`, `/correlate`, `/recipes`, `/export`) when the request does not match `/api/*` or an existing static file.

### 2. Ingestion & Session Management
- `POST /api/pipeline/start`: JSON body `{folder_path: str, max_concurrency: int, slow_batch_size: int, slow_batch_interval_seconds: float, model: str}` -> returns `{session_id: str, status: str}`.
- `POST /api/cards/upload`: Multipart form upload of card images for drag-and-drop ingestion -> returns `{session_id: str, card_ids: List[str], count: int}`.
- `GET /api/sessions`: Returns list of historical and active sessions stored in SQLite `[{session_id, folder_path, status, created_at, card_count, recipe_count}]`.
- `GET /api/sessions/{session_id}`: Returns session details and progress state.
- `DELETE /api/sessions/{session_id}`: Deletes a session and associated cached records.
- `GET /api/events?session_id={id}`: SSE stream broadcasting live JSON events `{event: "progress"|"state_change"|"recipe_extracted"|"error", data: {...}}`.

### 3. Card Images & Correlation HITL
- `GET /api/cards/{card_id}/image`: Returns image bytes with `Content-Type: image/jpeg` or `image/png` and caching headers.
- `GET /api/hitl/pending?session_id={id}`: Returns current pending correlation proposals.
- `GET /api/hitl/candidates?session_id={id}&card_id={card_id}`: Returns ranked alternate candidates with heuristic score, semantic score, composite score, and reasoning snippet.
- `POST /api/hitl/approve`: JSON body `{session_id: str, front_card_id: str, back_card_id: Optional[str], card_ids: Optional[List[str]], is_standalone: bool, is_rejected: bool, reverse_roles: bool}`.

### 4. Recipe Review, Mutation & Exports
- `GET /api/recipes?session_id={id}`: Returns list of extracted recipes for a session.
- `GET /api/recipes/{recipe_id}`: Returns single `RecipeSchema` JSON.
- `PUT /api/recipes/{recipe_id}`: Accepts updated `RecipeSchema` JSON and persists updates to SQLite -> returns updated `RecipeSchema`.
- `DELETE /api/recipes/{recipe_id}`: Removes a recipe.
- `GET /api/export/csv?session_id={id}`: Returns Plan To Eat RFC 4180 CSV attachment.
- `GET /api/export/json-ld?session_id={id}`: Returns Schema.org Recipe JSON-LD attachment.
- `GET /api/export/json?session_id={id}`: Returns raw RecipeSchema JSON attachment.

## Code Layout
```
recipeboxer/
├── api/
│   ├── app.py              # FastAPI app, static mounts, CORS, SPA fallback
│   ├── routes.py           # REST endpoints, SSE streaming, session & recipe CRUD
│   └── static/             # Vite production build output (index.html, assets/)
├── agent/
│   ├── session.py          # Session state machine, pipeline orchestration
│   └── orchestrator.py     # End-to-end pipeline runner
├── correlation/
│   ├── optimizer.py        # Card pairing optimizer, candidate ranking
│   └── scorer.py           # Heuristic & semantic correlation scoring
├── exporter/
│   ├── plan_to_eat.py      # RFC 4180 CSV exporter
│   ├── json_ld.py          # Schema.org Recipe JSON-LD exporter
│   └── json_export.py      # Raw Recipe JSON exporter
├── ingestion/
│   └── scanner.py          # Card discovery & image validation
├── models/
│   ├── card.py             # ScannedCard, VisualFingerprint models
│   ├── hitl.py             # CardPairApproval, CorrelationCandidate models
│   ├── recipe.py           # RecipeSchema, Ingredient, Direction models
│   └── session.py          # SessionSummary, IngestionConfig models
└── storage/
    ├── cache.py            # SQLite FingerprintCache
    └── session_store.py    # SQLite Session and Recipe Store

frontend/
├── index.html              # SPA entry point
├── package.json            # React 18, Vite, TypeScript, Tailwind CSS, Lucide icons
├── tsconfig.json           # TypeScript configuration
├── vite.config.ts          # Vite configuration with outDir: "../recipeboxer/api/static"
├── tailwind.config.js      # Tailwind CSS configuration with dark mode: 'class'
├── src/
│   ├── main.tsx            # React root mount
│   ├── App.tsx             # Main layout, router / view switcher, theme provider
│   ├── types/              # TypeScript API & state interfaces
│   ├── api/                # API client (REST & SSE EventSource hooks)
│   ├── components/
│   │   ├── Navbar.tsx              # Brand header, session indicator, theme toggle
│   │   ├── IngestionController.tsx # Folder path, dropzone, throttle controls, model selection
│   │   ├── RapidQueue.tsx          # Card comparison queue & keyboard shortcut listener
│   │   ├── CandidateDrawer.tsx     # Slide-out ranked candidate list & reasoning
│   │   ├── MultiCardGrouping.tsx   # 3+ card multi-part grouping tray & reordering
│   │   ├── CardImageViewer.tsx     # Pan/zoom, 90° rotation, contrast/invert filters
│   │   ├── RecipeEditor.tsx        # Side-by-side viewer & editable recipe form
│   │   └── ExportModal.tsx         # Plan To Eat CSV, JSON-LD, JSON download dialog
│   └── hooks/
│       ├── useTheme.ts             # Light/Dark mode state & localStorage persistence
│       ├── useSSE.ts               # Server-Sent Events listener with auto-reconnect
│       └── useKeyboardShortcuts.ts # Input-trapped keyboard navigation
└── tests/                  # Vitest + React Testing Library component tests
```
