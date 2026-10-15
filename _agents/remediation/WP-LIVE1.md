# Work Package Completion Report: WP-LIVE1 (Live Pipeline Defect Remediation)

**Agent ID:** WP-LIVE1  
**Branch / HEAD:** `remediation/phase0` @ `1d7bf59` (remediated)  
**Date:** 2026-10-15  
**Owned Files Written / Updated:**
1. [`recipeboxer/models/recipe.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/models/recipe.py) (Strict flat `RecipeStructureResponse` & sub-models)
2. [`recipeboxer/models/failures.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/models/failures.py) (`SCHEMA_UNSUPPORTED`, `INVALID_REQUEST`, `BUDGET_UNPRICED`, `BudgetUnpriced`)
3. [`recipeboxer/ai/client.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/ai/client.py) (Classification of Developer API schema errors, pass model to BudgetGuard)
4. [`recipeboxer/ai/cost.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/ai/cost.py) (`importlib.resources` package data pricing table, fail-closed `BudgetGuard`)
5. [`recipeboxer/config.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/config.py) (`DEFAULT_STAGE_TIMEOUTS["TRANSCRIBE"] = 120.0`, `pricing_path: Optional[Path] = None`)
6. [`recipeboxer/data/pricing.toml`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/data/pricing.toml) (Packaged single source of truth for pricing)
7. [`recipeboxer/data/__init__.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/data/__init__.py) (Package data marker)
8. [`pyproject.toml`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/pyproject.toml) (Included `**/*.toml` in package data)
9. [`recipeboxer/extraction/structure.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/extraction/structure.py) (Wired to `RecipeStructureResponse` with mapping to `Recipe`)
10. [`recipeboxer/extraction/repair.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/extraction/repair.py) (Wired to `RecipeStructureResponse` with validation error recovery)
11. [`recipeboxer/extraction/consistency.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/extraction/consistency.py) (Wired to `RecipeStructureResponse`)
12. [`recipeboxer/extraction/transcribe.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/extraction/transcribe.py) (Scaled timeout for thinking tokens and multi-face requests)
13. [`recipeboxer/headless.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/headless.py) (Deduplicate failures per stage and image set; maintainer debug copy)
14. [`evals/harness/run.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/evals/harness/run.py) (Added `RB_DEBUG_GROUPING` to `ENV_ALLOWLIST`; deduplicate mapped failures)
15. [`evals/report.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/evals/report.py) (Load pricing from package data with custom override support)
16. [`recipeboxer/correlation/optimizer.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/correlation/optimizer.py) (Raised dummy cost floor to 0.18; `_titles_conflict` repair)
17. [`recipeboxer/correlation/content.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/correlation/content.py) (Matching recipe titles treated as positive evidence, not clashes)
18. [`recipeboxer/correlation/engine.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/correlation/engine.py) (Multi-card continuation transitions; `RB_DEBUG_GROUPING=1` dump)
19. [`docs/ARCHITECTURE.md`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/docs/ARCHITECTURE.md) (Updated pricing table paths)
20. [`docs/EVALS.md`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/docs/EVALS.md) (Updated pricing table paths)
21. [`docs/ROADMAP.md`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/docs/ROADMAP.md) (Updated pricing table paths)
22. [`docs/USER_GUIDE.md`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/docs/USER_GUIDE.md) (Updated pricing table paths)
23. [`tests/test_wp_m1_model_pricing.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/tests/test_wp_m1_model_pricing.py) (Updated `PriceTable.load()` calls)
24. [`tests/test_wp_live1_response_schemas.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/tests/test_wp_live1_response_schemas.py) (New offline test suite)
25. [`_agents/remediation/WP-LIVE1.md`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/_agents/remediation/WP-LIVE1.md) (This report)

---

## 1. Summary of Defects Diagnosed & Remediation

### Defect 1: Gemini Developer API Incompatible Response Models
- **Root Cause:** In the live run, structure calls failed with:
  `additionalProperties is only supported in Gemini Enterprise Agent Platform mode, not in Gemini Developer API mode.`
  The root cause was that `Recipe` included complex nested fields such as `Provenance.prompt_versions: Dict[str, str]` and dictionaries with arbitrary keys. When converted by Google GenAI's schema transformer, dictionaries generate `additionalProperties`, which the Gemini Developer API rejects with HTTP 400. Furthermore, this was misclassified as `MODEL_UNAVAILABLE` with retries.
- **Remediation:**
  - Built strict flat Developer-API-compatible models in [`recipeboxer/models/recipe.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/models/recipe.py): `RecipeStructureResponse` and typed sub-models (`RecipeTextSpanResponse`, `RecipeIngredientLineResponse`, `RecipeStepResponse`, `RecipeSectionResponse`, `RecipeTimeValueResponse`, `RecipeYieldValueResponse`).
  - Added `.to_recipe()` converter mapping to Schema v2 `Recipe`.
  - Added `FailureReason.SCHEMA_UNSUPPORTED` (terminal, non-retryable) and `FailureReason.INVALID_REQUEST` to `recipeboxer/models/failures.py`.
  - Updated `_classify_error` in `recipeboxer/ai/client.py` to classify `additionalProperties` errors directly as `FailureReason.SCHEMA_UNSUPPORTED`.
  - Added offline test [`tests/test_wp_live1_response_schemas.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/tests/test_wp_live1_response_schemas.py) asserting zero `additionalProperties` across all production response models.

### Defect 2: Package Data Pricing Table & BudgetGuard Closed-Failure
- **Root Cause:**
  `Pricing configuration file not found at /tmp/rb_deck_.../config/pricing.toml. PriceTable initialized empty then Unpriced model gemini-3.8-flash.`
  Pricing path resolved relative to CWD; during harness evaluation, SUT executes in temporary isolated directories (`/tmp/rb_deck_...`), leaving `PriceTable` empty and `BudgetGuard` unable to track costs or trip.
- **Remediation:**
  - Moved `config/pricing.toml` to [`recipeboxer/data/pricing.toml`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/data/pricing.toml).
  - Loaded via `importlib.resources.files("recipeboxer.data").joinpath("pricing.toml")` with fallback support for custom overrides.
  - Added `**/*.toml` to setuptools package data in `pyproject.toml`.
  - Updated `BudgetGuard.check_before_call`: when `cap_usd` is active and USD cost is unknown (empty price table, unpriced model, or prior unpriced calls), `BudgetGuard` fails closed by raising `BudgetUnpriced(BudgetExceeded)` (`FailureReason.BUDGET_UNPRICED`).

### Defect 3: Duplicate Entries in Failures List
- **Root Cause:**
  The session summary in `recipeboxer/headless.py` looped over `session.failures` and then looped over `session.recipes` (which had `FailedRecipe` stubs with matching failure objects), appending duplicates for every failed card.
- **Remediation:**
  - In `recipeboxer/headless.py` and `evals/harness/run.py`, deduplicated failure entries by `(stage, frozenset(image_ids))`.

### Defect 4: Transcribe Timeout & Thinking Budget
- **Root Cause:**
  `DEFAULT_STAGE_TIMEOUTS["TRANSCRIBE"] = 60.0` was too tight for multimodal requests containing multiple card faces with adaptive or extended thinking budget (up to 2048 tokens).
- **Remediation:**
  - Increased `DEFAULT_STAGE_TIMEOUTS["TRANSCRIBE"] = 120.0` in [`recipeboxer/config.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/config.py).
  - Added dynamic timeout scaling in [`recipeboxer/extraction/transcribe.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/extraction/transcribe.py): `timeout_s = max(timeout_s, 120.0 + len(faces) * 15.0)` when thinking budget exceeds 1024 or face count exceeds 2.

### Defect 5: Multi-card Recipe Grouping Disconnection
- **Root Cause:** Multi-card recipes in deck `d03` collapsed into single-card standalones due to four distinct issues:
  1. `path_cover` dummy cost floors in `recipeboxer/correlation/optimizer.py` were clamped to `0.01`, meaning leaving two cards unlinked cost only `0.02`. To beat `0.02`, an edge required $p > 0.9802$, so even high-confidence calibrated links ($p \in [0.75, 0.95]$) were rejected.
  2. In `optimizer.py`, `_is_title_bearing_start` marked cards without titles as title starts if `is_start >= 0.70`, and unconditionally broke chains between cards even when they shared identical titles ("Salad Sticks").
  3. In `recipeboxer/correlation/content.py`, matching titles were treated as a title clash (`raw = 0.0`) instead of matching.
  4. In `recipeboxer/correlation/engine.py`, multi-card transitions from `SetVerification.continuations` beyond the first pair were not mapped to verified edges.
- **Remediation:**
  - Raised dummy stop cost floor in `optimizer.py` to `0.18` and rebalanced priors multipliers.
  - Created `_titles_conflict` helper to only forbid links and split chains between cards bearing genuinely conflicting (different) titles.
  - In `content.py`, treated identical/overlapping titles across cards as strong link evidence (`raw = 0.95`).
  - In `engine.py`, constructed `verified_positive_edges` across all adjacent continuation pairs in `SetVerification`.
  - Added opt-in maintainer debug dump via `RB_DEBUG_GROUPING=1` saving `grouping_debug.json` in the SUT output directory (with zero eval metadata) and whitelisted `RB_DEBUG_GROUPING` in `ENV_ALLOWLIST`.

---

## 2. Verification Evidence

1. **Offline Response Schema Test Suite:**
   ```bash
   pytest tests/test_wp_live1_response_schemas.py -q
   ```
   **Result:** 11 passed in 1.05s. Every production response model confirmed free of `additionalProperties` and unsupported keywords.

2. **Live Deck Run Verification (Cardforge Fresh Deck):**
   ```bash
   python -m cardforge -o ~/.cache/.../deck_small -n 2 --seed 999
   python -m recipeboxer analyze --input ~/.cache/.../deck_small --out ~/.cache/.../out_small.json --profile balanced --budget-usd 0.50
   ```
   **Output:**
   - Status: `ok`
   - Multi-card Recipe: `Aunt Clara's 1965 Rhubarb Custard Pie` correctly grouped 2 cards (`['rec_vhc_rhubarb_pie_1965_front.jpg', 'rec_vhc_rhubarb_pie_1965_back.jpg']`).
   - Extraction: All recipes transcribed, structured, grounded, and verified without failures.
   - Failures count: `0`.
   - Budget tracking: 14/14 calls priced; total spend $0.094214 USD.
   - Maintainer debug dump: `grouping_debug.json` generated cleanly.

3. **Full Test Suite:**
   ```bash
   pytest tests cardforge/tests -q -p no:randomly --continue-on-collection-errors
   ```
   **Result:** `13 failed, 2356 passed, 9 skipped in 324.27s (0:05:24)`.
   Exactly 13 known failures preserved (6 Windows path ZipSlip, 7 headless tkinter GUI).
