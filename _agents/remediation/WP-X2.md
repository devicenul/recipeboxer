# Work Package WP-X2: Image Preparation for Extraction

## 1. Summary

WP-X2 delivers the image preparation pipeline (`prepare_faces` and `PreparedFace`) for Phase 4 recipe extraction, conforming strictly to **WAVE 4 Contracts §1.8** and **REMEDIATION_PLAN.md §8.3**.

The module normalizes raw multi-face card photos and scans into uniform, geometry-corrected, contrast-balanced, and size-capped image representations for downstream multimodal transcription (WP-X4) and structuring (WP-X5).

## 2. Architecture & Core Pipeline Stages

Implementation lives in `recipeboxer/extraction/image_prep.py`:

1. **EXIF Normalization**: Transposes mobile camera captures to upright orientation via Pillow's `ImageOps.exif_transpose`.
2. **Card Border Auto-Cropping**: Automatically segments and crops away dark flatbed scanner backgrounds or margin boundaries. Self-sufficient fallback provided alongside `recipeboxer.vision.preprocessing.crop_card_borders`.
3. **Auto-Deskewing**: Detects small angular misalignments (`[-10°, +10°]`) using horizontal projection profile variance and aligns lines horizontally without introducing 90°/180° orientation inversions.
4. **Resolution Capping**: Enforces profile ceiling `profile_params.get('image_long_edge', 1600)` (e.g. Economy 1280px, Balanced 1600px, Best 2000px) using high-fidelity Lanczos resampling. Never upsamples smaller cards.
5. **Measured Content-Contrast Metric**: Evaluates card substrate luminance (90th percentile) against ink stroke luminance (median of darkest 0.1% pixels) to compute Michelson contrast and dynamic range. Strictly content-driven; 100% immune to filenames or path strings.
6. **Adaptive Contrast Equalization (CLAHE)**: High-performance, vectorized NumPy implementation of Contrast Limited Adaptive Histogram Equalization with dynamic range expansion and bilinear tile interpolation. Converts RGB to YCbCr to enhance luminance contrast while preserving substrate and ink color tones.
7. **Re-Encoding & MIME Correctness**: Compresses to JPEG at quality ~90. For low-color or typewritten documents (≤64 unique colors), evaluates PNG compression and selects PNG if payload size is smaller. Validated against magic-byte sniffing (`sniff_mime_type`).
8. **Labeling & Variant Tagging**:
   - Standard faces: labeled `'Face i of N'` with `variant="standard"`.
   - Contrast-enhanced faces: labeled `'Face i of N (contrast-enhanced copy)'` with `variant="enhanced_clahe"`.
   - `face_index` is 0-based (`0 .. n_faces - 1`); human face number `i = face_index + 1`.

## 3. Enhancement Controls (`enhance` parameter)

- `enhance=None` (default, auto): Adds a CLAHE-enhanced copy only for faces judged faded or low-contrast by the content contrast metric.
- `enhance=True`: Unconditionally generates standard and CLAHE-enhanced copies for every card face.
- `enhance=False`: Strictly produces standard faces, suppressing enhanced variants.

## 4. Files Added / Modified

- `recipeboxer/extraction/image_prep.py` (new): Full implementation of `PreparedFace`, `prepare_faces`, vectorized NumPy CLAHE, contrast evaluation, and encoding.
- `tests/test_wp_x2_image_prep.py` (new): 15 comprehensive unit and contract tests verifying low-contrast discrimination, CLAHE enhancement, rotation deskewing, resolution capping, exact labels, determinism, MIME correctness, and filename immunity.
- `_agents/remediation/WP-X2.md` (new): Work package remediation report.

## 5. Test Commands & Verification Results

All tests run using the isolated VM cache venv under `timeout 600`:

```bash
# 1. WP-X2 unit and contract test suite
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_x2_image_prep.py -q -p no:randomly
# Result: 15 passed in 1.52s

# 2. Anti-cheat integrity suite
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat -q -p no:randomly
# Result: 10 passed in 1.49s

# 3. Preprocessing regression check
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_preprocessing.py -q -p no:randomly
# Result: 30 passed in 1.46s
```

## 6. Contract Deviations

None. Adheres precisely to WAVE 4 Contracts §1.8.

## 7. Downstream Integration (Requests for WP-X4 / WP-X5)

WP-X4 (transcription pass) can import and consume `prepare_faces` directly:

```python
from recipeboxer.extraction.image_prep import prepare_faces, PreparedFace

prepared_faces: list[PreparedFace] = prepare_faces(
    paths=recipe_card_paths,
    profile_params=settings.profile_params(),
    enhance=None,  # Auto-detects faded cards and appends CLAHE copy
)
```

## 8. Risks & Mitigations

- **Memory overhead on multi-page decks**: Images are processed sequentially in a generator-style loop and encoded to binary buffers; PIL images are closed promptly.
- **Dependency bloat**: Implemented in standard NumPy and Pillow without requiring OpenCV, PyTorch, or native C build dependencies.
