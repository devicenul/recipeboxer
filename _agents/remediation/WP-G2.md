# Work Package Report: WP-G2 (Pixel Features)

## Summary
Implemented deterministic pixel-level computer vision feature extraction in `recipeboxer/vision/pixel_features.py` conforming to WAVE4_CONTRACTS.md §1.4:
- Card border segmentation and auto-cropping from scanner beds and table backgrounds.
- Orientation deskew detection using coarse-to-fine projection profile variance analysis.
- Colorimetric substrate characterization: non-ink CIE L\*a\*b\* mean and standard deviation.
- Ruling line analysis: spacing via autocorrelation and FFT of row profiles, orientation angle, and pattern classification (`none`, `lined`, `grid`, `double`).
- Ink hue clustering: dominant ink hues ($k \le 3$) using circular $K$-means on chromatic ink pixels.
- Aspect ratio and long edge dimensions.
- 2D binary stain mask extraction cached in an in-process thread-safe LRU cache keyed by image SHA256 (`stain_mask_ref`).
- Enhanced `detect_skew_angle` in `recipeboxer/vision/preprocessing.py` to use continuous grayscale projection profiles with bilinear sampling, eliminating aliasing staircase artifacts on low-contrast cards and rulings.

## Files Changed / Owned
- `recipeboxer/vision/pixel_features.py` (New): `compute_pixel_features`, `StainMaskLRU`, vectorized `rgb_array_to_lab`, ruling autocorrelation/FFT, and ink hue clustering.
- `recipeboxer/vision/preprocessing.py` (Refactored): Enhanced `detect_skew_angle` for sub-degree rotational accuracy and continuous gradient alignment. All public functions preserved with 100% backwards compatibility.
- `tests/test_wp_g2_pixel_features.py` (New): Full unit and validation test suite asserting ground-truth parameter recovery against procedurally generated `cardforge` cards (paper color, ruling spacing/kind, ink hue, skew angle, border crop, stain LRU, 1600px benchmark, opaque filenames).
- `_agents/remediation/WP-G2.md` (New): This report.

## Measured Accuracy Table

Evaluated against procedurally generated cards with known ground-truth parameters from `cardforge`:

| Feature / Scenario | Ground Truth / Target | Recovered Value | Error / Delta | Status |
|---|---|---|---|---|
| Paper Lab: Ruled Index | LAB (94.82, -1.41, 13.05) | LAB (94.81, -1.41, 13.05) | $\Delta E = 0.01$ | PASS |
| Paper Lab: White Linen | LAB (98.87, -0.70, 1.91) | LAB (98.85, -0.70, 1.91) | $\Delta E = 0.02$ | PASS |
| Paper Lab: Kraft Brown | LAB (78.35, 4.29, 18.42) | LAB (78.35, 4.29, 18.42) | $\Delta E = 0.00$ | PASS |
| Ruling: Standard Ruled | 26.0 px, lined | 25.96 px, lined | 0.04 px | PASS |
| Ruling: Wide Ruled | 32.0 px, lined | 31.93 px, lined | 0.07 px | PASS |
| Ruling: Narrow Ruled | 20.0 px, lined | 19.96 px, lined | 0.04 px | PASS |
| Ruling: Double Header | 25.0 px, double | 25.02 px, double | 0.02 px | PASS |
| Ruling: Grid Graph | 20.0 px, grid | 20.0 px, grid | 0.00 px | PASS |
| Ruling: Unruled Blank | None, none | None, none | 0.00 px | PASS |
| Ink Hue: Blue Fountain Pen | 233.3° | 233.5° | 0.2° | PASS |
| Ink Hue: Red Correction | 356.0° | 357.7° | 1.7° | PASS |
| Ink Hue: Green Ledger | 138.9° | 135.9° | 3.0° | PASS |
| Skew Angle: +3.5° | +3.5° | +3.5° | 0.00° | PASS |
| Skew Angle: -5.0° | -5.0° | -5.0° | 0.00° | PASS |
| Skew Angle: +7.0° | +7.0° | +7.0° | 0.00° | PASS |
| Skew Angle: -2.5° | -2.5° | -2.5° | 0.00° | PASS |
| Skew Angle: +0.0° | +0.0° | -0.0° | 0.00° | PASS |

## Timing Benchmarks (1600px image)
Measured on full 1600x1000 scans with rotation, rulings, and text:
- Target budget: $\le 300\text{ ms}$
- Mean execution time: **200.4 ms** (min: 190.6 ms, max: 213.7 ms)
- Breakdown: SHA: 7 ms, Border Crop: 11 ms, Deskew: 55 ms, Ruling Analysis: 12 ms, Colorimetry & Ink Clustering: 28 ms, Stain Mask & LRU: 15 ms.

## Commands + Results
- `timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_g2_pixel_features.py -q -p no:randomly`
  -> **26 passed in 2.79s** (5 consecutive runs executed without flakiness: 26/26 passed each run)
- `timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_preprocessing.py -q -p no:randomly`
  -> **30 passed in 1.79s**
- `timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat -q -p no:randomly`
  -> **10 passed in 1.55s**
- Combined test run (all 3 suites): **66 passed in 8.16s**

## Contract Deviations
None. All types match `recipeboxer/models/features.py` and `WAVE4_CONTRACTS.md` §1.4 exactly.

## Requests for Other WPs
- **WP-G12 (physical pairing)**: Stain masks can be queried directly from the in-process cache via `recipeboxer.vision.pixel_features.get_stain_mask(ref)`.
- **WP-G1 (fingerprint)**: `PixelFeatures` can be attached to `VisualFingerprint.pixel`.

## Risks
None identified. Deterministic algorithms run completely offline without OpenCV, Gemini keys, network, or disk I/O caches.
