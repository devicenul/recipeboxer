# Work Package Completion Report: WP-C2 (Semantic Continuity Verifier Remediation)

**Package Owner:** WP-C2 (flash)  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  

---

## 1. Summary of Remediation

In accordance with `_agents/remediation/REMEDIATION_PLAN.md` (§2, §4.1, §9.1, §10), `_agents/INVARIANTS.md` (§10, INV-AC1..INV-AC6), and `_agents/TESTING_GUIDELINES.md` (§7), WP-C2 eliminated all answer-key lookups, cluster manifest caches, and prompt identifier leaks from Stage 2 semantic continuity verification, replacing them with honest degraded behavior and boundary text cues.

### Key Changes:

1. **Answer Key & Cluster Cache Elimination (INV-AC1, INV-AC3)**:
   - Deleted `_MANIFEST_RECIPE_CLUSTER_CACHE` and `_get_manifest_recipe_cluster_lookup()` from `recipeboxer/correlation/semantic.py`.
   - Removed the SHA cluster manifest lookup branch from `_evaluate_offline_continuity()`. The module now has zero references to `manifest.json`, `tests/fixtures`, or `sample_cards`.

2. **Schema Enhancement for Degradation Tracking (INV-AC4)**:
   - In `recipeboxer/models/correlation.py` (`CorrelationProposal`), added:
     - `semantic_verified: bool = Field(default=True, ...)`
     - `degraded_reason: Optional[str] = Field(default=None, ...)`
     - `semantic_score: Optional[float] = Field(default=0.0, ge=0.0, le=1.0, ...)`
   - All defaults remain completely backward-compatible with existing consumers and constructors.

3. **Honest Offline and Failure Behavior (INV-AC4, INV-AC6)**:
   - When no model is available (`api_key=None`, client unconfigured) or when the live API call fails:
     - Produces a proposal explicitly marked with `semantic_verified=False` and `degraded_reason="NO_MODEL"` or `degraded_reason="API_ERROR: <ErrorType>"`.
     - If real boundary text cues are present (`has_continuation_symbol`, `is_cut_off_mid_sentence`, or continuation headers in boundary OCR): evaluates honest boundary cues yielding higher scores (0.85/0.70), but caps confidence (max 0.80 for strong, 0.65 for moderate) and sets `recommended_action="UNCERTAIN"` unless evidence is strong.
     - If no boundary text cues are present: `semantic_score=0.5` (neutral float preventing downstream consumer crashes in `optimizer.py`), `confidence = heuristic_score` (Stage 1 heuristic only), `recommended_action="UNCERTAIN"`.
     - Never emits the fabricated 0.95 "Strong semantic continuity" explanation without genuine boundary evidence.

4. **Live API Error Resilience**:
   - `verify_pair()` catches exceptions from live Gemini API calls, logs a warning, and returns a degraded proposal with `degraded_reason=f"API_ERROR: {type(exc).__name__}"` so processing continues smoothly to HITL review.

5. **Prompt Identifier Leak Elimination (INV-AC2)**:
   - Removed the `"card_"` substring check in `_call_gemini_semantic_api()` that previously allowed original filenames to pass through. The Gemini multimodal call now strictly passes opaque labels `"Front Card: Card A\nBack Card: Card B"`.

---

## 2. Test Verification

### Targeted Test Suite (`tests/test_wp_c2_semantic.py`)

Authored a new, non-cheating test suite with opaque card IDs and explicit injected mocks:

```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_c2_semantic.py -v
```

**Results:**
```
tests/test_wp_c2_semantic.py::test_no_key_degraded_proposal PASSED       [ 16%]
tests/test_wp_c2_semantic.py::test_boundary_cue_fingerprints_higher_but_capped_scores PASSED [ 33%]
tests/test_wp_c2_semantic.py::test_live_api_exception_handled_as_degraded_with_reason PASSED [ 50%]
tests/test_wp_c2_semantic.py::test_opaque_labels_sent_to_gemini PASSED   [ 66%]
tests/test_wp_c2_semantic.py::test_no_manifest_reference_in_module PASSED [ 83%]
tests/test_wp_c2_semantic.py::test_correlation_proposal_defaults_and_backward_compatibility PASSED [100%]

============================== 6 passed in 0.13s ===============================
```

### Static Invariants Anti-Cheat Suite

Ran `tests/anticheat/test_static_invariants.py` with `--runxfail`:
- **Owned files (`recipeboxer/correlation/semantic.py`, `recipeboxer/models/correlation.py`, `tests/test_wp_c2_semantic.py`)**: **0 offending hits** for INV-AC1 (no answer keys/manifest references), INV-AC2 (no filename semantics), INV-AC3 (no fixture content/hashes), and INV-AC5 (no harness leaks).
- Overall suite: `4 passed in 1.29s`.

---

## 3. Observed Test Failures in External Suites (Cataloged in `evals/triage.yaml`)

As expected from removing the cluster cache and fake 0.95 offline shortcuts, legacy tests expecting fabricated offline high confidence fail and will be rewritten in WP-C8:

1. **`tests/test_correlation.py`**:
   - `TestGeminiSemanticVerifier::test_semantic_verifier_custom_weights` (triage line 638: asserts 0.875 unmocked confidence).
   - `TestGeminiSemanticVerifier::test_semantic_verifier_offline_continuity_cues` (triage line 645: asserts semantic_score >= 0.90).
   - `TestGeminiSemanticVerifier::test_semantic_verifier_mismatched_pair_rejected` (triage line 652: asserts semantic_score <= 0.15).

2. **`tests/test_challenger_m2.py`**:
   - `TestSemanticVerifierMockAndFallback::test_offline_fallback_strong_continuity_cues` (triage line 484: asserts 0.95 offline score).
   - `TestSemanticVerifierMockAndFallback::test_offline_fallback_completely_unrelated_cards` (triage line 491: asserts 0.10 offline score).
   - `TestSemanticVerifierMockAndFallback::test_offline_fallback_moderate_heuristic_uncertain_action` (triage line 498: asserts 0.65 offline score).

All 6 failures were already inventoried in `evals/triage.yaml` for WP-C8. Zero uncataloged regressions were introduced.

---

## 4. Requests for Other WPs

- **WP-G7 / WP-C8**: In `recipeboxer/correlation/optimizer.py:180`, `avg_s = sum(s_scores) / len(s_scores)` assumes non-None floats. When WP-G7 refactors optimizer path weighting, consider `[s for s in s_scores if s is not None]` if `semantic_score=None` is adopted.
- **WP-C8**: Rewrite the 6 legacy tests in `test_correlation.py` and `test_challenger_m2.py` to use injected mocks or order-based stubs.

---

## 5. Owned Files Manifest

- `recipeboxer/correlation/semantic.py` (Remediated)
- `recipeboxer/models/correlation.py` (Enhanced)
- `tests/test_wp_c2_semantic.py` (New targeted test suite)
- `_agents/remediation/WP-C2.md` (Remediation report)
