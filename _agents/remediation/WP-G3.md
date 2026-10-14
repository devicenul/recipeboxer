# WP-G3: Capture Metadata & Natural Sorting Remediation

## Summary
Implemented capture metadata extraction (`read_capture_meta`) and temporal proximity helpers (`capture_gap_seconds`) under `recipeboxer/ingestion/capture.py`, and wired them additively into `recipeboxer/ingestion/scanner.py`.

- **EXIF Extraction:** Extracts `DateTimeOriginal` (EXIF 36867/36868/306), `SubSecTimeOriginal` (EXIF 37521/37520/37522), `OffsetTimeOriginal` (EXIF 36881/36880/36882), and device `Make`/`Model` (EXIF 271/272).
- **Fallback:** Falls back gracefully to filesystem `mtime` with nanosecond/millisecond resolution (`source="mtime"`), or `source="none"` if unavailable/missing.
- **Natural Sorting:** Maintains generic alphanumeric sorting (`natural_sort_key`) splitting purely on digit runs. `natural_sort_index` conveys order only without inspecting semantic tokens (INV-AC2).
- **Scanner Wiring:** `ScannedCard` carries `capture_meta` (`CaptureMeta`), convenience property `.capture`, and `natural_sort_index`. `validate_and_load_card` and `scan_directory` populate these fields without altering existing attributes.
- **Temporal Gap:** `capture_gap_seconds(a, b, signed=False)` computes temporal difference between `CaptureMeta`, `ScannedCard`, or `datetime` instances, accommodating mixed timezone awareness and subsecond resolution.

## Files Changed
- `recipeboxer/ingestion/capture.py` (new): Implements `read_capture_meta` and `capture_gap_seconds`.
- `recipeboxer/ingestion/scanner.py`: Added additive fields `capture_meta`, `natural_sort_index`, and `.capture` to `ScannedCard`; wired metadata extraction into `validate_and_load_card` and `scan_directory`.
- `tests/test_wp_g3_capture.py` (new): 10 targeted test cases covering full EXIF, partial EXIF, mtime fallback, nonexistent file, gap calculations, natural sorting, misleading name invariance (INV-AC2), and scanner integration.
- `_agents/remediation/WP-G3.md` (this report).

## Commands & Results
- **WP-G3 Tests:**
  `PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_g3_capture.py -v -p no:randomly`
  Result: 10 passed in 0.12s.
- **Scanner / Ingestion Tests:**
  `PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_ingestion.py -v -p no:randomly`
  Result: 16 passed in 0.44s.
- **Anti-cheat Invariants:**
  `PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat -v -p no:randomly`
  Result: 10 passed in 1.93s (INV-AC1, INV-AC2, INV-AC3, INV-AC4 all verified).

## Contract Deviations
None. Adheres strictly to `WAVE4_CONTRACTS.md` §1.5, §2 and `REMEDIATION_PLAN.md` §9, §9.1.

## Requests for Other WPs
- **WP-G5 / WP-G10:** Can consume `capture_gap_seconds` and `card.capture_meta.natural_sort_index` for temporal proximity and sequence prior scoring.

## Risks
None identified. Additive fields preserve full backward compatibility with existing `ScannedCard` usage across callers and fixtures.
