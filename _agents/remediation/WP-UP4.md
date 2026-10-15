# WP-UP4 — uplift round 4: OOD precision guard, empty-response retry

Base: HEAD 3186a0f on `remediation/phase0`. Nothing was committed (the orchestrator commits). `setup.sh` was not touched; its working-tree change belongs to someone else. `recipeboxer/api/static/index.html` was **not** changed.

## Summary

1. **T3 precision regression (generic fix, no T3 access).** The UP3 link/single/multi models were fitted only on cardforge decks. I added a **domain-support guard** and **conservative link gates**, both in `recipeboxer/correlation/guards.py` and wired into `GroupingEngine.group`.
   - **Domain support.** `DomainSupport` is fitted on the UP3 fit-deck dumps (263 cards) and stored at `calibration_default.json["up3"]["meta"]["support"]`. A card counts as out of support when either:
     - its boundary-line words-per-line exceed the fit q99 (7.0; cards are about 4, manuscript pages 6–12), or
     - its descriptor pattern was seen fewer than 2 times in the fit data.
   - **Strict mode.** It turns on when ≥ `OOD_FRAC` (0.20) of the deck's cards are out of support. In strict mode:
     - orphan rescue is skipped;
     - a link is only kept if SetVerifier said yes, with at least 1 verified quote, 0 unverified quotes, and confidence ≥ 0.60.
   - **Link gates.** These always apply when the up3 path runs. A gated link's probability is capped at `GATE_CAP` = 0.02. The gates are:
     - `nonrecipe`: distractor or non-recipe page;
     - `physical_conflict`: paper LLR ≤ −3;
     - `new_heading`: page b starts a new titled recipe, unless the SV positive has confidence ≥ 0.8 with clean quotes;
     - `rescue_unconfirmed`;
     - `strict_unconfirmed`.
   - **Reporting.** `stats` now includes `domain_guard` (frac_out, strict, reasons) and `link_gates` (counts).
   - **Synthetic gains are untouched.** No held-out cardforge deck triggers strict mode (frac_out ≤ 0.15), and the gates never fired on a true predicted link in any dump.
2. **Empty/blank model responses.**
   - `GenAIClient.generate`: an empty or whitespace-only body always gets at least 2 attempts (`MIN_ATTEMPTS_ON_EMPTY`), even when the per-request cap is 1.
   - The terminal error is INVALID_RESPONSE "Model returned an empty response after N attempts (finish_reason=…)", with `retryable=True` and `details.empty_response=True`. The JSON-decode message now ends with "(after N attempts)".
   - Stage level: `repair.generate_with_empty_retry` retries once after a 2.0 s backoff, with `max_output_tokens` doubled and `metadata.empty_retry=True`. It never retries `BudgetExceeded`. It is used in `transcribe()`, `structure()` and the `validate_and_repair` loop.
3. **SetVerifier back→next-front: NOT fixed.**
   - **v2 prompt.** `set_verify_v2.md` is used only for back/continuation anchors (`SET_VERIFY_EXT_PROMPT_ID`). On homo_large it accepted exactly the same 12 true links as v1, so it is a neutral, cached change.
     - Root cause: the model judges that the title-less back already "looks complete", or that "Card 2: X" belongs to another recipe.
   - **v3 attempt (reverted).** Passing the chain-head title as context made homo_large false split worse (0.416 → 0.428), so it was fully reverted in the engine, semantic.py and the tests.
4. **`link_ood` refit (tried, reverted).** I fitted a strict-mode link model on manuscript fit decks (`evals/calibration_fit.py --up4-link-ood`).
   - It helped synthetic manuscripts: ho_ms_mixed F1 .983 → 1.000, ho_ms_book .875 → .909.
   - It lost every true link on the real pages: real_b R .944 → .778, ECE .151 → .252.
   - The model was removed from `calibration_default.json`. The engine still uses `up3.models.link_ood` if one is ever present, and the fitting tool remains.

## Before/after (offline replay, cached LLM, same responses for both heads)

Columns: P / R / F1 / false split / false merge / ECE (BCubed; my own labels).

| Deck | HEAD 3186a0f | UP4 (final) |
|---|---|---|
| UP3 ho_mixed | 1.000 / .950 / .974 / .191 / 0 / .076 | identical |
| UP3 ho_neardup | 1.000 / .976 / .988 / .143 / 0 / .036 | identical |
| UP3 ho_standalone | 1.000 / 1.000 / 1.000 / 0 / 0 / .019 | identical |
| UP3 ho_homo_large | .983 / .827 / .898 / **.416** / **0** / **.052** | identical (meets ≤ .42 / 0 / ≤ .10) |
| UP4 ho_ms_mixed (synthetic manuscript) | 1.000 / .967 / .983 / .50 / 0 / .040 | identical / .039 |
| UP4 ho_ms_book (synthetic manuscript, flowing pages) | .967 / .800 / .875 / .889 / .002 / .132 | identical / .144 |
| real_a (21 real pages) | 1.000 / .952 / .976 / .25 / 0 / .124 | 1.000 / .889 / .941 / .75 / 0 / .120 |
| real_b (18 real pages) | 1.000 / .944 / .971 / .25 / 0 / .151 | identical / .151 |

All 4 OOD decks are flagged strict (frac_out 0.24–0.87). On real_a, strict mode drops one true link that HEAD only found through orphan rescue; that is a deliberate precision-over-recall trade.

**Honest caveat:** I could **not reproduce the T3 over-linking** on my proxies. HEAD made no false merges on the real pages and only 1 on ho_ms_book. The guard is therefore a defensive, generic measure, and it is unvalidated against the actual T3 failure. Supporting evidence: on the OOD fit decks, SetVerifier positives were only about 75% precise (9 of 12), which is why unconfirmed or weakly confirmed links are capped when the deck is out of support.

## Data created (outside the workspace, `$PCD/up4/`)

- **Real pages.** `$PCD/up4/real/{real_a,real_b}` holds 39 images in total. Provenance is in `$PCD/up4/real/SOURCES.md`.
  - All images are from UPenn Kislak Center via OPenn (openn.library.upenn.edu/Data/0002/…): Ms. Codex 625, 251, 626, 644, 813 and 633.
  - Licenses: CC Public Domain Mark, except Ms. Codex 626, which is **CC BY 4.0** (attribution: University of Pennsylvania Libraries).
  - No Folger and no Wellcome items. The T3 exclusion was based only on the public attribution listing and is recorded in `$PCD/up4/T3_EXCLUDE.txt`.
  - real_a: 21 images, 18 recipes, 3 true links, 2 distractors. real_b: 18 images, 14 recipes, 4 true links, 3 distractors.
  - I labelled the grouping myself. One link (644 Scotch collops → forced-meat) is a judgement call. Most 18th-century pages carry several recipes.
- **Synthetic manuscript decks.** `_agents/remediation/scratch_up4/gen_up4.py` generates prose receipts in script fonts on aged paper. Pages are portrait 900×1300, with flowing page breaks, distractors and same-dish pairs.
  - Fit decks: `fit_ms_{mixed,book,flow1,flow2}`. Held-out decks: `ho_ms_{mixed,book}`.
  - The generator's phrase vocabulary is repetitive, and this causes one SV false link in ho_ms_book.
- **Harness.** `scratch_up4/harness.py` is adapted from UP3. Setting `UP3_SRC=$PCD/up4/head_src` (a `git archive 3186a0f` snapshot) replays HEAD.

## Live spend

About **\$3.99** of the \$6 cap:

| Run | Cost |
|---|---|
| HEAD on ms decks | \$0.73 |
| HEAD on real pages | \$0.50 |
| v2 prompt | \$0.63 |
| v3 attempt | \$0.39 |
| ms fit decks | \$1.61 |
| flow2 | \$0.13 |

`--budget-usd` was passed on every run. Everything is cached in `$PCD/up4/llm_cache`.

## Files changed

- `recipeboxer/correlation/guards.py` (NEW)
- `recipeboxer/correlation/engine.py`: guard and gates, strict mode, rescue skip, `sv_q`, optional `link_ood`, stats
- `recipeboxer/correlation/semantic.py`: v2 routing for extension anchors
- `recipeboxer/prompts/set_verify_v2.md` (NEW)
- `recipeboxer/correlation/calibration_default.json`: `up3.meta.support` added; models unchanged
- `evals/calibration_fit.py`: `--up4-support`, `--up4-link-ood`; `fit_up3_bundle` preserves `meta.support`
- `recipeboxer/ai/client.py`: empty-response attempts and error mapping
- `recipeboxer/extraction/repair.py`, `transcribe.py`, `structure.py`: stage-level empty retry
- `tests/test_wp_up4_empty_retry.py` (13 tests), `tests/test_wp_up4_guards.py` (20 tests)
- Scratch only: `_agents/remediation/scratch_up4/*`

## Commands and results

All commands were run with the API keys unset.

- Targeted tests, `pytest tests/test_wp_up4_*.py tests/test_wp_up3_*.py test_wp_rt1_redteam test_wp_gd_engine test_wp_c2_semantic test_wp_gc_semantic test_wp_xa_extraction test_wp_xb_grounding_repair_consistency test_wp_x1_client tests/anticheat`: **174 passed**.
- Full suite (the contract command): **13 failed, 2432 passed, 10 skipped** (502 s). The 13 failures are only the known ones: 6 Windows-path zip-slip vectors and 7 tkinter `test_browse_directory` tests.
- Offline evals: `harness.py --decks … --dump --tag v1|v2|v4` (cache hits only, \$0).

## Contract deviations

- `semantic.py` was edited. It is under `correlation/**`, so this is within ownership.
- The UP3 bundle gained `meta.support`. `Up3Models` passed explicitly without meta disables the guard, which keeps backward compatibility.
- `fit_up3_bundle` now carries `support` across refits.

## Requests for other WPs / risks

- **`OOD_FRAC` margin.** ho_mixed sits at frac_out 0.15 against the 0.20 threshold. Real photographed index cards (portrait, dense) may trigger strict mode. That costs recall (no rescue, SV required) but not precision. The eval owner should watch T1/T2 recall.
- The T3 failure was not reproduced, so the fix's effect on T3 is unverified. If T3 still over-links, the next step is to look at which links pass `strict_unconfirmed` (SV-positive but wrong); `stats.link_gates` and `stats.domain_guard` will show whether strict mode engaged.
- SetVerifier back→front rejection remains (UP3 limitation). It probably needs a different signal, such as ingredient/step continuity scoring, rather than prompt wording.
- Empty-response retry adds at most 1 client retry plus 1 stage retry. On a persistently empty model this costs about 2 extra calls per stage.
