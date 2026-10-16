# WP-T3A — Deactivate the antique T3 tier (archive as opt-in T3A)

Branch `remediation/phase0`, base HEAD `02af3b7`. No git state changes (plain `mv`); the orchestrator commits.
No API keys used, no live Gemini calls.

## User decisions recorded
- The antique T3 set (25 real 17th-century English manuscript recipes, Folger/Wellcome via FromThePage) is
  **out of scope**: the product targets recipes from the 1900s onward. It is **deactivated, not deleted**.
- The 20% GT spot-check (`r001,r004,r009,r021,r024`) is **WAIVED**. The user looked at the package, judged the
  decoding "quite good overall", and declined to give line-level corrections. Structured GT stays
  `drafted_unreviewed`. Recorded in `docs/ROADMAP.md` §5.1, `docs/EVALS.md` §8.1, and the archive README.
- A new modern (1900s+) real-world T3 set will be imported later by the orchestrator. Not created here.

## Summary
- **Moves (shell `mv`)**:
  - `evals/corpora/t3/{DECKS.json,d01/}` → `evals/corpora/archive/t3_antique/`
  - `evals/corpora/real_web/{ATTRIBUTION.md,CANDIDATES.md}` → `evals/corpora/archive/t3_antique/`
  - `evals/vault/t3.gt.enc` → `evals/vault/archive/t3a.gt.enc` (ciphertext unchanged. The GT JSON still says
    `"tier": "T3"`, the legacy label)
  - Emptied dirs `evals/corpora/t3/` and `evals/corpora/real_web/` were removed. New file: `evals/corpora/archive/t3_antique/README.md`.
- **Tier registry** `evals/tiers.py` (new): `ACTIVE_TIERS` T0–T4, `DEFAULT_TIERS` T1–T4 (no T3A),
  `ARCHIVED_TIERS={"T3A": corpus/vault paths, legacy_label "T3"}`, `PENDING_CURATION_TIERS=("T3",)`,
  `TierPendingCurationError(FileNotFoundError)` with message
  `"T3 corpus pending curation; see evals/INTAKE.md (... use --tier T3A to re-score it)"`.
- **Harness** (`evals/harness/run.py`): `resolve_deck_images` uses the registry. T3A resolves to the archive dir.
  T3 raises the typed error when its dir is missing or holds no images. Other tiers behave as before. The CLI prints the
  message and exits 2 (no traceback). A log NOTE is printed for archived-tier runs. `--tier` help mentions T3A.
- **Scorer**: `evals/scoring/gt.py::load_ground_truth` loads T3A from `vault/archive/t3a.gt.enc`. Missing T3 GT
  raises the typed error. `evals/score.py`: `--tier T3A` relabels legacy `"T3"` outputs to `T3A` in the report
  (so honest_v0/v1 T3 outputs stay re-scorable). The CLI exits 2 with the message for pending T3. Feedback
  policy is unchanged (aggregate only).
- **Vault** `rekey` also re-encrypts `archive/*.enc`. **Intake** refuses archived tiers. **calibration_fit**
  forbids `evals/corpora/archive` (the vault was already covered). The SUT audit hook already denies all
  `/evals/` and `*.enc` paths, so it needed no change; a new canary proves it denies the archive paths.
  `.github/workflows/eval-score.yml` accepts `T3A` in the outputs-tier allowlist.
- **Curation tools (era-neutral)**:
  - `fetch.py`: the 420-line hard-coded antique `CANDIDATE_SPECS` moved to `curation_t3/specs_t3a_antique.py`
    (`T3A_ANTIQUE_SPECS`). Fetch now needs `--specs <json>` (validated by `load_candidate_specs`) or
    `--preset t3a_antique`, plus `--tier` (default T3). ATTRIBUTION.md is now written to `<corpus_dir>/ATTRIBUTION.md`
    instead of `corpora/real_web/`.
  - `draft_structured.py`: the prompt and schema descriptions no longer say "historical culinary manuscripts",
    "archaic" or "medicinal/historical". Rule 2 "PRESERVE ORIGINAL SPELLING" is kept, worded as "preserve the original spelling
    verbatim ... Do not modernize or correct".
  - `memorization_probe.py`: the probe prompt is "Provide the complete recipe for ..." and the fallback title is "Recipe".
  - `spotcheck.py`: HTML text changed "manuscript" → "source/card/page".
- **Docs**: `docs/EVALS.md`: the tiers table now has T3 = modern pending and T3A = archived opt-in, with a note that all honest_v0/v1
  "T3" numbers are T3A. The §7.2 column headers and the §8.1 pipeline are updated. `evals/baselines/README.md`: same note (baseline
  JSONs left unchanged). `docs/ROADMAP.md`:
  - The spot-check is marked waived, with the reason.
  - The P1 "T3 title CER/manuscript" and P2 "T3 calibration" items are replaced by "P1 modern T3: curate + GT-draft, then run when quota allows" and "P2 INVALID_RESPONSE".
  - Antique handling is a P4 optional item, and §3.1.2/3.1.3 are rewritten.

  `evals/INTAKE.md` has a new T3 section (status, import paths via `intake_real --tier T3` or `curation_t3.fetch --specs`, offline GT drafting/spot-check).
  `evals/vault/README.md` layout and `_agents/TESTING_GUIDELINES.md` T3 row are updated.
- **Production code (`recipeboxer/**`)**: unchanged (`git diff --stat -- recipeboxer` is empty).

## Files changed
New: `evals/tiers.py`, `evals/curation_t3/specs_t3a_antique.py`, `evals/corpora/archive/t3_antique/README.md`,
`tests/test_wp_t3a_archive.py` (19 tests). Moved: see above. Modified: `evals/harness/run.py`, `evals/score.py`,
`evals/scoring/gt.py`, `evals/vault.py`, `evals/intake_real.py`, `evals/calibration_fit.py`,
`evals/curation_t3/{__init__,fetch,draft_structured,memorization_probe,spotcheck}.py`,
`.github/workflows/eval-score.yml`, `tests/test_wp_r2_curation.py` (paths → archive, legacy-label comment),
`tests/test_wp_ge_calibration.py` (+archive forbidden paths), `docs/EVALS.md`, `docs/ROADMAP.md`,
`evals/baselines/README.md`, `evals/INTAKE.md`, `evals/vault/README.md`, `_agents/TESTING_GUIDELINES.md`.

## Commands + results
`PCD=$HOME/.cache/projects/recipeboxer-ba754a91`, keys unset, all wrapped in `timeout`.
- Targeted: `pytest tests/test_wp_t3a_archive.py tests/test_wp_r2_curation.py tests/test_wp_t3gt_curation.py
  tests/test_wp_ge_calibration.py tests/test_wp_rt2a_eval_robustness.py tests/test_wp_e7_report.py
  tests/test_wp_r3_intake.py tests/test_wp_v1_vault.py` → **81 passed**.
- `pytest tests/test_wp_t3a_archive.py tests/anticheat tests/test_wp_e3_runner.py tests/test_wp_rt1_redteam.py
  tests/test_wp_scale_robustness.py` → **72 passed, 1 skipped**.
- Full suite: `pytest tests cardforge/tests --continue-on-collection-errors` → **13 failed, 2481 passed, 10 skipped** (396 s). The 13 failures are the known ones: 6 Windows zip-slip, 7 headless Tkinter.

## Contract deviations
- None to data shapes. Archived-tier layout (`evals/corpora/archive/<name>/`, `evals/vault/archive/<tier>.gt.enc`)
  extends WAVE3_CONTRACTS §1 and is resolved only through `evals/tiers.py`.
- `curation_t3.fetch` no longer has built-in default specs (it previously defaulted to the antique list). Running it without
  `--specs`/`--preset` is an explicit error.

## Requests / notes for the orchestrator
- Commit the moves as renames (`git add -A evals/corpora evals/vault`). `setup.sh` was already modified before this WP
  and was not touched.
- When importing the modern set, use `intake_real --tier T3` (or `curation_t3.fetch --specs`). `evals/corpora/t3/` and
  `evals/vault/t3.gt.enc` then exist and the pending error goes away automatically. The tests do not depend on T3
  being empty (they monkeypatch the repo/vault roots).
- To re-score archived honest outputs: `python -m evals.score --tier T3A --outputs <honest_v1 T3 outputs> --report <dir>`.

## Risks
- Anyone running `--tier T3` against old honest outputs now gets the pending error. The message points to T3A.
- `test_wp_r2_curation.py` still needs the real age identity at `/home/droid/.config/recipeboxer/age.key` (unchanged precondition).
