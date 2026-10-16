# WP-T3I — Import the modern (1900s+) real-world set as tier T3

Branch `remediation/phase0`, base HEAD `da8f33f`. No state-changing git commands (the orchestrator commits). `setup.sh`
is untouched; it was already modified before this WP. **No Gemini/API calls. No recipe text GT was drafted.**

## Summary
- **Images.** 50 web-sized copies were written to `evals/corpora/t3/d01/`:
  - each has a new random 12-hex id, and none equals a staging id
  - long side is at most 2400 px
  - sRGB JPEG at **q85**, because q90 gave 32 MB and the target was ≤ 25 MB; q85 gives 24.99 MB
  - all EXIF/GPS/XMP/ICC/COM segments are stripped, verified segment by segment; 2 COM segments were removed losslessly
  - `DECKS.json` is `[{"deck_id":"d01","n_images":50}]`

  Originals stay only in staging. The private map `$PCD/t3m_staging/meta/repo_idmap.json` is chmod 600. The import
  and GT-builder scripts are private, in `$PCD/t3i/`, and are not in the repo.
- **GT** `evals/vault/t3.gt.enc`: age-encrypted to the existing recipients, rb-gt/1, tier T3, one deck.
  - `deck_type: real_modern`, `multi_recipe_images: true`, and `non_recipe_images` lists the 9 distractors.
  - Each image has `role`, `modality` (`HANDWRITTEN`/`TYPEWRITTEN`/`PRINTED`/`MIXED`/`BLANK` plus a detail field),
    era, `famous` and `sha256`.
  - 49 recipes, each with: `faces` in reading order, `layout`, `modality`, `era`/`era_bucket`, `famous`,
    `memorization_risk` (famous → high, OCR-online cookbook → medium, otherwise low), `source_cluster`,
    `pairing`/`pairing_inferred`, `shared_faces`, and a private `draft_hint` (curator title and note, needed for drafting
    multi-recipe pages).
  - `structured` is an empty skeleton with `structured_status: pending_draft`; `verbatim` is `[]` with
    `verbatim_status: pending`.
  - The tail fragment has `score_policy: exclude` plus an `excluded_reason`, so 48 groups are scored.
  - T3A had no distractors, so distractors use the new deck-level `non_recipe_images` list plus image `role`.
- **Public docs.**
  - `evals/corpora/t3/ATTRIBUTION.md` maps filename → title / creator+institution / source page / licence / rights
    statement, sorted by filename. It has no roles, groups or order. Caveat: some source titles are self-describing
    (e.g. Commons "… (page 1)/(page 2)", Flickr "front/back of recipe card"). That is what the source pages say and the
    licences require it.
  - `evals/corpora/t3/README.md` covers era, modality mix, licences, process, the pending text GT and the flags.
- **Scorer** (`evals/scoring/evaluator.py`, `gt.py`):
  - Multi-recipe images: grouping metrics are computed on image-level connected components.
    `metrics.grouping_detail` adds `recipe_set_exact_acc`, recipe counts and `distractor_rejection_rate`.
  - Excluded recipes are dropped from extraction and recipe counts.
  - `pending_draft` recipes are not content-matched. If a deck has no text GT, extraction is
    `status: not_available, reason: "structured GT pending"`, all values are None (rendered N/A) and ECE is None.
    In mixed decks `hallucinated_recipe_rate` is None.
  - The oracle plan merges overlapping sets.
  - Legacy GT is unchanged: same totals, same oracle RNG sequence, and no `grouping_detail` key.
- **Harness**: T3 now resolves. Offline dry check: `resolve_deck_images("T3")` gives 50 images; the obfuscation step
  produces 50 files; `load_ground_truth("T3")` succeeds and its images match the corpus exactly; oracle plan gives
  30 sets. A no-knowledge "one recipe per image" output scored without a crash: grouping numeric, extraction N/A,
  report shows `N/A`. **The SUT was not run.**
- **Curation tools.**
  - `draft_structured.py`: accepts `pending_draft`, skips excluded recipes, and adds `--from-images`/`--corpus-dir`.
    With `--from-images`, gemini-3.1-pro-preview drafts per-face verbatim and structured GT from the face images plus
    `draft_hint`, validates the structured part against the drafted verbatim, and marks the result
    `drafted_unreviewed` with `*_source: image_draft`.
  - `memorization_probe.py`: skips recipes without verbatim and excluded recipes; it never clears a non-probe
    `excluded_reason`.
  - `spotcheck.py`: skips excluded recipes; the sample count is now dynamic.
  - Offline CLI runs against the real vault (no key) left the ciphertext sha unchanged.
- **Docs**:
  - `docs/EVALS.md`: tiers table (T3 = 50 images / 48 scored groups, structured GT pending), a scorer-features note,
    and §8.1/§8.1.1 (procedure, cost, commands).
  - `docs/ROADMAP.md`: P1 row, §3.1.2 and §5.1.
  - `evals/INTAKE.md` §T3.
  - `evals/tiers.py` docstring.

## Proposed drafting procedure (when quota allows), ~$2–3
There are no human transcriptions for this set.
1. `python -m evals.curation_t3.draft_structured --from-images --dry-run`, then `--limit 3` as a pilot, then the
   remaining recipes. Cost is about $0.04–0.06 per recipe at $2/$12 per MTok.
2. `python -m evals.curation_t3.memorization_probe` (about $0.5–1).
3. `python -m evals.curation_t3.spotcheck --sample-rate 0.2 --html-dir <outside repo>`. The user checks ≥ 20%,
   including both inferred pairings and one multi-recipe page. This check is mandatory, because the GT is drafted by
   the same model family as the SUT.
4. Run the first modern-T3 baseline.

## Files changed
New:
- `evals/corpora/t3/{d01/*.jpg (50), DECKS.json, ATTRIBUTION.md, README.md}`
- `evals/vault/t3.gt.enc`
- `tests/test_wp_t3i_import.py` (14 tests, fictitious fixtures and stubbed clients; also public-corpus invariants)

Modified:
- `evals/scoring/evaluator.py`, `evals/scoring/gt.py`, `evals/tiers.py` (docstring)
- `evals/curation_t3/{draft_structured,memorization_probe,spotcheck}.py`
- `docs/EVALS.md`, `docs/ROADMAP.md`, `evals/INTAKE.md`

## Commands + results
`PCD=$HOME/.cache/projects/recipeboxer-ba754a91`; keys unset; everything wrapped in `timeout`.
- Targeted run → **157 passed**:
  `pytest tests/test_wp_t3i_import.py tests/test_wp_t3a_archive.py tests/test_wp_t3gt_curation.py tests/test_wp_r2_curation.py tests/test_wp_e4_scorer.py tests/test_wp_sv_scorer_validity.py tests/test_wp_rt3fix.py tests/test_wp_ge_calibration.py tests/test_wp_e7_report.py tests/test_wp_rt2a_eval_robustness.py tests/test_wp_r3_intake.py tests/test_wp_v1_vault.py tests/test_wp_e3_runner.py tests/anticheat`
- Anti-cheat grep: all 50 staging image ids, the 50 staging sha256s and the long curation keys were searched across
  the whole repo excluding `.git` and `*.enc` → **no hits**. No `groups.json` references.
- Full suite: `pytest tests cardforge/tests --continue-on-collection-errors` → **13 failed (the known 6 Windows zip-slip + 7 headless Tkinter), 2495 passed, 10 skipped** (573 s).

## Contract deviations
- WAVE3_CONTRACTS §2 says "every image belongs to exactly one recipe". T3 GT intentionally breaks this
  (multi-recipe images, and distractors that belong to no recipe). The scorer handles it as described above, and
  legacy GT behaviour is unchanged.
- The GT has extra recipe/deck fields; all are additive.
- JPEG quality is 85 instead of about 90, to meet the ≤ 25 MB size target.

## Requests / notes for the orchestrator
- Commit `evals/corpora/t3/`, `evals/vault/t3.gt.enc`, the code/docs listed above, and the new test.
- `evals/report.py` (not owned by this WP) renders pending extraction as plain `N/A`. Optionally, show
  `extraction.reason` and the `grouping_detail` block in `report.md`.
- `evals/baselines/README.md` could note that modern T3 has no baseline yet.

## Risks
- ATTRIBUTION source titles partly hint at front/back or page pairs. This is accepted per the task, and the SUT
  cannot read `evals/` anyway (audit hook).
- Image-drafted GT may be biased toward Gemini readings until it is spot-checked.
- `test_t3_public_corpus_invariants` assumes exactly 50 files in `d01`; adding images later means updating it.
