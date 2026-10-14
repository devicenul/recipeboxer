# RED-TEAM Challenger Report: Gate A Pre-Check Audit (REDTEAM-1)

**Audit Date:** 2026-10-14  
**Target Branch:** `remediation/phase0`  
**Target Commit:** `f724e1d` (Wave 3 merged)  
**Auditor Role:** Independent Red-Team Challenger (Untrusted Reviewer)  
**Execution Environment:** Android Terminal Linux VM (Python 3.13.5, pytest 9.1.1)

---

## 1. Executive Summary

As the Red-Team Challenger for the RecipeBoxer eval-cheat remediation (Gate A pre-check), an adversarial security, integrity, and anti-cheat audit was conducted across production code (`recipeboxer/**`), evaluation harness and scoring (`evals/**`), synthetic generation (`cardforge/**`), test suites (`tests/**`), corpora, and CI workflows (`.github/workflows/**`).

### Key Findings Summary:
- **Total Findings:** 9
- **Blockers (Gate A criteria unmet):** 2
- **Major (integrity/correctness/cheat-detection gaps):** 4
- **Minor (hygiene/consistency items):** 3

### Core Takeaways:
1. **Procedural Secrecy & Vault Architecture:** Ground-truth segregation is well-engineered. Corpora images in T1 and T3 are strictly content-addressed with opaque 12-hex filenames (`secrets.token_hex(6)`), `DECKS.json` publishes only deck IDs and image counts without leaking `deck_type`, and answer keys exist only as `age`-encrypted vault files (`*.gt.enc`). No plaintext ground truth was committed.
2. **Canary 4 Blind Spot (Major):** When a test client is injected into `run_headless_analysis`, `HeadlessClientAdapter` replaces the production extractors and constructs `LLMRequest` payloads with empty prompt text (`ImagePart` only). Consequently, `test_canary_prompt_leak_scan` tests empty strings and vacuously passes without ever inspecting production prompt templates.
3. **`optimizer.py` TypeError & Fabricated 0.85 (Major):** In `recipeboxer/correlation/optimizer.py`, `getattr(prop, "semantic_score", prop.confidence)` returns `None` if `semantic_score` is explicitly `None`, causing `sum()` to crash with `TypeError`. Additionally, empty sequence transitions fall back to a hardcoded `0.85` confidence, violating INV-AC4.
4. **Headless Output Hardcoded Confidence (Major):** `run_headless_analysis` emits all predicted recipes with `"confidence": 1.0`, blinding the downstream calibration (ECE) metric.
5. **Audit Hook Event Coverage (Major):** `evals/harness/sut_boot.py` only intercepts Python `open` events. It does not monitor `os.scandir`, `os.listdir`, `subprocess.Popen`, or package imports.
6. **Gate A Readiness Blockers:** Gate A cannot be certified until (a) live model cost calibration (WP-B0) is run and `honest_v0` baseline is recorded on T1–T3, and (b) T3 structured ground truth is drafted and spot-checked (~20%).

---

## 2. Findings Inventory

| ID | Severity | File:Line | Issue Description | Suggested Fix | Owner WP |
|---|---|---|---|---|---|
| **FIND-01** | **Blocker** | `evals/baselines/` | **Missing `honest_v0` baseline and WP-B0 cost calibration.** `evals/baselines/` contains only `pre_remediation.json` (contaminated). `config/pricing.toml` has all `0.0` unverified rates. Live baseline on T1, T2, T3 not run. | Execute WP-B0 with live Gemini API key, update pricing table, set eval caps A–D, and record `honest_v0.json`. | Orchestrator / WP-B0 |
| **FIND-02** | **Blocker** | `evals/curation_t3/`, `WP-R2.md:25` | **T3 ground truth has `null` structured GT; spot-check unperformed.** T3 vault contains verbatim text but structured GT is set to `null` with `"pending_text_only_draft"`. Maintainer spot-check (~20%) has not occurred. | Run `draft_structured.py` with independent LLM tier, conduct spot-check via `spotcheck.py`, and re-encrypt `t3.gt.enc`. | Curator R2 / Orchestrator |
| **FIND-03** | **Major** | `recipeboxer/headless.py:150-160, 218-237`, `tests/anticheat/test_canaries.py:331-344` | **Canary 4 (`test_canary_prompt_leak_scan`) is a paper tiger.** `HeadlessClientAdapter` replaces production extractors and creates `LLMRequest` with zero prompt text (`ImagePart` only). `test_canary_prompt_leak_scan` scans empty string `""` which always passes. Production prompts are never evaluated. | In `HeadlessClientAdapter`, include production system instructions and prompt text (or invoke production extractors with scripted transport). Add static AST prompt-leak scanner. | WP-E3E4 |
| **FIND-04** | **Major** | `recipeboxer/correlation/optimizer.py:137, 170, 180` | **`optimizer.py` crashes on `semantic_score=None` and fabricates `0.85` confidence.** `getattr(prop, "semantic_score", prop.confidence)` returns `None` if the field is `None`, causing `sum(s_scores)` to crash with `TypeError`. Additionally, empty transition score lists fall back to hardcoded `0.85`. | Use `prop.semantic_score if prop.semantic_score is not None else prop.confidence`. Default empty scores to `0.0` or genuine input confidence instead of hardcoding `0.85`. | WP-C2 / WP-E3E4 |
| **FIND-05** | **Major** | `recipeboxer/headless.py:503` | **Headless output hardcodes recipe `"confidence": 1.0`.** Every recipe written to `outputs.json` receives hardcoded `confidence: 1.0`, violating INV-AC4 and blinding calibration metric `ece(confidences, correct)`. | Propagate genuine proposal confidence or recipe confidence score from pipeline session into `recipes_out`. | WP-E3E4 |
| **FIND-06** | **Major** | `evals/harness/sut_boot.py:21-22` | **Audit hook intercepts only `open` event, omitting directory listings, subprocesses, and imports.** `os.listdir("evals")`, `os.scandir("evals")`, `subprocess.run(["cat", ...])`, and `import cardforge` succeed without tripping exit code 86. SUT also receives `repo_root` via `sys.argv[0]` and `PYTHONPATH`. | Expand `_audit_hook` to intercept `os.scandir`, `os.listdir`, `subprocess.Popen`, and `import` events matching forbidden paths. | WP-E3E4 |
| **FIND-07** | **Minor** | `recipeboxer/headless.py:325-334`, `recipeboxer/agent/orchestrator.py:127-138` | **`has_usable_gemini_key` accepts mock keys (`mock`, `mock-key`).** Unlike `extractor.py:374` and `fingerprint.py:149` which reject `startswith("mock")`, `headless.py` and `orchestrator.py` treat mock strings as usable keys, causing late stage failures instead of clean early blocked states. | Align `has_usable_gemini_key` to require `k and not str(k).strip().lower().startswith("mock")`. | WP-E3E4 / WP-C7 |
| **FIND-08** | **Minor** | `recipeboxer/vision/router.py:273, 283, 293, 386, 395, 404, 412` | **Heuristic modality classifier assigns hardcoded confidences (`0.90`–`0.96`).** Heuristic rule checks assign static high confidence numbers rather than calibrated or continuous metrics. | Label these as heuristic estimates or calculate continuous confidence based on feature margin/SNR. | WP-C3 |
| **FIND-09** | **Minor** | `evals/INTAKE.md:58-80` | **`INTAKE.md` user guide contains "Lemon Bars" recipe text in markdown documentation.** While provided only as documentation for T4 contributors, "Lemon Bars" was historically one of the canned recipes. | Replace the documentation example with a fictitious recipe (e.g. "Starlight Berry Compote") to prevent keyword confusion. | Curator R3 / WP-E2R3 |

---

## 3. Canary Mutation Verification Results

Adversarial mutations were reintroduced outside the workspace in `$PROJECT_CACHE_DIR/redteam/` to evaluate whether `tests/anticheat/` detects reintroduced eval cheats.

### Mutation Experiment 1: Reintroducing Filename Semantics
- **Mutation:** In `recipeboxer/vision/fingerprint.py`, mutated line 411:
  ```python
  role = CardRole.SEQUENCE_START if "front" in card_id else CardRole.UNKNOWN
  ```
- **Static Detection (`tests/anticheat/test_static_invariants.py`):**
  - **Result: CAUGHT (PASS).**
  - AST check `test_no_filename_semantics` immediately caught the mutation:
    ```text
    Line 411: Semantic token {'front'} tested against filename var {'card_id'}
    ```
- **Runtime Detection (`tests/anticheat/test_canaries.py`):**
  - **Result: MISSED (FAIL).**
  - `test_canary_rename_invariance` passed because `HeadlessClientAdapter` completely replaces `GeminiVisualExtractor` when an injected client is provided. `fingerprint.py` was never executed.

### Mutation Experiment 2: Reintroducing Manifest Read
- **Mutation:** In `recipeboxer/extraction/extractor.py`, inserted:
  ```python
  manifest = open("tests/fixtures/sample_cards/manifest.json").read()
  ```
- **Static Detection (`tests/anticheat/test_static_invariants.py`):**
  - **Result: CAUGHT (PASS).**
  - AST check `test_no_answer_key_paths` immediately caught the forbidden tokens:
    ```text
    Line 1: Contains forbidden string token 'tests/fixtures'
    Line 1: Contains forbidden string token 'sample_cards'
    ```
- **Runtime Detection (`tests/anticheat/test_canaries.py`):**
  - **Result: MISSED (FAIL).**
  - Because `HeadlessClientAdapter` replaces `RecipeExtractor`, the mutated line in `extractor.py` was not executed during `test_canaries.py`.

### Mutation Experiment 3: Canary 4 Prompt Leak Detection
- **Mutation:** Checked whether `test_canary_prompt_leak_scan` detects forbidden strings in prompts.
- **Analysis:**
  - `HeadlessClientAdapter` passes only `ImagePart(data=image_bytes)`.
  - `req.system_instruction` is `None` and `req.parts` contains zero `TextPart`s.
  - Variable `full_prompt_text` in `test_canaries.py` is empty `""`.
  - The assertion `assert token.lower() not in full_prompt_text` is vacuously true.
  - **Result: Canary 4 does not evaluate production prompts.**

### Mutation Experiment 4: Audit Hook Trip
- **Action:** Executed `test_canary_audit_hook_trip`.
- **Result: CAUGHT (PASS).**
- Subprocess attempting to open `evals/vault/T1.gt.enc` was immediately terminated by `sys.addaudithook` with exit code 86 and emitted `CHEAT_DETECTED`.

---

## 4. GitHub Actions CI SHA & Security Verification

All GitHub Actions workflows (`.github/workflows/ci.yml`, `eval-run.yml`, `eval-score.yml`) were verified for security boundaries, untrusted code isolation, and pinned commit SHAs.

### 4.1 Workflow Security Controls
- **`pull_request_target` Absence:** Verified absent across all workflows.
- **Secret Segregation:** Untrusted job `run-sut` (`eval-run.yml`) receives only `CI_GEMINI_API_KEY` (gated by `eval-live` environment protection and `run-eval` label) and has zero vault secrets. Trusted job `score` (`eval-score.yml`) triggers via `workflow_run`, checks out `ref: ${{ github.event.repository.default_branch }}` (never PR head), and holds `CI_AGE_IDENTITY`.
- **Untrusted Artifact Guardrails:** `eval-score.yml` strictly parses JSON artifacts using Python without shell interpolation, validates file sizes, enforces strict identifier regex (`^[a-zA-Z0-9_.-]{1,128}$`), and securely shreds the age key file upon completion.

### 4.2 Pinned Action Commit SHAs Verification Table

Each pinned action commit SHA was verified against the official repository release tags via live web inspection on GitHub:

| Action Repository | Claimed Tag | Pinned Commit SHA | Live Web Verification URL | Verified Match? |
|---|---|---|---|---|
| `actions/checkout` | `v4.2.2` | `11bd71901bbe5b1630ceea73d27597364c9af683` | [actions/checkout@v4.2.2](https://github.com/actions/checkout/releases/tag/v4.2.2) | **YES (Confirmed)** |
| `actions/setup-python` | `v5.4.0` | `42375524e23c412d93fb67b49958b491fce71c38` | [actions/setup-python@v5.4.0](https://github.com/actions/setup-python/releases/tag/v5.4.0) | **YES (Confirmed)** |
| `actions/setup-node` | `v4.2.0` | `1d0ff469b7ec7b3cb9d8673fde0c81c44821de2a` | [actions/setup-node@v4.2.0](https://github.com/actions/setup-node/releases/tag/v4.2.0) | **YES (Confirmed)** |
| `actions/upload-artifact` | `v4.6.1` | `4cec3d8aa04e39d1a68397de0c4cd6fb9dce8ec1` | [actions/upload-artifact@v4.6.1](https://github.com/actions/upload-artifact/releases/tag/v4.6.1) | **YES (Confirmed)** |
| `actions/download-artifact` | `v4.1.9` | `cc203385981b70ca67e1cc392babf9cc229d5806` | [actions/download-artifact@v4.1.9](https://github.com/actions/download-artifact/releases/tag/v4.1.9) | **YES (Confirmed)** |
| `actions/github-script` | `v7.0.1` | `60a0d83039c74a4aee543508d2ffcb1c3799cdea` | [actions/github-script@v7.0.1](https://github.com/actions/github-script/releases/tag/v7.0.1) | **YES (Confirmed)** |

---

## 5. Gate A Readiness Checklist

| Gate A Item | Requirement | Status | Notes |
|---|---|---|---|
| **Anti-Cheat Static Invariants** | `tests/anticheat/test_static_invariants.py` passes 100% | **READY** | All 4 tests pass (INV-AC1, INV-AC2, INV-AC3, INV-AC4). |
| **Canary Tests Suite** | `tests/anticheat/test_canaries.py` passes 100% | **READY (WITH FIND-03)** | All 6 tests pass, but Canary 4 exercises adapter rather than production prompts. |
| **Test Triage Remediated** | All tests in triage inventory rewritten, deleted, or moved to `@live` | **READY** | All 1,633+ tests cleanly handled across WP-C8-A/B/C. Zero offline accuracy assertions. |
| **Vault Tooling & Key Setup** | Multi-recipient age encryption (`pyrage`) working | **READY** | `evals/vault.py` verified with maintainer key in `~/.config/recipeboxer/age.key`. |
| **Synthetic Card Generator** | Isolated in `cardforge/` with zero `recipeboxer` imports | **READY** | Top-level package, pure models, tests green. |
| **T1 Dev-Eval Corpus** | 4 decks (386 cards) committed with opaque 12-hex IDs and encrypted GT | **READY** | `evals/corpora/t1/`, `DECKS.json` clean, `t1.gt.enc` valid. |
| **T2 Held-Out Corpus** | Encrypted tar images and encrypted GT | **READY** | `t2.images.tar.enc` and `t2.gt.enc` committed. |
| **T3 Real-Web Corpus** | 26 archival images committed with encrypted GT | **BLOCKED (FIND-02)** | Verbatim text encrypted, but structured GT is `null` (`pending_text_only_draft`) and spot-check unperformed. |
| **Black-Box Harness** | SUT executes in temp dir with scrubbed env and reverse ID mapping | **READY** | Subprocess isolation, reverse ID mapping post-run verified. |
| **Scorer & Metrics** | Hungarian IoU matching, B-Cubed, CER, F1, feedback policy | **READY** | Pure functions in `evals/metrics/`, per-item forbidden on T1–T4. |
| **CI Workflows** | Two-workflow split, untrusted runner, verified pinned action SHAs | **READY** | `.github/workflows/{ci,eval-run,eval-score}.yml` verified. |
| **Cost Calibration (WP-B0)** | Calibration run on live model to measure USD cost per card/recipe | **BLOCKED (FIND-01)** | Not yet executed; `config/pricing.toml` unverified. |
| **Eval Budget Caps** | Maintainer sets caps A–D based on calibration report | **BLOCKED (FIND-01)** | Awaiting WP-B0 results. |
| **Honest Baseline (`honest_v0`)** | Live benchmark baseline recorded on T1, T2, T3 | **BLOCKED (FIND-01)** | Awaiting live Gemini key and WP-B0 caps. |

---

## 6. Recommendations for Orchestrator Ahead of Gate A

1. **Fix FIND-04 (`optimizer.py`) immediately**: Replace line 137 with `s_score = prop.semantic_score if prop.semantic_score is not None else prop.confidence` and eliminate fabricated `0.85` fallbacks.
2. **Fix FIND-05 (`headless.py`) immediately**: Propagate actual proposal/session confidence into `recipes_out` instead of hardcoding `1.0`.
3. **Fix FIND-03 (`HeadlessClientAdapter`)**: Ensure canary prompt checks evaluate production prompts, or add an AST/source prompt scanner that asserts no sample tokens appear in `FINGERPRINT_SYSTEM_INSTRUCTION` or prompt constants.
4. **Complete T3 Structured GT Draft & Spot-Check**: Execute `draft_structured.py` and maintainer spot-check (~20%) to enable T3 extraction scoring.
5. **Run WP-B0 & Record `honest_v0`**: Provide `GEMINI_API_KEY`, run calibration, populate `config/pricing.toml`, set caps, and execute the live baseline run.
