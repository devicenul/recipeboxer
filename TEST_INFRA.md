# E2E Test Infrastructure: RecipeBoxer Phase 9 Multi-Session Persistence & Export Curation

## 1. Test Philosophy & Architecture

The RecipeBoxer Phase 9 E2E Test Suite provides an exhaustive, requirement-driven, opaque-box verification platform across both the React 18+ TypeScript SPA frontend and the FastAPI backend engine.

### Core Testing Principles
1. **Opaque-Box Verification**: Exercise platform functionality strictly through public REST API endpoints, Server-Sent Events (SSE), standard filesystem interfaces, `.recipeboxer` archive containers, and frontend DOM component trees / user events without relying on private implementation details or fragile internal monkeypatches.
2. **Progressive 4-Tier Test Matrix**:
   - **Tier 1 (Feature Coverage)**: Validates nominal happy path functionality across all Phase 9 platform features (≥5 tests per feature).
   - **Tier 2 (Boundary & Corner Cases)**: Stress tests limits, zero-states, extreme inputs, ZipSlip path traversal payloads, corrupted archives, hash mismatches, and network anomalies (≥5 tests per feature).
   - **Tier 3 (Cross-Feature Combinations)**: Evaluates pairwise interaction dynamics across archive export/import, incremental duplicate-skipping ingestion, baseline recipe merging, and multi-format curated cookbook publishing (≥10 comprehensive interaction tests).
   - **Tier 4 (Real-World Application Scenarios)**: Simulates complex multi-generational heirloom digitization workflows spanning multi-day scanning sessions, archive backup & merge, duplicate bypass, and styled heirloom cookbook publishing (≥5 end-to-end multi-stage scenarios).
3. **Deterministic & Isolated Execution**: Every test runs in isolated temporary workspaces with unique session IDs, dedicated temp directories for archive extraction, in-memory/isolated SQLite session stores, and self-contained mock responses.

---

## 2. Feature Inventory & Coverage Mapping

| # | Feature | Milestone | Description & Contract | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|---------|-----------|------------------------|:------:|:------:|:------:|:------:|
| **F1** | Archive Models & Manifest Schema | M1 | Pydantic models for `.recipeboxer` archive manifest (`manifest.json`), `ArchiveAssetRecord`, `ArchiveCardRecord`, `StepImageBinding`, `MarginaliaEntry` | ≥5 | ≥5 | T3.1 | T4.S1 |
| **F2** | `.recipeboxer` ZIP Storage Engine | M1 | ZIP packing/unpacking, SHA-256 content addressing (`assets/scans/<sha256>.<ext>`), ZipSlip rejection, integrity validation | ≥5 | ≥5 | T3.1, T3.2 | T4.S1, T4.S4 |
| **F3** | Project Export/Import/Merge Endpoints | M1 | REST endpoints `GET /api/project/export`, `POST /api/project/import`, `POST /api/project/merge` | ≥5 | ≥5 | T3.2, T3.3 | T4.S1, T4.S2 |
| **F4** | Frontend Archive UI Touchpoints | M1 | `.recipeboxer` tab in `ExportDialog`, restore & import dropzones in `SessionLauncher` and `SessionHistoryTable` | ≥5 | ≥5 | T3.3 | T4.S1 |
| **F5** | Baseline Archive Ingestion Loading | M2 | `PipelineStartRequest` and `POST /api/cards/upload` accepting baseline `.recipeboxer` archive or session ID | ≥5 | ≥5 | T3.4 | T4.S2 |
| **F6** | SHA-256 Duplicate-Skipping Engine | M2 | Card SHA-256 calculation against baseline manifest, partitioning skipped vs new cards | ≥5 | ≥5 | T3.4, T3.5 | T4.S2, T4.S3 |
| **F7** | Pipeline Stage Bypass & Recipe Merge | M2 | Bypassing vision/OCR/HITL/LLM extraction for duplicate cards, merging baseline recipes with new extractions | ≥5 | ≥5 | T3.4, T3.5 | T4.S2, T4.S3 |
| **F8** | Telemetry & SSE Progress Accounting | M2 | `ProgressMetrics` emitting `skipped_cards`, `newly_processed_cards`, `baseline_recipes_count` via SSE | ≥5 | ≥5 | T3.5 | T4.S2 |
| **F9** | Frontend Ingestion Baseline Controls | M2 | Baseline archive dropzone/session selector in `SessionLauncher`, duplicate skipping telemetry in `ProgressDashboard` | ≥5 | ≥5 | T3.4 | T4.S2 |
| **F10** | Export Curation & Grouping Engine | M3 | `GroupingPreset` (`none`, `course`, `contributor`, `tags`, `alphabetical`, `custom`), `CuratedCookbook`, `CuratedSection`, reorder logic | ≥5 | ≥5 | T3.6, T3.7 | T4.S4, T4.S5 |
| **F11** | DOCX Exporter TOC & Section Dividers | M3 | Decorative section divider pages (Georgia 28pt bold, ornaments) and styled TOC with dot leaders (`w:leader="dot"`) | ≥5 | ≥5 | T3.6, T3.8 | T4.S5 |
| **F12** | PDF Exporter TOC & Section Dividers | M3 | 2-pass pagination engine, dedicated TOC canvas page with dot leaders, and double-bordered section divider pages | ≥5 | ≥5 | T3.6, T3.8 | T4.S5 |
| **F13** | Google Docs & Markdown Curation | M3 | Google Docs section breaks / dividers & hyperlinked TOC; Markdown section headers & index | ≥5 | ≥5 | T3.7, T3.8 | T4.S5 |
| **F14** | Export REST Endpoints Curation Args | M3 | `/api/export/*` endpoints accepting `recipe_order`, `grouping_preset`, `include_section_dividers`, `include_toc`, `toc_title` | ≥5 | ≥5 | T3.7 | T4.S4, T4.S5 |
| **F15** | Frontend Export Curation Toolbar | M3 | Drag-and-drop / manual reordering, preset selector pills, TOC / divider toggles in `ExportDialog` | ≥5 | ≥5 | T3.8 | T4.S4, T4.S5 |
| **F16** | 4-Tier Automated Test Matrix | M4 | Unit, integration, regression, and E2E lifecycle test suites across archive, ingestion, and export curation | ≥5 | ≥5 | T3.1..T3.8 | T4.S1..S5 |

---

## 3. Test Tier Breakdown & Methodology

### Tier 1: Feature Coverage (Happy Path Verification)
- **Objective**: Verify nominal functionality of every Phase 9 feature under expected inputs and user interactions.
- **Backend Scope**:
  - Archive manifest schema validation, format versioning, Pydantic model serialization/deserialization.
  - ZIP creation with `manifest.json` and `assets/scans/<sha256>.<ext>`, archive extraction, asset content addressing.
  - Endpoints `GET /api/project/export`, `POST /api/project/import`, `POST /api/project/merge` returning 200 OK and valid session data.
  - Ingestion pipeline with baseline archive skipping 100% of duplicate cards and restoring baseline recipes.
  - Ingestion pipeline with 0% duplicates processing 100% of new cards.
  - Curation presets (Course, Contributor, Tags, Alphabetical, Custom) grouping recipes accurately.
  - Exporters (DOCX, PDF, Google Docs, Markdown) generating styled section divider pages and dot-leader Tables of Contents.
- **Frontend Scope**:
  - `.recipeboxer` export tab in `ExportDialog` triggering archive download.
  - Baseline archive file dropzone in `SessionLauncher`.
  - Telemetry counters in `ProgressDashboard` showing `skipped_cards` and `newly_processed_cards`.
  - Curation toolbar in `ExportDialog` enabling preset selection (Course, Contributor, Tag, Alphabetical) and TOC/divider toggles.

### Tier 2: Boundary & Corner Cases (Adversarial & Security Verification)
- **Objective**: Verify resilience against edge conditions, malformed archives, ZipSlip directory traversal attacks, SHA-256 checksum mismatches, extreme recipe counts, and zero-state groupings.
- **Scenarios**:
  - **ZipSlip Security**: Archives containing malicious entry names (`../../evil.py`, `..\..\windows\system32\calc.exe`, `/etc/passwd`, embedded NUL characters) must be rejected with security exceptions before extraction.
  - **Archive Integrity**: Archives with missing `manifest.json`, malformed JSON, corrupted ZIP bytes, or SHA-256 digest mismatches between manifest records and asset bytes.
  - **Ingestion Boundary**: Empty baseline archive, baseline with zero recipes, 100% duplicate deck, 0% duplicate deck, baseline with missing asset files, modified scan bytes triggering re-extraction.
  - **Curation Boundary**: Single recipe export, empty recipe list, recipes with missing tags or missing source notes/contributors, special XML/HTML characters (`<>&"'`) in titles, long recipe titles spanning multiple lines in TOC.
  - **Endpoint Validation**: Non-existent session IDs, non-zip file uploads to import/merge endpoints, invalid query parameter values.

### Tier 3: Cross-Feature Combinations (Pairwise Interaction Dynamics)
- **T3.1**: Archive Serialization + Content-Addressed Asset Hashing + Step-Image Bindings + Marginalia Annotations.
- **T3.2**: Project Export -> Disk Persistence -> Project Import -> Session Restoration -> Full Equality Verification.
- **T3.3**: Project Merge: Merging Archive A (Recipes 1..3) with Archive B (Recipes 4..5) into unified Archive C with deduplicated assets.
- **T3.4**: Baseline Project Ingestion: Day 1 Session -> Export `.recipeboxer` -> Day 2 Ingestion with Day 1 Baseline -> 50% Duplicate Skip + 50% New Card Extraction.
- **T3.5**: Multi-Card Pairing + Baseline Ingestion: Baseline contains 2-card recipe; new deck contains 1 new standalone card -> baseline 2-card recipe retained intact while new card creates recipe #2.
- **T3.6**: Custom Reordering + Course Grouping + DOCX & PDF Exporters with Styled Section Dividers and Dot-Leader TOCs.
- **T3.7**: Contributor Grouping + Alphabetical Sorting + Markdown & Google Docs Exporters with Hyperlinked TOCs.
- **T3.8**: Frontend Export Dialog Curation Toolbar -> Reorder Cards -> Select Contributor Preset -> Enable Dividers & TOC -> Download DOCX/PDF -> Verify structure.

### Tier 4: Real-World Application Scenarios (Multi-Stage Workflows)
- **Scenario 1 (Multi-Session Heirloom Archive Portability & Restoration)**:
  Digitize 4 heirloom cards with marginalia notes and step bindings -> Save `.recipeboxer` project archive -> Clear session store / restart server -> Import archive -> Verify 100% fidelity of cards, fingerprints, proposals, recipes, notes, and image assets.
- **Scenario 2 (Multi-Day Incremental Batch Ingestion with Zero Redundant OCR)**:
  Day 1: Ingest 5 vintage cards -> Extract 3 recipes -> Save baseline project. Day 2: Ingest 8 cards (5 identical from Day 1 + 3 new cards) with Day 1 baseline -> Verify duplicate-skipping engine bypasses Vision & LLM extraction for the 5 existing cards, processes only the 3 new cards, and yields 5 recipes in the active session.
- **Scenario 3 (Archive Project Merging from Multiple Family Contributors)**:
  Branch A: Cousin Alice digitizes 3 dessert cards -> exports `alice_desserts.recipeboxer`. Branch B: Uncle Bob digitizes 4 savory cards -> exports `bob_mains.recipeboxer`. Merge Alice & Bob archives via `/api/project/merge` -> Verify unified project contains all 7 recipes and 7 card scans without asset collisions or duplicates.
- **Scenario 4 (End-to-End Heirloom Cookbook Curation & Multi-Format Publishing)**:
  Ingest mixed heirloom card deck (Appetizers, Mains, Desserts from Grandma Rose and Aunt Clara) -> Group by Course with alphabetical sorting -> Generate Heirloom DOCX and Printable PDF -> Verify decorative section divider pages, 2-pass TOC page numbering with dot leaders, and recipe fidelity.
- **Scenario 5 (Adversarial Archive Attack Mitigation & Fault Isolation)**:
  Simulate malicious user uploading crafted `.recipeboxer` zip with ZipSlip payloads, tampered SHA-256 hashes, and corrupt manifest -> Verify backend rejects payloads cleanly with HTTP 400/422 without compromising filesystem or server stability.

---

## 4. Execution Commands & Automation

### Backend Pytest Suite
```powershell
# Run the complete Phase 9 test suite
powershell -ExecutionPolicy Bypass -File .\test.ps1 tests/test_archive.py tests/test_archive_endpoints.py tests/test_incremental_ingestion.py tests/test_export_curation.py -v

# Run full project pytest suite
powershell -ExecutionPolicy Bypass -File .\test.ps1
```

### Frontend Vitest Suite
```bash
# Run Phase 9 frontend Vitest suites
cd frontend && npm test -- tests/archive_export_import.test.tsx tests/export_curation.test.tsx --run

# Run all frontend tests
cd frontend && npm test -- --run
```

---

## 5. Coverage Goals & Verification Standards
- **Total Phase 9 Test Cases**: ≥80 dedicated tests across Tiers 1–4.
- **Feature Coverage**: 100% of Features F1–F16 covered in backend pytest and frontend vitest suites.
- **Pass Rate**: 100% passing rate once implementation milestones M1–M3 are complete.
