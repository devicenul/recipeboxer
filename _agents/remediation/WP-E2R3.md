# Work Package Note: WP-E2 & WP-R3

## Summary
Implemented WP-E2 (Naming-variant generator `evals/variants.py`) and WP-R3 (T4 Real photo dataset intake script `evals/intake_real.py` and non-expert guide `evals/INTAKE.md`).

- **WP-E2 (`evals/variants.py`)**:
  - Exposes `apply_variant(images: list[Path], variant: str, seed: int, out_dir: Path) -> dict[str, str]` mapping `new_filename -> original_corpus_image_id`.
  - Supported variants:
    - `opaque`: Random 12-char hex filenames in random order.
    - `misleading`: Names that actively lie using neutral dish words, reversed role indicators (front labeled back, card 2), and swapped numbers.
    - `shuffle`: Camera-roll style `IMG_0001..` in random order.
    - `reencode`: Format conversion between JPEG, PNG, and WebP with quality jitter and EXIF stripped, while strictly preserving dimensions and pixels.
  - Pure Pillow, deterministic per seed, zero imports from `recipeboxer`.
- **WP-R3 (`evals/intake_real.py`, `evals/INTAKE.md`)**:
  - CLI & library: `python -m evals.intake_real --photos DIR --transcriptions DIR_OR_CSV --deck-id d01 --tier T4`.
  - Steps:
    1. Transposes photos according to EXIF orientation.
    2. Strips GPS coordinates and privacy EXIF tags, preserving and writing back `DateTimeOriginal` (capture timestamp).
    3. Assigns random opaque hex names and writes images to `evals/corpora/<tier>/<deck_id>/`.
    4. Updates public deck manifest `evals/corpora/<tier>/DECKS.json` (`n_images`, no GT).
    5. Parses recipe transcriptions (folders with `faces.txt` and `transcription.md`, or CSV) and constructs GT JSON conforming to schema `rb-gt/1`.
    6. Encrypts directly into `evals/vault/<tier>.gt.enc` using `evals.vault.encrypt_bytes`. If archive exists, decrypts in-memory, merges, and re-encrypts. Plaintext GT is never written to disk.
    7. Includes `--dry-run` flag to validate inputs and print counts without disk/vault modifications.
  - `evals/INTAKE.md`: Friendly guide for non-expert users covering photo capture guidelines (plan §3.3), privacy guarantees, folder structure, and CLI usage.

## Files Changed / Created
- `evals/variants.py`: Implementation of `apply_variant` and variant transformations.
- `evals/intake_real.py`: Intake pipeline and CLI for user photos and ground truth.
- `evals/INTAKE.md`: User-facing photo and intake instructions.
- `tests/test_wp_e2_variants.py`: 9 unit tests for WP-E2.
- `tests/test_wp_r3_intake.py`: 7 unit tests for WP-R3.
- `_agents/remediation/WP-E2R3.md`: This work package note.

## Commands + Results
- Unit tests:
  ```bash
  PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_e2_variants.py tests/test_wp_r3_intake.py -q -p no:randomly
  ```
  Result: `16 passed in 0.58s`.
- Anticheat static invariant check:
  ```bash
  PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat/test_static_invariants.py -q -p no:randomly
  ```
  Result: `4 passed in 1.38s`.

## Contract Deviations
None. Adheres strictly to `WAVE3_CONTRACTS.md` §0, §1, §2, §3, and §8.

## Requests for Other WPs
None. `evals/harness/run.py` (WP-E3E4) can import `apply_variant` directly from `evals.variants`.

## Risks
- When merging into existing vault archives, an authorized age identity must be configured in `RB_EVAL_AGE_KEY_FILE` or default path.
- Non-expert users shooting in Apple HEIC format should convert to JPEG/PNG before intake or install `pillow-heif`.
