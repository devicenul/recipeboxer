# WP-RT3FIX — Red-team #3 (Gate B) fixes

**Base:** `6590f32` (worktree `wt_rt3`, branch `rt3fix`). **No commits** (the orchestrator commits). `setup.sh` was not touched. No API keys and no live calls were used. The UP3 refit ran fully offline from the UP3 disk cache: 1280 cache hits, 0 misses.

## 1. Findings → fix → test

| ID | Fix | Tests |
|---|---|---|
| **RT3-01** (major) SUT isolation | `evals/harness/run.py`: each deck's SUT runs in a fresh private sandbox (`prepare_sut_sandbox`).<br>- `HOME`, `TMPDIR`/`TEMP`/`TMP` and `XDG_{CONFIG,CACHE,DATA,STATE,RUNTIME}` all point into that sandbox.<br>- `PROJECT_CACHE_DIR` is dropped.<br>- The hook config is passed in `RB_SUT_SANDBOX` (JSON), which `sut_boot` pops from the environment at boot.<br>`evals/harness/sut_boot.py` has three layers:<br>(a) **Name-pattern denies**, active anywhere: `/.gemini/`, `/t3gt/`, `/t3_cache/`, `/gutenberg_cache/`, `/t2_corpus_`, `/honest_v*`, `/bench_out`, `/redteam*`, `/cardforge/`. These are on top of the existing evals / fixtures / `*.enc` / `*.key` / manifest denies.<br>(b) **Deny roots**: `$HOME/.cache/projects` of the current HOME, plus the harness-passed *real* `~/.cache/projects`, `~/.config/recipeboxer`, `~/.gemini`, the `*/AntiGravity/.gemini` artifact dirs and the dirs of `RB_EVAL_AGE_KEY_FILE`.<br>(c) **Strict read/write allowlist** in harness mode. Allowed: interpreter prefixes / site-packages, pycache prefix, the `recipeboxer` package dir, the cwd/input dir, the sandbox, `/dev`, `/proc` (own pid only), `/sys`, `/etc`, `/usr/share`, `/usr/lib*`, `/lib*`. The import machinery may list (not open) exact `sys.path` entries.<br>Interpreter-owned roots are exempt from (a) and (b). New audit events covered: `pathlib.Path.glob`/`rglob`, `glob root_dir`, `shutil.copytree`/`move`, `os.rename`. | `tests/anticheat/test_rt3_canaries.py`:<br>- `test_hook_blocks_fake_gt_reads[6]`: fake GT under `$PCD/t3gt`-like, `t3_cache`, `gutenberg_cache`, an innocent name under the project cache, the artifact-dir pattern, and `t3gt` anywhere. All 6 → exit 86.<br>- `test_hook_blocks_project_cache_glob_and_listing`<br>- `test_hook_configured_deny_root_blocks_real_home_cache`<br>- `test_hook_strict_allowlist`: numpy/ssl/tempfile/recipeboxer import OK; outside read → 86; config not visible to the SUT.<br>- **`test_headless_under_strict_sandbox_with_planted_gt`**: a full 12-card headless pipeline runs under the harness-built strict sandbox, with planted fake GT under HOME. Exit 0, recipes produced. This is the "real run works" check.<br>`tests/test_wp_rt3fix.py::test_build_scrubbed_env_sandbox`.<br>Manual check: `python -m evals.harness.run --tier T0` with no key gives SUT exit 0, `no_live_model`, no CHEAT. |
| **RT3-02** (major) ECE honesty | `evals/scoring/evaluator.py::calibration_summary` (documented in its docstring):<br>- `ece` = **`ece_all`**: every predicted recipe with a confidence; unmatched predictions count as incorrect.<br>- **`ece_matched`**: matched-only, diagnostic.<br>- **`confidence_coverage`** = n_conf / n_pred; `None` = abstention.<br>- Also reported: `n_pred`, `n_with_confidence`, `accuracy_all`, `mean_confidence_all`.<br>Available per deck (`calibration`; `calibration_ece` = `ece_all`) and per run (`metrics.calibration`).<br>`evals/report.py`: a new "Confidence Calibration" markdown table shows both ECEs plus coverage (applies to the T1 report). Directions were added for the new metrics.<br>`docs/EVALS.md` §5.5 documents the populations, the UP3 matched-only fit, and the baseline-comparison caveat. | `test_wp_rt3fix.py::test_ece_all_counts_unmatched_predictions_and_coverage` (ece_matched 0.1 vs ece_all 0.4, coverage 2/3); `::test_report_markdown_shows_both_eces` |
| **RT3-03** (major) prior-as-confidence | `orchestrator.py` grouping-failure fallback: `confidence=fp.role_confidence` → `None`.<br>The legacy `optimizer.py` standalone: role confidence → `None`.<br>`semantic.py`: a SetVerifier `None` confidence with unverified quotes was replaced by the constant `0.35`; it now stays `None`.<br>Headless already maps `grp_conf is None` → `recipes[].confidence = None` (no constant).<br>The `fingerprint.py` 0.8 default for `role_confidence` now only feeds the fitted `role_conf` model feature. It is no longer reported as a confidence anywhere.<br>Remaining literals were grepped and reviewed: the oracle-track `confidence=1.0` is a GT input; the optimizer's `conf = 0.0` is a filter sentinel; the `models/recipe.py` 1.0 values are legacy flat-list line coercion; the router 0.7 values are ink heuristics (RT1). | `test_no_grouping_evidence_means_no_recipe_confidence` (`engine.group` raises → all `None`); `test_no_literal_confidence_assignments` (AST scan over headless / orchestrator / engine / optimizer / linkmodel / semantic); `test_wp_rt1_redteam::test_no_hardcoded_085_fallback` updated to expect `None` |
| **RT3-04** (major) canary coverage | `tests/anticheat/fake_stage_client.py` is a stage-keyed offline client (keyed by stage + call order; never by filename, id or content).<br>New 12-card prompt-leak canary: it scans the system instruction and all text parts of **every** request, and asserts these stages ran: FINGERPRINT, SET_VERIFY **incl. `set_verify_v2`**, TRANSCRIBE, STRUCTURE, **consistency** (`consistency_v1`), GROUNDING, REPAIR.<br>Deterministic SetVerifier ext-anchor scan.<br>Fabricated-confidence canaries.<br>The rt1 actual-proposal-confidence check is promoted into `tests/anticheat`. | `test_prompt_leak_scan_all_stages_fitted_path`, `test_set_verifier_ext_anchor_prompt_has_no_card_ids`, `test_recipe_confidence_is_evidence_driven_not_constant`, `test_headless_emits_actual_proposal_confidence` |
| RT3-05 | The engine explanation labels the source of `chain_conf`: `Calibrated p=` only for the fitted `multi` model; otherwise `Uncalibrated SetVerifier confidence=` / `Minimum link probability=` / `No evidence-based confidence.` | `test_legacy_explanation_does_not_claim_calibration`, `test_fitted_explanation_says_calibrated` |
| RT3-06 | Margin notes take the transcript face confidence, not `0.9`. | covered by the AST scan scope plus the full suite |
| RT3-07 | `PairEvidence.gate` / `p_link_ungated`. The engine sets them whenever a UP4 guard caps `p_link` at `GATE_CAP`. The UI still shows `p_link`; the gate field is in the API payload (frontend badge is follow-up). | `test_pair_evidence_has_gate_marker_fields`, `test_engine_records_gate_on_evidence_static` |
| RT3-08 | `Up3Models.load` logs ERROR on bundle or model load failure and keeps `load_status`. `provenance()` is emitted in the headless output (`provenance.up3_models`) and passed through by the harness per deck. | `test_model_bundle_load_failure_is_logged_and_recorded`, `test_model_bundle_ok_provenance`, sandbox headless test asserts provenance |
| RT3-13 | `_safe_extractall`: `tar.extractall(filter="data")`, with a manual traversal / link guard as fallback. T2 extraction lives outside SUT reach (`/t2_corpus_` is denied, and so is the strict allowlist path). | `test_safe_extractall_rejects_unsafe_members[3]`, `test_safe_extractall_extracts_regular_files` |
| RT3-09/10/11/12 | Not changed: advisory or cleanup only. Notes: RT3-10 means Gate B "beats baseline" claims must rest on T2. RT3-11: report T3/T4 separately. | — |

## 2. UP3 recipe-model refit (RT3-02, offline)

The subagent reproduced the UP3 extraction-calibration rows exactly (128/128 fit and 140/140 held-out) and added unmatched predictions as negatives (fit +8, held-out +2). Files are in `$PCD/rt3_refit/`.

| Held-out population | n | acc | shipped model ECE | refit ECE | raw group_conf |
|---|---|---|---|---|---|
| matched-only | 140 | 0.786 | 0.055 [.031,.106] | 0.055 [.031,.107] | 0.109 |
| all predictions | 142 | 0.775 | 0.056 [.032,.104] | 0.053 [.030,.105] | 0.107 |

**Not shipped.** The change is within noise, and the refit is slightly worse on ho_standalone and ho_neardup.

**Caveat:** the 40-chain samples undercount fragments. The full held-out homo_large deck has ≥34 unmatched predictions that were not sampled, and extracting them would need live calls. The live `ece_all` from the scorer is the number to trust.

## 3. Mutant re-runs

Mutants were rebuilt from `$PCD/redteam3/m*` diffs applied to copies of this worktree in `$PCD/rt3fix_mut/`.

Suites run: `tests/anticheat`, `test_wp_rt1_redteam`, `test_wp_rt3fix`, `test_wp_rt2b_static`, `test_wp_rt2b_prompts`, `test_wp_up3_single_path`.

| Mutant | Result | Killed by |
|---|---|---|
| A (filenames in TRANSCRIBE) | **killed** | `test_canary_prompt_leak_scan`, `test_prompt_leak_scan_all_stages_fitted_path` |
| **A2** (card_id in set_verify_v2) | **killed** | `test_prompt_leak_scan_all_stages_fitted_path`, `test_set_verifier_ext_anchor_prompt_has_no_card_ids` |
| B (manifest.json) | **killed** | `test_static_invariants::test_no_answer_key_paths` |
| **B2** (glob `~/.cache/projects/*/…/t3gt/spotcheck`) | **killed** | `test_headless_under_strict_sandbox_with_planted_gt` (CHEAT_DETECTED deny root) |
| C (constant 0.95) | **killed** | 4 RT3 canaries + rt1 |
| **C2** (`grp_conf = 0.95` prior) | **killed** | `test_no_grouping_evidence_means_no_recipe_confidence`, `test_no_literal_confidence_assignments` |

Each mutant run also failed `test_no_hardcoded_085_fallback`, which still asserted the old role-confidence behaviour. It has since been updated to expect `None`. `test_canary_poisoned_vault` failed in the mB and mC2 copies, with the status differing between two otherwise identical runs ("partial" vs "failed"). These copies were running 3 in parallel on a CPU shared with the live benchmark (≈28 min per suite), which caused timeouts. The full-suite result on the clean worktree is in §4.

## 4. Test runs

- `tests/anticheat/test_rt3_canaries.py`: 16 passed.
- `tests/test_wp_rt3fix.py`: all passed.
- Full suite: see `$PCD/full_rt3fix.log` (summary below).

Full suite (final): **14 failed / 2401 passed / 10 skipped** (533 s), log `$PCD/full_rt3fix2.log`. The failures are:
- the 13 known ones: 6 zip-slip, 7 tkinter browse-directory;
- `test_scripts::test_setup_sh_fails_when_no_compatible_python`. This is **environmental**: it fails the same way on a pristine `git archive 6590f32` copy outside the main workspace. `setup.sh` is untouched.

The test suite creates an ignored `.venv` in the repo root; it was removed after the run.

The first full run also exposed and fixed:
- strict-mode reads of `*.egg-info`/`*.dist-info` inside `sys.path` entries (importlib.metadata) are now allowed;
- `TextSpan.confidence` is now `Optional`, needed for RT3-06;
- `test_wp_rt2b_priors` and `test_wp_rt1_redteam` expectations were updated to `None` (RT3-03).

> [!WARNING]
> The strict allowlist was verified with an offline 12-card headless run and a T0 no-key harness run, but **not** with a live T1 run. Run a 1-deck live smoke (`--decks d01`) before the next benchmark. If it trips, the CHEAT_DETECTED line names the path to allow.

## 5. Plaintext GT-adjacent material for the orchestrator to shred or move into the vault

This list comes from a read-only scan; names only, nothing was opened. Nothing was deleted.
- `$PCD/t3gt/spotcheck/` (README, index.html, recipe_r*.html) and `$PCD/t3gt/score_check/`
- `$PCD/t3_cache/raw/` (52 files: `*_pNN.html` source transcriptions + jpgs), `$PCD/t3_cache/raw_transcripts/`, `$PCD/t3_cache/raw_images/` (rw-*.jpg)
- `$PCD/gutenberg_cache/pg*.txt` (6 files, T1/T2 source text)
- `/storage/emulated/10/AntiGravity/.gemini/antigravity/brain/965c2b98-1aca-490d-a2ab-1d1fd16aea5f/spotcheck/` (orchestrator artifact dir)
- Review: `$PCD/sv/audit_report.json`, `$PCD/sv/check_*_gt.py` / `inspect_*.py` (per-item GT inspection scripts), `$PCD/gate_a/evals/corpora/{t1,t3,real_web}` (corpus copies), `$PCD/redteam3/fakegt` (fake; harmless)
- Scratch from this WP: `$PCD/rt3fix_mut/` (mutant copies), `$PCD/rt3_refit/` (contains extracted recipes for UP3 fit/held-out decks — fresh decks, not eval GT)
