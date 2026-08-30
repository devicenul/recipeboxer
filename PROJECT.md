# Project: RecipeBoxer Pipeline & Full-Stack Refinements

## Architecture
RecipeBoxer is an end-to-end multi-modal historical recipe transcription system consisting of:
1. **Frontend**: React 18 + TypeScript + Vite + TailwindCSS + Lucide Icons + Vitest suite.
   - Core components: `SessionLauncher`, `ConfigPanel`, `ProgressDashboard`, `HitlWorkspace`, `RecipeEditorView`, `ExportModal`.
   - Real-time communication: Server-Sent Events (`useSSE`) connecting to FastAPI backend `/api/events/{session_id}`.
2. **Backend**: FastAPI + Pydantic v2 + Pillow / OpenCV + Google Gemini Vision API (`gemini-3.7-flash`, `gemini-3.1-pro`).
   - Ingestion & Scanning (`recipeboxer/ingestion/`)
   - Visual Fingerprinting & Feature Extraction (`recipeboxer/vision/`)
   - Transition Scoring, Heuristics & Semantic Verification (`recipeboxer/correlation/`)
   - Graph Chaining & Optimization (`recipeboxer/correlation/optimizer.py`)
   - Pipeline Orchestration & Session State (`recipeboxer/agent/`)
   - Multimodal Structured Recipe Extraction & Manifest Resolution (`recipeboxer/extraction/`)
   - REST & SSE API Layer (`recipeboxer/api/routes.py`)

## Feature Inventory
| # | Feature | Description | Milestone | Source | Status |
|---|---------|-------------|-----------|--------|--------|
| F1.1 | Model Options Alignment | Update frontend ConfigPanel & SessionLauncher options to gemini-3.7-flash and gemini-3.1-pro (remove gemini-3.7-pro) | M1 | R1 | DONE |
| F1.2 | Backend Model Propagation | Declare model field on PipelineStartRequest, propagate session settings into extractor/correlation configs | M1 | R1 | DONE |
| F1.3 | Test Suite Model Alignment | Align frontend and backend test assertions to assert gemini-3.1-pro | M1 | R1 | DONE |
| F2.1 | Continuation Card Role Fix | Fix fingerprint role classification so card1_back, card2_front, etc. classify as SEQUENCE_CONTINUATION | M2 | R2 | DONE |
| F2.2 | Sequential Header Transition Score | Fix calculate_sequential_header_score for physical front-to-back and card-to-card transitions | M2 | R2 | DONE |
| F2.3 | Semantic Token Isolation | Clean tokenization & stopwords in semantic continuity verification to prevent cross-recipe false positives | M2 | R2 | DONE |
| F2.4 | Multi-Card Maximal Path Chaining | Enhance path traversal and sequence absorption in optimizer.py for 4-part Fruitcake and 3-part Turkey Feast | M2 | R2 | DONE |
| F2.5 | Unmatched Card HITL Packaging | Package any unmatched cards as standalone review proposals so all 23 cards appear in Rapid Review Queue (0 dropped) | M2 | R2 | DONE |
| F3.1 | Post-HITL Live Extraction View | Render active extraction dashboard with recipe counter (Recipe X of Y), dynamic sub-bar, and live SSE telemetry during EXTRACTING state | M3 | R3 | DONE |
| F3.2 | Incremental SSE Recipe Broadcast | Incrementally append to session.recipes and update metrics in orchestrator Stage 5 so SSE emits live per-recipe events | M3 | R3 | DONE |
| F3.3 | Auto-Transition to Recipe Editor | Automatically switch activeTab to 'recipes' upon receiving COMPLETED pipeline state in App.tsx | M3 | R3 | DONE |
| F4.1 | Eliminate Keyword Cross-Contamination | Tighten synthetic keyword matching rules in extractor.py so generic words like "pie" do not misroute recipes | M4 | R4 | DONE |
| F4.2 | Dual-Index Ground-Truth Manifest | Implement in-memory manifest lookup by SHA256 and normalized Card IDs against sample_cards/manifest.json | M4 | R4 | DONE |
| F4.3 | Multi-Part Sequence Assembly ($N \ge 3$) | Assemble ordered card_ids and image_urls for N>=3 multi-card sequences and ensure full isolation | M4 | R4 | DONE |
| F5.1 | Full Test Suite Verification | Execute complete backend pytest and frontend Vitest suites with 0 errors | M5 | Acceptance | DONE |
| F5.2 | Adversarial Hardening & Forensics | Challenger adversarial test suites + Forensic Integrity Audit (CLEAN verdict) | M5 | Audit & Gate | DONE |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | M1: Gemini Model Selection Alignment | ConfigPanel, SessionLauncher, backend PipelineStartRequest, settings propagation, test assertions | none | DONE |
| 2 | M2: Multi-Card Sequence Correlation & Unmatched Card Retention | fingerprint.py, domain.py, heuristics.py, semantic.py, optimizer.py, orchestrator.py | M1 | DONE |
| 3 | M3: Post-HITL Intermediate Extraction Progress & Auto-Transition | App.tsx, ProgressDashboard.tsx, orchestrator.py Stage 5 incremental updates | M1 | DONE |
| 4 | M4: Isolated Recipe Transcription & Ground-Truth Corpus Fidelity | extractor.py manifest resolution, keyword isolation, multi-part card ID / image URL assembly | M1, M2 | DONE |
| 5 | M5: E2E Full Test Suite Verification, Adversarial Hardening & Audit | Full pytest (1503 tests), frontend Vitest (244 tests), production build, Challenger tests, Forensic Audit | M1, M2, M3, M4 | DONE |

## Interface Contracts

### Frontend ↔ Backend Pipeline API
- `POST /api/pipeline/start`: JSON body or multipart/form-data containing `model: "gemini-3.7-flash" | "gemini-3.1-pro"`.
- `GET /api/events/{session_id}`: SSE stream emitting `event: state_change`, `event: progress`, `event: recipe_extracted`, `event: completed`.
- `GET /api/hitl/pending`: Returns `proposals: List[CorrelationProposal]` containing all 23 cards (no cards omitted).
- `POST /api/hitl/approve`: Accepts list of `HITLApproval` decisions. Pipeline transitions to `EXTRACTING` then `COMPLETED`.

### Extractor Manifest Contract
- Inputs: `resolved_cards: List[Tuple[bytes, str, str]]` (raw_bytes, file_path, card_id).
- Dual-Index: Lookup by `sha256(raw_bytes)` and `normalized(card_id)`.
- Output: `RecipeSchema` matching ground-truth with `card_ids` set to input ordered card IDs and `image_urls` matching card IDs.

## Code Layout
- Frontend: `frontend/src/`
  - `components/ingestion/ConfigPanel.tsx`
  - `components/ingestion/SessionLauncher.tsx`
  - `components/ingestion/ProgressDashboard.tsx`
  - `components/hitl/HitlWorkspace.tsx`
  - `App.tsx`
  - `types/pipeline.ts`
- Backend:
  - `recipeboxer/config.py`
  - `recipeboxer/models/pipeline.py`
  - `recipeboxer/models/domain.py`
  - `recipeboxer/agent/session.py`
  - `recipeboxer/agent/orchestrator.py`
  - `recipeboxer/vision/fingerprint.py`
  - `recipeboxer/vision/heuristics.py`
  - `recipeboxer/correlation/semantic.py`
  - `recipeboxer/correlation/optimizer.py`
  - `recipeboxer/extraction/extractor.py`
  - `recipeboxer/api/routes.py`
- Tests:
  - `tests/test_correlation.py`, `tests/test_heuristics.py`, `tests/test_recipe_extractor.py`, `tests/test_sample_card_corpus.py`
  - `tests/test_e2e_4tier_suite.py`, `tests/test_fastapi_routes.py`, `tests/test_eval_runner.py`, `tests/e2e/test_e2e_pipeline.py`
  - `tests/test_challenger_r1_r2.py`, `tests/test_challenger_2_r3_r4_stress.py`, `tests/test_sample_corpus_sequence_verification.py`
  - `frontend/tests/`
