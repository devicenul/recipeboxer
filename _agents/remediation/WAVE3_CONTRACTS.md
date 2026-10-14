# Wave 3 Interface Contracts (authoritative for all wave-3 agents)

Branch: `remediation/phase0`. Plan: `_agents/remediation/REMEDIATION_PLAN.md` (§3, §3.4, §6, §6.1, §9.1, §10).
If you need to deviate from a contract, write the deviation prominently in your WP note under
"Contract deviations" and keep a compatibility shim; do not silently change shapes.

## 0. Common rules (every agent)

- Repo: `/storage/emulated/10/AntiGravity/Workspaces/recipeboxer`. `run_command` Cwd = repo.
- Python: `PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"`;
  `py="$PROJECT_CACHE_DIR/venv/bin/python"`. pytest:
  `PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" <targets> -q -p no:randomly`.
  **Always wrap test/long commands in `timeout`** (pytest-timeout is not installed). Any `asyncio` wait in tests
  must use `asyncio.wait_for(..., timeout=...)`. Never create caches/venvs/build dirs in the workspace.
- Unset `GEMINI_API_KEY` / `RECIPEBOXER_GEMINI_API_KEY` for tests. No live API key exists yet; all work is offline.
- **NEVER run state-mutating git commands** (add, commit, stash, checkout, restore, reset, switch, merge, rebase,
  clean, mv, rm). Use plain shell `cp`/`rm` for file moves. The orchestrator commits.
- Never modify `setup.sh`. Only edit files you own. Requests for other files go in your WP note.
- Run ONLY targeted tests, never the full suite (other agents are working concurrently).
- Never read other agents' conversation logs under `.gemini/antigravity/brain/`.
- Tests you add: `tests/test_wp_<id>_*.py` (or the location stated in your task). Use
  `recipeboxer.ai.replay.ScriptedLLMClient` or explicit local mocks keyed by call order / explicit maps.
  **Never use the conftest `mock_gemini_client` fixture in new or rewritten tests**, never key behaviour on
  filenames or image hashes, never assert accuracy numbers offline.
- Some tests rewrite `recipeboxer/api/static/index.html`; if `git diff --stat` shows it changed, tell the
  orchestrator in your note (do not restore it yourself).
- Deliver `_agents/remediation/WP-<id>.md`: summary, files changed, commands + results, contract deviations,
  requests for other WPs, risks. Final reply ≤ 250 words.

## 1. Corpus layout

```
evals/corpora/<tier>/<deck_id>/<image_id>          # T1 committed images (opaque names, e.g. 7f3a9c1e.jpg)
evals/vault/<tier>.gt.enc                           # age-encrypted GT JSON (section 2), all recipients
evals/vault/t2.images.tar.enc                       # T2 images (tar), encrypted; never committed in plaintext
evals/vault/<tier>_<deck>.spec.enc                  # optional encrypted generation spec for large decks
evals/corpora/<tier>/DECKS.json                     # PUBLIC deck list: [{deck_id, n_images}] - NO deck_type, NO GT
```
Image ids are opaque, random (`secrets.token_hex(6)` + extension). Deck directory names are opaque too
(`d01`, `d02`, …) and carry no deck_type.

## 2. Ground-truth JSON (plaintext only in memory; encrypted with `evals.vault.encrypt_bytes`)

```json
{
  "schema": "rb-gt/1", "tier": "T1", "created": "ISO8601", "generator": {"name": "cardforge", "version": "..."},
  "decks": [{
    "deck_id": "d01",
    "deck_type": "homogeneous|mixed|near_duplicate|large|real_distractor",
    "images": [{"image_id": "7f3a9c1e.jpg", "sha256": "...", "modality": "HANDWRITTEN_CURSIVE|TYPEWRITTEN|CLIPPING|MIXED", "stock_id": "s3"}],
    "recipes": [{
      "recipe_id": "r001",
      "faces": ["7f3a9c1e.jpg", "a1b2c3d4.jpg"],          // reading order
      "verbatim": ["text of face 1 exactly as rendered", "face 2"],
      "structured": {
        "title": "str", "yield": "str|null", "source_note": "str|null",
        "ingredients": [{"raw": "1 1/2 c. flour", "quantity": "1 1/2", "unit": "c.", "item": "flour", "section": "str|null"}],
        "directions": ["step 1", "step 2"], "notes": ["str"]
      },
      "difficulty": {"legibility": "good|fair|poor", "faces": 2}
    }]
  }]
}
```
Every image belongs to exactly one recipe (singleton recipes are standalones).

## 3. SUT run output (`python -m evals.harness.run`) - contains NO GT

CLI: `python -m evals.harness.run --tier T1 --out DIR [--decks d01,d02] [--images-dir PATH] [--oracle-plan FILE] [--variant opaque|misleading|shuffle|reencode]`
writes `DIR/outputs.json`, `DIR/ledger.json`, `DIR/run.log`:
```json
{
  "schema": "rb-out/1", "run_id": "...", "tier": "T1", "git_commit": "...", "track": "end_to_end|oracle_grouping",
  "variant": "opaque", "model_profile": "balanced", "live": false,
  "decks": [{
    "deck_id": "d01",
    "recipes": [{"pred_id": "p1", "image_ids": ["7f3a9c1e.jpg", "a1b2c3d4.jpg"], "recipe": { "...RecipeSchema model_dump()" }, "confidence": 0.8, "flags": []}],
    "unassigned_image_ids": [],
    "failures": [{"stage": "...", "reason": "...", "image_ids": [], "message": "..."}]
  }],
  "status": "ok|no_live_model|cheat_detected|error"
}
```
`image_ids` are the corpus image ids (the runner maps its random temp names back AFTER the SUT exits).
`ledger.json`: `{"calls": [{"stage","model","input_tokens","output_tokens","thinking_tokens","latency_ms","attempts"}], "totals": {...}}`.
The SUT subprocess: scrubbed env (no `RB_EVAL_*`, no `*AGE*` vars; only an explicit allow-list incl. the Gemini key var when live),
cwd = temp dir, a `sys.addaudithook` that aborts with exit code 86 and `CHEAT_DETECTED` on any open of a path
under `evals/`, `tests/fixtures`, `*.gt.enc`, `*.enc`, `manifest.json` (outside the temp dir), or the age key file.
Without a live key, status = `no_live_model` and metrics downstream are `N/A`.

Oracle track: the trusted side (`python -m evals.score oracle-plan --tier T1 --out plan.json`) writes
`{"decks":[{"deck_id","sets":[["imgA","imgB"], ...]}]}` with sets in random order and faces shuffled; no text.

## 4. Scorer (`python -m evals.score`)

`python -m evals.score --tier T1 --outputs DIR --report DIR [--feedback aggregate|per_item] [--compare evals/baselines/X.json]`
- Decrypts GT via `evals.vault.decrypt_bytes` (identity from `RB_EVAL_AGE_KEY_FILE` or default path).
- `per_item` allowed only for tier T0; T1–T4 are aggregate + per-category (deck_type, modality, faces count) only.
- Writes `report.json` + `report.md`.

## 5. Metrics API (`evals/metrics/`, owned by WP-E5; scorer imports these)

```python
# evals/metrics/grouping.py
def score_grouping(pred: list[list[str]], gold: list[list[str]]) -> dict
    # keys: bcubed_p, bcubed_r, bcubed_f1, ari, exact_cluster_acc, order_kendall_tau,
    #       standalone_acc, false_merge_rate, false_split_rate, n_pred, n_gold
def match_recipes(pred: list[list[str]], gold: list[list[str]]) -> list[tuple[int|None, int|None, float]]
    # Hungarian on card-overlap IoU; unmatched -> (i, None, 0) / (None, j, 0)
# evals/metrics/extraction.py
def score_recipe(pred: dict | None, gold_structured: dict, gold_verbatim: list[str] | None = None) -> dict
    # keys: title_cer, ingredient_p/r/f1 (line Hungarian, CER<=0.1), qty_unit_exact, direction_f1,
    #       direction_tau, field_cer{...}, hallucination_rate, omission_rate, illegible_precision
def aggregate(rows: list[dict], by: list[str] | None = None) -> dict
# evals/metrics/calibration.py
def ece(confidences: list[float], correct: list[bool], n_bins: int = 10) -> float
# evals/metrics/ops.py
def summarize_ledger(ledger: dict, pricing: dict | None = None) -> dict
```
All functions pure, deterministic, no I/O, no recipeboxer imports.

## 6. Judge & reports (`evals/judge.py`, `evals/report.py`, owned by WP-E6/E7)

```python
# evals/judge.py
class Judge(Protocol): async def judge(self, pred: dict, gold: dict) -> dict   # {"score": 0-5, "rationale": str}
def create_judge(settings, extractor_model: str) -> Judge | None   # None if no key; MUST use a different model tier than extractor
# evals/report.py
def render(report: dict, feedback: str) -> tuple[str, str]          # (json_text, markdown)
def compare(report: dict, baseline: dict, tolerances: dict | None = None) -> list[str]  # regressions; empty = pass
```

## 7. Production headless entry point (owned by WP-E3)

`python -m recipeboxer analyze --input DIR --out FILE.json --auto-approve [--profile balanced]`
Runs the real production pipeline (scan → fingerprint → correlate → extract) with HITL auto-approve and writes
predictions JSON (recipes with their image file names + StageFailures + ledger). This is the ONLY interface the
harness uses to execute the SUT.

## 8. Ownership map (wave 3)

| Agent | Owns |
|---|---|
| WP-E3E4 | `evals/harness/**`, `evals/score.py`, `evals/scoring/**`, `evals/run_benchmarks.py`, delete `recipeboxer/evals/**` (at the END), `recipeboxer/cli.py`, `recipeboxer/__main__.py`, `recipeboxer/headless.py` (new), `tests/fixtures/loader.py`, `tests/anticheat/test_canaries.py`, `tests/anticheat/test_static_invariants.py` (exclusion constant + import-graph check only), tests: `test_eval_runner.py`, `test_eval_obfuscation.py`, `test_challenger_m2_obfuscation_deep.py`, `test_challenger_m2_prompt_leak.py`, `test_wp_c6_harness.py`, `tests/test_wp_e3_*.py`, `tests/test_wp_e4_*.py` |
| WP-E5 | `evals/metrics/**`, `tests/test_eval_metrics.py`, `tests/test_wp_e5_*.py` |
| WP-E6E7 | `evals/judge.py`, `evals/report.py`, `evals/baselines/README.md`, `config/pricing.toml` (new), `tests/test_wp_e6_*.py`, `tests/test_wp_e7_*.py` |
| WP-E2R3 | `evals/variants.py`, `evals/intake_real.py`, `tests/test_wp_e2_*.py`, `tests/test_wp_r3_*.py` |
| Curator E1 | `cardforge/**`, `evals/curation/**`, `evals/corpora/t1/**`, `evals/vault/t1*.enc`, `evals/vault/t2*.enc` |
| Curator R2 | `evals/corpora/t3/**`, `evals/corpora/real_web/ATTRIBUTION.md`, `evals/curation_t3/**`, `evals/vault/t3*.enc` |
| WP-CI1 | `.github/**`, `docs/CI.md` |
| Triager A/B/C | the test files listed in their task; Triager A also `tests/conftest.py` |
| Orchestrator only | `evals/triage.yaml` - read-only for agents; triagers list per-nodeid outcomes (deleted / rewritten / moved_live) in their WP note and the orchestrator updates the file |
