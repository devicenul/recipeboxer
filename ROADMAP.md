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
| **M8** | **Sample Card Corpus, Provenance & Synthetic Generator** | 23 high-resolution historical card fixtures across 6 modalities, CC0/public domain provenance guide (`tests/fixtures/sample_cards/README.md`), strict Pydantic `manifest.json`, procedural generator tool (`recipeboxer/tools/card_generator.py`), and pytest loader fixtures. | `[x] Completed` |
| **M9** | **Agent & Prompt Evaluation Harness & Benchmark Suite** | Quantitative metric suite (Pairing F1, Levenshtein CER/WER, Plan To Eat / Schema.org schema conformity), LLM-as-a-Judge semantic rubric evaluator (`recipeboxer/evals/judge.py`), golden dataset benchmark runner CLI (`evals/run_benchmarks.py`), and 65 unit/integration tests. | `[x] Completed` |

---

## Prioritized Strategic Path Forward

```
[x] Phase 1: Curated Test Datasets & Benchmarks
       │
       ▼
[x] Phase 2: Agent & Prompt Evaluation Harness (evals/)
       │
       ▼
[ ] Phase 3: Adaptive Vision Prompts, Antigravity Skills & CV Tools
       │
       ▼
[ ] Phase 4: Archival Document Generation & Extended Exporters
       │
       ▼
[ ] Phase 5: Cloud & External Source Providers
       │
       ▼
[ ] Phase 6: Comprehensive User Guide, Guided UI Tutorial & MVP Release
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

- [ ] **Deterministic Computer Vision Preprocessing Tools**:
  - Integrate deterministic tools (OpenCV deskewing, card border auto-cropping, white-balance normalization, and color histogram clustering) as Antigravity tool extensions before passing images to Gemini.
- [ ] **Specialized Antigravity Analysis Skills**:
  - `vintage-handwriting-ocr`: Domain skill specialized in historical cursive letterforms, fraction parsing (`1/8`, `1/4`, `1/2`), and vintage abbreviations ("T" vs "t", "oleo", "saleratus").
  - `print-clipping-extractor`: Domain skill specialized in multi-column newspaper layouts, package labels, and advertisement/coupon exclusion.
- [ ] **Document Class Detection & Dynamic Prompt Routing**:
  - Lightweight classification pass routing cards to specialized prompt branches based on visual substrate cues (`HANDWRITTEN_CURSIVE`, `HANDWRITTEN_PRINT`, `TYPEWRITTEN`, `PRINTED_CLIPPING_OR_LABEL`, `MIXED_MEDIA`).
- [ ] **Dynamic Thinking Budget Allocation**:
  - Dynamically allocate deeper reasoning tokens to degraded cursive scans or low-contrast pencil cards while keeping clean typed cards on fast zero-overhead inference.

---

### Phase 4: Archival Document Generation & Extended Exporters
*Ecosystem expansion: Deliver high-value export formats, including visual family cookbook generation.*

- [ ] **Google Docs Archival Exporter (with Scanned Image Embeds)**:
  - Export full recipe collections to a structured **Google Doc** via the Google Docs API.
  - Page-break separated layout: each recipe presented with beautifully formatted typography (title, prep/cook/total times, yield, ingredients table, numbered directions, tags, and attribution notes).
  - High-resolution visual embedding: embeds the original scanned card images (front and back) directly below the formatted recipe, auto-cropped and enhanced for archival printing and family cookbooks.
- [ ] **Offline DOCX & Printable Archival PDF Exporter**:
  - Generate standalone `.docx` and styled printable PDF recipe books with the same split layout (transcribed recipe + original scanned cards).
- [ ] **Markdown / Obsidian Recipe Serializer**:
  - YAML frontmatter with tags, cook times, and ingredients list formatted for Obsidian Recipe plugins.
- [ ] **Cooklang (.cook) Serializer**:
  - Plain-text recipe markup format for the Cooklang ecosystem.
- [ ] **Paprika Recipe Manager (.paprikarecipes) Export**:
  - Compressed gzip archive format compatible with Paprika app imports.

---

### Phase 5: Cloud & External Source Providers
*Ingestion flexibility: Extend source ingestion beyond local folders to cloud storage and photo libraries.*

- [ ] **Unified `SourceProvider` Abstraction**:
  - Define an abstract base class `BaseSourceProvider` (`list_images()`, `fetch_thumbnail()`, `download_image()`, `get_metadata()`).
  - Standardize source pagination, credential management, rate limiting, and local SQLite caching.
- [ ] **Google Photos Integration**:
  - Implement Google Photos Library API connector with OAuth2 authentication.
  - Allow users to select specific Google Photos albums, date ranges, or search filters (e.g. "recipe", "food", "document").
  - Support automatic caching of remote photo assets into local SQLite WAL cache.
- [ ] **Cloud Drive Connectors**:
  - Google Drive folder selector.
  - Apple iCloud Drive / Dropbox / OneDrive folder connectors.
  - S3 / GCS bucket source connectors for batch archival jobs.

---

### Phase 6: User Guide, Guided UI Tutorial & Initial MVP Release
*Polish & onboarding: Deliver comprehensive documentation, interactive user onboarding, and production distribution.*

- [ ] **"First Run" Interactive Guided UI Tutorial**:
  - Built-in interactive walkthrough in the frontend introducing new users to the complete workflow: launching an ingestion job, using Rapid Queue keyboard shortcuts (`Space`, `S`, `B`, `X`, `Tab`), exploring the Candidate Drawer & 3+ card grouping, toggling image inspection filters, editing in the split-screen recipe editor, and exporting.
- [ ] **Comprehensive User Guide & End-to-End Walkthrough (`README.md`)**:
  - Visual onboarding: Architecture diagrams, UI feature walkthroughs, and screenshot guides.
  - Step-by-step multi-platform setup instructions (macOS, Linux, Windows PowerShell / CMD).
  - Troubleshooting FAQ: API key configuration (`GEMINI_API_KEY`), quota management with slow-batch pacing, port binding, and cache management.
- [ ] **Initial MVP Release & Pre-Built Binary Packaging**:
  - Tag initial MVP release and publish cross-platform standalone binaries (`macOS`, `Linux`, `Windows`).

---

## Contribution Guidelines & Roadmap Updates

When contributing to RecipeBoxer or updating this roadmap:

1. **Claiming an Item**:
   - Change the item's tag from `[ ] Planned` to `[~] In Progress`.
   - Ensure the feature is tracked in the active sprint or dispatch briefing.
2. **Completing an Item**:
   - Ensure all associated unit, boundary, pairwise, and integration tests pass (preserving the 100% test pass rate across all 910+ tests).
   - Change the item's tag from `[~] In Progress` to `[x] Completed`.
3. **Adding New Proposals**:
   - Add new proposals under the appropriate backlog section using the `[ ] Planned` tag.
   - Include a brief description of key technical deliverables and architecture touchpoints.
4. **Cross-Referencing**:
   - Maintain bidirectional links between `ROADMAP.md`, `README.md`, and `_agents/README.md`.
