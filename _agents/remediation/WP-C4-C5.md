# Work Package Completion Report: WP-C4 and WP-C5 (Fingerprint & Heuristics Remediation)

**Package Owner:** WP-C4 / WP-C5 (flash)  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  

---

## 1. Summary of Remediation

In accordance with `_agents/remediation/REMEDIATION_PLAN.md` (§2, §4.1, §9.1, §10), `_agents/INVARIANTS.md` (§10, INV-AC1..INV-AC4), and `_agents/TESTING_GUIDELINES.md` (§7), WP-C4 and WP-C5 eliminated all filename cheats, keyword-inferred attributes, and filename regexes from `recipeboxer/vision/fingerprint.py` and `recipeboxer/vision/heuristics.py`, replacing them with pure pixel analysis and strictly text-driven boundary heuristics.

### TASK C4 (`recipeboxer/vision/fingerprint.py` & `recipeboxer/models/domain.py`):
1. **Domain Model Extension (`models/domain.py`)**:
   - Added `degraded: bool = Field(default=False, description="Whether visual fingerprint was generated via degraded offline pixel analysis or fallback")` to `VisualFingerprint`.
   - Added `degraded_reason: Optional[str] = Field(default=None, description="Reason why visual fingerprint is degraded")` to `VisualFingerprint`.
   - Default values maintain 100% backward compatibility across all existing keyword-argument constructors.

2. **Removal of Filename Keyword Cheats (INV-AC2)**:
   - Eliminated the offline branch (~L253-379) that inferred role, ruling, ink, stains, and boundary text from filename tokens (`front`, `back`, `part1`, `card1`, `cinnamon`, `lemon`, `pecan`, `unruled`, `white`, `blue`, `stain`, `divider`, `standalone`, etc.).
   - Card ID and image path are treated strictly as opaque identifiers and are only stored in the output object or logged.

3. **Deterministic Pixel-Only Analysis (`recipeboxer/vision/preprocessing.py`)**:
   - Substrate paper colorimetry extracted via `extract_paper_color_palette` (CIELAB clustering) to populate `PaperColorInfo` (`tone_name`, `hex_approx`, `lab_l`, `lab_a`, `lab_b`, `tone_enum`).
   - Ruling detected via projection-profile edge variance and periodic peak analysis across rows, setting `has_ruling`, `ruling_type`, and `line_spacing_px`.
   - Ink color and type classified from `palette["ink_centroids"]` in CIELAB space when ink strokes exist; otherwise set to `InkColor.UNKNOWN` and `InkType.UNKNOWN`.
   - In offline mode, `role = CardRole.UNKNOWN`, `role_confidence = 0.0`, `boundary_text = BoundaryText()` (empty strings, False flags), `artifacts = []` (never fabricated from keywords), `degraded = True`, and `degraded_reason = "Offline pixel-only analysis (no API key configured)"`.

4. **Live Gemini Failure Handling (INV-AC4)**:
   - On multimodal Gemini API call failure, logs a warning and returns the pixel-only fingerprint with `degraded = True` and `degraded_reason = f"Gemini API fingerprint call failed: {exc}"`.

### TASK C5 (`recipeboxer/vision/heuristics.py`):
1. **Boundary-Text-Only Sequential Header Scoring (INV-AC2)**:
   - Refactored `calculate_sequential_header_score(fp1, fp2)` to evaluate ONLY OCR/LLM boundary text (`first_line`, `last_line`, `has_continuation_symbol`, `is_cut_off_mid_sentence`, and printed markers).
   - Removed `fn1 = fp1.card_id.lower()` and `fn2 = fp2.card_id.lower()` regexes matching `card\d`, `part\d`, `pt\d` against filenames.
   - Removed the front/back filename substring rule (`"front" in fn1 and "back" in fn2`) and the legacy apple_crisp comment.
   - Retained [0.0, 1.0] score range with 0.50 as neutral baseline.
   - Verified that card ID or filename changes never affect the header score.

---

## 2. Test Verification

### Targeted Test Suite (`tests/test_wp_c45_fingerprint_heuristics.py` & `tests/test_heuristics.py`)

Authored a new, dedicated test suite covering all WP-C4 and WP-C5 requirements:
- `test_fingerprint_rename_invariance`: Generated card saved as `rec_vhc_x_card1_front.jpg` vs `IMG_0001.jpg` produces identical visual fingerprints across all physical attributes.
- `test_header_score_continuation_vs_neutral_text`: Boundary text containing 'continued on back' scores higher than neutral text (0.75 vs 0.50).
- `test_card_id_changes_never_change_header_score`: Mutating card IDs from front/back to reversed or arbitrary opaque hashes yields identical header scores.
- `test_header_score_printed_markers`: Printed markers in boundary text ('Page 1' -> 'Page 2' scores 1.00; 'Page 2' -> 'Page 1' scores 0.10).
- `test_header_score_empty_boundary_text`: Default empty boundary text evaluates to 0.50 neutral score.
- `test_offline_extractor_sets_degraded_status`: Offline extraction sets `degraded = True`, `role = CardRole.UNKNOWN`, and descriptive `degraded_reason`.
- `test_live_gemini_failure_fallback_sets_degraded`: Simulated API failure logs warning and falls back to degraded pixel-only fingerprint.
- `test_ruling_detection_projection_profile`: Correctly identifies ruled cards vs blank cards using edge projection profiles.
- `test_ink_color_clustering`: Correctly identifies blue, black, or unknown ink from pixel clustering.

**Execution:**
```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_c45_fingerprint_heuristics.py tests/test_heuristics.py -v
```

**Results:**
```
tests/test_wp_c45_fingerprint_heuristics.py::test_fingerprint_rename_invariance PASSED [  2%]
tests/test_wp_c45_fingerprint_heuristics.py::test_header_score_continuation_vs_neutral_text PASSED [  5%]
tests/test_wp_c45_fingerprint_heuristics.py::test_card_id_changes_never_change_header_score PASSED [  7%]
tests/test_wp_c45_fingerprint_heuristics.py::test_header_score_printed_markers PASSED [ 10%]
tests/test_wp_c45_fingerprint_heuristics.py::test_header_score_empty_boundary_text PASSED [ 12%]
tests/test_wp_c45_fingerprint_heuristics.py::test_offline_extractor_sets_degraded_status PASSED [ 15%]
tests/test_wp_c45_fingerprint_heuristics.py::test_live_gemini_failure_fallback_sets_degraded PASSED [ 17%]
tests/test_wp_c45_fingerprint_heuristics.py::test_ruling_detection_projection_profile PASSED [ 20%]
tests/test_wp_c45_fingerprint_heuristics.py::test_ink_color_clustering PASSED [ 22%]
tests/test_heuristics.py::* (31 unit tests) PASSED [100%]

============================== 40 passed in 0.31s ==============================
```

### Static Anti-Cheat Invariants Verification

Executed `tests/anticheat/test_static_invariants.py` with `--runxfail`:
```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat/test_static_invariants.py -v --runxfail
```

**Results:**
```
tests/anticheat/test_static_invariants.py::test_no_answer_key_paths PASSED [ 25%]
tests/anticheat/test_static_invariants.py::test_no_filename_semantics PASSED [ 50%]
tests/anticheat/test_static_invariants.py::test_no_fixture_content PASSED [ 75%]
tests/anticheat/test_static_invariants.py::test_no_cardforge_or_evals_import PASSED [100%]

============================== 4 passed in 1.32s ===============================
```
**ZERO offending hits** remain in owned files (`recipeboxer/vision/fingerprint.py`, `recipeboxer/vision/heuristics.py`, `recipeboxer/models/domain.py`, `tests/test_wp_c45_fingerprint_heuristics.py`).

---

## 3. Observed Test Failures in External Suites

As documented in `evals/triage.yaml`, the following legacy tests expecting filename cheats fail as predicted:
1. `tests/test_correlation.py::TestGeminiVisualExtractor::test_extractor_offline_pixel_analysis`: Fails asserting `fp.role == CardRole.SEQUENCE_START` on `01_cinnamon_front.jpg`. Offline role is now legitimately `UNKNOWN`. (Triage action: rewrite with mock, WP-C8, `evals/triage.yaml:225`).
2. `tests/test_challenger_r1_r2.py::TestChallengerHeuristicsRobustness::test_calculate_sequential_header_score_exhaustive_naming_conventions`: Fails asserting `recipe_card1_front.jpg -> recipe_card1_back.jpg` returns 1.00 from filename alone. (Triage action: delete, WP-C8, `evals/triage.yaml:197`).

---

## 4. Requests for Other WPs

- **WP-C8 (Test Triage & Rewrites)**:
  - Rewrite `tests/test_correlation.py::TestGeminiVisualExtractor::test_extractor_offline_pixel_analysis` to test pixel analysis behavior with mock client or assert `UNKNOWN` offline role.
  - Delete `tests/test_challenger_r1_r2.py::TestChallengerHeuristicsRobustness::test_calculate_sequential_header_score_exhaustive_naming_conventions`.

---

## 5. Owned Files Manifest

- `recipeboxer/vision/fingerprint.py` (Remediated: pixel-only analysis, degraded status, zero filename cheats)
- `recipeboxer/vision/heuristics.py` (Remediated: text-only sequential header scoring, zero filename regexes)
- `recipeboxer/models/domain.py` (Added `degraded` and `degraded_reason` fields to `VisualFingerprint`)
- `tests/test_wp_c45_fingerprint_heuristics.py` (New targeted non-cheating test suite, 9 passed)
- `_agents/remediation/WP-C4-C5.md` (Work package completion report)
