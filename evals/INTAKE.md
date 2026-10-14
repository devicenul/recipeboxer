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
