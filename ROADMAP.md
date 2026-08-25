# RecipeBoxer Milestone Tracking & Future Enhancement Roadmap

This document serves as the canonical shared roadmap and backlog for **RecipeBoxer**. It tracks completed milestones, active work, and future architectural enhancements.

---

## Roadmap Status Conventions

All roadmap items and milestones use standardized status tags:

- `[x] Completed`: Fully implemented, verified against 4-tier testing suites, and integrated with zero test regressions.
- `[~] In Progress`: Active milestone or feature currently under active implementation or review.
- `[ ] Planned`: Approved future enhancement in the prioritized backlog.

---

## Milestone Overview & Tracking

| Milestone | Title | Key Deliverables | Status |
|:---:|---|---|:---:|
| **M1** | **Backend Architecture & Core Domain Engine** | Python package structure, Pydantic V2 models (`VisualFingerprint`, `RecipeSchema`), Gemini 2.5 Flash visual substrate analysis, CIELAB $\Delta E$ color math, scan proximity decay, horizontally mirrored stain bleed-through, ruling/ink heuristics, greedy bipartite matching optimizer. | `[x] Completed` |
| **M2** | **FastAPI REST & SQLite WAL Persistence** | FastAPI ASGI application, thread-safe `FingerprintCache` (content-addressed SHA-256 cache), relational `SessionStore` (sessions, cards, proposals, recipes), secure card image streaming, multipart uploads, CLI runner. | `[x] Completed` |
| **M3** | **Real-Time SSE & HITL Review Pipeline** | Server-Sent Events stream (`/api/events`), cooperative async barrier (`approval_event` & `cancellation_event`), Rapid Review Queue, Candidate Drawer ranking, 3+ card multi-part recipe grouping trays, global hotkey shortcuts. | `[x] Completed` |
| **M4** | **Multi-Format Exporters & Schema.org JSON-LD** | Plan To Eat RFC 4180 9-column CSV serializer with CRLF terminators, Schema.org Recipe JSON-LD exporter (`HowToStep`, `@graph`, ISO 8601 duration parser), standard JSON serializer, and download endpoints. | `[x] Completed` |
| **M5** | **React 18+ TypeScript SPA & Static Embedding** | React 18 SPA with Vite, Tailwind CSS, Lucide icons, custom hooks (`useSSE`, `useKeyboard`, `useImageViewer`, `useRecipeForm`, `useTheme`), interactive pan/zoom/filter card viewer, Vite build contract directly into `recipeboxer/api/static`, SPA catch-all fallback with `/api` 404 guard. | `[x] Completed` |
| **M6** | **Agent Guidance, Invariants & Repository Structure** | Complete `_agents/` documentation directory (`README.md`, `ARCHITECTURE.md`, `INVARIANTS.md`, `TESTING_GUIDELINES.md`, `EXTENDING_RECIPEBOXER.md`), 4-tier testing guide, extension playbooks, and standardized roadmap persistence conventions. | `[x] Completed` |

---

## Future Enhancement Backlog

### 1. Cloud & External Source Providers
- [ ] **Unified `SourceProvider` Abstraction**:
  - Define an abstract base class `BaseSourceProvider` (`list_images()`, `fetch_thumbnail()`, `download_image()`, `get_metadata()`).
  - Standardize source pagination, credential management, and rate limiting.
- [ ] **Google Photos Integration**:
  - Implement Google Photos Library API connector with OAuth2 authentication.
  - Allow users to select specific Google Photos albums, date ranges, or search filters (e.g., "recipe", "food", "document").
  - Support automatic caching of remote photo assets into local SQLite WAL cache.
- [ ] **Cloud Drive Connectors**:
  - Google Drive folder selector.
  - Apple iCloud Drive / Dropbox / OneDrive folder connectors.
  - S3 / GCS bucket source connectors for batch archival jobs.

---

### 2. Curated Sample Test Datasets & Benchmarks
- [ ] **Representative Sample Scans Repository (`tests/fixtures/sample_cards/`)**:
  - **Vintage Handwritten Cursive**: 1940s–1970s index cards with aged paper, fountain pen bleed, and cursive handwriting.
  - **Typewritten & Stamped**: Cards produced on manual typewriters with ink ribbon wear.
  - **Magazine Clippings & Package Labels**: Glued-on newspaper recipes, clipped product box backs, and multi-column magazine layouts.
  - **Stained / Damaged / Edge-Folded Cards**: Real-world kitchen artifacts (oil spots, grease stains spanning card boundaries, coffee rings, torn edges).
  - **Multi-Card Recipes (3–4 Cards)**: Extended canning, holiday baking, and multi-step confectionery recipes.
- [ ] **Synthetic Test Generator**:
  - Script to generate synthetic card front/back pairs with known physical textures, fonts, and procedural continuations for regression testing.

---

### 3. Agent, Skill & Prompt Evaluation Harness (`evals/`)
- [ ] **Quantitative Metric Suite for Gemini Vision**:
  - **Pairing Accuracy (F1 Score)**: Precision and recall of front/back correlation proposals against ground-truth labeled datasets.
  - **Transcription Accuracy (CER / WER)**: Character Error Rate (CER) and Word Error Rate (WER) on ingredient names, numeric quantities, and fractions (e.g., `1/4` vs `1/2`).
  - **Schema Conformity Score**: Automated validation rate for strict Plan To Eat schema adherence.
- [ ] **LLM-as-a-Judge Evaluation Pipeline**:
  - Independent judge prompt to evaluate semantic fidelity, preservation of vintage cooking notes, and step order accuracy.
- [ ] **Golden Dataset Regression Runner**:
  - Automated CI test running eval benchmarks against model updates (e.g., comparing `gemini-2.5-flash` vs `gemini-2.5-pro` vs future model versions).

---

### 4. Adaptive & Branching Vision Prompts
- [ ] **Document Class Detection & Prompt Routing**:
  - Add a lightweight pre-classification pass or fingerprint cue to classify card modality:
    - `HANDWRITTEN_CURSIVE`
    - `HANDWRITTEN_PRINT`
    - `TYPEWRITTEN`
    - `PRINTED_CLIPPING_OR_LABEL`
    - `MIXED_MEDIA` (handwritten notes on a printed clipping)
- [ ] **Specialized Extraction Prompt Branches**:
  - **Cursive Branch**: Emphasizes deciphering historical letterforms, archaic abbreviations (e.g., "T" vs "t" for Tbsp/tsp, "oleo" for margarine, "saleratus" for baking soda), and cursive flourishes.
  - **Clipping Branch**: Multi-column text extraction, OCR error correction for newsprint degradation.

---

### 5. Extended Multi-Format Exporters
- [ ] **Markdown / Obsidian Recipe Serializer**:
  - YAML frontmatter with tags, cook times, and ingredients list formatted for Obsidian Recipe plugins.
- [ ] **Cooklang (.cook) Serializer**:
  - Plain-text recipe markup format for Cooklang ecosystem.
- [ ] **Paprika Recipe Manager (.paprikarecipes) Export**:
  - Compressed gzip archive format compatible with Paprika app imports.

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
