# Project: RecipeBoxer Platform Enhancement

## Architecture
RecipeBoxer is a full-stack platform for digitizing, transcribing, correlating, editing, and exporting vintage physical recipe cards:
- **Frontend SPA**: React 18.3, TypeScript 5.7, Vite 6, Tailwind CSS, Vitest.
  - Ingestion Controller (`frontend/src/components/ingestion/`)
  - HITL Correlation Workspace (`frontend/src/components/hitl/`)
  - Recipe Editor & Review Inspector (`frontend/src/components/recipe/`, `frontend/src/components/viewer/`)
  - Export Center & Format Modals (`frontend/src/components/export/`)
  - SSE State Synchronization (`frontend/src/hooks/useSSE.ts`)
- **Backend API & Processing**: FastAPI, Pydantic v2 domain schemas, SQLite WAL storage, Google Antigravity multi-agent orchestration, Gemini multimodal extraction, ReportLab/PDF 1.4, OpenXML DOCX, Plan To Eat CSV, Google Docs/Drive API.
  - REST & SSE Routes (`src/recipeboxer/api/routes.py`)
  - Orchestration Agent (`src/recipeboxer/agent/orchestrator.py`)
  - Extraction & Prompt Routing (`src/recipeboxer/extraction/extractor.py`, `src/recipeboxer/vision/router.py`)
  - Correlation Engine (`src/recipeboxer/correlator/`)
  - Exporters (`src/recipeboxer/exporter/`)
  - Domain & Schema Models (`src/recipeboxer/models/`)

---

## Feature Inventory

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1.1 | Directory Picker (Modern API) | Add interactive folder browser button using `showDirectoryPicker()` with `<input webkitdirectory>` fallback in `SourceSelector.tsx`. | M1 | ORIGINAL_REQUEST §R1 |
| F1.2 | SSE Stability & Debounce | Fix SSE reconnection churn and eliminate rapid `/api/recipes` and `/api/events` polling loop using ref-stabilized callbacks and backoff. | M1 | ORIGINAL_REQUEST §R1 |
| F1.3 | Batch Default Tags | Accept session-level batch default tags in Ingestion Controller, store in session metadata, and merge into extracted recipes. | M1 | ORIGINAL_REQUEST §R1 |
| F2.1 | Multi-Card Full-Size Modal | Provide full-size modal & split canvas view for multi-card sets (3+ cards) during HITL correlation review. | M2 | ORIGINAL_REQUEST §R2 |
| F2.2 | Positional Drag/Reorder Controls | Support drag/reorder of 3+ cards with explicit positional indices (Front, Back, Continuation 1..N). | M2 | ORIGINAL_REQUEST §R2 |
| F2.3 | Multi-Card Sequence Preservation | Backend preserves reordered card sequences and merges them in user-specified order during multimodal extraction. | M2 | ORIGINAL_REQUEST §R2 |
| F3.1 | Card Images in Review Pane | Ensure valid card image rendering (`/api/cards/{card_id}/image`) with fallback loading states in Recipe Editor. | M3 | ORIGINAL_REQUEST §R3 |
| F3.2 | Workflow Navigation Dialog | Clicking "Next" on the last recipe triggers `"Go to export? [Yes] [Cancel]"` confirmation dialog routing to Export tab. | M3 | ORIGINAL_REQUEST §R3 |
| F3.3 | Inline Tag Editing & State Sync | Support inline tag addition, removal, keyboard triggers (Enter/comma/semicolon), and state synchronization in Recipe Editor. | M3 | ORIGINAL_REQUEST §R3 |
| F4.1 | Recipe Title Guessing Flag | Infer titles when not explicitly stated/low confidence and append ` [GUESS]` suffix to flag for verification. | M3 | ORIGINAL_REQUEST §R4 |
| F4.2 | Anecdote & Story Preservation | Extract family stories, marginalia, vintage dates into `description`/`source_notes` without polluting `ingredients`/`directions`. | M3 | ORIGINAL_REQUEST §R4 |
| F5.1 | Google Docs OAuth & Doc Creation | Implement Google OAuth 2.0 Web flow and Google Docs/Drive API creation with embedded scan images and formatted typography. | M4 | ORIGINAL_REQUEST §R5 |
| F5.2 | Embedded Images in PDF & DOCX | Embed high-resolution scanned card images alongside transcribed recipe content with proportional aspect scaling. | M4 | ORIGINAL_REQUEST §R5 |
| F5.3 | Customizable Export Filenames | Present editable suggested filename modal before initiating download across all 9 export formats. | M4 | ORIGINAL_REQUEST §R5 |
| F5.4 | Tag Serialization Across Formats | Enforce tag serialization compliance in Plan To Eat CSV (Col 8), Schema.org keywords, Markdown/Cooklang frontmatter, Paprika. | M4 | ORIGINAL_REQUEST §R5 |
| F6.1 | Full Automated Verification | Pass 100% pytest (`./test.sh`) and frontend vitest (`npm test -- --run`) across all new and existing features. | M5 | ORIGINAL_REQUEST §Acceptance Criteria |
| F7.1 | Clean-Break Domain Model & Schema Modernization | Remove binary `front_card_id`/`back_card_id` and `FRONT`/`BACK` role enums across Python models (`domain.py`, `correlation.py`, `hitl.py`, `session.py`) and TypeScript types (`hitl.ts`, `domain.ts`, `pipeline.ts`). Adopt canonical `ordered_card_ids: List[str]` and ordinal `part_index`/`total_parts`. | M6 | ORIGINAL_REQUEST §R2 |
| F7.2 | Open-Ended N-Part Sequence Correlation Engine | Directed transition graph and maximal path chaining evaluating physical $\Delta E$, scan proximity, ruling, ink, stain bleed-through reflection, "Part N" headers, and semantic continuation for $N \ge 1$ cards. | M7 | ORIGINAL_REQUEST §R3 |
| F7.3 | REST API, SQLite SessionStore & SSE Alignment | Update SQLite `session_proposals` table schema with dynamic migration, FastAPI endpoints (`/api/hitl/*`), and SSE telemetry to consume and emit `ordered_card_ids`. | M8 | ORIGINAL_REQUEST §R4 |
| F7.4 | Unified "Part 1 ... N" HITL Workflow & UI | Render cards uniformly as "Part 1 of N", "Part 2 of N", etc. across `HitlWorkspace`, `RapidQueueCard`, `MultiCardGrouping`, `MultiCardInspectionModal`, `CandidateDrawer`, and `RecipeEditorView` with drag-and-drop reordering, split, and merge. | M9 | ORIGINAL_REQUEST §R5 |
| F7.5 | Multimodal Extraction & Exporter Sequence Preservation | Transcribe ordered $N$-card image sequences in exact positional order and serialize card references uniformly across all 9 export formats. | M10 | ORIGINAL_REQUEST §R6 |
| F7.6 | Full-Stack Platform & Adversarial Verification | 100% pass across all 1,442 backend pytest tests (Tiers 1-5) and 218 frontend Vitest tests with zero regressions. | M11 | ORIGINAL_REQUEST §Acceptance Criteria |

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Ingestion & Connection Stability | Directory picker with `showDirectoryPicker()` & fallback, SSE reconnection debounce/backoff & fix rapid polling loop, Batch Default Tags. | none | DONE |
| M2 | Multi-Card (>2 Cards) HITL Workflow & Ordering | Full-size modal / split canvas view for 3+ cards, drag/reorder positional sequence controls, backend sequence preservation & merging. | M1 | DONE |
| M3 | Recipe Editor UX & Extraction Intelligence | Card images in review pane (`/api/cards/{card_id}/image`), last-recipe navigation confirmation dialog, inline tag editing, ` [GUESS]` title flagging, anecdote & marginalia preservation. | M1 | DONE |
| M4 | Exporters, Google Docs OAuth, Images & Filenames | Google Docs OAuth 2.0 & direct doc creation, high-res embedded scans in PDF & DOCX with proportional scaling, customizable export filenames modal, universal tag serialization. | M1, M3 | DONE |
| M5 | Final Verification & Adversarial Coverage Hardening | 100% E2E test pass (Tiers 1-4) and Tier 5 adversarial coverage hardening; sign-off verified. | M1, M2, M3, M4 | DONE |
| M6 | Clean-Break Domain Model & Schema Modernization | Canonical `ordered_card_ids: List[str]` across models, SQLite dynamic table migration, TypeScript interfaces. | M5 | DONE |
| M7 | Open-Ended N-Part Sequence Correlation Engine | Directed transition graph ($\Delta E$, scan proximity, ruling, ink, stains, headers, semantics) and maximal path chaining. | M6 | DONE |
| M8 | REST API, SQLite SessionStore & SSE Alignment | FastAPI routes (`/api/hitl/*`, `/api/cards/*`), SQLite JSON array persistence, SSE telemetry stream. | M6, M7 | DONE |
| M9 | Unified "Part 1 ... N" HITL Workflow & UI | "Part 1 of N" badging, drag-and-drop positional reordering, modal inspection, sequence reversal, split/merge. | M6, M8 | DONE |
| M10 | Multimodal Extraction & Exporter Sequence Preservation | Multimodal extraction prompt sequencing and 9 export format sequence serialization. | M6, M7, M8 | DONE |
| M11 | Final Verification & Adversarial Coverage Hardening | 100% pass on 1,442 backend pytest tests (Tiers 1-5) and 218 frontend Vitest tests, zero regressions. | M6, M7, M8, M9, M10 | DONE |

---

## Interface Contracts

### Ingestion ↔ Session Store & Pipeline
- **Request**: `PipelineStartRequest(directory_path=..., default_tags=['Tag1', 'Tag2'])`
- **Multipart Upload**: `POST /api/cards/upload` with form field `default_tags` (JSON string or comma-separated list).
- **Session Model**: `session.config.default_tags: List[str]`.
- **Extraction Hook**: `recipe.tags = clean_and_deduplicate(session.config.default_tags + extracted_tags)`.

### SSE Telemetry ↔ Frontend `useSSE`
- **Endpoint**: `GET /api/events?session_id={session_id}`
- **Behavior**: Emits `state_change`, `progress`, `hitl_ready`, `recipe_extracted`, `completed`, `failed`, `cancelled`, and `: ping` heartbeat every 10s. Never abruptly disconnects; stays open with heartbeats.
- **Frontend Hook**: `useSSE({ sessionId, enabled })` with ref-stabilized event handlers (`callbacksRef.current`) and exponential reconnect backoff.

### HITL Sequence Review ↔ Correlation Merger & Extraction
- **Approval Payload**: `ApprovalPayload(session_id=..., approvals=[CardSequenceApproval(proposal_id=..., ordered_card_ids=['c1', 'c2', 'c3', 'c4'], decision='APPROVE')], rejected_card_ids=[])`
- **Orchestrator Contract**: `orchestrator._extract_single_recipe` resolves `ordered_card_ids = approval.ordered_card_ids`, maps to `ordered_fps`, and invokes `extractor.extract_cards(ordered_fps)`.
- **Recipe Association**: `recipe.card_ids = ordered_card_ids`, `recipe.image_urls = ["/api/cards/{cid}/image?session_id=..." for cid in ordered_card_ids]`.

### Recipe Editor ↔ Navigation & Tags
- **Review Confirmation**: Clicking "Next" on `recipes[recipes.length - 1]` renders `<Modal title="Recipe Review Complete">` with `[Cancel]` and `[Yes, Go to Export]`.
- **Image URL**: Calls `GET /api/cards/{card_id}/image?session_id={session_id}` with `isLoading` and `hasError` reset on `cardId`/`imageUrl` change.
- **Tag Manager**: Emits `onAddTag(tag)` and `onRemoveTag(tag)` synchronizing with `useRecipeForm.isDirty` and saving via `api.updateRecipe`.

### Exporters ↔ Filenames & Google Docs OAuth
- **Custom Filenames**: All export endpoints accept `filename: Optional[str] = Query(None)`. Response sets `Content-Disposition: attachment; filename="{sanitized_filename}"`.
- **Frontend Export Modal**: `ExportFilenameModal` renders suggested filename `getSuggestedFilename(tab, sessionId, customPrefix)`, allows editing, and passes name to downloader.
- **Google Docs Direct Creation**:
  - `POST /api/export/google-docs/create-document`
  - Body: `{"session_id": "...", "access_token": "...", "document_title": "..."}`
  - Uploads card images to Google Drive, creates Google Doc, applies batch update requests, returns `{"document_id": "...", "url": "..."}`.
- **Tag Serialization**:
  - Plan To Eat CSV: Column 8 `Tags` (`", ".join(tags)` with RFC 4180 quote escaping)
  - Schema.org JSON-LD: `keywords` comma string + `recipeCategory`
  - Markdown: YAML frontmatter `tags: [...]`
  - Cooklang: `>> tags: ...`
  - Paprika: `"categories": [...]`

---

## Code Layout

- Frontend:
  - `frontend/src/components/ingestion/SourceSelector.tsx`
  - `frontend/src/components/ingestion/SessionLauncher.tsx`
  - `frontend/src/components/ingestion/ConfigPanel.tsx`
  - `frontend/src/components/hitl/HitlWorkspace.tsx`
  - `frontend/src/components/hitl/RapidQueueCard.tsx`
  - `frontend/src/components/hitl/MultiCardGrouping.tsx`
  - `frontend/src/components/hitl/MultiCardInspectionModal.tsx`
  - `frontend/src/components/recipe/RecipeEditorView.tsx`
  - `frontend/src/components/recipe/RecipeForm.tsx`
  - `frontend/src/components/recipe/TagManager.tsx`
  - `frontend/src/components/viewer/CardViewerCanvas.tsx`
  - `frontend/src/components/export/ExportDialog.tsx`
  - `frontend/src/components/export/ExportFilenameModal.tsx`
  - `frontend/src/hooks/useSSE.ts`
  - `frontend/src/App.tsx`
  - `frontend/src/types/pipeline.ts`
  - `frontend/src/utils/api.ts`
- Backend:
  - `src/recipeboxer/api/routes.py`
  - `src/recipeboxer/agent/orchestrator.py`
  - `src/recipeboxer/extraction/extractor.py`
  - `src/recipeboxer/vision/router.py`
  - `src/recipeboxer/models/pipeline.py`
  - `src/recipeboxer/models/hitl.py`
  - `src/recipeboxer/models/recipe.py`
  - `src/recipeboxer/models/session.py`
  - `src/recipeboxer/exporter/plan_to_eat.py`
  - `src/recipeboxer/exporter/google_docs.py`
  - `src/recipeboxer/exporter/docx_export.py`
  - `src/recipeboxer/exporter/pdf_export.py`
  - `src/recipeboxer/exporter/markdown_export.py`
  - `src/recipeboxer/exporter/cooklang.py`
  - `src/recipeboxer/exporter/paprika.py`
  - `src/recipeboxer/exporter/json_ld.py`
- Tests:
  - `tests/`
  - `frontend/tests/`
