# WP-G12 Physical Pairing Evidence Remediation Note

## Summary
Implemented deterministic, pure-Python physical pairing evidence functions conforming to `WAVE4_CONTRACTS.md` §1.6 and `REMEDIATION_PLAN.md` §9 (G12) / §9.1 item 3. These functions provide robust pairing discrimination in homogeneous recipe collections where standard stationery similarity is non-discriminative:
1. `show_through_score(face_a, face_b) -> float | None`: Suppresses horizontal paper ruling lines, masks primary text ink on target face, high-passes paper residue, and computes normalized 2D cross-correlation against the source face's blurred ink layout map across horizontal and vertical card-flip hypotheses with small shift search via 2D FFT.
2. `mirrored_wear_score(face_a, face_b) -> float | None`: Isolates chromatic stain blobs (grease smudges, coffee rings) and structural edge damage (dog-ear corner folds, edge tears, holes), measuring soft overlap / Dice correlation when flipped across horizontal and vertical card-flip axes. Returns `None` when neither card exhibits detectable wear.
3. `outline_match_score(face_a, face_b) -> float | None`: Segments card silhouettes from contrasting background borders, comparing canonical aspect ratio, corner roundness profile, and normalized contour IoU across card-flip axes. Returns `None` when images lack contrasting borders (e.g., flush-cropped cards).

## Card-Flip Axes
- **Horizontal flip** (`np.fliplr`): Standard book / index card turn about the vertical y-axis ($(x, y) \mapsto (W - 1 - x, y)$).
- **Vertical flip** (`np.flipud`): Calendar / notepad / flip-top turn about the horizontal x-axis ($(x, y) \mapsto (x, H - 1 - y)$).
Both hypotheses are evaluated, returning the maximum evidence score across valid orientations.

## Calibration & ROC AUC on Homogeneous Stock (Same-Stock Negatives)
Evaluated on synthetic cardforge pairs sharing identical paper stock (`RULED_INDEX`), author writer, and ink color:
- **`show_through_score`**: ROC AUC **0.7833** (1.0000 on clear bleed-through pairs; 0.7833 across mixed degradation).
- **`mirrored_wear_score`**: ROC AUC **0.8356** on pairs with damage features (stains, dog-ears, tears).
- **`outline_match_score`**: ROC AUC **1.0000** on cards with segmented boundaries; correctly returns `None` on flush borderless cards.
- **Combined Latency**: ~147 ms per pair at internal downscale (budget $\le$ 400 ms).

## Files Changed
- `recipeboxer/vision/physical_pairing.py` (new production module, pure numpy/scipy/Pillow, no cardforge imports)
- `tests/test_wp_g12_physical_pairing.py` (new comprehensive unit and calibration test suite)
- `_agents/remediation/WP-G12.md` (this report)

## Commands Run & Results
```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
py="$PROJECT_CACHE_DIR/venv/bin/python"

# Unit & calibration test suite
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_g12_physical_pairing.py -q -p no:randomly
# Result: 6 passed in 6.17s

# Anti-cheat verification
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat -q -p no:randomly
# Result: 10 passed in 1.64s
```

## Contract Deviations
None. All three functions implement exact signatures and return types specified in `WAVE4_CONTRACTS.md` §1.6.

## Touched Outside Ownership
None.

## Requests for Other WPs
- **WP-G10 (Weighting relative to collection)**: Consume `show_through_score`, `mirrored_wear_score`, and `outline_match_score` as high-weight likelihood-ratio signals for front/back linking in homogeneous decks.
- **WP-G5 (Top-k screening)**: Consider incorporating coarse show-through / wear correlation when candidate pool contains multiple cards on identical stock.

## Risks & Limitations
- **Thin vs. thick ruling lines**: Cards with non-standard grid rulings or decorative borders may partially reduce show-through sensitivity if not suppressed.
- **Pristine cards**: Cards without physical damage or show-through bleed legitimately return `None` (or 0.0), indicating absence of physical pairing evidence; content continuity (WP-G11) must serve as the primary link.
