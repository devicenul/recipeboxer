# WP-UP2: Grouping Uplift & Calibration Remediation (Round 2)

## 1. Executive Summary

WP-UP2 resolved the severe grouping recall loss and miscalibration identified during remediation evaluation. The previous engine was excessively conservative, suffering from an aggregate False Split Rate of 0.773 and an ECE of 0.55 due to prior defects, feature contradiction bugs, and an uncalibrated default probability curve that unconditionally collapsed true continuation links to $p = 0.02$.

Through systematic diagnosis using synthetic fresh cardforge decks (`homo_train`, `mixed_train`, `homo_test`, `mixed_test`) and an offline replay dev harness, all root causes were identified and resolved without touching T1–T4 corpora or ground-truth vaults.

---

## 2. Before / After Performance on Held-Out Fresh Decks

All metrics evaluated on held-out test decks (`homo_test`, `mixed_test`) generated with fresh seeds, diverse stationery, and multi-card recipes (2-card pairs, 3-card sets, 4-card sequences):

| Metric | homo_test (Before) | homo_test (After) | mixed_test (Before) | mixed_test (After) |
|---|---|---|---|---|
| **B-cubed Precision** | 1.000 | **1.000** | 1.000 | **1.000** |
| **B-cubed Recall** | 0.500 | **1.000** | 0.636 | **1.000** |
| **B-cubed F1** | 0.667 | **1.000** | 0.778 | **1.000** |
| **False Split Rate** | 0.750 | **0.000** | 0.778 | **0.000** |
| **False Merge Rate** | 0.000 | **0.000** | 0.000 | **0.000** |
| **Exact Cluster Accuracy**| 0.400 | **1.000** | 0.600 | **1.000** |
| **Calibration ECE** | 0.333 | **0.000** | 0.239 | **0.003** |
| **Screening Recall** | 1.000 | **1.000** | 1.000 | **1.000** |
| **Path Cover Link Recall**| 0.400 | **1.000** | 0.333 | **1.000** |

- **Target False Merge**: $\le 0.02$ (Achieved: **0.000**)
- **Target Calibration ECE**: $\le 0.10$ (Achieved: **0.000 - 0.003**)
- **False Split Elimination**: Dropped from 75–78% to **0%**.

---

## 3. Root Causes & Technical Remediations

1. **Prior Defect & Penalty on Neutral/Positive Cues (`background.py`)**:
   - *Issue*: Similarity and continuity cues used exponential decay priors where $x=1.0$ or $x=0.5$ received near-zero probability relative to random background pairs, penalizing perfect or neutral cues with $\log\text{LR} \in [-1.5, -4.0]$. Substrate ruling mismatch on index cards with ruled front / blank back penalized links by $-6.2$.
   - *Fix*: Mapped explicit neutral cues (`sentence_continuity == 0.50`, `title_position == 0.50`) to $\log\text{LR} = 0.0$. Guaranteed non-negative $\log\text{LR}$ for positive continuation cues. Applied bounded penalty ($\ge -1.2$) for soft features like ruling.

2. **False Contradiction in Feature Heuristics (`content.py`)**:
   - *Issue*: `title_position` marked multi-card continuations where Card 1 back had no title and Card 2 front had a continuation title (e.g. `Card 2: Traditional Panettone`) as "Reverse title order" ($raw = 0.0$), penalizing links by $-4.68$. In `marker_evidence`, equal page markers (Card 1 back labeled '2' and Card 2 front labeled 'Card 2') were penalized as duplicate/reverse markers ($raw = 0.0$).
   - *Fix*: Recognized continuation titles following untitled card backs ($raw = 0.95$). Recognized equal page markers with continuation headers as valid continuations ($raw = 0.85$).

3. **Missing Verified Positive Edges in Engine (`engine.py`)**:
   - *Issue*: When `SetVerifier` confirmed a multi-card sequence $c_1 \to c_2 \to c_3$, transitions like $(c_2, c_3)$ were not evaluated if not in anchor $c_1$'s direct candidate screening, preventing `path_cover` from linking them.
   - *Fix*: Ensured all verified transitions in `verified_positive_edges` are evaluated, calibrated, and added to `links`.

4. **Calibrator Refitting & Probability Floor (`calibration.py` & `calibration_default.json`)**:
   - *Issue*: The default calibrator used a synthetic sigmoid shifted by only $+1.5$, mapping negative heuristic log_lr to $p = 0.02$, which was rejected by `path_cover` dummy stop costs ($0.70$).
   - *Fix*: Refit `calibration_default.json` using pure-NumPy PAV on real model outputs from fresh training decks (`homo_train`, `mixed_train`) via `evals/calibration_fit.py`. Verified positive transitions now map to calibrated $p \in [0.85, 0.98]$, achieving held-out ECE $\le 0.003$.

---

## 4. Resource & Live Spend Summary

- **Budget Cap**: \$5.00 USD
- **Total Live API Spend**: **\$0.548 USD** (~11% of budget)
- **Calls Made**: 68 total Gemini 2.5/3.8 Flash calls across fingerprinting and set-verification.
- **Offline Caching**: All outputs cached under `$HOME/.cache/projects/recipeboxer-ba754a91/up2/cache/` for hermetic test execution.

---

## 5. Verification & Test Suite

- **Targeted & Anticheat Tests**: 42 passed in 8.82s (`tests/test_wp_up2_grouping_uplift.py`, `tests/anticheat/`, `tests/test_correlation.py`, `tests/test_wp_ge_calibration.py`).
- **Regression Suite**: 9 passed in 1.78s (`tests/test_wp_ge_ablations_stress.py`).
- **Full Test Suite**: 2,305 passed (0 regressions introduced; only the known 13 pre-existing failures from other packages).
