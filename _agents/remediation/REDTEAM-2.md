# RED-TEAM Challenger #2 Audit Report: Post-Wave 5 Verification

**Challenger:** Red-Team Challenger #2  
**Commit / HEAD:** `91d3778` (branch: `remediation/phase0`)  
**Scope:** Wave 5 Remediated Codebase (`GroupingEngine`, `ExtractionPipeline`, `evals`, `tests`, `frontend`)  
**Evaluation Standard:** `REMEDIATION_PLAN.md` (§2–§9.1, §10), `WAVE5_CONTRACTS.md`, `INVARIANTS.md` §10 (INV-AC1..INV-AC6)  
**Date:** October 2026  

---

## 1. Executive Summary

This independent red-team audit evaluated the integrity, security, anti-cheat compliance, and performance scalability of RecipeBoxer following Wave 5 integration and the WP-W5FIX remediation.

### Summary of Audit Findings
| Severity | Count | Primary Impact Areas |
|---|:---:|---|
| **BLOCKER** | **2** | Production mock-sniffing across 4 core modules (`fingerprint.py`, `extractor.py`, `semantic.py`, `orchestrator.py`); Raw Gemini API key in URL via legacy httpx call in `fingerprint.py` bypassing `CostLedger` / `BudgetGuard`. |
| **MAJOR** | **4** | Headless status returns `"ok"` when recipe extractions fail; Hardcoded uncalibrated confidence/priors (`0.85` / `0.90`) masquerading as evidence in `optimizer.py` and `engine.py`; Silent exception blocks in `engine.py` (empty bytes fallback) and `consistency.py` (swallowed second read); Severe performance bottleneck in `mirrored_wear_score` (recomputing median filters $O(N^2)$ times, taking ~75% of grouping runtime). |
| **MINOR** | **3** | `SpendMeter` displays `$0.0000` when spend is null/unknown (and potential crash on `null.toFixed`); Template titles ("Aunt Clara", "Peach Cobbler") cited in production prompts; SetVerifier quote validation false rejections on middle-of-card body quotes. |

**Gate B Recommendation:** **CONDITIONAL HOLD**. While the anti-cheat canaries and static AST invariants successfully caught adversarial test mutations, Gate B sign-off must remain blocked until the **2 Blockers** and **4 Major issues** are remediated.

---

## 2. Confirmation & Extension of Known Issues

| Issue | Status | Code Citation & Evidence | Impact & Recommendation |
|---|---|---|---|
| **Mock-sniffing in `fingerprint.py`** | **CONFIRMED & EXTENDED** | `recipeboxer/vision/fingerprint.py:391, 399, 404, 412, 418, 419`<br>`recipeboxer/extraction/extractor.py:268, 289, 319, 327, 336, 343, 350, 363`<br>`recipeboxer/correlation/semantic.py:542, 579, 584, 589, 597`<br>`recipeboxer/agent/orchestrator.py:1152-1174` | **BLOCKER.** Mock-sniffing is not isolated to `fingerprint.py`. It is pervasive across all legacy wrapper adapters (`type(...).__name__.endswith("Mock")`, `hasattr(..., "mock")`, `MockVerifierAdapter`). In `semantic.py:542`, an injected client conforming to `LLMClient` is refused if its class name ends with `Mock`. Remove all mock-name inspection; use structural protocols or explicit dependency injection. |
| **Legacy httpx Gemini API call with `?key=` in URL** | **CONFIRMED** | `recipeboxer/vision/fingerprint.py:453-465, 633-659`<br>`url = f"https://generativelanguage.googleapis.com/...:generateContent?key={self.api_key}"` | **BLOCKER.** `_call_gemini_api` bypasses `LLMClient`, `CostLedger`, and `BudgetGuard`. Transmitting the API key in the URL query string causes httpx, proxy logs, and logging frameworks to record the raw Gemini API key in plain text. Delete `_call_gemini_api` completely; route all fingerprint calls exclusively through `create_llm_client()`. |
| **Headless status `'ok'` on extraction failure** | **CONFIRMED & EXTENDED** | `recipeboxer/headless.py:466-470, 480-515`<br>`recipeboxer/agent/orchestrator.py:945-960` | **MAJOR.** When extraction fails, `orchestrator.py` instantiates `FailedRecipe(title="Extraction Failed (...)")` and appends it to `session.recipes`. `headless.py` sets `status = "ok"` and includes the failed recipe in `recipes_out`. Downstream scorers evaluate this as an extracted recipe with 0 F1 rather than recording an extraction stage failure. Set `status = "extraction_failed"` when any recipe has `status == "FAILED"`. |
| **Datetime session persistence** | **CONFIRMED** | `recipeboxer/storage/session_store.py:186-192`<br>`recipeboxer/models/archive.py` | Addressed in WP-W5FIX via `model_dump(mode="json")` and `default=str`, but non-ISO string fallbacks should be guarded across all serialization paths. |
| **`report.md` shows N/A for extraction metrics despite `report.json` values** | **CONFIRMED** | `evals/report.py:315-345` | `report.py` table formatter checks `r.get("metrics", {}).get("extraction")` under legacy keys while `evals/scoring/` emits hierarchical schema v2 metric names. |
| **Deck type breakdown `'unknown'`** | **CONFIRMED** | `evals/harness/run.py:110-125`<br>`evals/report.py:280-295` | Obfuscated evaluation deck manifests omit explicit `deck_type` (`homogeneous` vs `mixed`), defaulting to `'unknown'`. |
| **`order_kendall_tau` 0.0** | **CONFIRMED** | `evals/scoring/metrics.py:140-165` | Scorer Kendall tau calculation returns 0.0 when single-card recipes dominate or when tie-handling encounters singleton arrays. |

---

## 3. Comprehensive Red-Team Findings Inventory

| ID | Severity | File & Line | Summary & Quoted Code | Suggested Owner |
|---|:---:|---|---|:---:|
| **FIND-01** | **BLOCKER** | `recipeboxer/vision/fingerprint.py:418-419`<br>`recipeboxer/extraction/extractor.py:327`<br>`recipeboxer/correlation/semantic.py:542`<br>`recipeboxer/agent/orchestrator.py:1152` | **Production code detects test doubles by class name (`Mock`) and attribute (`mock`).**<br>```python\n# fingerprint.py:419\nif hasattr(self._call_gemini_api, "mock") or type(self._call_gemini_api).__name__.endswith("Mock"):\n# semantic.py:542\nif isinstance(self.client, LLMClient) and not type(self.client).__name__.endswith("Mock"):\n# extractor.py:327\nif (res is None or type(res).__name__.endswith("Mock")) and callable(self.client):\n# orchestrator.py:1152\nclass MockVerifierAdapter:\n```<br>Production code alters execution flow based on string matching on `"Mock"` in class names. This violates INV-AC1/AC6 and prevents clean test double behavior. | WP-C8 / WP-GD / WP-XA |
| **FIND-02** | **BLOCKER** | `recipeboxer/vision/fingerprint.py:453-465, 637` | **Legacy httpx `_call_gemini_api` bypasses ledger/BudgetGuard and leaks API key in query string.**<br>```python\n# fingerprint.py:637\nurl = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"\n```<br>Bypasses `LLMClient`, cost accounting, and spend caps. Furthermore, leaks the plain-text API key into HTTP request URLs and server/proxy access logs. | WP-GA / WP-G1 |
| **FIND-03** | **MAJOR** | `recipeboxer/headless.py:466-470, 480-515` | **Headless analysis reports overall status `"ok"` and outputs recipe stubs when extraction fails.**<br>```python\n# headless.py:466-470\nelif session.state == PipelineState.FAILED:\n    status = "error"\nelse:\n    status = "ok"\n```<br>When extractions fail, `FailedRecipe` objects with `status="FAILED"` are included in `recipes_out` with `status="ok"`. Scorer treats them as predicted recipes rather than pipeline failures. | WP-GD / WP-C7 |
| **FIND-04** | **MAJOR** | `recipeboxer/correlation/optimizer.py:494-500, 524`<br>`recipeboxer/correlation/engine.py:217, 365, 386` | **Hardcoded magic numbers (`0.85`, `0.90`) masquerade as evidence and distort reported confidences.**<br>```python\n# optimizer.py:524\nconf = getattr(fp, "role_confidence", 0.90) or 0.90\nstandalone_prop = CorrelationProposal(..., confidence=conf)\n# engine.py:365, 386\nis_comp = prior.get("is_complete", 0.90)\n... prop = CorrelationProposal(..., confidence=round(is_comp, 4))\n```<br>Priors are not calibrated; unverified single-card proposals are assigned high confidence (`0.90`–`0.92`) even when `can_verify` is False (offline / no LLM client). | WP-GD / WP-GE |
| **FIND-05** | **MAJOR** | `recipeboxer/correlation/engine.py:59-66`<br>`recipeboxer/extraction/consistency.py:372-378` | **Silent `except` blocks return placeholder bytes or swallow model verification failures.**<br>```python\n# engine.py:59-66\nif p and p.is_file():\n    try: return p.read_bytes()\n    except Exception: pass\nreturn b""\n# consistency.py:376-378\nexcept Exception as exc:\n    logger.warning("Self-consistency second read failed: %s", exc)\n    return recipe, []\n```<br>`TempDirImageProvider` silently returns empty bytes `b""` on I/O failure. `consistency.py` catches LLM failures and returns `recipe, []` without warning flags, masking secondary read breakdowns. | WP-GD / WP-XB |
| **FIND-06** | **MAJOR** | `recipeboxer/vision/physical_pairing.py:184-260`<br>`recipeboxer/correlation/background.py:311-395` | **Severe performance bottleneck: `mirrored_wear_score` recalculates median filters without per-card caching.**<br>```python\n# physical_pairing.py:217\ndef _extract_wear_field(arr_rgb: np.ndarray, gray: np.ndarray) -> np.ndarray:\n    smooth_rgb = np.zeros_like(arr_rgb)\n    for c in range(3):\n        smooth_rgb[:, :, c] = median_filter(arr_rgb[:, :, c], size=9)\n```<br>cProfile reveals that 75.2% of total grouping time (34.86s out of 46.36s for N=20 cards) is spent in `mirrored_wear_score`, with 26.17s spent in `scipy.ndimage.rank_filter`. Recomputed $O(N^2)$ times for the same card during pair screening. | WP-GA / WP-G12 |
| **FIND-07** | **MINOR** | `frontend/src/components/layout/SpendMeter.tsx:14, 45`<br>`frontend/src/components/layout/StatusBar.tsx:25` | **`SpendMeter` displays `$0.0000` when spend is unknown or null, masking unattached ledgers.**<br>```typescript\nexport const SpendMeter: React.FC<SpendMeterProps> = ({\n  spendUsd = 0.0,\n...\n  <span className=\"font-mono text-xs\">${spendUsd.toFixed(4)}</span>\n```<br>When `spendUsd` is null, passing `null` can cause a runtime crash on `null.toFixed(4)` or display `$0.0000` instead of indicating that the live ledger has not yet recorded usage. | WP-F2 |
| **FIND-08** | **MINOR** | `recipeboxer/prompts/transcribe_handwritten_v1.md:29`<br>`recipeboxer/prompts/structure_v1.md:30`<br>`recipeboxer/extraction/extractor.py:58` | **Template recipe titles ("Aunt Clara", "Peach Cobbler") copied into production prompts.**<br>```markdown\n# transcribe_handwritten_v1.md:29\n(e.g. \"Good for church suppers\", \"Aunt Clara's recipe 1928\")\n# structure_v1.md:30\ninfer a descriptive title from the ingredients/instructions (e.g. \"Peach Cobbler\")\n# extractor.py:58\nAppend ' [GUESS]' if inferred or untitled.\n```<br>"Aunt Clara" is from `cardforge/templates_t0.py:45` ("Aunt Clara's 1965 Rhubarb Custard Pie"), and "Peach Cobbler" is from fixture export tests. Production prompts should use fictitious examples. `extractor.py:58` also conflicts with `structure_v1.md:30` on `' [GUESS]'` appending. | WP-XA / WP-XB |
| **FIND-09** | **MINOR** | `recipeboxer/correlation/semantic.py:148-210` | **`SetVerifier` quote validation causes false rejections on middle-card text and false accepts on word bags.**<br>```python\n# semantic.py:148-168\ndef _extract_all_fingerprint_text(fps: Sequence[VisualFingerprint]) -> str:\n# Line 204\nif len(words) >= 2 and all(w in clean_corpus for w in words):\n    verified.append(quote)\n```<br>If OCR is disabled, `_extract_all_fingerprint_text` contains only header and footer lines. Verbatim quotes from the middle of the card image are flagged as `UNVERIFIED_QUOTE` and penalized by 50% confidence. | WP-GC |

---

## 4. Adversarial Mutation Verification Results

Adversarial mutations were reintroduced outside the workspace in `$PROJECT_CACHE_DIR/redteam2/` to evaluate whether `tests/anticheat/` canaries and static checks detect reintroduced eval cheats.

### Mutation Experiment 1: Injecting Filename into Transcribe Prompt
- **Mutation:** In `recipeboxer/extraction/transcribe.py`, injected the input card filename (`rec_vhc_apple_crisp_1948_front.jpg`) into the `TextPart` of the `LLMRequest` passed to `llm_client.generate()`.
- **Runtime Canary Detection (`tests/anticheat/test_canaries.py::test_canary_prompt_leak_scan`):**
  - **Result: CAUGHT (PASS).**
  - Canary immediately detected the forbidden filename token in the recorded request:
    ```text
    Prompt leak detected! Forbidden token 'rec_vhc_apple_crisp_1948_front.jpg'
    found in LLMRequest for stage LLMStage.TRANSCRIBE
    ```

### Mutation Experiment 2: Reintroducing Manifest Read in GroupingEngine
- **Mutation:** In `recipeboxer/correlation/engine.py`, inserted an attempt to open `tests/fixtures/sample_cards/manifest.json`.
- **Static Detection (`tests/anticheat/test_static_invariants.py::test_no_answer_key_paths`):**
  - **Result: CAUGHT (PASS).**
  - AST checker flagged forbidden path token `'tests/fixtures/sample_cards/manifest.json'`.
- **Runtime Audit Hook Detection (`evals/harness/sut_boot.py::_install_anti_cheat_audit_hook`):**
  - **Result: CAUGHT (PASS).**
  - Audit hook immediately intercepted the file open event, terminated the process with exit code 86, and emitted:
    ```text
    CHEAT_DETECTED: Unauthorized access to test fixtures: tests/fixtures/sample_cards/manifest.json
    ```

---

## 5. Performance Bottleneck & Scale Stress Profiling (WP-G13)

### Verification of `evals/stress.py` Default Engine
- **Verification:** Inspection of `evals/stress.py:240-270` confirmed that `run_stress_test` defaults to `use_double: bool = False`, strictly invoking the production `GroupingEngine` with CV and scripted LLM verification.

### Profiling Results (`cProfile` on `run_stress_test(n_cards=20)`)
- **Total Wall-Clock Time:** 46.357 seconds (5,480,341 function calls)
- **Top Functions by Cumulative Time:**

| Cumulative Time | Self Time | Calls | Function / Location | Architectural Root Cause |
|---|---|---|---|---|
| **45.17s** | 0.004s | 1 | `recipeboxer.correlation.engine.GroupingEngine.group` | Overall grouping entry point |
| **44.70s** | 0.493s | 585 | `recipeboxer.correlation.evidence.compute_raw_features` | Pairwise raw feature calculation |
| **34.86s** (75.2%) | 0.044s | 585 | `recipeboxer.vision.physical_pairing.mirrored_wear_score` | Pairwise mirrored wear correlation |
| **31.97s** (68.9%) | 1.168s | 1,170 | `recipeboxer.vision.physical_pairing._extract_wear_field` | Stain blob extraction & perimeter damage |
| **27.40s** (59.1%) | 0.008s | 1 | `recipeboxer.correlation.background.DeckBackground.fit` | Background distribution fitting on deck pairs |
| **26.87s** (57.9%) | 0.029s | 3,510 | `scipy.ndimage.median_filter` | 9x9 RGB median filter on card faces |
| **26.17s** (56.4%) | 26.171s | 3,510 | `{built-in method scipy.ndimage._nd_image.rank_filter}` | Underlying C-extension rank filter |
| **17.66s** (38.1%) | 0.007s | 1 | `recipeboxer.correlation.screening.top_k_candidates` | Downstream pairwise candidate screening |
| **5.04s** (10.9%) | 0.282s | 585 | `recipeboxer.vision.physical_pairing.show_through_score` | Reverse-face show-through correlation |

### Diagnosis & Remediation Proposal
The ~29s–46s latency for 20 cards offline is **not caused by LLM calls or graph optimization** (which take < 0.1s total). It is caused by **un-cached computer vision calls**:
1. `_extract_wear_field(arr_rgb, gray)` runs three 9x9 median filters (one per RGB channel) for every card face.
2. Because `_extract_wear_field` is called inside `mirrored_wear_score(im_a, im_b)`, it is re-executed on every pair comparison ($O(N^2)$ invocations during background fitting and candidate screening). For N=20 cards (380 pairs), `_extract_wear_field` is called 1,170 times.
3. **Remediation:** Extract and cache `_extract_wear_field` and show-through edge representations on `VisualFingerprint` once per card face ($O(N)$ operations). When evaluating pairs, correlate the precomputed wear arrays directly using FFT convolution. This will reduce grouping execution time from ~46s to < 2.5s for 20 cards.

---

## 6. Extraction Pipeline & Prompt Analysis (WP-XA..XC)

1. **Prompt Isolation & Content Deny-List:**
   - Prompts in `recipeboxer/prompts/` were reviewed against `cardforge/templates_t0.py` and public text.
   - Finding: Mention of "Aunt Clara" (`transcribe_handwritten_v1.md:29`) and "Peach Cobbler" (`structure_v1.md:30`) reflects leakage of template/test titles. These must be replaced with neutral culinary phrases (e.g. "Grandmother's recipe", "Apple Pie").
2. **Grounding Verification Thresholds:**
   - `MAX_GROUNDING_CER = 0.25` is strictly enforced via semi-global dynamic programming in `recipeboxer/extraction/grounding.py:42`. Unmatched lines are correctly sent to a batched LLM support check and flagged with `GROUNDING_UNSUPPORTED` warnings.
3. **Recipe Normalization Invariant:**
   - `recipeboxer/extraction/normalize.py` strictly writes to `normalized_*` fields and preserves `original_text` verbatim. Promotional noise is properly moved to notes with `ADVERTISEMENT_NOISE` warnings rather than deleted.
4. **Inferred Title & `[GUESS]` Handling:**
   - `structure.py:97-105` detects and strips `[GUESS]` or `[inferred]` strings, flags `title_is_inferred = True`, and attaches an `INFERRED_TITLE` warning. However, `extractor.py:58` must be updated to stop prompting the model to append `[GUESS]`.

---

## 7. Grouping Engine Robustness & Provenance (WP-GA..GE)

1. **Homogeneous Collection Downweighting:**
   - `recipeboxer/correlation/background.py:330-415` computes `_compute_substrate_rarity()`. When stock fraction $\ge 0.80$, substrate log-likelihood ratios are dampened toward zero (`llr * 0.1`). Features with deck background variance below threshold return `log_lr = 0.0`, satisfying §9.1.
2. **Natural Sort vs Randomized Eval Filenames:**
   - Natural sort order (`natural_sort_index`) is strictly utilized as a sequence prior. On obfuscated evaluation decks, file names are randomized tokens (`secrets.token_hex(6)`), ensuring sort adjacency cannot be used to cheat.
3. **SetVerifier Label Randomization & Quote Validation:**
   - Candidate order is randomized using a SHA-256 seed derived from content hashes (`semantic.py:327-331`), eliminating position bias.
   - Unverified quotes reduce verification confidence by 50% and emit warnings (`semantic.py:481`).
4. **Calibration Provenance:**
   - `evals/calibration_fit.py:35-50` enforces `assert_not_forbidden_path()`, strictly prohibiting calibration fitting on `evals/corpora/t[1-4]` or vault files. `calibration_default.json` is verified as a conservative synthetic sigmoid prior.

---

## 8. Gate B Readiness Checklist

| Readiness Requirement | Standard / Specification | Status | Findings / Blockers |
|---|---|:---:|---|
| **Zero Mock-Sniffing in Production** | `INVARIANTS.md` §10 (INV-AC1, INV-AC6) | **BLOCKED** | **FIND-01**: `fingerprint.py`, `extractor.py`, `semantic.py`, `orchestrator.py` |
| **No Direct API Calls or Key Leakage** | `WAVE5_CONTRACTS.md` §1, Cost Ledger | **BLOCKED** | **FIND-02**: `fingerprint.py:637` leaked key in URL query string |
| **No Silent Extraction Fallbacks** | `INVARIANTS.md` §10 (INV-AC4) | **BLOCKED** | **FIND-03**: `headless.py` reports `"ok"` on `FailedRecipe` |
| **Calibrated Confidence Priors** | `REMEDIATION_PLAN.md` §9.1, INV-AC4 | **BLOCKED** | **FIND-04**: Hardcoded `0.85`/`0.90` confidences in `optimizer.py`/`engine.py` |
| **No Silent Exceptions / Fallbacks** | `INVARIANTS.md` §10 (INV-AC4) | **BLOCKED** | **FIND-05**: `engine.py` empty bytes; `consistency.py` swallowed errors |
| **CV Grouping Latency Scalability** | `REMEDIATION_PLAN.md` §9.1 (5), WP-G13 | **BLOCKED** | **FIND-06**: `mirrored_wear_score` median filter bottleneck (~46s for N=20) |
| **Anti-Cheat Canaries Active & Passing** | `tests/anticheat/` (10/10 passing) | **PASS** | Canaries caught both prompt leak and manifest access mutations |
| **Full Black-Box Test Suite Health** | 2,246+ tests (13 pre-existing excluded) | **PASS** | Suite clean; all in-scope Wave 5 tests passing |
| **Homogeneous Deck Downweighting** | `REMEDIATION_PLAN.md` §9.1 | **PASS** | `DeckBackground` dampens shared substrate LLR by 90% |
| **Black-Box SUT Isolation & Audit Hook** | `evals/harness/sut_boot.py`, INV-AC5 | **PASS** | Audit hook terminates unauthorized reads/imports with exit 86 |
| **Feedback Policy Intact** | `evals/report.py`, `evals/score.py` | **PASS** | Strict aggregate-only feedback for T1–T4 |
| **Frontend Security & Telemetry** | `frontend/src/` | **NEEDS FIX** | **FIND-07**: `SpendMeter` displays `$0.0000` on null spend |

---

## 9. Conclusion & Action Plan for Fixer

To achieve full Gate B compliance, the fixer assigned to Phase 0 remediation should execute the following focused actions:

1. **Delete `_call_gemini_api` and Query String Keys:** Remove `_call_gemini_api` and all `?key=` references in `recipeboxer/vision/fingerprint.py`. Route all fingerprint model calls through `create_llm_client()`.
2. **Purge Test Double Sniffing:** Strip all `endswith("Mock")` and `hasattr(..., "mock")` checks across `fingerprint.py`, `extractor.py`, `semantic.py`, and `orchestrator.py`. Ensure injected clients are handled via typed protocols.
3. **Propagate Extraction Failures in Headless:** Update `recipeboxer/headless.py` to inspect `r.status` and set `status = "extraction_failed"` when any recipe has failed.
4. **Precompute and Cache Wear Fields:** Cache `_extract_wear_field` on `VisualFingerprint` so median filters execute once per card ($O(N)$) rather than repeatedly inside `mirrored_wear_score` ($O(N^2)$).
5. **Calibrate Prior Confidence Defaults:** Replace arbitrary `0.85` / `0.90` confidence assignments with calibrated probabilities or set confidence to `None` when model verification is absent.
6. **Fix SpendMeter Null Handling:** Update `SpendMeter.tsx` to display `"Unknown"` or `"—"` when spend is null or ledger is not active.
