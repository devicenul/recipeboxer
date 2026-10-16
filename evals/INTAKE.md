# RecipeBoxer Real Photo Intake Guide (Tier 4)

This guide walks you through preparing and importing real physical recipe card photos into the RecipeBoxer evaluation benchmark (**Tier 4 / T4**).

The T4 benchmark measures how well RecipeBoxer performs on genuine family recipe collections—with real handwriting, index cards, aging, stains, and camera photos.

---

## 1. Taking Your Photos (Photo Guide)

You don't need professional photography equipment—a standard smartphone camera works best!

### Recommendations
* **Recipe Count**: Aim for **10 to 20 recipes**.
* **Multi-Card Sets**: Include at least **5 multi-card recipes** (cards with front and back sides, or two separate cards continuing the same recipe). The rest can be single-face standalone cards.
* **Natural Framing**: Shoot under everyday indoor or kitchen lighting. A bit of realistic skew, shadows, uneven paper, or mild glare is completely fine and helps test real-world robustness.
* **Capture in Natural Order**: Photograph in sequence as you browse the recipe box (for example: Recipe A front, then Recipe A back; Recipe B card 1, then Recipe B card 2). The camera roll order is a legitimate, natural signal that people photograph items in sequence.

### Privacy & Metadata Protection
Your privacy is fully protected:
* **GPS coordinates are permanently stripped** from all image files before they are saved.
* **Camera serial numbers and device metadata are removed**.
* **DateTimeOriginal** (the camera capture timestamp) is preserved and written back to each image file so that capture order can be used for grouping evaluation.
* Photos are renamed with **random opaque identifiers** (e.g. `7f3a9c1e0b12.jpg`) so filenames cannot leak titles or card relationships.

---

## 2. Preparing Your Transcriptions

For each recipe, we need a typed "ground truth" transcription to evaluate against.

Create a folder for your transcriptions (e.g. `my_transcriptions/`). Inside it, create one subfolder per recipe (for example `recipe_01/`, `recipe_02/`, or named by dish `lemon_bars/`):

```text
my_transcriptions/
├── recipe_01/
│   ├── faces.txt
│   └── transcription.md
├── recipe_02/
│   ├── faces.txt
│   └── transcription.md
└── ...
```

### `faces.txt`
In each recipe folder, `faces.txt` lists the image filenames that belong to this recipe, in reading order (one filename per line). For example:

```text
IMG_1021.JPG
IMG_1022.JPG
```

### `transcription.md`
`transcription.md` contains the typed text of the recipe. You can use markdown headings to structure the recipe:

```markdown
# Starlight Berry Compote

Yield: 4 servings

## Ingredients
- 3 cups mixed wild blackberries and huckleberries
- 1/3 cup wildflower honey
- 1 tbsp fresh orange zest
- 1 pinch ground cardamom
- 1 tsp arrowroot powder dissolved in 1 tbsp cold water

## Directions
1. In a heavy saucepan, combine wild berries, honey, orange zest, and cardamom over medium heat.
2. Simmer gently for 8 to 10 minutes until berries burst and release rich juices.
3. Stir in dissolved arrowroot slurry and simmer for 1 additional minute until thickened and glossy.
4. Remove from heat and cool to lukewarm before serving over warm oat porridge or toast.

## Notes
- Hand-harvested alpine berries give the best tartness and aromatic finish.
```

*(Tip: If you have a two-sided card and want to record the exact verbatim text per face, you can separate faces with `### Face 1` and `### Face 2` or `---`.)*

### Optional CSV Format
If you prefer spreadsheets, you can also provide a single `transcriptions.csv` file with columns: `recipe_id`, `faces` (comma-separated photo filenames), `title`, `ingredients` (separated by newlines or semicolons), `directions`, and optional `notes`.

---

## 3. Running the Intake Script

Once your photos and transcriptions folder are ready, run the intake tool:

### Step 1: Preview with `--dry-run`
Always test your folder paths first using `--dry-run`. This verifies that all photos referenced in `faces.txt` exist and displays summary counts without modifying anything:

```bash
python -m evals.intake_real \
  --photos /path/to/my_photos \
  --transcriptions /path/to/my_transcriptions \
  --deck-id d01 \
  --dry-run
```

Output:
```text
[DRY RUN] Intake validation successful:
  Tier: T4
  Deck ID: d01
  Recipes found: 15
  Images referenced: 26
  Photos source: /path/to/my_photos
  Transcriptions: /path/to/my_transcriptions
  (No files were written to disk or vault.)
```

### Step 2: Ingest the Deck
When you are ready, run the intake command without `--dry-run`:

```bash
python -m evals.intake_real \
  --photos /path/to/my_photos \
  --transcriptions /path/to/my_transcriptions \
  --deck-id d01 \
  --tier T4
```

### What Happens Behind the Scenes:
1. **Orientation**: Images are automatically rotated using EXIF orientation tags so they display right-side up.
2. **GPS Stripping**: GPS coordinates and privacy EXIF tags are stripped; capture timestamps are retained.
3. **Opaque Names**: Photos are given secure random hex names and saved to `evals/corpora/t4/d01/`.
4. **Public Deck List**: `evals/corpora/t4/DECKS.json` is updated with `[{"deck_id": "d01", "n_images": 26}]` (contains NO ground-truth or titles).
5. **Encrypted Answer Key**: Ground truth is compiled into the `rb-gt/1` format and encrypted directly into `evals/vault/t4.gt.enc` using `age` encryption. **Plaintext answers are never written to disk anywhere in the project.**
6. **Merging**: If `t4.gt.enc` already exists, additional decks (like `d02`) are decrypted, merged, and re-encrypted seamlessly in memory.

---

## 4. Troubleshooting & FAQ

* **Photo Not Found Error**: Check that the filename inside `faces.txt` matches your camera photo filename (e.g. `IMG_0412.JPG` vs `IMG_0412.jpg`).
* **Vault Access**: To decrypt or merge an existing encrypted archive, an authorized `age` identity key must be configured in `~/.config/recipeboxer/age.key` or via the `RB_EVAL_AGE_KEY_FILE` environment variable.

---

## Tier 3 (T3): Real-World Modern Recipes (1900s+) — imported, text GT pending

**Status (WP-T3I, 2026-10-16):** T3 is the real-world *modern* tier: recipe cards, letters, notebook and
cookbook pages from the **1900s onward**. The first set is imported: 50 images, and 49 recipe groups of
which 48 are scored (see `evals/corpora/t3/README.md`). The harness and scorer accept `--tier T3`. The
grouping GT is final. The recipe-text GT (verbatim + structured) is `pending_draft`, so extraction metrics
show as N/A ("structured GT pending") until it is drafted. `TierPendingCurationError` is now raised only
when the T3 corpus or GT is missing.

The former T3 corpus (17th-century English manuscripts, Folger/Wellcome) is archived as the opt-in tier
**T3A** (`evals/corpora/archive/t3_antique/README.md`). The `honest_v0`/`honest_v1` "T3" numbers refer
to it. Re-score with `--tier T3A`. T3A is never part of default runs and accepts no new intake.

### How the current set was imported (WP-T3I)

The curator (WP-T3M) staged originals plus a private grouping outside the repo
(`$PROJECT_CACHE_DIR/t3m_staging/`). A private, one-off import step then did the following:

- Wrote web-sized copies (long side ≤ 2400 px, sRGB JPEG q85, all EXIF/GPS/XMP/ICC/comment metadata
  stripped) under **new** random 12-hex ids to `evals/corpora/t3/d01/`, plus `DECKS.json`.
- Encrypted the GT to `evals/vault/t3.gt.enc`.
- Wrote `ATTRIBUTION.md`, a filename-to-credit map only, sorted by filename.

The staging-id to repo-id map is kept only at `$PROJECT_CACHE_DIR/t3m_staging/meta/repo_idmap.json`
(chmod 600). T3 GT fields beyond the base contract:

- deck level: `deck_type: "real_modern"`, `multi_recipe_images`, `non_recipe_images`, and per-image
  `role`
- recipe level: `layout`, `modality`, `era` / `era_bucket`, `famous`, `memorization_risk`,
  `source_cluster`, `pairing` / `pairing_inferred`, `shared_faces`, `score_policy` / `excluded_reason`,
  the private `draft_hint`, and `structured_status` / `verbatim_status`

### Adding more modern T3 material later

Layout written by either path below (same contract as all tiers):
`evals/corpora/t3/d01/<opaque>.jpg`, `evals/corpora/t3/DECKS.json` (`[{deck_id, n_images}]` only),
`evals/vault/t3.gt.enc` (age-encrypted GT; plaintext never on disk in the workspace).

1. **Photos + transcriptions you hold locally** (e.g. family cards, downloaded archive scans): prepare
   `faces.txt` + `transcription.md` per recipe exactly as described in §2 above, then
   ```bash
   python -m evals.intake_real --photos /path/to/photos --transcriptions /path/to/transcriptions \
       --deck-id d02 --tier T3 --dry-run      # check first, then re-run without --dry-run
   ```
2. **Transcribed archive pages (FromThePage-style)**: write a JSON spec list (keys documented at the top
   of `evals/curation_t3/fetch.py`) and run
   ```bash
   python -m evals.curation_t3.fetch --specs /path/outside/repo/t3_specs.json \
       --cache-dir "$PROJECT_CACHE_DIR/t3_cache"
   ```
   Attribution is written to `evals/corpora/t3/ATTRIBUTION.md`. Merge by hand with the existing file.

### Ground-truth drafting and review (needs a key; run only when quota allows)

This set has no human transcriptions, so verbatim and structured GT are drafted **from the images** by
`gemini-3.1-pro-preview` (about $2–3 for 48 recipes), and a human then spot-checks the drafts. See
`docs/EVALS.md` §8.1 for the procedure and its caveats.

```bash
python -m evals.curation_t3.draft_structured --from-images --dry-run      # counts only, no API calls
python -m evals.curation_t3.draft_structured --from-images --limit 3      # pilot, then inspect
python -m evals.curation_t3.draft_structured --from-images                # the rest (idempotent)
python -m evals.curation_t3.memorization_probe                            # needs verbatim -> after drafting
python -m evals.curation_t3.spotcheck --sample-rate 0.2 --html-dir "$PROJECT_CACHE_DIR/t3gt/spotcheck"
```

- Without a key, every command reports the pending state and changes nothing.
- Recipes with `score_policy: exclude` are skipped.
- Without `--from-images`, the drafter only handles recipes that already have verbatim (text-only path).
- Spot-check at least 20%, always including the two `pairing_inferred` recipes and one multi-recipe page.
  Write the spot-check package outside the repository.

After the spot-check, record the first modern-T3 baseline. Nothing in the repo spends quota by default.

