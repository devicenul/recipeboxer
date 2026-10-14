# RecipeBoxer Evaluation Baselines

This directory stores historical and milestone benchmark baselines recorded across the RecipeBoxer pipeline remediation lifecycle.

---

## Baseline Inventory

| Baseline File | Date | Git Commit | Status / Integrity | Description |
|:---|:---:|:---:|:---:|:---|
| [`pre_remediation.json`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/evals/baselines/pre_remediation.json) | 2026-10-14 | `1272a55f0be19a82c509ccfbbec9811fdd072f85` | **CONTAMINATED** | Pre-remediation baseline recorded under Phase 0 (WP-00b). Contains eval cheats and answer-key lookups. |
| `honest_v0.json` *(upcoming)* | Gate A | Certified | **HONEST** | First certified honest baseline across T1 (dev-eval), T2 (held-out), and T3 (real-web) corpora without cheats. |

---

## Pre-Remediation Baseline (`pre_remediation.json`)

> [!CAUTION]
> **CONTAMINATED BASELINE — REFERENCE ONLY**
> 
> The metrics recorded in `pre_remediation.json` were produced by legacy codebase components containing known evaluation shortcuts, answer-key lookups, filename heuristics, and mock judge evaluations (detailed in `_agents/remediation/REMEDIATION_PLAN.md` §2).
> 
> **DO NOT CITE THESE SCORES AS MEASURES OF MODEL PERFORMANCE, ACCURACY, OR PRODUCT QUALITY.**

### Why the Unobfuscated Numbers are Artificially Inflated
- **Answer-Key Manifest Lookups**: `extractor.py` and `router.py` check image SHA-256 hashes against `tests/fixtures/sample_cards/manifest.json`.
- **Canned Recipe Fallbacks**: Hardcoded recipes in `extractor.py` and templates in `card_generator.py` allow instant retrieval (0.10s execution duration, 0.00% CER, 0.00% WER, 100% pairing F1).
- **Deterministic Mock Judge**: The default evaluation mode runs an offline deterministic mock that assigns near-perfect rubric scores (4.96 / 5.0) to ground-truth recipes.

### What the Obfuscated Numbers Reveal
- When filename obfuscation is enabled (`--obfuscate`, default runner behavior), filename-based cheat lookups fail.
- Recipe titles degrade to placeholders (e.g., `Recipe from card_b40fd0e8.jpg`), driving Overall CER to **78.67%** and Overall WER to **90.49%**, and reducing Fraction Accuracy to **39.2%**.
- However, SHA-256 hashes and physical characteristics still leaked through the legacy harness to `PairingOptimizer`, retaining artificial 100.0% pairing F1.

---

## Certified Honest Baseline (`honest_v0.json`)

At **Gate A** (following completion of Phase 1 and Phase 2), the first certified honest baseline (`honest_v0.json`) is recorded:
1. **Zero Cheats**: All answer-key lookups, filename heuristics, and canned recipe fallbacks in `recipeboxer/` are completely removed (verified by AST anti-cheat canaries in `tests/anticheat/`).
2. **Black-Box Runner**: Images are copied into a temporary directory with randomized filenames and order. Execution runs in a scrubbed subprocess with a `sys.addaudithook` preventing access to ground-truth vaults, answer keys, or fixtures.
3. **Encrypted Vault Ground Truth**: Ground-truth data is encrypted with `age` and decrypted strictly in memory by the trusted scorer (`evals/score.py`) after the SUT completes.
4. **Live Independent LLM Judge**: Qualitative rubric scoring uses `LiveJudge` on an independent model tier from the extractor (e.g. Pro judge if extractor is Flash), blinded to system and model names. Judge scores are reported and never gate.
5. **Multi-Corpus Scope**: Evaluated across T1 (dev-eval synthetic), T2 (held-out synthetic), and T3 (public-domain real-web archives with human transcriptions).

---

## Baseline Comparison & Regression Testing (`evals.report compare`)

Evaluation reports are compared against stored baselines to detect performance and accuracy regressions during development and CI.

### CLI Usage

```bash
# Compare a new evaluation report against the honest baseline
python -m evals.report compare path/to/report.json evals/baselines/honest_v0.json

# Compare with custom tolerances
python -m evals.report compare report.json baseline.json --tolerances '{"bcubed_f1": 0.02, "title_cer": 0.03}'
```

### Exit Codes
- `0`: All metrics are within allowable tolerances. Comparison **PASSED**.
- `1`: One or more metrics regressed beyond the specified tolerance, or cheat detected / status regression.
- `2`: File not found or argument error.

### Directionality and Tolerances

| Metric Category | Metrics | Direction | Default Tolerance |
|:---|:---|:---:|:---:|
| **Grouping** | `bcubed_f1`, `bcubed_p`, `bcubed_r`, `ari`, `exact_cluster_acc` | Higher is better | `0.01` |
| **Grouping (Errors)** | `false_merge_rate`, `false_split_rate` | Lower is better | `0.01` |
| **Extraction (Quality)** | `ingredient_f1`, `direction_f1`, `qty_unit_exact` | Higher is better | `0.01` |
| **Extraction (Errors)** | `title_cer`, `overall_cer`, `hallucination_rate`, `omission_rate` | Lower is better | `0.02` |
| **Calibration** | `ece` | Lower is better | `0.01` |
| **LLM Judge** | `judge_score`, `aggregate_score` (informational) | Higher is better | `0.20` |

### Feedback Policy Enforcement
When rendering reports (`python -m evals.report render ...`):
- **T0 (Train Tier)**: Allows both aggregate metrics and `--feedback per_item` for granular debugging and prompt development.
- **T1–T4 (Eval & Test Tiers)**: **Aggregate only**. Any request for `--feedback per_item` raises `ValueError`, and all per-item / per-image fields are automatically stripped to safeguard benchmark integrity and prevent test-set memorization.
