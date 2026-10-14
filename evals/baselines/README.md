# RecipeBoxer Evaluation Baselines

This directory stores historical and milestone benchmark baselines recorded across the RecipeBoxer pipeline remediation lifecycle.

---

## Baseline Inventory

| Baseline File | Date | Git Commit | Status / Integrity | Description |
|:---|:---:|:---:|:---:|:---|
| [`pre_remediation.json`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/evals/baselines/pre_remediation.json) | 2026-10-14 | `1272a55f0be19a82c509ccfbbec9811fdd072f85` | **CONTAMINATED** | Pre-remediation baseline recorded under Phase 0 (WP-00b). Contains eval cheats and answer-key lookups. |
| `honest_v0.json` *(upcoming)* | TBD | Gate A | **HONEST** | First certified honest baseline across T1 (dev-eval), T2 (held-out), and T3 (real-web) corpora without cheats. |

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

## Upcoming Milestone: Gate A (`honest_v0.json`)

At **Gate A** (following completion of Phase 1 and Phase 2):
1. All answer-key lookups, filename heuristics, and canned recipe fallbacks in `recipeboxer/` will be eliminated.
2. The black-box test harness (`evals/score.py` + vault tooling) will evaluate pixels strictly through live model APIs (`google-genai`).
3. Ground-truth data will be stored in an encrypted vault accessible only to the scoring harness.
4. The first certified honest baseline (`honest_v0.json`) will be recorded against T1 (dev-eval), T2 (held-out), and T3 (real-web) corpora.
