# Work Package Completion Note: WP-RT1 (Red-Team Challenger Findings Remediation)

**Remediation Date:** 2026-10-14  
**Target Work Package:** `WP-RT1`  
**Addressed Findings:** FIND-03, FIND-04, FIND-05, FIND-06, FIND-07, FIND-08, FIND-09  
**Specification References:** `_agents/remediation/REDTEAM-1.md`, `_agents/remediation/WAVE3_CONTRACTS.md` §0, `_agents/INVARIANTS.md` §10 (INV-AC1..INV-AC6)

---

## 1. Summary of Changes

All 7 assigned findings from the independent Red-Team Challenger report (`REDTEAM-1.md`) have been completely resolved, verified against anti-cheat invariants, and covered by automated tests.

### FIND-03: Production Prompt Construction & Canary 4 Blind Spot (Major)
- **Problem:** `HeadlessClientAdapter` bypassed production extractors (`GeminiVisualExtractor`, `RecipeExtractor`, `GeminiSemanticVerifier`) and built requests containing only `ImagePart`s. Canary 4 (`test_canary_prompt_leak_scan`) tested empty string prompts and vacuously passed without evaluating real prompt templates.
- **Remediation:**
  - Added native `llm_client: Optional[LLMClient] = None` support to `GeminiVisualExtractor` (`recipeboxer/vision/fingerprint.py`), `GeminiSemanticVerifier` (`recipeboxer/correlation/semantic.py`), and `RecipeExtractor` (`recipeboxer/extraction/extractor.py`).
  - Implemented production prompt builders (`_call_llm_client_fingerprint`, `_call_llm_client_semantic`, `_call_llm_client_extraction`) passing official system instructions, structured JSON schemas, opaque card tokens, and non-empty `TextPart`s.
  - In `recipeboxer/headless.py`, wired `run_headless_analysis` to instantiate the production visual extractor, semantic verifier, and recipe extractor with `_LedgerRecordingClient`, populating `UsageLedger` while ensuring production prompt generation.
  - Correctly wired `PipelineSession` components (`session.extractor = vis_extractor`, `session.recipe_extractor_fn = rec_extractor.extract`).
  - Strengthened Canary 4 in `tests/anticheat/test_canaries.py`: asserts 4 requests across all pipeline stages (`FINGERPRINT`, `SET_VERIFY`, `STRUCTURE`), verifies non-empty `system_instruction` and `TextPart`s, and checks that no original filenames or category codes leak.
  - Verified Canary 4 prompt-leak detection via mutation testing in `$PROJECT_CACHE_DIR` and unit tests in `tests/test_wp_rt1_redteam.py`.

### FIND-04: Optimizer `semantic_score=None` Handling & Removal of 0.85 Fallback (Major)
- **Problem:** In `recipeboxer/correlation/optimizer.py`, `getattr(prop, "semantic_score", prop.confidence)` returned `None` when `semantic_score` was explicitly `None`, causing `sum(s_scores)` to crash with `TypeError`. Additionally, empty transition lists fell back to a hardcoded `0.85` confidence, violating INV-AC4.
- **Remediation:**
  - In `optimizer.py`, updated score extraction to safely check `prop.semantic_score is not None`.
  - When semantic evidence is missing, transitions use heuristic evidence only, and the sequence proposal is flagged as `recommended_action="UNCERTAIN"`, `semantic_verified=False`, and `degraded_reason="MISSING_SEMANTIC_EVIDENCE"`.
  - Removed all hardcoded `0.85` fallbacks. Standalones derive confidence directly from `fp.role_confidence` or `0.0`.
  - Handled multi-part candidate proposals (N >= 3) with missing semantic scores similarly.

### FIND-05: Real Confidence Propagation & Evaluator Null Handling (Major)
- **Problem:** `recipeboxer/headless.py` emitted all predicted recipes with hardcoded `"confidence": 1.0`, blinding the downstream Expected Calibration Error (ECE) metric and violating INV-AC4.
- **Remediation:**
  - In `recipeboxer/headless.py`, recipe confidence in `recipes_out` is derived from `r.confidence` or `min(p.confidence for p in matching_proposals)`. If no confidence exists, emits `None` (`null` in JSON), never `1.0`.
  - In `evals/scoring/evaluator.py` (`evaluate_deck` and `score_evaluation`), null confidences are safely excluded when aggregating `confidences` and `correct_list` for ECE calculation, preventing calibration metric distortion.

### FIND-06: Comprehensive Runtime Audit Hook (Major)
- **Problem:** `evals/harness/sut_boot.py` only monitored Python `open` events. It did not intercept directory listings (`os.scandir`, `os.listdir`), subprocess execution, or package imports.
- **Remediation:**
  - Expanded `_install_anti_cheat_audit_hook` in `sut_boot.py` to intercept:
    - Path inspection: `open`, `os.listdir`, `os.scandir`, `os.walk`, `glob.glob`, `sqlite3.connect`
    - File copy: `shutil.copyfile` (checking both source and destination)
    - Process execution: `subprocess.Popen`, `os.system`, `os.posix_spawn`, `os.spawn`, `os.exec*`
    - Module imports: `import` events for `evals`, `cardforge`, and `tests`
  - Added path normalization resolving symlinks via `os.path.realpath`.
  - Violations immediately terminate with exit code `86` and write `CHEAT_DETECTED` to `sys.stderr`.
  - Cleaned `sut_dir` from `sys.path` in `sut_boot.py` so Python's importlib does not scan `evals/` during standard CLI startup.

### FIND-07: Single Shared Gemini Key Usability Helper (Minor)
- **Problem:** `headless.py` and `orchestrator.py` treated mock strings (e.g. `"mock"`, `"mock-key"`) as usable API keys, causing late pipeline errors instead of clean early blocked states.
- **Remediation:**
  - Added canonical helpers `is_usable_gemini_key(key)` and `has_usable_gemini_key(settings, extra_keys)` in `recipeboxer/config.py`. Requires key to be non-empty, non-whitespace, and not start with `"mock"` (case-insensitive).
  - Replaced ad-hoc checks in `recipeboxer/agent/orchestrator.py` and `recipeboxer/headless.py` with the shared helpers.

### FIND-08: Margin-Based Heuristic Modality Confidences in [0.3, 0.8] (Minor)
- **Problem:** `recipeboxer/vision/router.py` assigned static high confidences (`0.90`–`0.96`) in heuristic rules.
- **Remediation:**
  - In `recipeboxer/vision/router.py`, replaced static confidences with continuous margin-based confidence scores calculated from artifact count, ink confidence, column valleys ratio, edge variance margin, substrate Lab color margins, and ruling peaks.
  - Strictly clipped all heuristic confidences to `[0.3, 0.8]`.
  - Added `features["heuristic"] = True` to all heuristic classification results.
  - Updated assertions in `tests/test_wp_c3_router.py`.

### FIND-09: Fictitious Recipe in Documentation (Minor)
- **Problem:** `evals/INTAKE.md` user guide contained "Lemon Bars" recipe text, which was historically one of the canned recipes.
- **Remediation:**
  - Replaced "Lemon Bars" in `evals/INTAKE.md` with the fictitious, non-corpus "Starlight Berry Compote" recipe.

---

## 2. Files Changed

### Modified
- `recipeboxer/config.py`: Added `is_usable_gemini_key` and `has_usable_gemini_key`.
- `recipeboxer/vision/fingerprint.py`: Added `llm_client` support and `_call_llm_client_fingerprint`.
- `recipeboxer/correlation/semantic.py`: Added `llm_client` support and `_call_llm_client_semantic`.
- `recipeboxer/extraction/extractor.py`: Added `llm_client` support and `_call_llm_client_extraction`.
- `recipeboxer/correlation/optimizer.py`: Fixed `semantic_score=None` handling, eliminated hardcoded `0.85`, and added missing evidence degradation.
- `recipeboxer/headless.py`: Wired production extractors with `_LedgerRecordingClient`, session component references, and propagated real proposal confidence (or null).
- `recipeboxer/agent/orchestrator.py`: Used `has_usable_gemini_key` and `is_usable_gemini_key`.
- `recipeboxer/vision/router.py`: Replaced static confidences with margin-based continuous scores in `[0.3, 0.8]` with `heuristic=True`.
- `evals/harness/sut_boot.py`: Expanded audit hook to monitor imports, subprocesses, scandir/listdir, copyfile, sqlite3, and symlinks. Scrubbed `sys.path`.
- `evals/scoring/evaluator.py`: Gracefully handled null confidences in `evaluate_deck` and overall ECE aggregation.
- `evals/INTAKE.md`: Replaced "Lemon Bars" with "Starlight Berry Compote".
- `tests/anticheat/test_canaries.py`: Strengthened Canary 4 to assert real production prompts and updated Canary 1 & 2 scripted sequences.
- `tests/test_wp_c3_router.py`: Updated router confidence assertions for `[0.3, 0.8]` and `heuristic=True`.

### Created
- `tests/test_wp_rt1_redteam.py`: Comprehensive test suite for FIND-03..FIND-09 (20 tests).
- `_agents/remediation/WP-RT1.md`: This completion report.

---

## 3. Verification & Test Execution

1. **Targeted Test Suites:**
   ```bash
   pytest tests/anticheat tests/test_wp_e3_runner.py tests/test_wp_e4_scorer.py tests/test_wp_c2_semantic.py tests/test_wp_c3_router.py tests/test_wp_c7_failures.py tests/test_wp_rt1_redteam.py -q -p no:randomly
   # Result: 59 passed in 3.46s (100% pass)
   ```

2. **Canary 4 Prompt-Leak Mutation Check:**
   - Injected simulated filename leak into `FINGERPRINT_SYSTEM_INSTRUCTION` in isolated temporary directory.
   - Verified Canary 4 immediately detects the leak and raises `AssertionError: Prompt leak detected!`.

3. **Audit Hook Cheat Vectors Check:**
   - Verified that `os.listdir`, `os.scandir`, `subprocess.Popen`, `os.system`, `shutil.copyfile`, `sqlite3.connect`, imports (`evals`, `cardforge`, `tests`), and symlinks terminate with exit code 86 and emit `CHEAT_DETECTED`.

4. **Full Test Suite Run:**
   ```bash
   timeout 1800 $py -m pytest tests cardforge/tests -o cache_dir="$PROJECT_CACHE_DIR/pytest" -q -p no:randomly
   # Result: 1993 passed, 13 failed (13 known pre-existing failures), 9 skipped in 128.99s
   ```
   Zero regressions introduced. The only 13 failures are the pre-existing 6 zip-slip Windows path assertions and 7 tkinter headless environment failures.
