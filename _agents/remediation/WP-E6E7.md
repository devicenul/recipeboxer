# Work Package Note: WP-E6 & WP-E7

## 1. Summary
Delivered the Wave 3 LLM-as-a-Judge semantic evaluator (`evals/judge.py`) and Evaluation Reporting & Regression framework (`evals/report.py`) adhering strictly to `WAVE3_CONTRACTS.md` (§6), `REMEDIATION_PLAN.md` (§3.2, §6, §7.3), and anti-cheat invariants (INV-AC1..INV-AC6).

- **WP-E6 (`evals/judge.py`)**:
  - `Judge` protocol: `async def judge(self, pred: dict, gold: dict) -> dict` returning `{"score": 0.0-5.0, "rationale": str}`.
  - `LiveJudge`: Blinded evaluation (no model/system names, randomized field order, gold labelled strictly 'reference' and pred strictly 'candidate'). Calls `LLMClient.generate` under `LLMStage.JUDGE` with structured Pydantic response schema (`JudgeResultSchema`) and automatic [0.0, 5.0] score clamping.
  - Model tier independence: Enforces different model tiers between extractor and judge (`flash` vs `pro`), raising `ValueError` on identical tiers.
  - Offline guardrails: `create_judge()` returns `None` when offline/unkeyed, never substituting synthetic scores. `PlumbingJudge` is strictly restricted to test harnesses, labelling all outputs `'mock (not a quality score)'`. Judge scores are reported and never gate.
- **WP-E7 (`evals/report.py`, `config/pricing.toml`, `evals/baselines/README.md`)**:
  - `render()`: Outputs both JSON and GitHub Flavored Markdown. Enforces feedback policy: tier `T0` permits `per_item` feedback; tiers `T1`–`T4` strictly prohibit `per_item` (raises `ValueError`) and strip all per-item/per-image fields. Renders `N/A` when status is `no_live_model`.
  - `compare()`: Evaluates reports against stored baselines with directional thresholds (higher-better vs lower-better) and per-metric tolerances. Returns regression error list (`[]` on pass).
  - CLI: `python -m evals.report compare REPORT BASELINE` exiting 0 on pass and 1 on regression.
  - `config/pricing.toml`: User-editable model pricing table with placeholder values clearly designated `UNVERIFIED` (for WP-B0 to verify).
  - Baseline documentation: Updated `evals/baselines/README.md` with `honest_v0.json` specifications and `compare` CLI usage.

---

## 2. Files Changed & Owned
- `evals/judge.py` (new)
- `evals/report.py` (new)
- `config/pricing.toml` (new)
- `evals/baselines/README.md` (updated)
- `tests/test_wp_e6_judge.py` (new)
- `tests/test_wp_e7_report.py` (new)
- `_agents/remediation/WP-E6E7.md` (this note)

---

## 3. Verification Commands & Results

All tests executed offline in the project venv with `timeout` and no API keys:

1. **WP-E6 Test Suite**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_e6_judge.py -q -p no:randomly
   ```
   *Result:* `9 passed in 0.09s`

2. **WP-E7 Test Suite**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_e7_report.py -q -p no:randomly
   ```
   *Result:* `14 passed in 0.24s`

3. **Combined E6 + E7 Test Suite**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_e6_judge.py tests/test_wp_e7_report.py -q -p no:randomly
   ```
   *Result:* `23 passed in 0.31s`

4. **Static Anti-Cheat Invariants**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat/test_static_invariants.py -q -p no:randomly
   ```
   *Result:* `4 passed in 1.45s`

5. **CLI Verification**:
   ```bash
   $py -m evals.report --help
   ```
   *Result:* Exited 0 with compare/render subcommands displayed.

---

## 4. Contract Deviations
None. Implements all interfaces and protocols specified in `WAVE3_CONTRACTS.md` §6 and `REMEDIATION_PLAN.md` §3.2, §6, §7.3.

---

## 5. Requests for Other Work Packages
- **WP-B0 (Cost calibration)**: Calibrate official input, output, and thinking token pricing from Google's official pricing page into `config/pricing.toml` and update `verified_on`.
- **WP-E3E4 (Scoring harness)**: Integrate `evals.judge.create_judge` and `evals.report.render` / `compare` into `evals/score.py`.

---

## 6. Risks
- **Free-Tier Model Rate Limits**: `LiveJudge` invokes additional LLM calls on a separate model tier (`gemini-3.7-pro` when extractor is `flash`). On free-tier API keys, RPM token bucket limits may increase run latencies. `LiveJudge` is non-gating and reports qualitative metrics only.
