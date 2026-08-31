# Project: RecipeBoxer Phase 9 — Multi-Session Project Persistence & Export Curation

## Architecture

RecipeBoxer Phase 9 introduces portable project state persistence (`.recipeboxer` archive container), project-aware incremental ingestion with SHA-256 duplicate skipping, and configurable multi-format recipe ordering & categorical grouping with styled section divider pages and Tables of Contents across DOCX, printable PDF, and Google Docs exports.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 RecipeBoxer Architecture                               │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│  [ Input Scans / Baseline .recipeboxer ]                                               │
│             │                                                                          │
│             ▼                                                                          │
│  [ Stage 1: Ingestion & Duplicate-Skipping ]                                           │
│     ├── SHA-256 Hashing & Baseline Manifest Comparison                                 │
│     ├── Skipped Cards (Restore Fingerprints, Bypass Vision/OCR)                         │
│     └── New Cards (Forward to Fingerprinting)                                          │
│             │                                                                          │
│             ▼                                                                          │
│  [ Stage 2 & 3: Vision Fingerprinting & Correlation ]                                  │
│     ├── Vision API & OCR for New Cards Only                                            │
│     └── Pairing Optimization for New Cards Only                                        │
│             │                                                                          │
│             ▼                                                                          │
│  [ Stage 4 & 5: HITL Review & Recipe Extraction ]                                      │
│     ├── HITL Review Queue for New Proposals Only                                       │
│     ├── LLM Extraction for Newly Approved Proposals Only                               │
│     └── Unification: Baseline Archive Recipes + Newly Extracted Recipes                │
│             │                                                                          │
│             ▼                                                                          │
│  [ Stage 6: Persistence & Export Curation ]                                            │
│     ├── Portable Archive (.recipeboxer ZIP: manifest.json, scans/, fingerprints, notes)│
│     ├── Project Save / Import / Merge Endpoints (/api/project/*)                       │
│     ├── Export Curation Engine (Ordering, Course/Contributor/Tag/Alpha Presets)       │
│     └── Multi-Format Exporters (DOCX, PDF, Google Docs, Markdown, CSV, Paprika, etc.) │
│                                                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

## Feature Inventory

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Archive Data Models & Schemas | `RecipeBoxerArchiveManifest`, `ArchiveAssetRecord`, `ArchiveCardRecord`, `StepImageBinding`, `MarginaliaEntry` | M1 | R1 Survey |
| 2 | `.recipeboxer` Archive Storage Engine | ZIP packing/unpacking, SHA-256 content-addressing, ZipSlip defense, image extraction/materialization | M1 | R1 Survey |
| 3 | Project Export/Import/Merge Endpoints | `GET /api/project/export`, `POST /api/project/import`, `POST /api/project/merge` in backend routes | M1 | R1 Survey |
| 4 | Frontend Archive UI Touchpoints | `.recipeboxer` tab in `ExportDialog`, restore & baseline dropzones in `SessionLauncher` and `SessionHistoryTable` | M1 | R1 Survey |
| 5 | Baseline Archive Ingestion Loading | Accepting `baseline_project_path`, `baseline_archive`, or `baseline_session_id` in `/api/pipeline/start` & `/api/cards/upload` | M2 | R2 Survey |
| 6 | SHA-256 Duplicate-Skipping Engine | Card hashing against baseline manifest, partitioning skipped vs new cards, bypassing Vision/OCR API | M2 | R2 Survey |
| 7 | Pipeline Stage Bypass & Recipe Merge | Bypassing correlation/HITL/LLM extraction for duplicates, merging baseline recipes with new extractions | M2 | R2 Survey |
| 8 | Progress Telemetry & SSE Accounting | `ProgressMetrics` with `skipped_cards`, `newly_processed_cards`, `baseline_recipes_count` emitted via SSE | M2 | R2 Survey |
| 9 | Frontend Ingestion Baseline Controls | Baseline archive dropzone/session selector in `SessionLauncher`, duplicate skipping telemetry in `ProgressDashboard` | M2 | R2 Survey |
| 10| Export Curation Engine | `GroupingPreset` (None, Course, Contributor, Tags, Alphabetical), `CuratedCookbook`, `CuratedSection`, reorder logic | M3 | R3 Survey |
| 11| DOCX Exporter TOC & Section Dividers | Decorative section divider pages (Georgia 28pt bold, ornaments) and styled TOC with dot leaders (`w:leader="dot"`) | M3 | R3 Survey |
| 12| PDF Exporter TOC & Section Dividers | 2-pass pagination engine, dedicated TOC canvas page with dot leaders, and double-bordered section divider pages | M3 | R3 Survey |
| 13| Google Docs & Markdown Curation | Section dividers and hyperlinked TOC in Google Docs API / HTML, and markdown section headers + TOC | M3 | R3 Survey |
| 14| Export REST Endpoints Curation Args | `/api/export/*` endpoints accepting `recipe_order`, `grouping_preset`, `include_section_dividers`, `include_toc` | M3 | R3 Survey |
| 15| Frontend Export Curation Toolbar | Interactive drag-and-drop reordering, sequence badges, preset selector pills, TOC/divider toggles in `ExportDialog` | M3 | R3 Survey |
| 16| 4-Tier Automated Test Matrix | Unit, integration, regression, and E2E lifecycle tests across archive, ingestion, and export curation | M4 | R1-R4 Specs |

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Portable Project State Persistence (`.recipeboxer` Archive) | Models (`archive.py`), Storage Engine (`archive.py`), REST Endpoints (`/api/project/*`), Frontend Export Tab & History Import | none | DONE |
| 2 | Incremental Ingestion with Duplicate-Skipping | Pipeline bypass logic, SHA-256 card matcher, baseline recipe merger, SSE progress telemetry, Ingestion UI | M1 | DONE |
| 3 | Configurable Recipe Ordering, Categorical Grouping & Styled TOC Exporters | Curation engine (`curation.py`), DOCX/PDF/GDocs/MD section dividers & TOCs, Export API parameters, Frontend reorder UI | none | DONE |
| 4 | Dual Track E2E Verification & Hardening | Full regression pass (all backend/frontend tests), 4-tier automated test suite, adversarial edge case coverage | M1, M2, M3 | DONE |

## Interface Contracts

### 1. Archive Manifest Contract (`manifest.json` inside `.recipeboxer`)
```json
{
  "format_version": "1.0.0",
  "app_version": "1.0.0",
  "schema_version": "1.0.0",
  "project_id": "string",
  "project_name": "string",
  "created_at": "ISO-8601 string",
  "updated_at": "ISO-8601 string",
  "generator": "RecipeBoxer",
  "session_metadata": {},
  "assets": [
    {
      "sha256_hash": "string (hex)",
      "filename": "string",
      "archive_path": "assets/scans/<sha256>.<ext>",
      "mime_type": "image/jpeg",
      "size_bytes": 0,
      "width": null,
      "height": null
    }
  ],
  "cards": [
    {
      "card_id": "string",
      "sha256_hash": "string (hex)",
      "filename": "string",
      "role": "STANDALONE | SEQUENCE_START | SEQUENCE_CONTINUATION",
      "role_confidence": 1.0,
      "fingerprint": {}
    }
  ],
  "proposals": [
    {
      "proposal_id": "string",
      "ordered_card_ids": ["string"],
      "part_count": 1,
      "transition_scores": [],
      "heuristic_score": 0.0,
      "semantic_score": 0.0,
      "confidence": 0.0,
      "is_standalone": false,
      "continuity_explanation": "string",
      "recommended_action": "SEQUENCE",
      "decision": "APPROVE | REJECT | OVERRIDE",
      "notes": "string"
    }
  ],
  "recipes": [
    {
      "recipe_id": "string",
      "title": "string",
      "description": "string",
      "yield_servings": "string",
      "prep_time": "string",
      "cook_time": "string",
      "total_time": "string",
      "ingredients": ["string"],
      "directions": ["string"],
      "tags": ["string"],
      "source_notes": "string",
      "card_ids": ["string"],
      "image_urls": ["string"],
      "step_bindings": [
        {
          "step_index": 1,
          "card_id": "string",
          "card_sha256": "string (hex)",
          "bounding_box": null,
          "marginalia_note": null
        }
      ],
      "marginalia": [
        {
          "id": "string",
          "card_id": "string",
          "card_sha256": "string (hex)",
          "text": "string",
          "location_quadrant": "CENTER",
          "category": "family_story | culinary_tweak | attribution",
          "target_field": "source_notes"
        }
      ]
    }
  ]
}
```

### 2. Project API Endpoints
- `GET /api/project/export?session_id={id}&filename={fn}` -> Binary Stream (`application/x-recipeboxer`)
- `POST /api/project/import` -> Multipart file upload -> Restores session to `SessionStore` & `SessionManager` -> Returns `SessionDetail`
- `POST /api/project/merge` -> Multipart `base_archive` + `session_id` or `incoming_archive` -> Merged `.recipeboxer` stream

### 3. Pipeline Ingestion Baseline Contract
- `PipelineStartRequest`:
  - `baseline_project_path: Optional[str] = None`
  - `baseline_session_id: Optional[str] = None`
- `POST /api/cards/upload`:
  - `baseline_archive: Optional[UploadFile] = None`
  - `baseline_project_path: Optional[str] = None`
- `ProgressMetrics`:
  - `skipped_cards: int = 0`
  - `newly_processed_cards: int = 0`
  - `baseline_recipes_count: int = 0`

### 4. Export Curation Contract
- `GroupingPreset`: `"none" | "course" | "contributor" | "tags" | "alphabetical" | "custom"`
- `CuratedCookbook`: `title`, `grouping_preset`, `sections: List[CuratedSection]`, `include_section_dividers`, `include_toc`, `toc_title`
- Query Params on `GET /api/export/*`:
  - `recipe_order: Optional[str] = None` (comma-separated recipe IDs)
  - `grouping_preset: Optional[str] = 'none'`
  - `include_section_dividers: bool = True`
  - `include_toc: bool = True`
  - `toc_title: Optional[str] = 'Table of Contents'`

## Code Layout

### Backend (`recipeboxer/`)
- `recipeboxer/models/archive.py`: Pydantic schemas for `.recipeboxer` archive manifest, assets, step-bindings, marginalia.
- `recipeboxer/storage/archive.py`: Archive manager for ZIP packing, unpacking, SHA-256 verification, ZipSlip safety, project save/load/merge.
- `recipeboxer/exporter/curation.py`: Curation engine, `GroupingPreset` enums, course taxonomy matching, contributor extraction, section generators.
- `recipeboxer/exporter/docx_export.py`: Heirloom DOCX exporter with section divider pages and styled TOC dot leader tab stops.
- `recipeboxer/exporter/pdf_export.py`: Printable PDF exporter with 2-pass page numbering, TOC canvas renderer, and section divider pages.
- `recipeboxer/exporter/google_docs.py`: Google Docs exporter with batchUpdate section breaks & HTML hyperlinked TOC.
- `recipeboxer/exporter/markdown_export.py`: Markdown exporter with section headers and hyperlinked index.
- `recipeboxer/agent/orchestrator.py`: Pipeline runner with baseline loading, SHA-256 card partitioning, fingerprint restore, and duplicate skipping.
- `recipeboxer/agent/session.py`: Session state machine with baseline metadata and duplicate progress metrics.
- `recipeboxer/api/routes.py`: Endpoints for `/api/project/export`, `/api/project/import`, `/api/project/merge`, baseline pipeline start, and export curation parameters.

### Frontend (`frontend/src/`)
- `frontend/src/types/export.ts`: TypeScript interfaces for `GroupingPreset`, `CuratedSection`, `CuratedCookbook`, `ExportCurationOptions`.
- `frontend/src/types/pipeline.ts`: TypeScript interfaces for baseline archive params and `skipped_cards` metrics.
- `frontend/src/utils/exportCuration.ts`: Client-side ordering and grouping algorithms.
- `frontend/src/components/export/ExportCurationToolbar.tsx`: Drag-and-drop reordering, sequence badges, preset pills, TOC toggles.
- `frontend/src/components/export/ExportDialog.tsx`: Export dialog integrating `.recipeboxer` tab and `ExportCurationToolbar`.
- `frontend/src/components/ingestion/SessionLauncher.tsx`: Baseline `.recipeboxer` file picker / dropzone and session selector.
- `frontend/src/components/ingestion/ProgressDashboard.tsx`: Duplicate skipping telemetry metrics display.
- `frontend/src/components/ingestion/SessionHistoryTable.tsx`: "Import Archive (.recipeboxer)" toolbar button.
- `frontend/src/utils/api.ts`: API helpers for project export, import, merge, and curation parameters.

### Tests (`tests/` & `frontend/tests/`)
- `tests/test_archive.py`: Unit tests for archive manifest serialization, ZIP packaging, SHA-256 verification, ZipSlip rejection.
- `tests/test_archive_endpoints.py`: Integration tests for `/api/project/export`, `/api/project/import`, `/api/project/merge`.
- `tests/test_incremental_ingestion.py`: Unit and integration tests for SHA-256 duplicate skipping and baseline recipe merging.
- `tests/test_export_curation.py`: Unit and integration tests for course/contributor/alphabetical grouping presets, TOC, and section dividers across DOCX, PDF, Google Docs, Markdown.
- `frontend/tests/archive_export_import.test.tsx`: Vitest tests for frontend archive export tab and import workflows.
- `frontend/tests/export_curation.test.tsx`: Vitest tests for drag-and-drop reordering, grouping presets, and TOC toggles.
