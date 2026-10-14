# Work Package Report: WP-E1 — Evaluation Curation & Ground Truth Generation

## Executive Summary

Work Package **WP-E1** implements the evaluation curation pipeline and generates the Tier 1 (dev-eval) and Tier 2 (held-out) synthetic recipe card evaluation corpora in accordance with `WAVE3_CONTRACTS.md` (§0, §1, §2) and `REMEDIATION_PLAN.md` (§3, §6, §9.1).

All generation enforces **strict procedural secrecy**: recipe source texts were fetched from Project Gutenberg public-domain historical cookbooks (1880–1922) directly into an external cache directory (`$PROJECT_CACHE_DIR`), parsed into memory, rendered procedurally onto synthetic card canvases, and assembled directly into encrypted vault artifacts (`evals/vault/*.enc`). Zero plaintext recipe text is committed to git or stored in the repository workspace.

All Tier 1 images across all 4 decks (`d01`, `d02`, `d03`, and `d04` totaling 386 cards, 20 MB total) are committed directly under `evals/corpora/t1/`, enabling **untrusted CI jobs** (`eval-run.yml`) and external contributors to execute the SUT on the public dev-eval benchmark without an age private key.

---

## Changes Implemented

### 1. CardForge Enhancements (`cardforge/`)
Expanded `cardforge` with physical and typographical realism required for evaluation stress-testing:
- **Bundled Fonts & Open Source Licensing**:
  - `caveat.ttf` — Natural historical cursive handwriting (SIL Open Font License 1.1).
  - `special_elite.ttf` — Mechanical manual typewriter font (Apache License 2.0).
  - `marck_script.ttf` — Vintage cursive script (SIL Open Font License 1.1).
  - Added license files: `cardforge/fonts/OFL-Caveat.txt`, `cardforge/fonts/LICENSE-SpecialElite.txt`, `cardforge/fonts/OFL-MarckScript.txt`.
  - Added documentation: `cardforge/fonts/README.md` listing author, license, and upstream source URLs.
  - Packaged via `pyproject.toml` `package-data` (`fonts/*.ttf`, `fonts/*.txt`, `fonts/*.md`) and resolved dynamically in `cardforge.generator.get_font`.
- **Domain Models (`cardforge/models.py`)**:
  - `RulingStyle`: 10 distinct stationery rulings (`STANDARD_RULED`, `WIDE_RULED`, `NARROW_RULED`, `BLUE_NO_HEADER`, `GRID_GRAPH`, `DOT_GRID`, `DOUBLE_HEADER`, `FAINT_RULED`, `GREEN_LEDGER`, `UNRULED`).
  - `CardStock`: Stationer stock identity encapsulating paper substrate, ruling, base color, card dimensions, and aging profile.
  - `CardWriter`: Author identity encapsulating handwriting/typing modality, font style, ink type, ink color, and continuation cue habits (`"over"`, `"cont'd"`, `"pto"`, `"card N"`, or none).
  - `DegradationConfig`: Extended with `creases`, `perspective_warp`, `glare`, and `jpeg_noise`.
  - `GeneratedCard`: Extended with `stock_id`, `writer_id`, `verbatim_text`, `face_index`, `total_faces`.
- **Physical Canvas Rendering & Degradations (`cardforge/generator.py`)**:
  - Enhanced `create_paper_substrate` to draw all ruling variants and custom tint overrides.
  - Implemented `find_perspective_coeffs` (pure-Python 8x8 linear equation solver) and `apply_perspective_warp`.
  - Implemented `apply_crease`, `apply_glare`, `apply_jpeg_noise`, and `apply_show_through` (mirrored reverse-side ghost bleed-through).
- **Multi-Face Generation (`SyntheticCardGenerator.generate_recipe_card_faces`)**:
  - Supports coordinated generation of 1 to 5 faces per recipe.
  - Physical pairing coordination: front and back faces share mirrored wear, stains, dog-ears, tears, and show-through.
  - Exact verbatim tracking: records the verbatim text actually laid out on each face canvas for evaluation auditing.
  - Configurable continuation cues across face boundaries.
- **Unit Tests (`cardforge/tests/test_new_features.py`)**:
  - 7 comprehensive tests covering rulings, stock/writer models, degradations, 1–5 face partitioning, verbatim tracking, cue presence/absence, and determinism using neutral synthetic text.

### 2. Curation Pipeline (`evals/curation/`)
- **`evals/curation/build_synthetic.py`**:
  - Fetches public domain cookbooks into `$PROJECT_CACHE_DIR/gutenberg_cache/`:
    - Tier 1: Fannie Farmer (1896, PG #65061) and Alice Bradley (1922, PG #10582).
    - Tier 2: Foods That Will Win The War (1918, PG #15464), Chocolate & Cocoa Recipes (1909, PG #13177), and Francatelli (1880s, PG #22114).
  - Generates deterministic opaque 12-hex card filenames (`<12hex>.jpg`).
  - Assembles and encrypts ground truth adhering strictly to schema `rb-gt/1`:
    - `evals/vault/t1.gt.enc` (Tier 1 GT)
    - `evals/vault/t1_large.spec.enc` (Tier 1 large deck generation spec)
    - `evals/vault/t2.gt.enc` (Tier 2 held-out GT)
    - `evals/vault/t2.images.tar.enc` (Tier 2 held-out images archive)
  - Saves committed public images for Tier 1 (`d01/`, `d02/`, `d03/`, and `d04/`).
  - Writes public `evals/corpora/t1/DECKS.json` publishing only `deck_id` and `n_images`.
- **`evals/curation/materialize.py`**:
  - CLI and library tool for on-demand deterministic materialization of large decks.
  - Decrypts `evals/vault/t1_large.spec.enc` in memory and regenerates 319 card images for `d04` with bit-identical SHA-256 hashes for reproducibility testing.
- **Gitignore Policies**:
  - `evals/curation/.gitignore`: Ignores `large_deck_cache/`, `materialized/`, `*.tar`.
  - Removed `evals/corpora/t1/.gitignore`: All T1 images (including `d04/`) are tracked in git.

---

## License & Public Domain Compliance (Invariant 8)

### Font Licensing
All fonts bundled under `cardforge/fonts/` are distributed under certified permissive open-source licenses:
- **Caveat**: Copyright 2014 The Caveat Project Authors. Licensed under the SIL Open Font License, Version 1.1 (`cardforge/fonts/OFL-Caveat.txt`).
- **Special Elite**: Copyright 2010 Brian J. Bonislawsky / Astigmatic. Licensed under the Apache License, Version 2.0 (`cardforge/fonts/LICENSE-SpecialElite.txt`).
- **Marck Script**: Copyright 2011 Denis Masharov and Marck Fogel. Licensed under the SIL Open Font License, Version 1.1 (`cardforge/fonts/OFL-MarckScript.txt`).
- Upstream links, author credits, and license mappings are cataloged in `cardforge/fonts/README.md` and included in the distribution package.

### Public Domain Sources & Trademark Respect
The historical cookbooks ingested by the curation pipeline are confirmed public-domain works:
1. **PG #65061**: *The Boston Cooking-School Cook Book* by Fannie Merritt Farmer (Boston: Little, Brown, and Company, 1896). Published in the United States prior to 1929; in the public domain under US copyright law.
2. **PG #10582**: *For Luncheon and Supper Guests* by Alice Bradley (Boston: Whitcomb & Barrows, 1922). Published in the United States prior to 1929; in the public domain under US copyright law.
3. **PG #15464**: *Foods That Will Win The War and How To Cook Them* by C. Houston Goudiss & Alberta M. Goudiss (1918). Published in the United States prior to 1929; in the public domain under US copyright law.
4. **PG #13177**: *Chocolate and Cocoa Recipes* by Miss Parloa (1909). Published in the United States prior to 1929; in the public domain under US copyright law.
5. **PG #22114**: *The Royal Confectioner* by Charles Elmé Francatelli (1880s edition; originally 1862). Published prior to 1929; in the public domain worldwide.

**Project Gutenberg Trademark Terms**:
In compliance with Project Gutenberg's license terms (Section 1.C and 1.E):
- Only the underlying public-domain recipe text is ingested.
- All Project Gutenberg headers, footers, notices, and legal disclaimers were automatically stripped during intake.
- The Project Gutenberg trademark is not associated with, displayed on, or attached to any generated card images, ground truth files, or evaluation corpora.

---

## Corpus Statistics & Verification

### Tier 1 (Dev-Eval Corpus)
- **Total Decks**: 4
- **Total Recipes**: 179
- **Total Card Images**: 386 (All 386 committed on disk in `d01`, `d02`, `d03`, `d04`)
- **Committed Disk Footprint (`evals/corpora/t1/`)**: 20 MB (Budget: $\le 40$ MB)
- **Vault Footprint**:
  - `evals/vault/t1.gt.enc`: 640 KB
  - `evals/vault/t1_large.spec.enc`: 344 KB
- **Deck Composition**:
  - `d01` (homogeneous): 12 recipes, 22 card images (78% dominant cream stock & cursive blue hand)
  - `d02` (mixed): 12 recipes, 26 card images (diverse rulings, typewriters, clippings, pencils, sepia script)
  - `d03` (near_duplicate): 10 recipes, 19 card images (paired recipe variants of identical dishes)
  - `d04` (large): 145 recipes, 319 card images (homogeneous scale deck with 78% dominant cream stock & cursive blue hand)
- **Face Count Distribution**:
  - 1 face (standalone): 40 recipes
  - 2 faces (pair): 91 recipes
  - 3 faces: 32 recipes
  - 4 faces: 12 recipes
  - 5 faces: 4 recipes
- **Modality Distribution**:
  - `HANDWRITTEN_CURSIVE`: 320 cards
  - `TYPEWRITTEN`: 57 cards
  - `PRINTED_CLIPPING`: 5 cards
  - `MIXED_MEDIA`: 4 cards
- **Stock Distribution**:
  - `s_t1_cream` (Cream Standard Ruled): 269 cards
  - `s_t1_yellow` (Aged Yellow Ruled): 59 cards
  - `s_t1_linen` (White Linen Unruled): 35 cards
  - `s_t1_narrow` (Narrow Ruled Index): 10 cards
  - `s_t1_double` (Double Header Ruled): 6 cards
  - `s_t1_grid` (Graph Grid Card): 4 cards
  - `s_t1_kraft` (Kraft Wide Ruled): 3 cards

### Tier 2 (Held-Out Evaluation Corpus)
- **Total Decks**: 3
- **Total Recipes**: 34
- **Total Card Images**: 71
- **Vault Footprint**:
  - `evals/vault/t2.gt.enc`: 108 KB
  - `evals/vault/t2.images.tar.enc`: 3.4 MB
- **Deck Composition**:
  - `d01` (homogeneous): 12 recipes, 22 card images (held-out ivory stock & fountain black hand)
  - `d02` (mixed): 12 recipes, 25 card images (held-out green ledger, blue cards, canary stocks)
  - `d03` (near_duplicate): 10 recipes, 24 card images (held-out recipe variants)
- **Face Count Distribution**:
  - 1 face (standalone): 9 recipes
  - 2 faces (pair): 16 recipes
  - 3 faces: 6 recipes
  - 4 faces: 3 recipes
- **Modality Distribution**:
  - `HANDWRITTEN_CURSIVE`: 51 cards
  - `TYPEWRITTEN`: 14 cards
  - `PRINTED_CLIPPING`: 6 cards
- **Stock Distribution**:
  - `s_t2_ivory` (Ivory Ruled Card): 29 cards
  - `s_t2_canary` (Canary Ruled Index): 13 cards
  - `s_t2_parchment` (Aged Parchment): 12 cards
  - `s_t2_blue_card` (Light Blue Ruled): 6 cards
  - `s_t2_kraft` (Heavy Kraft Stock): 6 cards
  - `s_t2_green_ledger` (Green Accounting Ledger): 5 cards

---

## Verification & Integrity Testing

All verification checks were run in memory using the maintainer age key identity (`/home/droid/.config/recipeboxer/age.key`).

1. **CardForge Unit & Isolation Test Suite**:
   - Command: `python -m pytest cardforge/tests -q -p no:randomly`
   - Result: `49 passed in 11.29s` (100% pass rate).
   - Confirms backward compatibility of existing procedural generators and full functionality of new rulings, physical degradations, models, and multi-face layout.
   - Confirms strict architectural isolation (`test_isolation.py`): `cardforge` never imports `recipeboxer`, and `recipeboxer` never imports `cardforge`.
2. **Tier 1 Ground Truth Schema & Image Integrity (All 4 Decks)**:
   - Decrypted `evals/vault/t1.gt.enc` in memory.
   - Validated root schema (`rb-gt/1`), tier (`T1`), generator (`cardforge`), deck metadata, and recipe fields.
   - Verified that all 386 committed images across `d01/` (22), `d02/` (26), `d03/` (19), and `d04/` (319) match the expected SHA-256 recorded in GT with 100% byte fidelity.
3. **Tier 1 Large Deck (`d04`) Materialization**:
   - Decrypted `evals/vault/t1_large.spec.enc` in memory and regenerated all 319 card images via `evals.curation.materialize.materialize_deck`.
   - Verified that all 319 materialized images match the committed files in `evals/corpora/t1/d04/` and the expected SHA-256 hashes recorded in `evals/vault/t1.gt.enc` with 100% byte fidelity.
4. **Tier 2 Ground Truth Schema & Images Tar Integrity**:
   - Decrypted `evals/vault/t2.gt.enc` and `evals/vault/t2.images.tar.enc` in memory.
   - Validated root schema (`rb-gt/1`) and tier (`T2`).
   - Verified that all 71 image files inside the encrypted tar archive match the expected SHA-256 hashes recorded in GT with 100% byte fidelity.
5. **Secrecy & Anti-Cheat Adherence**:
   - No recipe text or recipe titles were printed to logs, console, or markdown documentation.
   - No files under `recipeboxer/` were modified.
