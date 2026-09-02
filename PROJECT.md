# Project: RecipeBoxer Cloud Storage Removal & Google Integrations Refactor

## Architecture
RecipeBoxer is a desktop-first web application featuring a Python FastAPI backend and a React (TypeScript + Vite + Tailwind CSS) frontend.
- **Backend**: FastAPI REST server providing ingestion pipelines, OCR/recipe extraction, search, curation, and export services. Sources are modular plugins inheriting from `BaseSourceProvider` registered in `SourceProviderRegistry`.
- **Frontend**: Single-page application using React 18, Tailwind CSS, Lucide icons, and Vitest for testing.
  - Ingestion: `SourceSelector` and `SessionLauncher` handle card scan ingestion from Local, Google Photos, and Google Drive.
  - Export: `ExportDialog` and `GoogleDocsPreview` handle multi-format recipe export, including Google Docs archival.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---|---|---|---|
| F1 | Ingestion: Google Photos Disabled State & Watermark | Google Photos source button is disabled, styled inactive/greyed out, displays visible "Coming Soon" badge, and has "Support In Progress" tooltip. | M2 | ORIGINAL_REQUEST §R1 |
| F2 | Ingestion: Remove S3 & GCS | Completely remove Amazon S3 and Google Cloud Storage options, tabs, sub-type switcher, bucket name inputs, and prefix inputs from Ingestion UI. | M2 | ORIGINAL_REQUEST §R2 |
| F3 | Ingestion: Dedicated Google Drive Button | Dedicated Google Drive source button on Ingestion page, styled inactive/greyed out, disabled, with visible "Coming Soon" badge and "Support In Progress" tooltip. | M2 | ORIGINAL_REQUEST §R2 |
| F4 | Export: Google Docs Button Disabled & Watermark | On Export format selection and preview, Google Docs export button is disabled, styled inactive/greyed out, displays visible "Coming Soon" badge and "Support In Progress" tooltip. | M3 | ORIGINAL_REQUEST §R3 |
| F5 | Export: Remove Manual OAuth Client ID Settings | Remove manual Google OAuth client ID configuration drawers, input fields, and setup guide modals from Google Docs export UI. Streamline 1-click OAuth initiation. | M3 | ORIGINAL_REQUEST §R3 |
| F6 | Backend: S3/GCS Removal & Clean Registry | Delete `s3_gcs.py`, remove S3/GCS from `registry.py` and `__init__.py`, update model/route docstrings, clean up out-of-scope references. | M1 | ORIGINAL_REQUEST §R4 |
| F7 | Backend: Local & Google Provider Interfaces | Maintain clean, modular provider interfaces for Local (`local.py`), Google Photos (`google_photos.py`), and Google Drive (`cloud_drive.py`). | M1 | ORIGINAL_REQUEST §R4 |
| F8 | Test Matrix: Backend Pytest Update | Update backend pytest suites (`test_sources.py`, `test_api_routes.py`) for S3/GCS removal and ensure 100% backend tests pass. | M1, M4 | ORIGINAL_REQUEST §R5 |
| F9 | Test Matrix: Frontend Vitest Update & Build | Update frontend Vitest test suites for new disabled/watermarked states and OAuth simplification. Ensure 100% tests pass and `npm run build` succeeds. | M2, M3, M4 | ORIGINAL_REQUEST §R5 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|---|---|---|---|
| M1 | Backend Clean-Up & S3/GCS Removal | Remove `recipeboxer/sources/s3_gcs.py`, clean provider registry, update models/routes, and update pytest suites. | none | DONE |
| M2 | Frontend Ingestion UI Refactor | Update `SourceSelector.tsx`, `SessionLauncher.tsx`, `tutorialSteps.ts`, remove S3/GCS UI, add Coming Soon badges & Support In Progress tooltips to Google Photos & Google Drive. Update Vitest tests. | none | DONE |
| M3 | Frontend Export UI Refactor | Update `ExportDialog.tsx` and `GoogleDocsPreview.tsx`, remove manual OAuth drawers/modals, add Coming Soon badges & Support In Progress tooltips to Google Docs export. Update Vitest tests. | none | DONE |
| M4 | Full Verification & Hardening | Execute full test matrix (backend pytest + frontend vitest + frontend build), adversarial testing, and forensic integrity audit. | M1, M2, M3 | DONE |

## Interface Contracts

### Backend Source Provider Registry ↔ API Routes
- `GET /api/sources/providers` returns a list of `SourceConnectionStatus` for registered providers: `local`, `google_photos`, `google_drive`.
- `GET /api/sources/{provider_id}/*` returns `404 Not Found` with `{"detail": "Source provider '{provider_id}' not found"}` for unregistered provider IDs (including `s3` and `gcs`).
- `POST /api/pipeline/start` raises `400 Bad Request` if `source_provider` is not in registered providers.

### Ingestion UI (`SourceSelector.tsx` ↔ `SessionLauncher.tsx`)
- `IngestionSourceType`: `'local' | 'google_photos' | 'google_drive'`
- Google Photos button: `disabled={true}`, `title="Support In Progress"`, contains `<Badge variant="neutral" size="sm">Coming Soon</Badge>`.
- Google Drive button: `disabled={true}`, `title="Support In Progress"`, contains `<Badge variant="neutral" size="sm">Coming Soon</Badge>`.
- `SourceSelectorProps`: `bucketName?: string`, `prefix?: string` marked optional or pruned.

### Export UI (`ExportDialog.tsx` ↔ `GoogleDocsPreview.tsx`)
- Google Docs format tile in `ExportDialog.tsx`: `disabled={true}`, `title="Support In Progress"`, contains `<Badge variant="warning" size="sm">Coming Soon</Badge>`.
- Google Docs action button in `GoogleDocsPreview.tsx`: `disabled={true}`, `title="Support In Progress"`, contains `<Badge variant="warning" size="sm">Coming Soon</Badge>`.
- No manual OAuth drawer (`#google-client-id-drawer`), setup guide (`#google-setup-guide`), unconfigured alert banner, or troubleshooting configuration buttons exist in DOM.

## Code Layout

### Backend Ownership Boundaries
- `recipeboxer/sources/s3_gcs.py` (to delete)
- `recipeboxer/sources/__init__.py`
- `recipeboxer/sources/registry.py`
- `recipeboxer/sources/base.py`
- `recipeboxer/api/routes.py`
- `recipeboxer/models/pipeline.py`
- `tests/test_sources.py`
- `tests/test_api_routes.py`

### Frontend Ingestion Boundaries
- `frontend/src/components/ingestion/SourceSelector.tsx`
- `frontend/src/components/ingestion/SessionLauncher.tsx`
- `frontend/src/components/tutorial/tutorialSteps.ts`
- `frontend/tests/sources.test.tsx`

### Frontend Export Boundaries
- `frontend/src/components/export/ExportDialog.tsx`
- `frontend/src/components/export/GoogleDocsPreview.tsx`
- `frontend/tests/google_docs_oauth_fallback.test.tsx`
- `frontend/tests/export.test.tsx`
- `frontend/tests/challenger_m4_adversarial.test.tsx`
- `frontend/tests/platform_oauth.test.tsx`
- `frontend/tests/e2e_platform.test.tsx`
