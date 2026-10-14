# Work Package Completion Report: WP-O1 (Spike: Complementary Local OCR)

**Package Owner:** WP-O1 (flash, spike)  
**Branch:** `remediation/phase0`  
**Status:** Completed & Evaluated  

---

## 1. Executive Summary & Findings

In accordance with `_agents/remediation/WAVE4_CONTRACTS.md` (WP-O1), `_agents/remediation/WAVE3_CONTRACTS.md` §0, and `_agents/remediation/REMEDIATION_PLAN.md` §8.4 (OCR is EVIDENCE ONLY, never a fallback; INV-AC6), this work package evaluated candidate local OCR engines on Python 3.13 in the Linux VM, implemented `recipeboxer/vision/ocr.py` with safe optional imports and boundary cue detection, and benchmarked accuracy, latency, and boundary cue precision across all 48 Tier T0 sample cards.

### 1.1 Candidate Engine & Python 3.13 Compatibility
- **RapidOCR (`rapidocr-onnxruntime` v1.2.3)**:
  - Installed into project venv using pip cache redirection (`--cache-dir "$PROJECT_CACHE_DIR/pip"`) without modifying `pyproject.toml` or `requirements.txt`.
  - Fully compatible with Python 3.13.5 (x86_64 Linux VM). Clean import and initialization.
  - Dependencies installed: `onnxruntime` (1.31.0), `opencv-python` (5.0.0.93), `Shapely` (2.2.0), `pyclipper` (1.4.0), `flatbuffers`, `six`.
- **Install Footprint & Size Impact**:
  - `onnxruntime`: 68 MB
  - `opencv-python` (`cv2`): 72 MB
  - `rapidocr_onnxruntime`: 14 MB (including ONNX PP-OCRv4 detection & recognition models)
  - `shapely`: 3.7 MB
  - `pyclipper`: 3.4 MB
  - **Total package content**: ~161 MB uncompressed.
  - **Site-packages expansion**: 493 MB → 774 MB (+281 MB on disk).

### 1.2 Alternatives Considered
1. **Tesseract (`pytesseract`)**: Lightweight Python wrapper (~50 KB), but requires system binary (`apt install tesseract-ocr`, ~40 MB) and system-level `tessdata`. Extremely fragile for self-contained PyInstaller packaging across Windows/macOS/Linux.
2. **docTR / PaddleOCR / EasyOCR**: Heavy deep learning dependencies requiring PyTorch (`torch` > 1.5 GB) or PaddlePaddle (`paddle` > 1 GB). Unviable for a lightweight desktop/CLI tool.
3. **Pure Text/Regex Boundary Heuristics**: Zero bundle overhead, applied to LLM transcripts.

---

## 2. Implementation: `recipeboxer/vision/ocr.py`

Implemented `recipeboxer/vision/ocr.py` with zero hard dependencies on `rapidocr_onnxruntime` or `cv2` at the module level:
1. `OCRLine`: Immutable frozen dataclass `(text: str, bbox: list[list[float]], confidence: float)`.
2. `LocalOCR`:
   - `available() -> bool`: Dynamically verifies if `rapidocr_onnxruntime` is installed and loadable.
   - `read(image) -> list[OCRLine]`: Converts file paths, PIL Images, numpy arrays, or raw bytes into normalized `OCRLine` items with 4-point bounding polygons and confidence scores.
   - **Fail-fast behavior (INV-AC6)**: Raises `RuntimeError` if invoked when `available()` is False. Never fabricates OCR output, never bypasses multimodal LLM transcription.
3. `boundary_cues(lines) -> dict`:
   - Generic boundary cue extractor operating over text lines or `OCRLine` objects.
   - Identifies forward continuation cues (`over`, `cont`, `continued`, `pto`, `turn over`, `see back`, `see card`, `->`, `>>>`).
   - Identifies reverse continuation headers (`(Directions continued...)`, `(instructions continued...)`, `continued from`, `cont`).
   - Extracts sequential step numbers (`1.`, `Step 2:`, `3)`), including OCR artifact corrections (`l,`, `I.` → 1).
   - Extracts page and card numbering at top/bottom boundaries (`Page X`, `X of Y`, `- 2 -`).
   - Detects incomplete sentence cutoffs at card trailing edges (trailing punctuation/conjunctions).
   - Zero corpus-specific or recipe-specific tokens; zero filename semantics (strictly adheres to INV-AC1..INV-AC3).

---

## 3. Evaluation on Tier T0 Sample Cards (48 Cards, 24 Recipes)

Offline evaluation was executed via `_agents/remediation/scratch_o1/eval_t0_ocr.py` on the 48 Tier T0 sample cards across all 4 modalities.

### 3.1 Performance Metrics by Modality

| Modality | Cards | Mean Latency | Mean CER vs GT | Cue Precision | Cue Recall | Cue F1 | Cue Acc |
|---|---|---|---|---|---|---|---|
| **HANDWRITTEN_CURSIVE** | 25 | 2,128.8 ms | 0.6445 | **1.000** | **1.000** | **1.000** | **1.000** |
| **TYPEWRITTEN** | 9 | 1,956.9 ms | 0.6182 | **1.000** | **1.000** | **1.000** | **1.000** |
| **MIXED_MEDIA (Damaged)** | 7 | 1,474.7 ms | 0.6709 | **1.000** | 0.667 | 0.800 | 0.857 |
| **PRINTED_CLIPPING** | 7 | 1,795.6 ms | 0.7225 | **1.000** | 0.000 | 0.000 | 0.571 |
| **Total / Overall** | **48** | **1,953.4 ms** | **0.6559** | **1.000** | **0.818** | **0.900** | **0.917** |

### 3.2 High-Level Comparison (Typed/Clipping vs Handwritten)

- **Handwritten Cursive (25 cards)**:
  - Latency: ~2.1s / card
  - CER: 0.6445 (Mean), 0.6981 (Median)
  - Cue Matrix: **TP=14, FP=0, FN=0, TN=11** (100% Precision, 100% Recall, 100% F1)
- **Typed / Clipping (16 cards: Typewritten + Printed Clipping)**:
  - Latency: ~1.9s / card
  - CER: 0.6646 (Mean), 0.7702 (Median)
  - Cue Matrix: **TP=4, FP=0, FN=3, TN=9** (100% Precision, 57.1% Recall, 72.7% F1)
  - *Analysis of False Negatives*: The 3 missed cues occurred on `PRINTED_CLIPPING` cards where small corner "PTO >>>" markers were omitted during DBNet bounding box detection due to multi-column newsprint contrast.

### 3.3 Key Takeaways
1. **Cue Precision is 1.000**: When `boundary_cues` detects a forward cue, it is 100% reliable (zero false alarms). Cooking instructions containing culinary "over" (e.g. "serve over ice", "prick surface all over") are correctly disambiguated.
2. **Latency is ~2.0s / card**: Running RapidOCR adds ~2 seconds of CPU inference per card.
3. **High CER (0.62–0.72)**: RapidOCR cannot transcribe cursive handwriting or styled clipping fonts reliably, confirming §8.4 and INV-AC6: OCR is suitable only as cheap boundary evidence and grounding verification, never as a recipe transcription fallback.

---

## 4. Recommendation: DEFER / OPTIONAL PLUGIN (OFF BY DEFAULT)

### Recommendation: **DEFER inclusion in default release bundle; keep as optional plugin.**

1. **Bundle-Size Impact on PyInstaller (`recipeboxer.spec`)**:
   - Bundling RapidOCR adds **~161 MB uncompressed dependencies** (`onnxruntime` 68 MB, `cv2` 72 MB, ONNX models 14 MB).
   - In PyInstaller single-file binaries, this increases the download/executable size by **~100–140 MB**, doubling the binary size.
   - Introduces C++ runtime dependencies (`libonnxruntime.so`, `libopencv_*.so`) that increase cross-platform build brittleness on Windows and macOS.
2. **Architectural Role (INV-AC6)**:
   - The primary transcription and visual grouping path is driven by multimodal LLMs (Gemini Flash / Flash-Lite / Pro).
   - OCR only provides secondary evidence (top-k pre-screening cues and grounding cross-checks on numbers/units).
3. **Packaging Strategy**:
   - Keep `recipeboxer/vision/ocr.py` in the core codebase with its clean zero-dependency design.
   - Offer OCR as an optional installation extra: `pip install "recipeboxer[ocr]"` (specifying `rapidocr-onnxruntime`).
   - Leave OCR disabled in the default PyInstaller desktop builds unless Phase 3 ablations (WP-G9) on large homogeneous collections (T2/T3) prove that local OCR produces grouping improvements that outweigh the binary size penalty.

---

## 5. Files Changed

- `recipeboxer/vision/ocr.py` (new): Optional local OCR class and generic text boundary cue parser.
- `tests/test_wp_o1_ocr.py` (new): 12 unit tests covering OCRLine, LocalOCR availability/missing package monkeypatching, mock engine execution, and boundary cue detection.
- `_agents/remediation/scratch_o1/eval_t0_ocr.py` (new): Offline spike analysis script evaluating T0 sample cards.
- `_agents/remediation/scratch_o1/eval_results.json` (new): Detailed per-card and per-modality evaluation metrics.
- `_agents/remediation/WP-O1.md` (new): This work package report.

---

## 6. Verification & Test Results

Executed commands:
```bash
# 1. WP-O1 Unit and Behavior Tests
$py -m pytest tests/test_wp_o1_ocr.py -q -p no:randomly
# Result: 12 passed in 0.14s

# 2. Anticheat Static Invariants & Canary Tests
$py -m pytest tests/anticheat -q -p no:randomly
# Result: 10 passed in 1.36s

# 3. Combined Targeted Suite
$py -m pytest tests/test_wp_o1_ocr.py tests/anticheat -q -p no:randomly
# Result: 22 passed in 1.82s
```

---

## 7. Contract Deviations & Touch Outside Ownership

- **Contract Deviations**: None. Adheres strictly to `WAVE4_CONTRACTS.md`, `WAVE3_CONTRACTS.md` §0, and INV-AC1..INV-AC6.
- **Touched outside ownership**: None. Candidate packages installed solely in virtual environment; `pyproject.toml` and `requirements.txt` untouched.

---

## 8. Coordination & Requests for Next Waves

- **Orchestrator**: In Phase 5 packaging, consider adding an optional `[ocr]` extra in `pyproject.toml` (`rapidocr-onnxruntime`) rather than adding it to core dependencies.
- **WP-G5 / WP-G6 (Wave 5 - Grouping)**: When `LocalOCR.available()` is True, `boundary_cues()` can supply cheap `has_continuation_cue` / `has_continuation_start` / `page_markers` features to improve top-k screening before spending LLM tokens.
- **WP-X6 (Wave 5 - Grounding)**: Can invoke `LocalOCR` as a second opinion to verify printed numbers and units on typewritten cards.
