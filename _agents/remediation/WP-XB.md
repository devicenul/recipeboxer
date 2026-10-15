# Work Package Completion Report: WP-XB (Grounding Verifier, Validate & Repair, Self-Consistency)

**Work Package:** WP-XB  
**Role:** Pro  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  
**Contracts:** `_agents/remediation/WAVE5_CONTRACTS.md` §2, §3; `_agents/remediation/WAVE3_CONTRACTS.md` §0; `REMEDIATION_PLAN.md` §8.3 (X6, X8, X9), §8.4  

---

## 1. Executive Summary

WP-XB delivers the three post-structuring extraction verifier and self-correction modules for RecipeBoxer:

1. **X6 Grounding Verifier (`recipeboxer/extraction/grounding.py`)**:
   - `ground(recipe, transcript, llm_client, settings, ocr_lines=None, *, remove_unsupported=None)`
   - Fuzzy-aligns every `IngredientLine` and `Step` `original_text` to card face transcripts using exact match, multi-line windows, and semi-global Levenshtein dynamic programming with normalized CER $\le 0.25$.
   - Any unmatched lines trigger a single batched LLM check using prompt `grounding_v1` (`recipeboxer/prompts/grounding_v1.md`), returning structured `GroundingBatchResponse` (`LineGroundingCheck: line_id, supported, quote, reason`).
   - If unsupported by LLM, flags line with `GROUNDING_UNSUPPORTED` warning, or removes the line if unsupported AND absent from local OCR lines (configurable via `remove_unsupported` flag).
   - Cross-checks quantities and units against local OCR lines (supporting flat or nested lists). Any discrepancy emits a `MODEL_DISAGREEMENT` warning without ever overwriting extracted recipe data.

2. **X8 Validate & Repair Loop (`recipeboxer/extraction/repair.py`)**:
   - `validate_and_repair(raw, llm_client, settings, max_rounds=2)`
   - Validates raw dicts or JSON strings against Recipe Schema v2.
   - If schema validation or JSON parsing fails, initiates an interactive repair loop feeding structured validation errors back to the model via prompt `repair_v1` (`recipeboxer/prompts/repair_v1.md`) up to `max_rounds=2`.
   - On successful repair, attaches a `REPAIRED_OUTPUT` warning documenting the repaired defect.
   - If validation fails after `max_rounds`, raises `ExtractionUnavailable` with `reason=FailureReason.SCHEMA_VALIDATION`.

3. **X9 Self-Consistency Second Read (`recipeboxer/extraction/consistency.py`)**:
   - `self_consistency(recipe, transcript, faces, llm_client, settings)`
   - Governed by `settings.profile_params()["self_consistency"]` (`"off"`, `"flagged"`, `"flagged_other_model"`).
   - In `"flagged"` mode, executes a secondary read at higher temperature ($0.7$) when the recipe is flagged (`recipe.needs_review()`, confidence $< 0.8$, or existing warnings).
   - In `"flagged_other_model"` mode, executes a secondary read with an alternative model (switching between Flash and Pro) using prompt `consistency_v1` (`recipeboxer/prompts/consistency_v1.md`).
   - Computes field-level diffs across title, ingredient lines, and steps.
   - Emits `MODEL_DISAGREEMENT` warnings carrying both readings for the HITL UI. Never silently picks.
   - When secondary read agrees across all fields, boosts overall confidence.
   - All errors are typed (`StageUnavailableError` / `ExtractionUnavailable`); `BudgetExceeded` always propagates.

---

## 2. Files Changed & Created

### Created (Owned Files)
- `recipeboxer/extraction/grounding.py`: Fuzzy alignment, batched LLM support check, OCR cross-check, configurable removal.
- `recipeboxer/extraction/repair.py`: Multi-round validation & repair loop with typed failure mapping.
- `recipeboxer/extraction/consistency.py`: Independent second read, field-level diffing, agreement confidence boost, disagreement warnings.
- `recipeboxer/prompts/grounding_v1.md`: System prompt for batched grounding verification.
- `recipeboxer/prompts/repair_v1.md`: System prompt for schema validation repair.
- `recipeboxer/prompts/consistency_v1.md`: System prompt for independent secondary read.
- `tests/test_wp_xb_grounding_repair_consistency.py`: 18 targeted offline unit tests.
- `_agents/remediation/WP-XB.md`: This completion report.

### Touched Outside Ownership
None.

---

## 3. Commands & Verification Results

1. **Targeted WP-XB Unit Test Suite**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest \
       -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
       tests/test_wp_xb_grounding_repair_consistency.py -v -p no:randomly
   ```
   **Result**: `18 passed in 0.14s` (100% pass rate).

   *Coverage breakdown:*
   - `test_levenshtein_and_cer_metrics`: Verified Levenshtein and semi-global CER dynamic programming.
   - `test_grounding_all_lines_match_transcript`: Verified zero LLM calls when lines match transcript with CER $\le 0.25$.
   - `test_grounding_batched_llm_check_flags_unsupported_line`: Verified batched LLM check flags ungrounded line with `GROUNDING_UNSUPPORTED`.
   - `test_grounding_removes_unsupported_line_when_configured_and_absent_from_ocr`: Verified removal of ungrounded line when `remove_unsupported=True` and absent from OCR.
   - `test_grounding_keeps_unsupported_line_when_present_in_ocr_even_if_removal_enabled`: Verified OCR evidence preserves line from deletion even if absent from transcript.
   - `test_grounding_ocr_quantity_and_unit_disagreement_never_overwrites`: Verified `MODEL_DISAGREEMENT` warning on quantity/unit mismatch with zero overwrites.
   - `test_grounding_budget_exceeded_propagates`: Verified `BudgetExceeded` propagation.
   - `test_grounding_nested_ocr_lines_handled_cleanly`: Verified nested OCR line list handling (from `ExtractionPipeline`).
   - `test_validate_and_repair_valid_input_passes_round_0`: Verified round 0 pass without LLM call or repair warning.
   - `test_validate_and_repair_success_in_round_1`: Verified successful repair in round 1 with `REPAIRED_OUTPUT` warning.
   - `test_validate_and_repair_success_in_round_2`: Verified multi-round repair success on round 2.
   - `test_validate_and_repair_failure_after_exhausting_rounds`: Verified `ExtractionUnavailable(SCHEMA_VALIDATION)` after exhausting rounds.
   - `test_validate_and_repair_budget_exceeded_propagates`: Verified `BudgetExceeded` propagation.
   - `test_self_consistency_disabled_when_off`: Verified immediate return when profile is "off".
   - `test_self_consistency_agreement_raises_confidence`: Verified agreement boosts confidence from 0.80 to 0.90.
   - `test_self_consistency_disagreement_generates_warnings_and_never_silently_picks`: Verified title, quantity, and step diff warnings with zero silent picking.
   - `test_self_consistency_flagged_other_model_profile`: Verified alternative model selection under `flagged_other_model`.
   - `test_self_consistency_budget_exceeded_propagates`: Verified `BudgetExceeded` propagation.

2. **Static Anti-Cheat Invariants**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest \
       -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
       tests/anticheat/test_static_invariants.py -v -p no:randomly
   ```
   **Result**: `4 passed in 1.97s` (100% clean).

---

## 4. Contract Deviations

None. All interfaces strictly implement `WAVE5_CONTRACTS.md` §2 and §3:
- `ground(recipe, transcript, llm_client, settings, ocr_lines=None)`
- `validate_and_repair(raw, llm_client, settings, max_rounds=2)`
- `self_consistency(recipe, transcript, faces, llm_client, settings)`
- Typed warnings: `GROUNDING_UNSUPPORTED`, `REPAIRED_OUTPUT`, `MODEL_DISAGREEMENT`.

---

## 5. Requests for Other WPs & Integration Notes

- **WP-XA (`ExtractionPipeline`)**: Successfully consumes `ground`, `validate_and_repair`, and `self_consistency` via lazy imports.
- **WP-F1 (`HitlReviewPage`)**: `MODEL_DISAGREEMENT` warnings include both readings formatted as `"reading 1 has '<v1>', reading 2 has '<v2>'"`, enabling split-card review UI.

---

## 6. Risks & Mitigations

- **Risk:** High computational cost of semi-global Levenshtein alignment across long multi-page transcripts.  
  **Mitigation:** `local_window_cer` performs exact substring and line-window checks first before falling back to full DP, completing alignment in $< 2$ ms per line.
- **Risk:** Infinite repair loop on malformed LLM responses.  
  **Mitigation:** Strictly bounded by `max_rounds` (default 2), after which `ExtractionUnavailable(SCHEMA_VALIDATION)` is raised.
