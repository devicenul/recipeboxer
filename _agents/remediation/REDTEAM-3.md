# RED-TEAM Challenger #3 — Gate B review

**HEAD:** `6590f32` (`remediation/phase0`) · **Date:** 2026-10-15 · **Mode:** read-only. The only repo write is this file. Scratch, mutants and logs are under `$PCD/redteam3/`.

**Constraints honoured:**
- No vault decryption, no `age.key`, no `.env`, no API keys, no live calls.
- `honest_v1_rc4` was not touched, and no other agents' transcripts were read.
- Plaintext GT-adjacent files under `$PCD` were only *listed* (names, permissions), never opened. The audit-hook probe used a **fake** GT file I created.

## 0. Summary

| Severity | Count |
|---|---|
| BLOCKER | **0** |
| MAJOR | **4** (RT3-01 … RT3-04) |
| MINOR | **9** |

**Verdict:** Gate B is **CONDITIONAL**.
- No eval cheat was found in production code.
- UP2/UP3/UP4 fitted nothing on T1–T4. The UP4 real pages do not overlap T3 sources.
- The single code path holds, and test-double sniffing is gone.
- Remaining problem 1: GT-adjacent plaintext is reachable by the SUT. The audit hook does not cover `$PCD`.
- Remaining problem 2: the scorer's ECE excludes hallucinated and `None`-confidence predictions, and the recipe-confidence model was fitted to the same blind spot. This flatters calibration claims.
- Remaining problem 3: two of six mutation canaries survived (three counting RT3-02). These are coverage gaps, not live cheats.

**Test runs:**
- Full suite: **13 failed / 2372 passed / 10 skipped** (722 s). Exactly the 13 known failures: 6 zip-slip, 7 tkinter.
- Targeted anticheat + static + rt1 + up3/up4: **70 passed**.

## 1. Findings

| ID | Sev | Location | Finding (quoted code) | Proposed fix |
|---|---|---|---|---|
| **RT3-01** | **MAJOR** | `$PCD/t3gt/spotcheck/recipe_r*.html` (12 files, `-rw-r--r--`) · `$PCD/t3_cache/raw_transcripts/rw-00{1..5}.txt` · `$PCD/gutenberg_cache/pg*.txt` (6 files: T1/T2 source text) · `evals/harness/sut_boot.py:48-72` | **Plaintext GT-adjacent material sits on disk outside the vault, and the SUT can read it.** Plan §3.2 says "Plaintext GT never touches the repo or the workspace" and "plaintext never lands on disk". WP-T3GT claims `chmod 700`, but within one Unix user that protects nothing. The audit hook only blocks `"/evals/"`, `tests/fixtures`, `*.enc`, `*.key` and `manifest.json` outside cwd. I installed the real hook and opened a fake `$PCD/redteam3/fakegt/t3gt/spotcheck/recipe_r999.html`: `READ OK (not blocked)`. `listdir($PCD/gutenberg_cache)` and `honest_v1_rc2` were also not blocked. Mutant **B2** (GroupingEngine globbing `~/.cache/projects/*/…/t3gt/spotcheck/*.html` via a split string literal) passed every static and anticheat test. | Shred or encrypt `t3gt/spotcheck`, `t3_cache/raw_*` and `gutenberg_cache` (or move them into the vault). In `sut_boot._is_forbidden_path`, deny `$HOME/.cache/projects/**` except the SUT temp dir and venv, plus `/tmp/t2_corpus_*`. Better: run the SUT with a `HOME` pointing at an empty temp dir. Test: hook trips on a fake `$PCD/t3gt/x.html`. |
| **RT3-02** | **MAJOR** | `evals/scoring/evaluator.py:81-151, 224-228` · `_agents/remediation/scratch_up3/extract_cal.py:67-76` · `docs/EVALS.md:136` | **ECE only covers matched predictions with non-None confidence.** `for pred_idx, gold_idx, sim in matched_pairs: … raw_conf = pred_rec_obj.get("confidence"); if raw_conf is not None: confidences.append(conf)`. Hallucinated or fragment recipes (`unmatched_preds`) never enter ECE, even though they are by definition incorrect. A SUT that emits `None` for doubtful recipes also drops them. The UP3 `recipe` model was fitted on exactly this population (`matched, _, _ = match_recipes_by_content(...)`, then `for pi, gi, sim in matched:`), so it never saw a false-positive recipe. The headline "T3 ECE 0.95 → ~0.05" is therefore measured on the population where the model is strongest. Not disclosed in EVALS.md. The scorer is unchanged since WP-SV, so this is not new. | Score unmatched predictions as `correct=False` in ECE. Report `confidence_coverage = n_conf / n_pred` and treat `None` as abstention. Refit `recipe` with unmatched predictions as negatives. Document in EVALS.md §ECE. |
| **RT3-03** | **MAJOR** | `recipeboxer/agent/orchestrator.py:674-683` · `recipeboxer/vision/fingerprint.py:202` | **Prior-as-confidence on the grouping-failure path (INV-AC4).** When `engine.group` raises (StageUnavailable or any Exception, L638-662), every card becomes an "unmatched" standalone proposal with `confidence=fp.role_confidence`. That is the fingerprint's role confidence, and it defaults to 0.8 (`role_conf = float(data.get("role_confidence", 0.8))`). Headless then feeds it as `grp_conf` into `recipe_confidence` (`headless.py:650-662`), so `recipes[].confidence` becomes a fitted transform of a non-grouping prior. The run is marked `partial`, but these recipe confidences are scored in ECE. | Set `confidence=None` for these fallback proposals. Drop the `0.8` default (use `None`, or raise on a missing field). Test: force `engine.group` to raise; assert all `recipes[].confidence is None`. |
| **RT3-04** | **MAJOR** | Canary coverage (§2) | **Two mutation canaries survive** (details in §2):<br>**A2:** a filename injected into the SetVerifier **ext-anchor (v2)** prompt. The prompt-leak canary uses a 2-card deck, so the fitted path (N ≥ 10, ext anchors, orphan rescue) is never exercised.<br>**C2:** `if grp_conf is None: grp_conf = 0.95` in headless. Only the spy test for the exact-proposal case exists.<br>Mutant B2 (RT3-01) also survives. | (1) Add a ≥ 10-card prompt-leak canary with a scripted client that returns SV continuations from back/continuation anchors. Assert no filename or card_id token in any request, including `set_verify_v2` and rescue. (2) Headless test where no proposal has a confidence: assert `recipes[].confidence is None`. (3) Static test: no float literal ≥ 0.5 assigned to `confidence`/`grp_conf`/`rec_conf` in `headless.py`/`orchestrator.py` (AST). |
| RT3-05 | minor | `recipeboxer/correlation/engine.py:789, 833` | On the legacy path (N < 10, or no fitted models), `chain_conf = min(s_scores)` is the **raw verbal SetVerifier confidence**, but the explanation says `f"Calibrated p={chain_conf:.3f}."`. | Label it "uncalibrated SV confidence", or use the calibrated `t_scores`. |
| RT3-06 | minor | `recipeboxer/extraction/structure.py:192` | Margin notes get hard-coded `confidence=0.9`. Today this does not feed `recipe_feature_vector` (it only reads ingredient/step confidences), but it is still a fabricated line confidence. | Use `None` or the transcript line confidence. |
| RT3-07 | minor | `recipeboxer/correlation/guards.py:38-40, 619-624 (engine)` | `GATE_CAP = 0.02` is documented (UP4 note, module docstring) and is never a recipe confidence. However, the gated value is written into `ev.p_link` (`ev.p_link = round(p, 4)`) with no gate marker, so HITL evidence shows 0.02 as if it were a calibrated probability. | Add `ev.gated = gate` to the evidence and show it in the UI. |
| RT3-08 | minor | `recipeboxer/correlation/linkmodel.py:395-406` | `Up3Models.load`: `except Exception: return cls()`. A missing or corrupt `calibration_default.json` silently switches production to the legacy path and changes reported confidences, with no warning. | Log an error and add `stats["models_loaded"]`. |
| RT3-09 | minor | `evals/calibration_fit.py:36-40` | The path guard only matches `evals/corpora/t[1-4]` and `evals/vault`. It misses eval outputs (`honest_v*`, `bench_*`), the decrypted `/tmp/t2_corpus_*`, `$PCD/t3gt`, `$PCD/t3_cache`, `gutenberg_cache`, and symlinks / relative `../corpora/t1` (it does not resolve paths). No current violation was found. | Use `Path(p).resolve()` and extend the deny-list. |
| RT3-10 | minor | `_agents/remediation/WP-UP3.md:5` · `guards.py:5` | UP3 read `ERROR`/`WARNING` lines from `honest_v1_rc2/*/run.log` (SUT logs on eval decks). UP4 targeted a "T3 precision regression" using aggregate T3 feedback, and the guards.py docstring says "the eval showed false merges there". This is allowed under the aggregate-feedback policy, but it is adaptive. The Gate B "beats baseline" claim should rest on **T2** (held-out), with T3 deltas labelled "post-feedback". | Note this in the Gate B report. |
| RT3-11 | minor | `recipeboxer/correlation/guards.py:43` · `prompts/set_verify_v2.md:13` · `content.py` (UP2) | Continuation cues `"card 2"`, `"card 3"`, `"Card 2: X"` match the cardforge generator's style, which T0, T1 and T2 all share. These are content cues (not filename cues) and legitimate conventions, but the synthetic gains may overstate real-world performance. | Report T3/T4 separately and do not extrapolate synthetic recall. |
| RT3-12 | minor | `recipeboxer/agent/orchestrator.py:563` | The test-adapter branch survives in production: `elif session.mock_client is not None and hasattr(session.mock_client, "verify_correlation")` routes to the legacy `PairingOptimizer`. It is structural duck-typing (allowed by `test_wp_rt2b_static`), and production clients lack that method. Headless also still builds an unused `PairingOptimizer` (`headless.py:346, 378`). | Delete it in the next cleanup. |
| RT3-13 | minor | `evals/harness/run.py:269-270` | T2 tar is extracted with `tar.extractall(path=t2_dir)` and no `filter="data"` (trusted input, but path traversal is possible). The SUT can list `/tmp/t2_corpus_*` (other T2 decks' images and original names) while it runs. | Use `extractall(filter="data")` and keep the T2 dir outside SUT reach (see RT3-01). |

### Verified clean (no finding)

**Item 1: contamination since RT2**
- `calibration_default.json` `up3.models.*.meta.fit_decks = [fit_mixed, fit_neardup, fit_standalone, fit_homo_large]`, and `recipe.meta.source = [xcal_fit.pkl]`. The support source is the same fit decks.
- All scratch_up2/3/4 deck roots are `$PCD/up2|up3|up4/decks`.
- `grep` over the scratch scripts found no reference to `evals/corpora`, vault, `honest_v*`, `t3gt`, `t3_cache` or gutenberg.
- UP2 used `cardforge.templates_t0` (T0, allowed). UP3 uses procedural vocabularies.
- **UP4 real pages:** all from OPenn/UPenn Ms. Codex 625, 251, 626, 644, 813 and 633. T3 `ATTRIBUTION.md` lists **only** Folger/Wellcome items (26 rows, all `fromthepage.com/folger/early-modern-recipe-books`). No overlap.
- `CANDIDATES.md` does list UPenn Codex 633 (`rw-032`) as an unused *candidate*, not in T3. Watch this if T3 is extended.
- No constants tuned to eval numbers found: `OOD_FRAC = 0.20` and `wpl_q99 = 7.0` come from fit-deck dumps.

**Item 2: fabrication**
- The consistency 0.95 boost is removed (`consistency.py:452-455`).
- Engine single/multi confidences are fitted, or `None`.
- `STRUCTURAL_PRIOR_*` (optimizer.py:421-427) are path-cover node costs, used only on the oracle/legacy optimizer path.
- The router 0.3–0.8 values are heuristic router scores (RT1), not recipe confidences.
- Oracle track `confidence=1.0` (`headless.py:108`) is only the GT-grouping input.

**Item 3: single code path**
- `ConcurrentGroupingEngine` is a docstring-only subclass. `test_wp_up3_single_path` passes.
- Headless → `RecipeBoxerAgent.run_pipeline` → `engine.group`. Extraction → `RecipeExtractor._delegate_to_pipeline` → `ExtractionPipeline.run`.
- `test_wp_rt2b_static` passes.
- Harness names are `secrets.token_hex(6)`, shuffled. Corpus images have **no EXIF** (T1 d01/d04, T3 d01 checked), so there is no capture-order leak.
- `ENV_ALLOWLIST` contains no GT or `deck_type` variables. `RB_DEBUG_GROUPING` only writes `grouping_debug.json` into the SUT temp cwd (output-only, off unless set).

**Item 4: scorer**
- `git log 1d7bf59..HEAD` touches only `evals/report.py` (pricing loader → packaged pricing) and `harness/run.py` (timeout, T2, failure dedupe). `score.py`, `scoring/` and `metrics/` are unchanged.
- Matched-only and end-to-end are both emitted (`evaluator.py:201-213`). Note that `"overall"` aliases **matched-only**.

**Item 5: prompts**
- The only prompt change since RT2 is the new `set_verify_v2.md`, which contains no corpus titles.
- `test_wp_rt2b_prompts` and the prompt-leak canary pass.
- The scorer deny-list test (score tier) was not run: it needs the key.

**Item 7: security**
- No `?key=` anywhere.
- `httpx`/`google_genai` loggers are set to WARNING in headless and the harness. The SDK uses a header key.
- The frontend only handles `has_api_key` / `key_source`; the key is write-only (`SettingsContext.tsx:104-125`).

## 2. Mutation canaries

Mutants were run in copies (`git archive HEAD`) under `$PCD/redteam3/m*`. Suites per mutant: `tests/anticheat`, `test_wp_rt2b_static`, `test_wp_rt2b_prompts`, `test_wp_up3_single_path`, `test_wp_rt1_redteam`, `test_wp_up3_models`, `test_wp_up4_guards`. Unmutated HEAD: 70 passed.

| Mutant | Injection | Result | Caught by |
|---|---|---|---|
| **A** | Card filenames (`ordered_card_ids` + face path names) appended as a `TextPart` in the TRANSCRIBE request (extractor → `sys.modules[...transcribe]._LEAK`) | **CAUGHT** | `test_canary_prompt_leak_scan`: "Forbidden token 'rec_vhc_apple_crisp_1948_front.jpg' … LLMStage.TRANSCRIBE" |
| **A2** | `card_id` appended to the SetVerifier prompt only `if is_ext_anchor` (fitted N ≥ 10 path) | **SURVIVED** (70/70) | none → RT3-04(1) |
| **B** | `GroupingEngine.group` reads `Path(fps[0].image_path).parent / 'manifest.json'` | **CAUGHT** | `test_static_invariants::test_no_answer_key_paths` (the runtime audit hook also blocks `manifest.json` outside cwd) |
| **B2** | `GroupingEngine.group` globs `$HOME/.cache/projects/*/…/t3`+`gt/spot`+`check/*.html` and reads it | **SURVIVED** (70/70); live hook probe: not blocked | none → RT3-01 |
| **C** | `"confidence": 0.95` hard-coded in the headless recipe output | **CAUGHT** | `test_wp_rt1_redteam::test_headless_emits_actual_proposal_confidence`. Not in `tests/anticheat`; consider promoting it. |
| **C2** | `if grp_conf is None: grp_conf = 0.95` (prior when no proposal confidence) | **SURVIVED** (70/70) | none → RT3-04(2) |

**Observations from the canary run:**
- The first variant of A (setting `recipeboxer.extraction.transcribe._LEAK` via `import … as`) silently bound to the re-exported *function* and leaked nothing. That run was a mutant bug, not a survival; it was re-done with `sys.modules`.
- The prompt-leak canary makes only 4 requests: FINGERPRINT×2, SET_VERIFY, TRANSCRIBE. **STRUCTURE / GROUNDING / CONSISTENCY / REPAIR prompts are never scanned.** Extend the scripted responses so the whole extraction pipeline runs.

## 3. Gate B checklist

| Item | Status | Notes |
|---|---|---|
| No answer-key access in production (INV-AC1) | **PASS** | Static and runtime checks catch the manifest canary |
| SUT isolation covers all GT-adjacent plaintext (INV-AC5) | **FAIL** | RT3-01 |
| No filename semantics / prompt leaks (INV-AC2) | **PASS (partial coverage)** | A caught. A2 survives (fitted path and extraction stages after transcribe not scanned) |
| No corpus content in prompts (INV-AC3) | PASS (offline) | Score-tier deny-list not run |
| No fabricated confidences (INV-AC4) | **FAIL (minor scope)** | RT3-03 failure path; RT3-06 margin notes |
| Calibration provenance: fit on fresh decks only | **PASS** | Metadata and scripts verified; UP4 real pages disjoint from T3 |
| Single grouping and extraction code path | **PASS** | RT3-12 dead legacy code only |
| No test-double sniffing (RT2 FIND-01) | **PASS** | `test_wp_rt2b_static` green |
| Scorer unchanged since WP-SV; matched-only and e2e reported | **PASS** | |
| ECE honest (SUT confidence, all predictions) | **FAIL** | RT3-02 |
| API key hygiene (no URL key, no logging, frontend write-only) | **PASS** | |
| Anticheat canaries pass at HEAD | **PASS** | 10/10 |
| Mutation canaries all caught | **FAIL** | A2, B2, C2 survive (RT3-04) |
| Full suite (only the 13 known failures) | **PASS** | 2372 passed / 13 known failed / 10 skipped |
| Gate B claims based on held-out T2 | **ADVISORY** | RT3-10: T3 was used as aggregate feedback by UP4 |
