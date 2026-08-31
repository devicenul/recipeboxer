# RecipeBoxer Milestone Tracking & Strategic Roadmap

This document serves as the canonical shared roadmap and prioritized backlog for **RecipeBoxer**. It tracks completed milestones, active work, and the strategic path forward.

---

## Roadmap Status Conventions

All roadmap items and milestones use standardized status tags:

- `[x] Completed`: Fully implemented, verified against 4-tier testing suites, and integrated with zero test regressions.
- `[~] In Progress`: Active milestone or feature currently under active implementation or review.
- `[ ] Planned`: Approved future enhancement in the prioritized backlog.

---

## Completed Milestones Overview

| Milestone | Title | Key Deliverables | Status |
|:---:|---|---|:---:|
| **M1** | **Backend Architecture & Core Domain Engine** | Python package structure, Pydantic V2 models (`VisualFingerprint`, `RecipeSchema`), Gemini 3.7/2.5 visual substrate analysis, CIELAB $\Delta E$ color math, scan proximity decay, horizontally mirrored stain bleed-through, ruling/ink heuristics, greedy bipartite matching optimizer. | `[x] Completed` |
| **M2** | **FastAPI REST & SQLite WAL Persistence** | FastAPI ASGI application, thread-safe `FingerprintCache` (content-addressed SHA-256 cache), relational `SessionStore` (sessions, cards, proposals, recipes), secure card image streaming, multipart uploads, CLI runner. | `[x] Completed` |
| **M3** | **Real-Time SSE & HITL Review Pipeline** | Server-Sent Events stream (`/api/events`), cooperative async barrier (`approval_event` & `cancellation_event`), Rapid Review Queue, Candidate Drawer ranking, 3+ card multi-part recipe grouping trays, global hotkey shortcuts. | `[x] Completed` |
| **M4** | **Multi-Format Exporters & Schema.org JSON-LD** | Plan To Eat RFC 4180 9-column CSV serializer with CRLF terminators, Schema.org Recipe JSON-LD exporter (`HowToStep`, `@graph`, ISO 8601 duration parser), standard JSON serializer, and download endpoints. | `[x] Completed` |
| **M5** | **React 18+ TypeScript SPA & Static Embedding** | React 18 SPA with Vite, Tailwind CSS, Lucide icons, custom hooks (`useSSE`, `useKeyboard`, `useImageViewer`, `useRecipeForm`, `useTheme`), interactive pan/zoom/filter card viewer, Vite build contract directly into `recipeboxer/api/static`, SPA catch-all fallback with `/api` 404 guard. | `[x] Completed` |
| **M6** | **Agent Guidance, Invariants & Repository Structure** | Complete `_agents/` documentation directory (`README.md`, `ARCHITECTURE.md`, `INVARIANTS.md`, `TESTING_GUIDELINES.md`, `EXTENDING_RECIPEBOXER.md`), 4-tier testing guide, extension playbooks, and standardized roadmap persistence conventions. | `[x] Completed` |
| **M7** | **License Compliance & Dependency Vetting Audit** | Full automated audit of 60+ Python and 17+ npm dependencies confirming 100% GPLv3 compatibility; formalized Invariant 8 in `_agents/INVARIANTS.md`. | `[x] Completed` |
| **M8** | **Sample Card Corpus, Provenance & Synthetic Generator** | 48 high-resolution historical card fixtures across 6 modalities (24 recipes), CC0/public domain provenance guide (`tests/fixtures/sample_cards/README.md`), strict Pydantic `manifest.json`, procedural generator tool (`recipeboxer/tools/card_generator.py`), and pytest loader fixtures. | `[x] Completed` |
| **M9** | **Agent & Prompt Evaluation Harness & Benchmark Suite** | Quantitative metric suite (Pairing F1, Levenshtein CER/WER, Plan To Eat / Schema.org schema conformity), LLM-as-a-Judge semantic rubric evaluator (`recipeboxer/evals/judge.py`), golden dataset benchmark runner CLI (`evals/run_benchmarks.py`), and 65 unit/integration tests. | `[x] Completed` |
| **M10** | **Adaptive Vision Prompts, Antigravity Skills & CV Tools** | Deterministic CV preprocessing (coarse-to-fine projection deskewing $\pm 1^\circ$, border cropping, CLAHE contrast enhancement, CIE $L^*a^*b^*$ clustering), Antigravity domain skills (`vintage-handwriting-ocr`, `print-clipping-extractor`), modality classifier (95.7% accuracy), and dynamic thinking budget allocation. | `[x] Completed` |
| **M11** | **Archival Document Generation & Extended Exporters** | Google Docs batch update archival exporter with card scan embedding, offline OpenXML DOCX & printable PDF cookbook generators, Obsidian Markdown with YAML frontmatter, Cooklang (`.cook`), Paprika (`.paprikarecipes`), FastAPI export endpoints, and React frontend export UI integration. | `[x] Completed` |
| **M12** | **Cloud & External Source Providers** | Unified `BaseSourceProvider` abstraction, Google Photos Library API connector with OAuth2 auto-refresh & album filtering, Google Drive folder selector, S3/GCS bucket connectors, REST source management endpoints, and React album picker UI. | `[x] Completed` |
| **M13** | **Interactive UI Guided Tour, User Guide & MVP Packaging** | Accessible 6-step interactive onboarding tutorial overlay (`frontend/src/components/tutorial/`), complete multi-platform documentation and architecture walkthrough (`README.md`), CLI option verification (`--concurrency`), and standalone PyInstaller packaging verification. | `[x] Completed` |
| **M14** | **Open-Ended N-Part Sequence Correlation & Model Modernization** | Clean-break deprecation of `front_card_id`/`back_card_id` across models and schemas; directed transition graph and DAG maximal path chaining ($N \ge 1$); dynamic SQLite `session_proposals` sequence migration; unified "Part 1 of N" HITL UI with drag-and-drop reordering, split, and merge; sequence-preserving multimodal extraction and 9 exporters; 1,660 passing tests. | `[x] Completed` |
| **M15** | **Integration Findings Resolution & Eval Anti-Cheating Hardening** | Dynamic OS-adaptive folder import path placeholders (`C:\...`, `/Users/...`, `/home/...`), evaluation filename and metadata obfuscation/sanitization (`obfuscation.py`, `runner.py`) preventing LLM cheating, authentic public domain magazine/box/can label clippings added to corpus, and interactive Google Identity Services (GIS) OAuth popup authorization for Docs export and Drive/Photos ingestion; 1,592 pytest + 290 vitest tests passing. | `[x] Completed` |
| **M16** | **Multi-Session Project Persistence & Export Curation** | Portable `.recipeboxer` ZIP archive format with content-addressed SHA-256 asset storage, project save/import/merge REST endpoints, incremental duplicate-skipping ingestion engine, 5 export curation grouping presets (Course, Contributor, Tag, Alphabetical, Custom) with manual drag-and-drop reordering, styled section divider pages and TOCs across DOCX, PDF, and Google Docs; 1,848 pytest + 357 vitest tests passing. | `[x] Completed` |

---

## Prioritized Strategic Path Forward

```
[x] Phase 1: Curated Test Datasets & Benchmarks
       │
       ▼
[x] Phase 2: Agent & Prompt Evaluation Harness (evals/)
       │
       ▼
[x] Phase 3: Adaptive Vision Prompts, Antigravity Skills & CV Tools
       │
       ▼
[x] Phase 4: Archival Document Generation & Extended Exporters
       │
       ▼
[x] Phase 5: Cloud & External Source Providers
       │
       ▼
[x] Phase 6: Comprehensive User Guide, Guided UI Tutorial & MVP Release
       │
       ▼
[x] Phase 7: Open-Ended N-Part Sequence Correlation & Model Modernization
       │
       ▼
[x] Phase 8: Integration Findings Resolution & Anti-Cheating Eval Hardening
       │
       ▼
[x] Phase 9: Multi-Session Project Persistence & Export Curation
       │
       ▼
[ ] Phase 10: Official Google Cloud OAuth Provisioning & Fallback Deprecation
```

---

### Phase 1: Curated Test Datasets & Benchmarks
*Foundation priority: Establish a rich, diverse corpus of recipe card scans to ground all subsequent evaluation, prompt tuning, and regression testing.*

- [x] **Curate Public Domain Historical Recipe Scans**:
  - Sourced and curated high-resolution card scans from historical public domain collections (Library of Congress, MSU Feeding America) and synthetic cleanroom generator covering 12 culinary recipes.
- [x] **Representative Sample Scans Repository (`tests/fixtures/sample_cards/`)**:
  - **Vintage Handwritten Cursive**: 1948 Apple Crisp, 1965 Rhubarb Custard Pie (aged paper, fountain pen bleed, faded pencil).
  - **Typewritten & Stamped**: 1956 Beef Stroganoff with club stamp, 1952 Mystery Spice Cake.
  - **Magazine Clippings & Package Labels**: Crisco Fried Chicken cutout, 1962 Borden's Eagle Brand Chocolate Pie.
  - **Stained / Damaged / Edge-Folded Cards**: Mom's Lasagna (coffee cup rings, oil stains), Shortbread with ragged tears.
  - **Multi-Card Recipe Sets (3–4 Cards)**: Grandma's Thanksgiving Turkey (3 cards), 1942 Dark Wedding Fruitcake (4 cards).
  - **Standalone Single-Sided Cards**: Quick Chocolate Fudge, Kraft paper Pecan Pie.
- [x] **Dataset Provenance & Public Domain Licensing Guide (`tests/fixtures/sample_cards/README.md`)**:
  - Full archive provenance documentation and Creative Commons CC0 1.0 Universal / MIT terms.
- [x] **Ground-Truth Metadata Manifest (`tests/fixtures/sample_cards/manifest.json`)**:
  - Strict Pydantic-validated JSON mapping 100% of images to physical features, cluster roles, and ground-truth `RecipeSchema` objects.
- [x] **Procedural Synthetic Card Generator (`recipeboxer/tools/card_generator.py`)**:
  - Reusable CLI/Python generator with 6 paper substrates, 4 rendering modes, realistic stains/bleed/tears, and deterministic seeding.
- [x] **Dataset Loader & Pytest Fixture Utilities (`tests/fixtures/loader.py`)**:
  - Query APIs and pytest fixtures (`sample_card_corpus`, `sample_card_pairs`) with 975 passing tests across the repo.

---

### Phase 2: Agent & Prompt Evaluation Harness (`evals/`)
*Objective measurement: Implement reproducible evaluation pipelines to benchmark transcription accuracy, pairing precision, and schema conformity.*

- [x] **Quantitative Metric Suite for Gemini Vision (`recipeboxer/evals/metrics.py`)**:
  - **Pairing Metrics (Precision / Recall / F1)**: Benchmarks front/back correlation proposals and multi-card groupings against ground-truth manifest clusters.
  - **Transcription Error Rates (CER / WER)**: Dynamic programming Levenshtein distance calculating Character Error Rate (CER) and Word Error Rate (WER) across full recipe text, ingredients, fractions (`1/8`, `1/4`, `1/2`, `3/4`), numbered directions, and cooking temperatures.
  - **Schema Conformity & Completeness**: Strict validator verifying Plan To Eat 9-column RFC 4180 rules, Schema.org Recipe structure, non-empty ingredients/directions, and ISO 8601 durations (`PT25M`).
- [x] **LLM-as-a-Judge Semantic Evaluation Pipeline (`recipeboxer/evals/judge.py`)**:
  - Independent Gemini evaluation rubric assessing 4 qualitative dimensions (Semantic Fidelity, Preservation of Marginalia, Cross-Consistency, Step Ordering Logic) with structured `JudgeEvaluationResult` output and hermetic offline mock mode.
- [x] **Golden Dataset Continuous Regression Runner (`recipeboxer/evals/runner.py`, `evals/run_benchmarks.py`)**:
  - Standalone CLI runner with model selection (`--models`), stage filtering (`--stage`), modality filtering (`--modalities`), and structured Markdown and JSON benchmark report generation.
- [x] **Automated Benchmark Test Suite (`tests/test_eval_metrics.py`, `tests/test_eval_runner.py`)**:
  - 65 new automated tests verifying metric calculation accuracy, unicode fraction handling, mock judge parsing, and runner CLI execution.

---

### Phase 3: Adaptive Vision Prompts, Antigravity Skills & Deterministic Tools
*Model optimization: Leverage Phase 1 datasets and Phase 2 metrics to engineer specialized skills, prompt branches, and deterministic preprocessing.*

- [x] **Deterministic Computer Vision Preprocessing Tools (`recipeboxer/vision/preprocessing.py`)**:
  - Coarse-to-fine projection profile deskewing correcting $\pm 45^\circ$ skew within $\pm 1^\circ$ tolerance with non-clipping canvas expansion.
  - Rectangular card border auto-cropping stripping scanner bed and tabletop shadows with configurable padding.
  - Tile-based CLAHE adaptive contrast enhancement and white-balance normalization for faint pencil, aged ink, and cursive text.
  - CIE $L^*a^*b^*$ K-Means color clustering for paper and ink centroid extraction.
- [x] **Specialized Antigravity Analysis Skills & Tools (`recipeboxer/agent/skills/`)**:
  - `vintage-handwriting-ocr`: Paleography prompts and normalization for archaic culinary units ("oleo", "saleratus", "butter size of an egg", "gills", "pecks", "drams") and fractions.
  - `print-clipping-extractor`: Multi-column newsprint reading order rules and promotional noise filtering (`filter_advertisement_noise`).
  - Antigravity agent tool wrappers (`recipeboxer/agent/skills/tools.py`) for safe deterministic preprocessing invocation.
- [x] **Document Class Detection & Dynamic Prompt Routing (`recipeboxer/vision/router.py`)**:
  - Fast classifier achieving 95.7% accuracy across benchmark cards routing images to specialized prompt templates and domain skills.
- [x] **Dynamic Thinking Budget Allocation (`recipeboxer/extraction/extractor.py`)**:
  - `compute_adaptive_thinking_budget` automatically allocating 0 reasoning tokens to clean typed cards and 2,048–4,096 tokens to degraded cursive, faded pencil, and complex clippings.

---

### Phase 4: Archival Document Generation & Extended Exporters
*Ecosystem expansion: Deliver high-value export formats, including visual family cookbook generation.*

- [x] **Google Docs Archival Exporter (with Scanned Image Embeds)**:
  - Export full recipe collections to a structured **Google Doc** via the Google Docs API.
  - Page-break separated layout: each recipe presented with beautifully formatted typography (title, prep/cook/total times, yield, ingredients table, numbered directions, tags, and attribution notes).
  - High-resolution visual embedding: embeds original scanned card images (front and back) directly below formatted recipe text for archival printing.
  - Supports downloadable Google Docs JSON/HTML interchange archives.
- [x] **Offline DOCX & Printable Archival PDF Exporter**:
  - Generate standalone OpenXML `.docx` and styled printable PDF cookbook documents with heirloom typography, metadata tables, and embedded card scan photos.
- [x] **Markdown / Obsidian Recipe Serializer**:
  - YAML frontmatter with tags, cook times, ingredients list, and wikilink card image links formatted for Obsidian Recipe plugins.
- [x] **Cooklang (.cook) Serializer**:
  - Plain-text recipe markup format for the Cooklang ecosystem with `@ingredient{qty%unit}`, `#cookware{}`, and `~timer{}` annotations.
- [x] **Paprika Recipe Manager (.paprikarecipes) Export**:
  - Compressed gzip archive format compatible with Paprika app imports with base64 embedded card photos.
- [x] **FastAPI REST Export Endpoints & React 18 UI Integration**:
  - REST endpoints (`/api/export/docx`, `/api/export/pdf`, `/api/export/markdown`, `/api/export/cooklang`, `/api/export/paprika`, `/api/export/google-docs`) and comprehensive frontend export dialog with live previews.

---

### Phase 5: Cloud & External Source Providers
*Ingestion flexibility: Extend source ingestion beyond local folders to cloud storage and photo libraries.*

- [x] **Unified `SourceProvider` Abstraction (`recipeboxer/sources/base.py`)**:
  - Async provider interface declaring `list_items()`, `list_containers()`, `fetch_thumbnail()`, `download_image()`, `get_metadata()`, and `test_connection()`.
  - Pydantic models: `SourceItem`, `SourceContainer`, `SourceProviderConfig`, and `SourceCapabilities`.
  - High-performance SQLite WAL content-addressed cache (`RemoteAssetCache`) with SHA-256 validation.
- [x] **Google Photos Integration (`recipeboxer/sources/google_photos.py`)**:
  - OAuth2 connector with auto-refresh, category filters (`FOOD`, `DOCUMENTS`, date ranges), and resilient rate-limit backoff.
- [x] **Cloud Drive & Object Storage Connectors (`recipeboxer/sources/cloud_drive.py`, `recipeboxer/sources/s3_gcs.py`)**:
  - Google Drive folder browser and pure-Python AWS SigV4 / GCS XML object storage connectors.
- [x] **FastAPI Source Endpoints & React Ingestion UI**:
  - Source routes under `/api/sources/*` and interactive tabbed `SourceSelector` with modal `AlbumPickerModal`.

---

### Phase 6: User Guide, Guided UI Tutorial & Initial MVP Release
*Polish & onboarding: Deliver comprehensive documentation, interactive user onboarding, and production distribution.*

- [x] **"First Run" Interactive Guided UI Tutorial**:
  - Built-in interactive walkthrough in the frontend introducing new users to the complete workflow: launching an ingestion job, using Rapid Queue keyboard shortcuts (`Space`, `S`, `B`, `X`, `Tab`), exploring the Candidate Drawer & 3+ card grouping, toggling image inspection filters, editing in the split-screen recipe editor, and exporting.
- [x] **Comprehensive User Guide & End-to-End Walkthrough (`README.md`)**:
  - Visual onboarding: Architecture diagrams, UI feature walkthroughs, and screenshot guides.
  - Step-by-step multi-platform setup instructions (macOS, Linux, Windows PowerShell / CMD).
  - Troubleshooting FAQ: API key configuration (`GEMINI_API_KEY`), quota management with slow-batch pacing, port binding, and cache management.
- [x] **Initial MVP Release & Pre-Built Binary Packaging**:
   - Tag initial MVP release and publish cross-platform standalone binaries (`macOS`, `Linux`, `Windows`).

---

### Phase 7: Open-Ended N-Part Sequence Correlation & Model Modernization
*Generalization & sequence intelligence: Eliminate rigid binary front/back pairing in favor of open-ended directed sequence clustering and unified "Part 1 ... N" HITL review.*

- [x] **Clean-Break Domain Model & Schema Modernization**:
  - Removed legacy binary `front_card_id`/`back_card_id` and `FRONT`/`BACK` constraints across backend models (`domain.py`, `correlation.py`, `hitl.py`, `session.py`) and frontend TypeScript types (`domain.ts`, `hitl.ts`, `pipeline.ts`).
  - Standardized on canonical `ordered_card_ids: List[str]` and ordinal `part_index` / `total_parts` representation.
- [x] **Directed Sequence Correlation & Maximal DAG Path Chaining**:
  - Replaced bipartite matching with a directed transition graph evaluating pairwise directed compatibility ($\Delta E$, scan proximity, ruling, ink, mirrored stains, sequential "Part N" headers, and semantic continuation) with cycle prevention for sequences of length $N \ge 1$.
- [x] **REST API, SSE Telemetry & SQLite SessionStore Alignment**:
  - Dynamic SQLite schema migration of `session_proposals` to `ordered_card_ids TEXT NOT NULL`.
  - Updated FastAPI endpoints (`/api/hitl/*`, `/api/pipeline/*`, `/api/recipes/*`) and SSE events to transmit sequence arrays.
- [x] **Unified "Part 1 ... N" HITL Workflow & UI**:
  - Rebranded all review cards, candidate drawers, and modals from "Front/Back" to "Part 1 of N", "Part 2 of N", etc.
  - Added drag-and-drop positional reordering, sequence inversion, and split/merge controls.
- [x] **Sequence-Preserved Multimodal Extraction & Exporters**:
  - Multimodal transcription and all 9 recipe export formats preserve ordered card sequences.
- [x] **Full Platform Test Suite Expansion**:
  - Expanded test suite to **1,660 passing tests** (1,442 backend pytest + 218 frontend Vitest across 20 suites) with 100% pass rate.

---

### Phase 8: Integration Findings Resolution & Anti-Cheating Eval Hardening
*Production refinement & eval rigor: Resolve cross-platform runtime discrepancies, eliminate model evaluation cheating, curate authentic physical clippings, and implement 1-click interactive Google OAuth.*

- [x] **OS-Adaptive Ingestion Path Placeholders (`frontend/src/utils/platform.ts`, `SourceSelector.tsx`)**:
  - Dynamically detect host runtime environment (`navigator.userAgentData.platform` with fallback to `navigator.platform` / `userAgent`).
  - Render native path semantics: Windows (`C:\Users\username\Documents\RecipeScans`), macOS (`/Users/username/Documents/RecipeScans`), and Linux/default (`/home/username/recipes`).
- [x] **Evaluation Filename Obfuscation & Anti-Cheating Pipeline (`recipeboxer/evals/obfuscation.py`)**:
  - Built `ObfuscatedCardCorpus` with deterministic SHA-256 and UUIDv4 identifier masking (`card_a7b8c9d0.jpg`), preventing models from using filenames, roles (`front`/`back`), dates, or recipe titles as evaluation shortcuts.
  - Image EXIF metadata stripping (`sanitize_image_exif`) saving clean pixel buffers.
  - Multimodal prompt sanitization in `recipeboxer/ai/gemini.py` stripping lexical ground truth clues.
  - Bidirectional ground truth mapping preserving precision/recall/F1 scoring in `evals/run_benchmarks.py`.
- [x] **Authentic Physical Clipping Fixtures (`tests/fixtures/sample_cards/`, `manifest.json`)**:
  - Sourced and curated authentic public domain / CC0 historical specimens: magazine advertisements (Crisco Fried Chicken, Campbell's 1968 Green Bean Casserole), food packaging box cutouts (1975 Toll House Cookies), and canned goods labels (1962 Borden's Eagle Brand Pie).
  - Registered ground truth physical dimensions, paper tones, printing methods, and full recipe metadata in `manifest.json`.
  - Documented archive provenance (Library of Congress, Internet Archive, MSU Feeding America) in `tests/fixtures/sample_cards/README.md`.
- [x] **Interactive Google Identity Services (GIS) OAuth Popup Flow (`frontend/src/utils/googleAuth.ts`)**:
  - Wrapped `google.accounts.oauth2.initTokenClient` with `VITE_GOOGLE_CLIENT_ID` for Docs, Drive, and Photos scopes.
  - Removed manual token input drawer in `GoogleDocsPreview.tsx`; replaced with 1-click "Export directly to Google Docs" GIS popup authorization.
  - Added interactive "Connect Account" / "Sign in with Google" flows in `SourceSelector.tsx` and `AlbumPickerModal.tsx` for Google Drive and Google Photos ingestion with automatic token expiration and 401 re-auth handling.

---

### Phase 9: Multi-Session Project Persistence & Export Curation
*Workflow continuity & publication polish: Enable iterative multi-session project building via `.recipeboxer` archives with incremental duplicate-skipping ingestion, and add configurable recipe re-ordering and categorised grouping for cookbook exports.*

- [x] **Portable Project State Persistence (`.recipeboxer` Project Archive)**:
  - **Standardized Archive Format**: Define a portable, compressed `.recipeboxer` project archive (containing JSON metadata manifest + original high-resolution card scans with content-addressed SHA-256 digests).
  - **Full State Retention**: Preserve matched card sequences, visual fingerprints, transcribed recipe schemas, human-in-the-loop review approvals, marginalia notes, and image-to-step bindings.
  - **Export-Stage Project Save & Merge**:
    - Allow users on the Export view to "Save Project As..." (`<name>.recipeboxer`) to store their work for future sessions.
    - Enable "Merge into Existing Project..." to append current session recipes into an existing `.recipeboxer` archive, resolving duplicates and updating the combined collection before saving or exporting.
- [x] **Incremental Ingestion with Duplicate-Skipping**:
  - **Project-Aware Ingestion**: Allow users at the Ingest stage to select an existing `.recipeboxer` file as a baseline when pointing to a local directory or cloud album.
  - **Intelligent SHA-256 Skip**: Compute fast image hashes during directory/album scanning and compare against the project archive's registered cards; automatically skip previously processed scans.
  - **Targeted Incremental Processing**: Only run OCR, fingerprinting, and correlation on newly detected scans, dramatically saving API tokens and processing time.
  - **Automated Export Consolidation**: Seamlessly combine existing archive recipes with newly extracted recipes in the HITL and Export stages for consolidated editing and export.
- [x] **Configurable Multi-Format Recipe Ordering & Grouping (DOCX, PDF, Google Docs)**:
  - **Interactive Reordering UI in Export Dialog**:
    - Visual drag-and-drop list allowing manual reordering of recipes prior to export.
  - **Categorical & Metadata Grouping Presets**:
    - **By Culinary Category / Course**: Breakfast, Appetizers & Snacks, Drinks & Beverages, Main Courses, Side Dishes, Desserts, Baked Goods, Preserves.
    - **By Attribution / Contributor**: Family member, heirloom tin collection, historical contributor, vintage era/decade.
    - **By Arbitrary Tags**: Dietary attributes (Gluten-Free, Vegetarian), occasion, custom tags.
    - **Alphabetical Ordering**: A–Z or Z–A by recipe title.
  - **Cookbook Layout & Section Formatting**:
    - Automatically generate formatted section divider pages and styled Tables of Contents in DOCX, printable PDF, and Google Docs cookbook exports reflecting the user's chosen grouping.

---

### Phase 10: Official Google Cloud OAuth Provisioning & Fallback Deprecation
*Zero-configuration cloud publishing: Provision an official Google Cloud project with public OAuth 2.0 Web Client credentials for localhost/production origins, complete Google app verification for Docs/Drive scopes, bake the default client ID into open-source distributions, and deprecate manual token/client ID entry fallback drawers once verified working.*

- [ ] **Official Google Cloud Project & OAuth 2.0 Provisioning**:
  - **Google Cloud Console Setup**: Create an official, zero-cost Google Cloud project dedicated to RecipeBoxer.
  - **API Enablement**: Enable the Google Docs API and Google Drive API (`drive.file` and `documents` scopes).
  - **OAuth 2.0 Web Client Configuration**: Register an OAuth 2.0 Client ID with authorized JavaScript origins for local development (`http://localhost:8000`, `http://127.0.0.1:8000`) and any future hosted domain.
  - **Verification & Consent Screen**: Configure the OAuth consent screen with privacy policy documentation and submit for standard Google verification so any user can authenticate without developer test-user restrictions.
- [ ] **Repository Configuration & Zero-Friction Distribution**:
  - **Default Client ID Integration**: Embed the official public Client ID into `frontend/.env.production` and default client configuration (`VITE_GOOGLE_CLIENT_ID`) so GitHub clones run 1-click Google sign-in out of the box.
- [ ] **Fallback Deprecation & UI Cleanup**:
  - **Deprecate Manual Token Drawer**: Once the official OAuth popup flow is confirmed operational across all target environments, retire the manual token input drawer and client ID entry prompts from `GoogleDocsPreview.tsx` to streamline the UI to a pure 1-click export experience.
  - **Retain Power-User Overrides**: Preserve environment variable (`VITE_GOOGLE_CLIENT_ID`) overrides for self-hosted instances running on custom non-localhost domain names.

---

## Contribution Guidelines & Roadmap Updates

When contributing to RecipeBoxer or updating this roadmap:

1. **Claiming an Item**:
   - Change the item's tag from `[ ] Planned` to `[~] In Progress`.
   - Ensure the feature is tracked in the active sprint or dispatch briefing.
2. **Completing an Item**:
   - Ensure all associated unit, boundary, pairwise, and integration tests pass (preserving the 100% test pass rate across all 1,660+ tests).
   - Change the item's tag from `[~] In Progress` to `[x] Completed`.
3. **Adding New Proposals**:
   - Add new proposals under the appropriate backlog section using the `[ ] Planned` tag.
   - Include a brief description of key technical deliverables and architecture touchpoints.
4. **Cross-Referencing**:
   - Maintain bidirectional links between `ROADMAP.md`, `README.md`, and `_agents/README.md`.
