# WP-D2 — Final Documentation Update (Gate B Baseline & Remediation)

**Role:** Documentation agent (eval-cheat remediation lifecycle).  
**Base:** HEAD `f020add` (+ uncommitted `evals/baselines/honest_v1.json` and `honest_v1_outputs.tar.gz.enc`).  
**Rules Honoured:** No state-mutating git commands, `setup.sh` untouched, docs only (zero code changes), no API keys or live calls.

---

## 1. Summary of Changes

### 1. `evals/baselines/README.md`
- Added `honest_v1.json` entry to the baseline inventory table (Gate B certified post-uplift honest baseline across T1 d01–d03/d04, T2 held-out, and T3 real-web).
- Documented SUT commit (`6590f32`, WP-UP4), Scorer commit (`f020add`, WP-RT3FIX), quality profile (`balanced`), and model assignments (Gemini 3.8 Flash for main stages, Gemini 3.6 Flash for consistency).
- Added step-by-step instructions for decrypting `evals/baselines/honest_v1_outputs.tar.gz.enc` using maintainer `age` identity keys and re-scoring tiers with `evals.score`.
- Added held-out evaluation caveat: aggregate feedback on T3 during WP-UP4 means T3 is no longer strictly held-out; **Tier T2 is the cleanest held-out tier** for Gate B generalization claims.

### 2. `docs/EVALS.md`
- **SUT Sandbox Documentation (§3.2)**: Documented the strict SUT execution sandbox (`prepare_sut_sandbox` with private `HOME`, `TMPDIR`, and `XDG_*` directories; `PROJECT_CACHE_DIR` dropped) and the runtime audit hook's filesystem read allowlist and global deny roots/patterns (RT3-01).
- **Matched-Only vs. End-to-End Extraction Distinction (§5.2, §7.2)**: Clarified that matched-only metrics isolate extraction fidelity on correctly grouped recipes, while end-to-end metrics account for full pipeline performance (penalizing false splits, false merges, and omitted recipes).
- **Calibration Populations (§5.5, §7.2)**: Documented `ece_all` (headline ECE across all predicted recipes with confidence, penalizing unmatched predictions as $0\%$ accuracy) vs. `ece_matched` (diagnostic matched-only ECE), plus `confidence_coverage`.
- **Replaced all 'TBD after honest_v1' Placeholders (§7.1)**: Inserted certified `honest_v1` numbers across all grouping, extraction, and operational metrics.
- **Comprehensive v0 $\rightarrow$ v1 Comparison Table (§7.2)**: Added comparison across T1 (d01–d03), T1 (d04), T2 (held-out), and T3 (real-web) for grouping B-Cubed F1, false split/merge, ingredient F1, direction F1, hallucination/omission (end-to-end and matched-only), ECE (`ece_all` vs `ece_matched`), calls, and USD spend. Noted that `d04` and T2 exist only in `honest_v1`.
- **Milestone Certification Status (§7.3)**: Documented Gate A (`honest_v0.json`) and Gate B (`honest_v1.json`) completion.

### 3. `docs/ARCHITECTURE.md`
- **UP3/UP4 Grouping Pipeline Flow (§1, §5)**: Updated Mermaid diagram and narrative to reflect:
  - All-pairs text screening prefilter (`pre` model over all $N(N-1)$ ordered pairs)
  - Candidate pool ranking and adaptive $k$ screening ($k \in [3, 10], p \ge 0.02$)
  - Multi-modal `SetVerifier` with primary and back/continuation extension anchors (`set_verify_v1/v2`) under bounded concurrency
  - Domain support guard (`DomainSupport`) activating strict mode when $\ge 20\%$ of cards are out-of-distribution
  - Hard link gates (`nonrecipe`, `physical_conflict`, `new_heading`, `rescue_unconfirmed`, `strict_unconfirmed`) capping edge probability at `GATE_CAP = 0.02`
  - Fitted link model (`calibration_default.json["up3"]["models"]["link"]`)
  - Global minimum-cost path cover optimization (`path_cover` Hungarian)
  - Orphan rescue for unclaimed title-less cards (skipped in strict mode)
  - Proposal and recipe confidence models: standalone proposals scored by fitted `single` model, multi-card chains scored by fitted `multi` model; `recipes[].confidence` scored by fitted `recipe_confidence` model ($P(\text{grouped AND extracted correctly})$ or `None`, never a hardcoded constant prior)
- **Error Mapping & Retry Policy (§2.1, §2.2)**:
  - Exception classification (`_classify_error`): 429 (`RATE_LIMITED`), 408/504/timeouts (`TIMEOUT`), 5xx/transport/SSL/empty-message exceptions (`MODEL_UNAVAILABLE`, retryable), 4xx client errors (terminal).
  - Empty-response policy: blank or whitespace-only model responses always force $\ge 2$ attempts (`MIN_ATTEMPTS_ON_EMPTY = 2`); terminal errors typed `INVALID_RESPONSE` with `retryable = True`.
  - Stage-level retry: `generate_with_empty_retry` wraps transcribe/structure/repair with 2.0s backoff and doubled `max_output_tokens`.
  - Single grouping path: `ConcurrentGroupingEngine` documented as a docstring-only subclass of `GroupingEngine.group`.

### 4. `docs/ROADMAP.md`
- **Gate B Done Criteria Certified (§2)**: Summarized achievement of all four Gate B criteria (T2 held-out generalization beating baseline, `d04` homogeneous scaling in $O(N \cdot k)$, Red-Team #3 verification, and `honest_v1.json` committed).
- **Updated Post-Gate B Backlog (§3, §4)**:
  - **`d04` Same-Stock False Split (`0.5664`)**: Identified as the **#1 top quality item**; detailed root cause (`SetVerifier` rejecting true back $\rightarrow$ next-front links) and action plan.
  - **T3 Real-Manuscript Extraction & Title CER**: Documented historical handwriting challenges (T3 title CER `0.4840` matched / `0.5665` e2e).
  - **T3 Recipe ECE (`0.2844`) & Occasional `INVALID_RESPONSE`**: Documented under-calibration on archival layouts and stage failure resilience.
  - **T3 Ground-Truth Human Spot-Check**: Tracked pending ~20% maintainer spot-check of structured ground truth.
  - **Tier T4 Real User Photo Intake**: Tracked smartphone family recipe intake via `evals/intake_real.py`.
  - **CI Secrets Provisioning**: Outlined maintainer configuration of `CI_AGE_IDENTITY` and `CI_GEMINI_API_KEY`.
  - **Dual Roadmap Architecture**: Explicitly documented that root `ROADMAP.md` (canonical M1–M16 product backlog) and `docs/ROADMAP.md` (eval remediation) co-exist intentionally and must **not** be merged.
  - **Known Test Failures**: Documented 13 pre-existing platform failures (6 Windows zip-slip, 7 headless Tkinter) plus the `test_setup_sh` environmental path issue.

### 5. `README.md`
- Audited the headline Evaluation Honesty Note. Confirmed it contains no quantitative numbers, so it remains accurate without edits.

---

## 2. File Verification & Test Invariants
- `git status`: Clean working tree modifications strictly limited to docs files (`evals/baselines/README.md`, `docs/EVALS.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, and this report `_agents/remediation/WP-D2.md`).
- `setup.sh`: Untouched.
- `honest_v1.json` and `honest_v1_outputs.tar.gz.enc`: Untouched.
