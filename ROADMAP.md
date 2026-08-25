# RecipeBoxer Future Roadmap & Enhancement Backlog

This document maintains the running backlog of future features, experimental investigations, and architectural enhancements for the **RecipeBoxer** project.

---

## 1. Cloud & External Source Providers

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

## 2. Curated Sample Test Datasets & Benchmarks

- [ ] **Representative Sample Scans Repository (`tests/fixtures/sample_cards/`)**:
  - **Vintage Handwritten Cursive**: 1940s–1970s index cards with aged paper, fountain pen bleed, and cursive handwriting.
  - **Typewritten & Stamped**: Cards produced on manual typewriters with ink ribbon wear.
  - **Magazine Clippings & Package Labels**: Glued-on newspaper recipes, clipped product box backs, and multi-column magazine layouts.
  - **Stained / Damaged / Edge-Folded Cards**: Real-world kitchen artifacts (oil spots, grease stains spanning card boundaries, coffee rings, torn edges).
  - **Multi-Card Recipes (3–4 Cards)**: Extended canning, holiday baking, and multi-step confectionery recipes.
- [ ] **Synthetic Test Generator**:
  - Script to generate synthetic card front/back pairs with known physical textures, fonts, and procedural continuations for regression testing.

---

## 3. Agent, Skill & Prompt Evaluation Harness (`evals/`)

- [ ] **Quantitative Metric Suite for Gemini Vision**:
  - **Pairing Accuracy (F1 Score)**: Precision and recall of front/back correlation proposals against ground-truth labeled datasets.
  - **Transcription Accuracy (CER / WER)**: Character Error Rate (CER) and Word Error Rate (WER) on ingredient names, numeric quantities, and fractions (e.g., `1/4` vs `1/2`).
  - **Schema Conformity Score**: Automated validation rate for strict Plan To Eat schema adherence.
- [ ] **LLM-as-a-Judge Evaluation Pipeline**:
  - Independent judge prompt to evaluate semantic fidelity, preservation of vintage cooking notes, and step order accuracy.
- [ ] **Golden Dataset Regression Runner**:
  - Automated CI test running eval benchmarks against model updates (e.g., comparing `gemini-3.7-flash` vs `gemini-3.7-pro` vs future model versions).

---

## 4. Adaptive & Branching Vision Prompts

- [ ] **Document Class Detection & Prompt Routing**:
  - Add a lightweight pre-classification pass or fingerprint cue to classify card modality:
    - `HANDWRITTEN_CURSIVE`
    - `HANDWRITTEN_PRINT`
    - `TYPEWRITTEN`
    - `PRINTED_CLIPPING_OR_LABEL`
    - `MIXED_MEDIA` (handwritten notes on a printed clipping)
- [ ] **Specialized Extraction Prompt Branches**:
  - **Cursive Branch**: Emphasizes deciphering historical letterforms, abbreviations (e.g., "T" vs "t" for Tbsp/tsp, "oleo" for margarine, "saleratus" for baking soda), and cursive flourishes.
---

## 5. Developer & Agent Guidelines Repository (`agents/` / `AGENTS.md`)

- [ ] **Dedicated `agents/` Context & Architecture Directory**:
  - `agents/ARCHITECTURE.md`: High-level system overview, module dependencies, and state machine diagrams.
  - `agents/INVARIANTS.md`: Critical architectural non-negotiables (SQLite WAL concurrency patterns, Plan To Eat RFC 4180 CSV escaping, Vite SPA static bundle paths, HITL async barrier semantics).
  - `agents/TESTING_GUIDELINES.md`: Standards for 4-tier acceptance testing, mock isolation, and adversarial test writing.
  - `agents/EXTENDING_RECIPEBOXER.md`: Step-by-step developer guides for adding new source providers, implementing custom prompt branches, and creating new export format serializers.

