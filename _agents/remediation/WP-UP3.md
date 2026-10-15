# WP-UP3 — Uplift round 3 (grouping recall, calibration, error mapping)

Base: HEAD 92f3e74, branch `remediation/phase0`. **No commits made**, per contract §0. `setup.sh` and `recipeboxer/api/static/index.html` were not touched.
Tuning used only fresh decks under `$PCD/up3/decks`. Fit decks: `fit_mixed`, `fit_neardup`, `fit_standalone`, `fit_homo_large`. Held-out decks: `ho_mixed`, `ho_neardup`, `ho_standalone`, `ho_homo_large` (261 cards, homogeneous).
No T1–T4 corpus, ground truth, vault, or eval output was accessed. From `honest_v1_rc2/*/run.log`, only the ERROR/WARNING lines were read.

## 0. Red-team-class findings (fixed)

1. **Orchestrator drift (two grouping code paths).**
   - HEAD's `ConcurrentGroupingEngine` (orchestrator.py) re-implemented `GroupingEngine.group` (~370 lines) and had drifted from it. Production headless ran the copy, while tests and calibration ran the engine.
   - It is now a docstring-only subclass. `GroupingEngine.group` runs SetVerifier with bounded concurrency (`asyncio.Semaphore(llm_max_concurrency)` plus `gather`, in deterministic job order).
   - Guarded by `tests/test_wp_up3_single_path.py`, which checks there is no `group` override and that headless calls `GroupingEngine.group` exactly once.
2. **Fabricated consistency confidence.**
   - `extraction/consistency.py` raised confidence to `max(conf, 0.95)` (0.85 + 0.10 boost) whenever the two reads agreed. This was not evidence-based, and it is what produced T3's ECE of 0.95.
   - The boost is removed: agreement now leaves confidence unchanged.
   - Confidence is now produced only by the fitted models (§3).
3. **Empty `MODEL_UNAVAILABLE after 1 attempts:` messages.**
   - Root cause: an exception whose `str()` is empty (SSL/read timeouts, bare `asyncio.TimeoutError`) was formatted directly, and per-request `max_attempts` was ignored on some paths.
   - `ai/client.py` now classifies errors (`_classify_error`): 503/UNAVAILABLE/overloaded, 429, timeouts and SSL errors are retryable. Messages always include the exception type and HTTP status. The per-request `max_attempts` cap is honoured.
   - The consistency second read is bounded: timeout `max(60, 1.5×STRUCTURE)`, `max_attempts=2`, plus an overall `wait_for`. Its warning is never empty.
   - Tests: `tests/test_wp_up3_errors.py` (24 tests).

### Residual duplicate-path grep findings (reported, left as is)

- The orchestrator keeps the legacy `PairingOptimizer` paths. They are used only when `session.optimizer` is set (headless sets it only for oracle image_sets) or when a mock client exposes `verify_correlation` (test adapter `_build_mock_optimizer`).
- Headless builds an unused `PairingOptimizer(verifier=sem_verifier)`.
- The orchestrator has unused imports: `DeckBackground`, `LinkCalibrator`, `path_cover`, `top_k_candidates`, `SetVerifier`.

## 1. Scored confidence field (exact definition)

The scorer reads `recipes[].confidence` from the headless output JSON. The scorer was not changed.

- **`recipes[].confidence`** = `linkmodel.recipe_confidence(group_conf, recipe, n_cards)` = fitted P(this recipe is **grouped correctly AND extracted correctly**).
  - Label used for fitting: content-matched to gold, sim ≥ 0.5, and ingredient F1 ≥ 0.7. These are scorer-identical matching functions.
  - Inputs:
    - `group_conf`: confidence of the proposal whose card set equals the recipe's cards. If no proposal matches exactly, the minimum over overlapping proposals; failing that, `r.confidence`.
    - Extraction evidence: ingredient and step counts, `title_inferred`, warning-code counts, line confidences.
  - Selection happens in `headless.py` around L633–665. If no fitted model exists, the value falls back to `group_conf`. If there is no evidence, it is `None`.
- **Proposal confidence** (engine, decks ≥ 10 cards with SetVerifier available):
  - Multi-card proposals: fitted `multi` model output.
  - Single-card proposals: fitted `single` model = P(standalone).
  - Otherwise `None`, never a constant (INV-AC4).
- The model bundle is `calibration_default.json["up3"]`: models `pre`, `rank`, `link`, `single`, `multi`, `recipe`. It is fit with `evals/calibration_fit.py --up3 …` using NumPy IRLS logistic regression with L2 regularisation.

## 2. Large-deck recall: what changed (`correlation/engine.py`, `ranking.py`, `linkmodel.py`)

1. **Ranked screening.**
   - First, a text-only `pair_evidence` pass covers all ordered pairs, scored by the fitted `pre` model.
   - The pool is the top-M outgoing and top-M incoming pairs, plus capture neighbours ±1, with M = clip(0.2·N, 10, 30).
   - Full evidence is computed on the pool and scored by the fitted `rank` model.
   - Adaptive k per anchor: between 3 and 10, keeping candidates with p ≥ 0.02. The model has no capture-order feature (capture-order invariance is tested).
   - Result: SetVerifier candidate coverage on `ho_homo_large` is **0.75**, versus **0.23** for HEAD's `total_log_lr@5`.
2. **Anchor extension.**
   - Back and continuation cards whose top successor has rank p ≥ 0.15 are also verified, to catch back → next-card-front links.
   - An extension job is skipped if all of its pairs were already offered in reverse by a primary anchor.
3. **Orphan rescue.**
   - Title-less cards not claimed by any verification are offered to their top-3 predecessors.
   - Capped at 0.15·N calls.
   - Skipped when every first-pass verification failed (verifier unusable).
4. **Fitted link model.**
   - Features: rank logit, SetVerifier direct/chain/negative/seen flags, SetVerifier confidence, rescue flag, in/out positive counts, and log N.
   - It replaces the legacy calibrator. `path_cover` is unchanged.
5. **Domain guard.**
   - The fitted path is used only when SetVerifier can run and the deck has N ≥ 10 cards.
   - Otherwise the legacy top-k + calibrator path runs unchanged (offline and tiny decks).

## 3. Before / after

"Before" means HEAD 92f3e74. It is measured on `fit_mixed`, plus the user's aggregate eval numbers. A HEAD SetVerifier run on held-out decks was skipped for budget, with parent approval; the eval benchmark provides the before/after comparison.

| Deck (held-out unless noted) | N | P | R | F1 | false split | false merge | grouping ECE | SV calls | est. cost |
|---|---|---|---|---|---|---|---|---|---|
| **HEAD** fit_mixed | 40 | 1.000 | 0.792 | 0.884 | 0.500 | 0 | 0.413 | — | — |
| **HEAD** T1 d01–d03 (user aggregate) | 67 | 0.967 | 0.869 | 0.914 | 0.249 | 0 | 0.325 | — | — |
| **HEAD** T1 d04 homogeneous (user aggregate) | 319 | — | 0.66 | 0.793 | 0.652 | — | 0.425 | — | — |
| UP3 fit_mixed (in-sample) | 40 | 1.000 | 0.863 | 0.926 | 0.385 | 0 | 0.086 | — | — |
| UP3 ho_mixed | 40 | 1.000 | 0.950 | 0.974 | 0.191 | 0 | 0.076 | 35 | ≈ \$0.58 |
| UP3 ho_neardup | 41 | 1.000 | 0.976 | 0.988 | 0.143 | 0 | 0.036 | 38 | ≈ \$0.62 |
| UP3 ho_standalone | 40 | 1.000 | 1.000 | 1.000 | 0 | 0 | 0.019 | 40 | ≈ \$0.64 |
| UP3 ho_homo_large | 261 | 0.983 | 0.827 | 0.898 | 0.416 | 0.000 | 0.052 | 200 | ≈ \$3.5 |

- Metrics are BCubed P, R and F1.
- Grouping ECE uses the harness: proposal confidence against exact-group correctness.
- Cost = fingerprints at ≈ \$0.0049/card + SetVerifier at ≈ \$0.011/call. Extraction (≈ \$0.0135/recipe) is not included.
- The final shipped bundle was re-run offline through the production orchestrator alias (tag `final_ho`, 695 cache hits, 0 misses) and gave identical results.

### Held-out calibration (bootstrap 95% CI)

| Quantity | n | ECE | 95% CI |
|---|---|---|---|
| **`recipes[].confidence` (recipe model)**, all held-out | 140 | **0.055** | [0.031, 0.106] |
| same, raw group_conf as recipe confidence (no recipe model) | 140 | 0.109 | [0.069, 0.171] |
| per deck: ho_homo_large / ho_mixed / ho_neardup / ho_standalone | 40 / 26 / 35 / 39 | 0.088 / **0.307** / 0.054 / 0.076 | — |
| link model (pairs) | — | 0.002 | [0.001, 0.003] |
| single (P standalone) | — | 0.028 | [0.022, 0.042] |
| multi | — | 0.117 | [0.069, 0.233] |

- Held-out extraction accuracy was 0.786, and 84% of groups were exact.
- In-sample (fit decks, n=128), the recipe model gave ECE 0.039 versus 0.081 for raw confidence.

## 4. Limitations (honest)

- **Homogeneous large decks still split falsely (0.42).** Most missed true links *were* offered to SetVerifier and **rejected**, typically back → next-card-front. Fixing this needs a set_verify prompt change, which would invalidate the SetVerifier cache and needs fresh fit data. Out of scope this round.
- **ho_mixed recipe ECE is 0.307** (n=26). Groups were exact (25/26) but 9 extractions failed scoring, while confidence was about 0.92. The extraction-quality features do not predict these failures. The overall CI upper bound (0.106) slightly exceeds the 0.10 target.
- **The `multi` model is overconfident** on held-out data (mean p 0.805 vs accuracy 0.706). It was fit on only 47 rows.
- With 30–40 recipes per deck type, per-deck ECEs are noisy.

## 5. Tests

New test files:
- `tests/test_wp_up3_errors.py`
- `tests/test_wp_up3_single_path.py`
- `tests/test_wp_up3_models.py`

Tests updated to the documented semantics (none in anticheat):
- `test_wp_rt1_redteam::test_headless_emits_actual_proposal_confidence` now spies that the real proposal confidence (0.77) is the input to `recipe_confidence`, and that the output equals the fitted value.
- `test_wp_xb_…consistency` agreement test now expects confidence unchanged.
- `test_wp_up3_models` sets `MIN_CARDS_FITTED = 0` for its 4-card synthetic deck.

The full suite result is in §7.

## 6. Spend

| Item | Cost |
|---|---|
| Earlier fingerprints + baseline | \$3.43 |
| Extraction probe | \$0.08 |
| fit SetVerifier | \$2.27 |
| held-out SetVerifier | \$3.28 |
| fit extraction | \$1.82 |
| held-out extraction | \$1.95 |
| **Total** | **≈ \$12.83** (cap \$14) |

## 7. Full-suite result

`pytest tests cardforge/tests -q -p no:randomly --continue-on-collection-errors`, with GEMINI keys unset: **2400 passed, 13 failed, 10 skipped** (403 s).

All 13 failures are known and pre-existing:
- 6 Windows-path zip-slip vectors in `test_archive_empirical_challenger`
- 7 tkinter failures in `test_browse_directory`

Targeted runs (anticheat, rt1, gd_engine, up2, up3_*) all pass.
