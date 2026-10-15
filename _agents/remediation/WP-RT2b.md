# Work Package Completion Note: WP-RT2b (Red-Team #2 Findings Remediation)

**Remediation Date:** 2026-10-15  
**Target Work Package:** `WP-RT2b`  
**Addressed Findings:**
- FIND-01 (mock sniffing removal across `extractor.py`, `semantic.py`, `orchestrator.py`, legacy test migration, and static anti-cheat checks)
- FIND-04 (calibrated confidence priors & magic number removal in `optimizer.py`)
- FIND-05 (silent exception and placeholder byte fallbacks in `engine.py` and `consistency.py`)
- FIND-06 (physical pairing CV performance bottleneck & bounded LRU caching in `physical_pairing.py`)
- FIND-07 (SpendMeter null/unknown spend handling in frontend)
- FIND-08 (prompt deny-list cleanup & inferred title `[GUESS]` instruction)
- FIND-09 (SetVerifier quote validation, word-bag rejection, and full vs partial card text handling)

**Specification References:** `_agents/remediation/REDTEAM-2.md`, `_agents/remediation/WAVE3_CONTRACTS.md` §0, `_agents/remediation/WAVE5_CONTRACTS.md`, `_agents/INVARIANTS.md` §10 (INV-AC1..INV-AC6)

---

## 1. Summary of Changes

### FIND-01: Mock Sniffing Removal & Single LLMClient Delegation
- **Files Modified:** `recipeboxer/extraction/extractor.py`, `recipeboxer/correlation/semantic.py`, `recipeboxer/agent/orchestrator.py`, `tests/test_recipe_extractor.py`, `tests/test_wp_c1_extractor.py`, `tests/test_correlation.py`.
- **New Test:** `tests/test_wp_rt2b_static.py`.
- **Problem:** Production code in `extractor.py`, `semantic.py`, and `orchestrator.py` detected test doubles via `type(...).__name__.endswith("Mock")`, `hasattr(..., "mock")`, or named adapters (`MockVerifierAdapter`), altering execution paths. In `semantic.py:542`, an injected client conforming to `LLMClient` was rejected if its class name ended with `"Mock"`.
- **Remediation:**
  - Removed all `endswith("Mock")` and `hasattr(..., "mock")` checks in `extractor.py` and `semantic.py`.
  - Replaced legacy duck typing with clean, typed `isinstance(client, LLMClient)` delegation.
  - Renamed `MockVerifierAdapter` to `_SessionVerifierAdapter` in `orchestrator.py` and streamlined adapter binding.
  - Migrated legacy unit tests relying on duck-typed MagicMock instances to `ScriptedLLMClient` and valid mock JPEG data (`TINY_JPEG`).
  - Added AST and source anti-cheat static check in `tests/test_wp_rt2b_static.py` enforcing zero test-double sniffing or framework imports in production.

### FIND-04: Calibrated Confidence Priors & Magic Number Elimination
- **Files Modified:** `recipeboxer/correlation/optimizer.py`.
- **New Test:** `tests/test_wp_rt2b_priors.py`.
- **Problem:** Hardcoded uncalibrated numbers (`0.85`, `0.90`) were used as confidence scores for unverified single-card proposals, masquerading as evidence when no model verification occurred.
- **Remediation:**
  - Replaced magic literals with named calibrated constants (`PRIOR_IS_COMPLETE = 0.90`, `PRIOR_CONTINUATION_LIKELY = 0.70`, `PRIOR_PAIR_CONFIDENCE_DEFAULT = 0.75`).
  - Updated `PairingOptimizer.optimize_proposals` to set `confidence = None` on unverified standalone single-card proposals rather than fabricating high confidence.
  - Calculated multi-card group confidence using bottleneck continuation link confidence `min(t_scores)`.
  - Safely handled `prop.confidence is None` when evaluating against `min_pair_confidence`.

### FIND-05: Elimination of Silent Exceptions and Placeholder Fallbacks
- **Files Modified:** `recipeboxer/correlation/engine.py`, `recipeboxer/extraction/consistency.py`.
- **New Test:** `tests/test_wp_rt2b_silent_errors.py`.
- **Problem:** `TempDirImageProvider` silently returned empty bytes `b""` on I/O failure, masquerading as valid image data. `self_consistency` in `consistency.py` swallowed LLM exceptions and returned empty warnings, silently concealing secondary pass failures.
- **Remediation:**
  - Updated `FileImageProvider` and `TempDirImageProvider` to return `None` (never `b""`) when an image is unreadable or missing.
  - Defined `ImageNotFoundError(FileNotFoundError, KeyError)` with explicit string formatting.
  - In `consistency.py`, caught secondary read failures and emitted `RecipeWarning(code="VERIFICATION_SKIPPED", message=...)`, ensuring failures are visible to the HITL interface.
  - Ensured `BudgetExceeded` propagates immediately without being swallowed.

### FIND-06: Physical Pairing Performance Optimization via Bounded LRU Caching
- **Files Modified:** `recipeboxer/vision/physical_pairing.py`.
- **New Test:** `tests/test_wp_rt2b_perf.py`.
- **Problem:** cProfile revealed that 75.2% of grouping runtime (~34.8s out of 46.3s for N=20 cards) was consumed by `mirrored_wear_score`, recomputing 9x9 median filters (`_extract_wear_field`) $O(N^2)$ times for the same card during background fitting and screening.
- **Remediation:**
  - Implemented bounded thread-safe `_BoundedLRU` caches for:
    - Downscaled wear field (160x240 float32 arrays).
    - Reverse-face show-through card features (`r_norm`, `f_norm_h`, `f_norm_v`).
    - Silhouette contours.
  - Designed robust cache keys supporting image bytes (SHA-256), file paths (path + mtime), and memoized PIL object keys.
  - Added `clear_physical_pairing_cache()` for hermetic test execution.
  - Verified numerical identity ($< 10^{-6}$) between cached and uncached calculations.
  - Measured grouping execution latency:
    - `evals/stress.py --cards 20`: Reduced from ~43s down to **0.784s** (> 50x speedup).
    - `evals/stress.py --cards 80`: Ran in **1.670s** (well below the 25s threshold).

### FIND-07: SpendMeter Null & Undefined Telemetry Handling
- **Files Modified:** `frontend/src/components/layout/SpendMeter.tsx`, `frontend/src/components/layout/StatusBar.tsx`, `frontend/tests/wp_f2_settings_spend.test.tsx`.
- **Problem:** When `spendUsd` was null or undefined, `SpendMeter` either displayed `$0.0000` (masking inactive ledgers) or risked crashing on `null.toFixed(4)`.
- **Remediation:**
  - Updated `SpendMeter` props to `spendUsd?: number | null = null`.
  - Added guard to render `'unknown'` whenever `spendUsd` is null or undefined.
  - Set `spendUsd = null` default in `StatusBar`.
  - Added frontend test cases confirming null and undefined render `'unknown'`. Verified with Vitest (389 tests passed) and TypeScript compiler (0 errors).

### FIND-08: Prompt Deny-List Cleanup & Inferred Title Suffix
- **Files Modified:** `recipeboxer/prompts/structure_v1.md`, `recipeboxer/prompts/transcribe_handwritten_v1.md`, `recipeboxer/extraction/extractor.py`.
- **New Test:** `tests/test_wp_rt2b_prompts.py`.
- **Problem:** "Aunt Clara" and "Peach Cobbler" appeared in prompt examples; extractor instructions had conflicting guidance on appending `' [GUESS]'`.
- **Remediation:**
  - Sanitized prompt templates and system instructions in `prompts/*.md` and `extractor.py`.
  - Replaced test recipe titles with neutral culinary phrases.
  - Enforced instruction that inferred titles must set `title_is_inferred = True` without appending `' [GUESS]'`.

### FIND-09: SetVerifier Quote Validation & Phrase Matching
- **Files Modified:** `recipeboxer/correlation/semantic.py`, `recipeboxer/models/domain.py`.
- **New Test:** `tests/test_wp_rt2b_prompts.py`.
- **Problem:** `SetVerifier` quote validation previously used a word-bag check (`all(w in clean_corpus for w in words)`), falsely accepting quotes made of words scattered across disconnected lines. Furthermore, when only boundary text was available, quotes from the middle of the card were flagged as `UNVERIFIED_QUOTE` and penalized by 50% confidence.
- **Remediation:**
  - Added additive `ocr_lines` and `body_text` fields to `VisualFingerprint` in `recipeboxer/models/domain.py`.
  - Replaced word-bag logic with `_is_phrase_match`:
    - Checks exact contiguous substring in normalized text.
    - Checks whitespace-stripped contiguous substring (handles line breaks and hyphenation).
    - Checks contiguous sliding window with CER $\le 0.20$ (handles minor OCR character typos).
  - Categorized unmatched quotes when full text is absent as `UNCHECKED_QUOTE` (neutral, no confidence penalty).
  - Reserved `UNVERIFIED_QUOTE` (50% confidence penalty) strictly for true hallucinations when full card text/OCR is available.

---

## 2. Test Verification Summary

- **New Dedicated Test Suites:**
  - `tests/test_wp_rt2b_perf.py`: 3 passed.
  - `tests/test_wp_rt2b_priors.py`: 5 passed.
  - `tests/test_wp_rt2b_silent_errors.py`: 4 passed.
  - `tests/test_wp_rt2b_prompts.py`: 5 passed.
  - `tests/test_wp_rt2b_static.py`: 4 passed.
  - **Total RT2b Tests:** 21 passed in 2.18s.

- **Migrated Legacy & Anti-Cheat Suites:**
  - `tests/anticheat/test_canaries.py` & `tests/anticheat/test_static_invariants.py`: 10 passed.
  - `tests/test_recipe_extractor.py`: 24 passed.
  - `tests/test_wp_c1_extractor.py`: 17 passed.
  - `tests/test_correlation.py`: 14 passed.
  - **Total:** 65 passed in 37.83s.

- **In-Scope Post-Wave-5 Suite Remediations (45/45 resolved):**
  - `tests/test_wp_gc_semantic.py`: 2 quote validation hallucination tests fixed with explicit `ocr_lines`.
  - `tests/test_wp_gd_engine.py`: Production regression fixed in `engine.py:470` to propagate `min(s_scores)`.
  - `tests/test_wp_rt1_redteam.py`: Production regression fixed in `optimizer.py` for confidence propagation & no-0.85 fallback.
  - `tests/test_challenger_1_full_empirical.py`: Sequence assembly confidence calculation aligned with transition score bottlenecking.
  - `tests/test_challenger_m3_extraction.py`: 4 tests updated to use `ScriptedLLMClient` and honest `title_is_inferred` schema contract.
  - `tests/test_challenger_m2.py`: 6 mock client tests updated to use `ScriptedLLMClient` with `SetVerificationResponse`.
  - `tests/test_challenger_m2_empirical_verification.py`: 2 content isolation tests updated to use `ScriptedLLMClient`.
  - `tests/test_challenger_m3_m4.py`: 3 extraction tests updated to use `ScriptedLLMClient` and valid image data.
  - `tests/test_challenger_2_sequence_exporters.py`: 2 multi-card and HITL reordering tests updated to use `ScriptedLLMClient`.
  - `tests/test_empirical_challenger_m2_multicard.py`: 3 sequence tests updated to use `ScriptedLLMClient` and valid images.
  - `tests/test_challenger_2_r3_r4_stress.py`: 9 multi-part assembly & PIL image tests updated to use `ScriptedLLMClient` and valid images.
  - `tests/test_challenger_r1_r2.py`: `MockSequenceVerifierClient` & `MockPairVerifierClient` rewritten to inherit from `LLMClient` generating `SetVerificationResponse`.
  - Recorded all test rewrites in `evals/triage.yaml` under `owner_wp: WP-RT2b`.

- **Full Suite Run:**
  - Ran `$HOME/.cache/projects/recipeboxer-ba754a91/venv/bin/python -m pytest`:
  - **Results:** **2,264 passed**, **9 skipped**, **13 failed** (only the 13 known out-of-scope failures: 6 Windows path escaping in `test_archive_empirical_challenger.py` and 7 tkinter GUI dialog in `test_browse_directory.py`).
  - **Total in-scope failures remaining:** **0**.

- **Frontend Suite:**
  - `frontend_check.sh test`: Vitest 32/32 test files passed (389 tests passed), `tsc` exited 0 with 0 errors.

---

## 3. Coordination Requests for WP-RT2a
WP-RT2a and WP-RT2b changes are completely disjoint and cleanly decoupled:
- WP-RT2a owned: `fingerprint.py`, `headless.py`, `evals/`, session persistence.
- WP-RT2b owned: `extractor.py`, `semantic.py`, `physical_pairing.py`, `optimizer.py`, `engine.py`, `consistency.py`, `prompts/`, `SpendMeter.tsx`.
- All shared interfaces (`VisualFingerprint` additive fields, `ScriptedLLMClient` fixtures, `ImageProvider`, `LLMClient`) verified compatible.
