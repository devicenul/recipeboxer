# Project: RecipeBoxer Corpus Expansion & Frontend Defect Resolution

## Architecture
RecipeBoxer is an archival culinary document processing pipeline combining computer vision, physical substrate heuristics, multimodal semantic extraction, and a React frontend for ingestion, human-in-the-loop review, recipe editing, and multi-format export.

- **Frontend Subsystem (`frontend/src/`)**: React 18 + TypeScript + Vite + Tailwind CSS + Lucide Icons. Manages application state, tabs (`ingest`, `hitl`, `recipes`, `export`), SSE connection (`useSSE`), and recipe review/editing.
- **Corpus & Generator Subsystem (`recipeboxer/tools/`, `tests/fixtures/sample_cards/`)**: PIL-based high-resolution archival document synthesizer generating realistic recipe cards across 6 historical modalities with textures, aging, stamps, clippings, and ink physics. Ground truth stored in `manifest.json` and documented in `README.md`.
- **Backend Correlation & Heuristics Subsystem (`recipeboxer/engine/`, `recipeboxer/heuristics/`)**: Substrate colorimetry (CIE L*a*b*), ruling lines, ink analysis, bleed-through detection, continuation heuristics, and DAG sequence chaining (`PairingOptimizer`).
- **Extraction & Export Subsystem (`recipeboxer/pipeline/`, `recipeboxer/export/`)**: Isolated recipe transcription, preservation of attribution and provenance (`source_notes`, `description`), and 8 export formats (PlanToEat, Paprika, Markdown, Cooklang, JSON-LD, GoogleDocs, PDF, Docx).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Single-fire Pipeline Completion Tab Transition | Ensure `App.tsx` transitions to `recipes` tab strictly once upon entering `COMPLETED` state using `completedSessionsRef` execution tracker. | M1 | ORIGINAL_REQUEST R1.1, R1.2 |
| 2 | Stable Tab Switching Post-Completion | Fix auto-reversion defect so switching to Export, Ingest, HITL, or dialogs remains on target view without resetting on SSE reconnects/heartbeats. | M1 | ORIGINAL_REQUEST R1.1, R1.3 |
| 3 | Proceed to Export Navigation Stability | In Recipe Editor, clicking Next -> Proceed to Export stays on Export view and preserves editor state without snapping back to Recipe 1. | M1 | ORIGINAL_REQUEST R1.1, R1.3 |
| 4 | Corpus Expansion: Vintage Handwritten Cursive (1940s–1970s) | Add 2 new recipes (4 high-res front/back card scans) with fountain pen / ballpoint ink dynamics and paper aging. | M2 | ORIGINAL_REQUEST R2.1 |
| 5 | Corpus Expansion: Typewritten & Stamped (1950s–1970s) | Add 2 new recipes (4 high-res front/back card scans) with typewriter ribbon wear, character bounce, and vintage club date stamps. | M2 | ORIGINAL_REQUEST R2.2 |
| 6 | Corpus Expansion: Magazine Clippings & Package Labels (1960s–1980s) | Add 2 new recipes (4 high-res front/back card scans) with multi-column newsprint cutouts, glue boundaries, and tape residue. | M2 | ORIGINAL_REQUEST R2.3 |
| 7 | Corpus Expansion: Stained / Damaged / Edge-Worn Substrates | Add 2 new recipes (4 high-res front/back card scans) with oil smudges, coffee rings, dog-ears, and torn edges. | M2 | ORIGINAL_REQUEST R2.4 |
| 8 | Corpus Expansion: Multi-Card Narrative Sets | Add 2 new multi-card sets (one 3-card continuous sequence and one 4-card continuous sequence with explicit continuation cues; 7 cards total). | M2 | ORIGINAL_REQUEST R2.5 |
| 9 | Corpus Expansion: Standalone Single-Sided Cards | Add 2 new single-sided standalone recipes (2 high-res card scans). | M2 | ORIGINAL_REQUEST R2.6 |
| 10 | Card Generator Tool Update | Update `recipeboxer/tools/card_generator.py` with `ARCHIVAL_RECIPE_TEMPLATES` and deterministic generation logic for all 48 cards and manifest creation. | M2 | ORIGINAL_REQUEST R2 |
| 11 | Ground Truth Manifest Synchronization | Update `tests/fixtures/sample_cards/manifest.json` with ground truth data, roles, SHA256 hashes, and provenance for all 48 cards across 24 recipes. | M2 | ORIGINAL_REQUEST R2, R3.1 |
| 12 | Provenance & Licensing Documentation | Update `tests/fixtures/sample_cards/README.md` with complete provenance, public domain / CC0 licensing, and historical background for all 24 recipes. | M3 | ORIGINAL_REQUEST R3.2 |
| 13 | Attribution Preservation & Display | Verify recipe attribution metadata (`source_notes`, `description`, dates, archive series) flows into frontend Recipe Editor and Export subsystems. | M3 | ORIGINAL_REQUEST R3.3 |
| 14 | Generalization & Anti-Overfitting Verification | Ensure all heuristics evaluate physical document traits without hardcoded card ID bypasses; assert ground truth in tests while keeping production heuristics disciplined and well-calibrated. | M4 | ORIGINAL_REQUEST R4.1, Follow-up |
| 15 | Multi-Card Sequence Chaining & Review Retention | Verify that 3-card and 4-card sets chain into sequence proposals, 0 cards are dropped from review queues, and strict content isolation is maintained. | M4 | ORIGINAL_REQUEST R4.2 |
| 16 | Test Suite Regression & Count Updates | Update test assertions expecting 23 cards / 12 recipes to 48 cards / 24 recipes, ensuring 0 regressions across all existing tests. | M4 | ORIGINAL_REQUEST Verification |
| 17 | Full End-to-End Suite Verification | Verify 100% pass across backend Pytest suite, frontend Vitest suite (`npm test -- --run`), and frontend production build (`npm run build`). | M5 | ORIGINAL_REQUEST Verification |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Frontend Tab Navigation & Reversion Defect Fix | Fix `frontend/src/App.tsx` completion tracker ref, prevent SSE reconnect reversion, add vitest routing tests. | None | DONE |
| M2 | Corpus Generator & Image Expansion (25 new cards / 12 new recipes across 6 dimensions) | Update `card_generator.py`, generate 25 new high-res cards (total 48 cards), update `manifest.json`. | None | DONE |
| M3 | Attribution, Provenance & Metadata Subsystems | Update `README.md`, verify `source_notes` and provenance preservation across frontend/export. | M2 | DONE |
| M4 | Generalization, Correlation Heuristics & Test Suite Updates | Verify physical traits heuristics, update test suite card count assertions, verify 3-card/4-card chaining. | M2 | DONE |
| M5 | Full End-to-End Verification & Gate Hardening | Run complete backend pytest suite, frontend Vitest suite, and frontend build. | M1, M3, M4 | DONE |

## Interface Contracts
### Frontend `App.tsx` ↔ `useSSE.ts`
- `App.tsx` tracks completed sessions via `completedSessionsRef = useRef<Set<string>>(new Set())`.
- `handlePipelineCompleted(sessionId, status)` executes `setActiveTab('recipes')` and `fetchRecipes(...)` strictly once per unique completed session run.
- Non-completed state events reset the session ID from `completedSessionsRef` to allow re-runs.

### Card Generator ↔ `manifest.json` & Image Fixtures
- Tool: `recipeboxer/tools/card_generator.py`
- Output directory: `tests/fixtures/sample_cards/`
- Manifest path: `tests/fixtures/sample_cards/manifest.json`
- Each recipe entry defines: `recipe_id`, `title`, `dimension`, `provenance`, `source_notes`, `historical_date`, `archive_series`, `cards` array (with `filename`, `role`, `sha256`, `dimensions`), `ground_truth` (`ingredients`, `steps`, `servings`, `prep_time`, `cook_time`).

### Heuristics ↔ Test Assertions
- Ground truth test cases explicitly test expected matching from `manifest.json`.
- Production heuristics in `recipeboxer/heuristics/` operate on extracted visual/textual features (CIE L*a*b* ΔE ≤ 5.0, ruling pitch, ink classification, continuation headers, sequence cues) without hardcoded card ID bypasses.

## Code Layout
- Frontend: `frontend/src/App.tsx`, `frontend/src/components/`, `frontend/tests/app_routing.test.tsx`
- Generator Tool: `recipeboxer/tools/card_generator.py`
- Test Fixtures: `tests/fixtures/sample_cards/*.png`, `tests/fixtures/sample_cards/manifest.json`, `tests/fixtures/sample_cards/README.md`
- Backend Engine & Tests: `recipeboxer/engine/`, `recipeboxer/heuristics/`, `tests/test_sample_cards.py`, `tests/test_pipeline.py`, `tests/test_heuristics.py`, `tests/test_exporter.py`
