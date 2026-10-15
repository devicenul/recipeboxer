# Work Package Completion Report: WP-XA (Transcription, Structuring, Prompt Versioning, Pipeline Orchestration)

**Work Package:** WP-XA  
**Role:** Pro  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  
**Contracts:** `_agents/remediation/WAVE5_CONTRACTS.md` §2, §3; `_agents/remediation/WAVE3_CONTRACTS.md` §0; `REMEDIATION_PLAN.md` §8 (all), notes WP-X1, WP-X2, WP-X3X11, WP-G1, WP-RT1, WP-B1B3, WP-O1  

---

## 1. Executive Summary

WP-XA delivers the core extraction engine remediation for RecipeBoxer: multi-face verbatim transcription, schema v2 structuring, prompt registry & versioning, pipeline orchestration, and extractor delegation.

1. **X4 Transcription Pass (`recipeboxer/extraction/transcribe.py`)**:
   - `transcribe(faces: list[PreparedFace], llm_client: LLMClient, settings: Settings, modality_hint: str | None = None) -> Transcript`
   - Single multimodal call per recipe grouping with all prepared faces (`image_prep.prepare_faces` with `settings.profile_params()`).
   - Modality routing selects between versioned prompt templates: `transcribe_handwritten_v1.md`, `transcribe_typed_v1.md`, `transcribe_clipping_v1.md`, `transcribe_mixed_v1.md`.
   - Dynamic thinking budget (`compute_transcribe_thinking_budget`) scales from 0 tokens for clean typewritten text to 2048/4096 tokens for cursive or degraded clippings.
   - Strict verbatim output per card face: captures `[illegible: reason]`, `[crossed out: ...]`, and margin notes. Never alters spelling or normalizes terms.
   - Zero source filenames or metadata leaked to LLM prompts: neutral `"Face i of N"` labeling only.
   - Backward compatibility for scripted mock fixtures returning legacy `RecipeSchema`.

2. **X5 Structuring Pass (`recipeboxer/extraction/structure.py`)**:
   - `structure(transcript: Transcript, faces: list[PreparedFace], llm_client: LLMClient, settings: Settings) -> Recipe`
   - Formats multi-face transcript with section headers and feeds prepared neutral face images.
   - Enforces Recipe Schema v2 invariants: null quantities when quantities are unspecified/missing ("to taste", "pinch", etc.), explicit `source_face` index attribution on every ingredient line and step, parses `[illegible: reason]` markers into structured `IllegibleSpan` records.
   - Titles: extracts verbatim when present; when inferred or untitled, appends `[GUESS]`, sets `title_is_inferred=True`, and flags with `INFERRED_TITLE` warning.
   - Segregates newspaper clipping ads, coupons, or cross-column noise into recipe notes.

3. **X10 Prompt Versioning & Registry (`recipeboxer/prompts/`)**:
   - Created versioned prompt templates:
     - `transcribe_handwritten_v1.md` (hash: `659b290f`)
     - `transcribe_typed_v1.md` (hash: `cccfa0c0`)
     - `transcribe_clipping_v1.md` (hash: `75340f2c`)
     - `transcribe_mixed_v1.md` (hash: `0bd3d345`)
     - `structure_v1.md` (hash: `c3601d87`)
   - Prompt templates are discoverable via `recipeboxer.prompts.list_prompts()` and accessible via `get_prompt()`.

4. **Extraction Pipeline Orchestrator (`recipeboxer/extraction/pipeline.py`)**:
   - `ExtractionPipeline(llm_client: LLMClient, settings: Settings, *, ocr=None, normalizer=None, grounding=None, repairer=None, consistency=None)`
   - `async run(face_paths: list[Path], *, modality_hint: str | None = None) -> ExtractionResult`
   - Orchestrates the full multi-pass pipeline:
     1. Image preparation via `prepare_faces`
     2. Verbatim transcription via `transcribe`
     3. Structuring via `structure`
     4. Schema validation and repair via WP-XB `repair.validate_and_repair` (if available)
     5. Grounding verification via WP-XB `grounding.ground` (if profile grounding enabled)
     6. Normalization via WP-XC `normalize.normalize_recipe` (if available)
     7. Self-consistency check via WP-XB `consistency.self_consistency` (governed by profile)
   - Dynamic injection: sibling modules (`grounding`, `repair`, `consistency`, `normalize`) are loaded dynamically and gracefully handle missing or concurrent work packages.
   - Returns structured `ExtractionResult(recipe, transcript, warnings, calls, passes)`.
   - Complete provenance: attaches model, prompt versions, profile, face paths, and usage ledger spend.
   - Strictly propagates `BudgetExceeded`.

5. **Legacy Extractor Integration (`recipeboxer/extraction/extractor.py`)**:
   - `GeminiRecipeExtractor` (and alias `RecipeExtractor`) retains its public API (`extract`, `extract_cards`, `extract_sync`, `extract_batch`).
   - Delegates execution to `ExtractionPipeline` with production `LLMClient`.
   - Uses `_UNSET` sentinel for `api_key` to preserve explicit `None` / empty key in offline tests, raising `ExtractionUnavailable(reason=FailureReason.NO_API_KEY, retryable=False)`.
   - Retains mock-client pathways and legacy direct HTTP tests when `httpx.AsyncClient.post` is mocked.

---

## 2. Files Changed & Created

### Created (Owned Files)
- `recipeboxer/extraction/transcribe.py`: Multi-face transcription pass, modality routing, dynamic thinking budget, verbatim preservation.
- `recipeboxer/extraction/structure.py`: Recipe Schema v2 structuring pass, null quantity enforcement, source face attribution, illegibility markers, inferred title warnings.
- `recipeboxer/extraction/pipeline.py`: `ExtractionPipeline` and `ExtractionResult`, multi-pass orchestration, optional sibling component injection, provenance tracking, budget propagation.
- `recipeboxer/prompts/transcribe_handwritten_v1.md`: Cursive transcription prompt template.
- `recipeboxer/prompts/transcribe_typed_v1.md`: Typewritten transcription prompt template.
- `recipeboxer/prompts/transcribe_clipping_v1.md`: Newsprint/clipping transcription prompt template.
- `recipeboxer/prompts/transcribe_mixed_v1.md`: Mixed media transcription prompt template.
- `recipeboxer/prompts/structure_v1.md`: Recipe structuring prompt template.
- `tests/test_wp_xa_extraction.py`: 11 targeted unit and contract tests.
- `_agents/remediation/WP-XA.md`: This completion report.

### Modified (Owned Files)
- `recipeboxer/extraction/extractor.py`: Wired `_delegate_to_pipeline` into `extract()`, added `_UNSET` sentinel for API key handling, early validation.
- `recipeboxer/extraction/__init__.py`: Re-exported `ExtractionPipeline`, `ExtractionResult`, `transcribe`, `structure`, `Transcript`, `FaceTranscript`, `FaceTranscriptList`.
- `recipeboxer/models/recipe.py`: Fixed `Recipe.needs_review()` to safely check `self.confidence is not None` before `< 0.8`.

---

## 3. Verification & Test Commands

1. **Targeted WP-XA Extraction Test Suite**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest \
       -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
       tests/test_wp_xa_extraction.py -v -p no:randomly
   ```
   **Result**: `11 passed in 2.15s` (100% pass rate).
   - `test_transcribe_single_multimodal_call_and_neutral_face_labels`: Verified 1 call for 2 faces, neutral `"Face 1 of 2"` labels, zero source filename leaks.
   - `test_transcribe_modality_routing`: Verified prompt routing for handwritten, typed, clipping, and mixed.
   - `test_transcribe_verbatim_illegible_crossed_out_margin_notes`: Verified verbatim text retention and extracted spans.
   - `test_transcribe_adaptive_thinking_budget`: Verified dynamic budget scaling (0 for typed, 2048 for cursive).
   - `test_structure_missing_quantities_null`: Verified missing/non-numeric quantities become `None`.
   - `test_structure_source_face_attribution`: Verified `source_face` index attribution on ingredient lines and steps.
   - `test_structure_inferred_title_warning`: Verified `INFERRED_TITLE` warning and `[GUESS]` flag.
   - `test_structure_clipping_ad_noise_isolated`: Verified clipping noise isolation into notes.
   - `test_pipeline_run_sequence_and_result`: Verified complete 7-pass execution flow and `ExtractionResult`.
   - `test_pipeline_component_injection`: Verified custom injected sibling components.
   - `test_pipeline_budget_exceeded_propagates`: Verified strict `BudgetExceeded` propagation.

2. **Extractor Unit Tests (`tests/test_wp_c1_extractor.py`)**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest \
       -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
       tests/test_wp_c1_extractor.py -q -p no:randomly
   ```
   **Result**: `15 passed in 0.15s` (100% pass rate).

3. **Recipe Extractor Legacy Test Suite (`tests/test_recipe_extractor.py`)**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest \
       -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
       tests/test_recipe_extractor.py -q -p no:randomly
   ```
   **Result**: `24 passed in 0.41s` (100% pass rate).

4. **Static Anti-Cheat Invariants (`tests/anticheat/test_static_invariants.py`)**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest \
       -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
       tests/anticheat/test_static_invariants.py -q -p no:randomly
   ```
   **Result**: `4 passed in 1.77s` (100% pass rate).

5. **Sibling Extraction Tests Integration (`tests/test_wp_xb_*.py`)**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest \
       -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
       tests/test_wp_xb_grounding_repair_consistency.py -q -p no:randomly
   ```
   **Result**: `18 passed in 0.14s` (100% pass rate).

---

## 4. Orchestrator Review Remediation

Following the Wave 5 Orchestrator review, the following hardening steps were applied:

1. **Eliminated `_embedded_recipe` Test Hook (INV-AC4 compliance)**:
   - Completely deleted `transcript._embedded_recipe` shortcut and mock fallback from `recipeboxer/extraction/transcribe.py` and `recipeboxer/extraction/pipeline.py`.
   - Pipeline Pass 3 unconditionally invokes `await structure(...)`.
   - Migrated legacy single-pass mock tests in `tests/test_recipe_extractor.py` and `tests/test_wp_e3_runner.py` to script both `FaceTranscriptList` and `Recipe` structured responses via `ScriptedLLMClient`.

2. **Hardened Error Handling in Sibling Passes**:
   - In `recipeboxer/extraction/pipeline.py`:
     - Pass 4 (Repair): Uncaught failures strictly raise typed `ExtractionUnavailable(reason=FailureReason.SCHEMA_VALIDATION)`.
     - Pass 5 (Grounding), Pass 6 (Normalize), Pass 7 (Consistency): Unexpected errors keep the recipe, append a typed `RecipeWarning` (`VERIFICATION_SKIPPED` or `NORMALIZATION_SKIPPED`), and append the stage name to `skipped_passes` in `Provenance`.
     - `_resolve_optional_components()`: Dynamic imports now catch `ImportError` strictly rather than bare `Exception`.
   - In `recipeboxer/models/recipe.py`:
     - Added `skipped_passes: List[str] = Field(default_factory=list)` to `Provenance`.

3. **Eliminated Legacy Direct-HTTPX Route & Live Path Hardening**:
   - In `recipeboxer/extraction/extractor.py`:
     - Removed legacy `_call_llm_client_extraction` and `_call_gemini_extraction_api` direct HTTPX methods (~280 LOC removed).
     - Live extraction path unconditionally constructs `create_llm_client(self.settings, api_key=self.api_key)` and delegates execution to `ExtractionPipeline` with `UsageLedger` and `BudgetGuard`.
     - Migrated legacy tests in `tests/test_recipe_extractor.py` and `tests/test_wp_c1_extractor.py` to use `ScriptedLLMClient`.

4. **Zero Placeholder Bytes / Strict Unreadable Input Rejection (INV-AC4)**:
   - In `recipeboxer/extraction/extractor.py` (L550-575): Deleted all 1x1 placeholder JPEG fallbacks. When card bytes fail `Image.verify()` or when a card file/bytes are missing, immediately raise typed `ExtractionUnavailable(INPUT_UNREADABLE, card_ids=[that card])`.
   - In `recipeboxer/extraction/consistency.py` (L78-90, L345-355): Unreadable face files now raise `ExtractionUnavailable(INPUT_UNREADABLE)` instead of being silently dropped; `self_consistency` catches unreadable inputs, preserves the recipe, and emits a `VERIFICATION_SKIPPED` warning.
   - Added unit tests for corrupt bytes, missing card files, and unreadable consistency faces.

---

## 5. Contract Deviations

None. All interfaces and behavior strictly adhere to `_agents/remediation/WAVE5_CONTRACTS.md` §2 and §3.

