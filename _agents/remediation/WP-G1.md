# Work Package Completion Report: WP-G1 (Structured Fingerprint Call)

**Work Package:** WP-G1  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  
**Contracts:** `_agents/remediation/WAVE4_CONTRACTS.md` §1.3, §1.4, §1.5, §2; `_agents/remediation/WAVE3_CONTRACTS.md` §0; `REMEDIATION_PLAN.md` §9, §9.1  

---

## 1. Summary of Changes

1. **Created `recipeboxer/models/features.py` (First Action)**:
   - Defined `PixelFeatures` exactly per §1.4 (`paper_lab`, `paper_lab_std`, `ruling_spacing_px`, `ruling_angle_deg`, `ruling_kind`, `ink_hues`, `aspect_ratio`, `long_edge_px`, `deskew_angle_deg`, `crop_box`, `stain_mask_ref`).
   - Defined `CaptureMeta` exactly per §1.5 (`taken_at`, `subsec`, `device`, `source`, `natural_sort_index`).

2. **Extended `VisualFingerprint` (`recipeboxer/models/domain.py`) per §1.3**:
   - Added optional fields with neutral defaults: `visible_title`, `first_lines`, `last_lines`, `printed_markers`, `text_cut_off_end`, `text_cut_off_start`, `sections_detected`, `script_descriptor`, `orientation_deg`, `looks_like_back`, `has_ingredients`, `has_directions`, `pixel`, `capture`, `embedding_ref`.
   - Maintained 100% backward compatibility with all existing fields and validators.

3. **Created Prompt Registry (`recipeboxer/prompts/`)**:
   - `recipeboxer/prompts/__init__.py`: loads prompt markdown by ID, returns `PromptRecord(id, text, version)`, where `version` is an 8-character deterministic SHA-256 hash for provenance and caching. Exposes `get_prompt`, `get_prompt_text`, `get_prompt_version`, and `list_prompts`.
   - `recipeboxer/prompts/fingerprint_v2.md`: official system instruction requiring verbatim transcription of printed text, explicit prohibition on guessing/hallucination, and structured extraction of title, boundary lines, markers, substrate, and orientation.

4. **Structured LLMClient Fingerprinting (`recipeboxer/vision/fingerprint.py`)**:
   - Structured output via `FingerprintV2Response`: flat, Gemini-schema-friendly Pydantic model with enums and primitive optionals.
   - Stage: `LLMStage.FINGERPRINT`, model from `settings.get_stage_model(LLMStage.FINGERPRINT)`.
   - Prompt loaded from registry (`fingerprint_v2`).
   - Image payload sent as a single `ImagePart` with a neutral label (`recipe_card_image`), never leaking filenames or IDs.
   - Comprehensive payload mapping function `_map_payload_to_fingerprint` handling `FingerprintV2Response`, `VisualFingerprint`, and dictionaries.
   - Typed error mapping to `FingerprintUnavailable` with appropriate `FailureReason` (`RATE_LIMITED`, `TIMEOUT`, `INVALID_RESPONSE`, `SCHEMA_VALIDATION`).
   - Retained offline degraded pixel analysis (`degraded=True`, `role=UNKNOWN`) with zero fabricated text fields.
   - Persistent content caching: `compute_fingerprint_cache_key(image_hash, model, prompt_version)` included in `FingerprintCache` lookups and storage.
   - Routing: prefers `create_llm_client` when no client is injected while preserving legacy mock/httpx paths.

5. **Heuristic Sequential Header Scoring (`recipeboxer/vision/heuristics.py`)**:
   - Enhanced `calculate_sequential_header_score(fp1, fp2)`:
     - `printed_markers`: forward progression (`1` -> `2`, `Card 1` -> `Card 2`) scores `1.00`, reverse scores `0.10`.
     - `text_cut_off_end` (fp1) and `text_cut_off_start` (fp2) add continuation bonuses (`+0.25`).
     - `looks_like_back`: front->back adds `+0.15`, back->front penalizes `-0.25`.
     - `has_ingredients` / `has_directions`: ingredients->directions adds `+0.15`, reverse penalizes `-0.25`.
     - Baseline remains strictly `0.50` neutral when new fields are absent or None.

6. **Automated Verification (`tests/test_wp_g1_fingerprint.py`)**:
   - Authored 12 offline tests covering prompt registry loading, request shape, privacy/zero-leakage, response mapping, cache key composition, cache hits/misses, failure typing, degraded fallback, heuristics progression, and feature model contracts.

---

## 2. Files Changed

### Created
- `recipeboxer/models/features.py` (PixelFeatures & CaptureMeta)
- `recipeboxer/prompts/__init__.py` (Prompt registry)
- `recipeboxer/prompts/fingerprint_v2.md` (Fingerprint v2 prompt markdown)
- `tests/test_wp_g1_fingerprint.py` (12 targeted unit tests)
- `_agents/remediation/WP-G1.md` (This note)

### Modified
- `recipeboxer/models/domain.py` (VisualFingerprint §1.3 field extensions)
- `recipeboxer/vision/fingerprint.py` (Structured LLMClient call, cache integration, mapping)
- `recipeboxer/vision/heuristics.py` (calculate_sequential_header_score v2 evaluation)

---

## 3. Commands & Results

1. **Targeted WP-G1 Tests**:
   ```bash
   timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_g1_fingerprint.py -v -p no:randomly
   # Result: 12 passed in 0.09s (100%)
   ```

2. **Targeted Fingerprint, Heuristics, Cache & Anticheat Suite**:
   ```bash
   timeout 120 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
       tests/test_wp_g1_fingerprint.py \
       tests/test_wp_c45_fingerprint_heuristics.py \
       tests/test_heuristics.py \
       tests/test_cache.py \
       tests/anticheat \
       -v -p no:randomly
   # Result: 69 passed in 2.84s (100%)
   ```

3. **Static Anti-Cheat Invariants**:
   ```bash
   timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat/test_static_invariants.py -v -p no:randomly
   # Result: 4 passed in 1.74s (100% clean)
   ```

4. **Canaries (Leak Scans, Vault, Hook)**:
   ```bash
   timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat/test_canaries.py -v -p no:randomly
   # Result: 6 passed in 0.53s (100% clean)
   ```

---

## 4. Contract Deviations

None. All interfaces strictly implement `WAVE4_CONTRACTS.md` §1.3, §1.4, and §1.5.

---

## 5. Requests for Other WPs

- **WP-G2 (`recipeboxer/vision/pixel_features.py`)**: Can import `PixelFeatures` directly from `recipeboxer.models.features`.
- **WP-G3 (`recipeboxer/ingestion/capture.py`)**: Can import `CaptureMeta` directly from `recipeboxer.models.features`.
- **WP-X10 (Prompt Versioning)**: Can register new prompt markdown files in `recipeboxer/prompts/` and access them via `recipeboxer.prompts.get_prompt`.

---

## 6. Risks & Mitigations

- **Risk**: Cache invalidation when prompt template changes.  
  **Mitigation**: Cache keys incorporate the 8-character SHA-256 version hash of `fingerprint_v2.md`. Any prompt modification automatically invalidates stale entries without requiring cache truncation.
