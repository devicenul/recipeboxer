# WP-00a: Static Anti-Cheat Test Suite & Invariant Enforcement

## 1. Summary

Work Package **WP-00a** establishes static anti-cheat tests enforcing invariants **INV-AC1..INV-AC3** against the production package `recipeboxer/`.

The test file is located at `tests/anticheat/test_static_invariants.py` with companion package initialization at `tests/anticheat/__init__.py`.

All tests that detect active cheats in the legacy codebase are marked `@pytest.mark.xfail(strict=True, reason="Cheat present; removed in Phase 2 WP-C1..C6 - flip to hard pass then")`. Tests that verify clean architectural boundaries already pass normally.

---

## 2. Invariants & What the Tests Check

### INV-AC1: `test_no_answer_key_paths`
- **Rule**: Production code must never reference test fixtures, answer keys, manifest paths, or eval environment variables.
- **Checks**:
  - Scans AST string constants and source text for forbidden tokens: `'tests/fixtures'`, `'sample_cards'`, `'manifest.json'`, `'ground_truth'`, `'RB_EVAL'`.
  - Checks AST path joins for `/ "tests"` and `os.path.join(..., 'tests', ...)`.
  - Legitimate exceptions for `manifest.json` are permitted solely for `.recipeboxer` project archive serialization and web app static manifest serving (`storage/archive.py`, `models/archive.py`, `agent/orchestrator.py`, `api/app.py`).
- **Status**: **XFAIL** (strict) — detects 23 offending locations across `extractor.py`, `semantic.py`, and `router.py`.

### INV-AC2: `test_no_filename_semantics`
- **Rule**: Core pipeline stages (`vision`, `correlation`, `extraction`, `ai`, `agent`, `ingestion`) must not infer card roles, sequencing, modality, or recipe identity from filename tokens or regexes.
- **Checks**:
  - Regex calls (`re.search`, `re.match`, `re.compile`, `re.findall`, `re.finditer`) for patterns matching `card\s*\d`, `part\s*\d`, `pt\s*\d`, and `front|back`.
  - Comparison and membership expressions (`in`, `not in`, `==`, `!=`) where a semantic token (`front`, `back`, `card1`..`card9`, `part1`..`part9`, `pt1`..`pt9`, category codes `vhc`, `clp`, `dmg`, `mcs`, `sta`, `typ`, `_vhc_` style, or recipe keywords `cinnamon`, `lemon`, `fudge`, `pecan`, `unruled`, `stain`, `clipping`, `typewriter`) is evaluated against variables representing filenames/identifiers (`fname`, `fn1`, `fn2`, `card_id`, `filename`, `name`, `path`, `stem`, `c_str`, `all_names`, `clean_cid`, `s1`, `s2`, or `s`).
- **Status**: **XFAIL** (strict) — detects 56 offending locations across `fingerprint.py`, `heuristics.py`, `router.py`, and `extractor.py`.

### INV-AC3: `test_no_fixture_content`
- **Rule**: Production code must not hardcode corpus recipe titles, distinctive ingredient lines, recipe IDs, or fixture image SHA-256 digests.
- **Checks**:
  - Generates a deny-list at test time from `tests/fixtures/sample_cards/manifest.json`:
    - All 24 ground-truth recipe titles (case-insensitive substring).
    - All 24 ground-truth recipe IDs (case-insensitive substring).
    - All 48 fixture image SHA-256 hashes (case-insensitive substring).
    - All distinctive multi-word ingredient lines (>= 4 words), excluding universal pantry staples (e.g. `'1 tsp baking soda'`, `'2 cups all-purpose flour'`).
  - Asserts none appear in scoped production files.
  - *Note*: In Phase 1, the deny-list will additionally be generated directly by the scorer from the encrypted vault (`evals/vault/<tier>.gt.enc`).
- **Status**: **XFAIL** (strict) — detects 8 offending locations in `extractor.py` (canned recipe titles and ingredients).

### INV-AC4: `test_no_cardforge_or_evals_import`
- **Rule**: Production modules under `recipeboxer/` must never import `cardforge`, `evals`, or `tests`.
- **Checks**:
  - AST inspection of `ast.Import` and `ast.ImportFrom` statements.
  - Verifies that nothing outside `recipeboxer/evals` imports `recipeboxer.evals` (except allowed in `recipeboxer/cli.py`).
- **Status**: **PASSED** (0 violations).

---

## 3. Scoping & False-Positive Handling

### Package Scoping & Exclusions
- Scoped to `recipeboxer/`.
- `recipeboxer/api/static/**` is excluded (static web UI assets).
- `TEMPORARY_EXCLUSIONS`:
  - `recipeboxer/tools/card_generator.py` and `recipeboxer/tools/__init__.py`: TODO(WP-V2) moving to standalone `cardforge/` project.
  - `recipeboxer/evals`: TODO(WP-E3) moving to top-level `evals/` runner/harness.

### False-Positive Precautions
1. **Enum Members & Normalizers**:
   - `CardRole.FRONT_ONLY` and `_norm(r)` in `recipeboxer/vision/heuristics.py:457` (checking `s in ("FRONT_ONLY", "FRONT", "START", "SEQUENCE_START")`) operate on `_get_enum_str(r)`. These evaluate role enum strings, not filenames, and are not flagged.
2. **Multimodal LLM Prompt Text**:
   - Prompt templates like `SEMANTIC_VERIFICATION_PROMPT` in `semantic.py` and `GEMINI_PROMPT_TEMPLATE` in `extractor.py` mention "front and back of card" in English prose to instruct the model. Because these are prompt template string constants and not comparisons against filename variables, they are not flagged.
3. **Card ID Sanitizer Generic Role Fallbacks**:
   - In `recipeboxer/ai/gemini.py:42-43`, `sanitize_card_id()` checks `clean.lower() in ("card_front", "card_back", "card_1", "card_2", ...)` to preserve generic safe role identifiers when already sanitized. This specific check is explicitly permitted.
4. **Generic Pantry Staples**:
   - Universal staple ingredient lines with 4 words (e.g. `'1 tsp baking soda'` or `'2 cups all-purpose flour'`) can appear in normalizer examples (`vintage_handwriting_ocr/parser.py`) or LLM prompt formatting guides (`extractor.py`). These are filtered from the ingredient deny-list so only distinctive recipe-specific lines trigger violations.

---

## 4. Test Execution & Verification

### Commands Run

```bash
# Set up isolated cache and run anti-cheat tests
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" \
"$PROJECT_CACHE_DIR/venv/bin/python" -m pytest \
  -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
  tests/anticheat/test_static_invariants.py -v
```

**Standard Pytest Output**:
```
tests/anticheat/test_static_invariants.py::test_no_answer_key_paths XFAIL [ 25%]
tests/anticheat/test_static_invariants.py::test_no_filename_semantics XFAIL [ 50%]
tests/anticheat/test_static_invariants.py::test_no_fixture_content XFAIL [ 75%]
tests/anticheat/test_static_invariants.py::test_no_cardforge_or_evals_import PASSED [100%]

========================= 1 passed, 3 xfailed in 1.34s =========================
```

### Verification with `--runxfail`

```bash
# Run with --runxfail to confirm failure reasons and extract inventory
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" \
"$PROJECT_CACHE_DIR/venv/bin/python" -m pytest \
  -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
  tests/anticheat/test_static_invariants.py -v --runxfail
```

Result: 3 failed, 1 passed as expected.

---

## 5. Cheat Inventory (Offending Locations)

### Summary of Hits per File

| File | `test_no_answer_key_paths` | `test_no_filename_semantics` | `test_no_fixture_content` | Total Hits |
|---|:---:|:---:|:---:|:---:|
| `recipeboxer/correlation/semantic.py` | 6 | 0 | 0 | **6** |
| `recipeboxer/extraction/extractor.py` | 11 | 4 | 8 | **23** |
| `recipeboxer/vision/fingerprint.py` | 0 | 11 | 0 | **11** |
| `recipeboxer/vision/heuristics.py` | 0 | 25 | 0 | **25** |
| `recipeboxer/vision/router.py` | 6 | 16 | 0 | **22** |
| **Total** | **23** | **56** | **8** | **87** |

---

### Detailed Offending Hits

#### `recipeboxer/correlation/semantic.py` (6 hits)
- `semantic.py:55`: Contains forbidden string token `'sample_cards'`
- `semantic.py:55`: Path / join chain contains `'tests'` segment
- `semantic.py:55`: References `'manifest.json'` outside legitimate archive files
- `semantic.py:56`: Contains forbidden string token `'sample_cards'`
- `semantic.py:56`: Contains forbidden string token `'tests/fixtures'`
- `semantic.py:56`: References `'manifest.json'` outside legitimate archive files

#### `recipeboxer/extraction/extractor.py` (23 hits)
- `extractor.py:126`: Contains forbidden string token `'sample_cards'`
- `extractor.py:126`: Contains forbidden string token `'tests/fixtures'`
- `extractor.py:126`: References `'manifest.json'` outside legitimate archive files
- `extractor.py:132`: Contains forbidden string token `'sample_cards'`
- `extractor.py:132`: Path / join chain contains `'tests'` segment
- `extractor.py:132`: References `'manifest.json'` outside legitimate archive files
- `extractor.py:133`: Contains forbidden string token `'sample_cards'`
- `extractor.py:133`: Contains forbidden string token `'tests/fixtures'`
- `extractor.py:133`: References `'manifest.json'` outside legitimate archive files
- `extractor.py:146`: Contains forbidden string token `'ground_truth'`
- `extractor.py:190`: Contains forbidden string token `'sample_cards'`
- `extractor.py:773`: Semantic token `{'cinnamon'}` tested against filename var `{'all_names'}`: `'cinnamon' in all_names`
- `extractor.py:815`: Semantic token `{'lemon'}` tested against filename var `{'all_names'}`: `'lemon' in all_names`
- `extractor.py:831`: Contains ground-truth ingredient line: `'1/2 cup fresh lemon juice'`
- `extractor.py:847`: Semantic token `{'fudge'}` tested against filename var `{'all_names'}`: `'fudge' in all_names`
- `extractor.py:850`: Contains ground-truth recipe title: `'10-Minute Quick Chocolate Fudge'`
- `extractor.py:857`: Contains ground-truth ingredient line: `'3 cups semi-sweet chocolate chips'`
- `extractor.py:858`: Contains ground-truth ingredient line: `'1 can (14 oz) sweetened condensed milk'`
- `extractor.py:873`: Semantic token `{'pecan'}` tested against filename var `{'all_names'}`: `'pecan' in all_names`
- `extractor.py:876`: Contains ground-truth recipe title: `'Southern Pecan Pie'`
- `extractor.py:883`: Contains ground-truth ingredient line: `'1 unbaked 9-inch pie shell'`
- `extractor.py:885`: Contains ground-truth ingredient line: `'1 cup dark corn syrup'`
- `extractor.py:889`: Contains ground-truth ingredient line: `'1 1/2 cups pecan halves'`

#### `recipeboxer/vision/fingerprint.py` (11 hits)
- `fingerprint.py:320`: Semantic token `{'back'}` tested against filename var `{'fname'}`: `'back' in fname`
- `fingerprint.py:335`: Semantic token `{'front'}` tested against filename var `{'fname'}`: `'front' in fname`
- `fingerprint.py:336`: Semantic token `{'part1'}` tested against filename var `{'fname'}`: `'part1' in fname`
- `fingerprint.py:337`: Semantic token `{'card1'}` tested against filename var `{'fname'}`: `'card1' in fname`
- `fingerprint.py:338`: Semantic token `{'pt1'}` tested against filename var `{'fname'}`: `'pt1' in fname`
- `fingerprint.py:356`: Semantic token `{'lemon'}` tested against filename var `{'fname'}`: `'lemon' in fname`
- `fingerprint.py:356`: Semantic token `{'unruled'}` tested against filename var `{'fname'}`: `'unruled' in fname`
- `fingerprint.py:363`: Semantic token `{'cinnamon'}` tested against filename var `{'fname'}`: `'cinnamon' in fname`
- `fingerprint.py:368`: Semantic token `{'cinnamon'}` tested against filename var `{'fname'}`: `'cinnamon' in fname`
- `fingerprint.py:368`: Semantic token `{'stain'}` tested against filename var `{'fname'}`: `'stain' in fname`
- `fingerprint.py:369`: Semantic token `{'back'}` tested against filename var `{'fname'}`: `'back' in fname`

#### `recipeboxer/vision/heuristics.py` (25 hits)
- `heuristics.py:517`: Regex pattern `r'card\s*([0-9]+)[_\s]*(front|back)?'` inspects card/part/side numbers
- `heuristics.py:518`: Regex pattern `r'part\s*([0-9]+)[_\s]*(front|back)?'` inspects card/part/side numbers
- `heuristics.py:519`: Regex pattern `r'pt\s*([0-9]+)[_\s]*(front|back)?'` inspects card/part/side numbers
- `heuristics.py:523`: Regex pattern `r'card\s*([0-9]+)[_\s]*(front|back)?'` inspects card/part/side numbers
- `heuristics.py:524`: Regex pattern `r'part\s*([0-9]+)[_\s]*(front|back)?'` inspects card/part/side numbers
- `heuristics.py:525`: Regex pattern `r'pt\s*([0-9]+)[_\s]*(front|back)?'` inspects card/part/side numbers
- `heuristics.py:533`: Semantic token `{'back'}` tested against filename var `{'fn1'}`: `'back' in fn1`
- `heuristics.py:534`: Semantic token `{'back'}` tested against filename var `{'fn2'}`: `'back' in fn2`
- `heuristics.py:537`: Filename var `{'s1'}` tested against semantic token `{'front'}`: `s1 == 'front'`
- `heuristics.py:537`: Filename var `{'s2'}` tested against semantic token `{'back'}`: `s2 == 'back'`
- `heuristics.py:541`: Filename var `{'s1'}` tested against semantic token `{'back'}`: `s1 == 'back'`
- `heuristics.py:541`: Filename var `{'s2'}` tested against semantic token `{'front'}`: `s2 == 'front'`
- `heuristics.py:545`: Filename var `{'s1'}` tested against semantic token `{'front'}`: `s1 == 'front'`
- `heuristics.py:545`: Filename var `{'s2'}` tested against semantic token `{'front'}`: `s2 == 'front'`
- `heuristics.py:545`: Semantic token `{'back'}` tested against filename var `{'fn1'}`: `'back' not in fn1`
- `heuristics.py:545`: Semantic token `{'back'}` tested against filename var `{'fn2'}`: `'back' not in fn2`
- `heuristics.py:545`: Semantic token `{'front'}` tested against filename var `{'fn1'}`: `'front' not in fn1`
- `heuristics.py:545`: Semantic token `{'front'}` tested against filename var `{'fn2'}`: `'front' not in fn2`
- `heuristics.py:549`: Filename var `{'s1'}` tested against semantic token `{'front'}`: `s1 == 'front'`
- `heuristics.py:549`: Filename var `{'s2'}` tested against semantic token `{'front'}`: `s2 == 'front'`
- `heuristics.py:553`: Filename var `{'s1'}` tested against semantic token `{'back'}`: `s1 == 'back'`
- `heuristics.py:553`: Filename var `{'s2'}` tested against semantic token `{'back'}`: `s2 == 'back'`
- `heuristics.py:566`: Semantic token `{'back'}` tested against filename var `{'fn1'}`: `'back' not in fn1`
- `heuristics.py:566`: Semantic token `{'back'}` tested against filename var `{'fn2'}`: `'back' in fn2`
- `heuristics.py:566`: Semantic token `{'front'}` tested against filename var `{'fn1'}`: `'front' in fn1`

#### `recipeboxer/vision/router.py` (22 hits)
- `router.py:42`: Contains forbidden string token `'sample_cards'`
- `router.py:42`: Path / join chain contains `'tests'` segment
- `router.py:42`: References `'manifest.json'` outside legitimate archive files
- `router.py:43`: Contains forbidden string token `'sample_cards'`
- `router.py:43`: Contains forbidden string token `'tests/fixtures'`
- `router.py:43`: References `'manifest.json'` outside legitimate archive files
- `router.py:78`: Category code token `{'clp'}` checked in comparison: `'CLP' in s`
- `router.py:89`: Category code token `{'dmg'}` checked in comparison: `'DMG' in s`
- `router.py:91`: Category code token `{'typ'}` checked in comparison: `'TYP' in s`
- `router.py:97`: Category code token `{'mcs', 'sta', 'vhc'}` checked in comparison: `s in ('VHC', 'MCS', 'STA')`
- `router.py:98`: Category code token `{'_\vhc_'}` checked in comparison: `'_vhc_' in s.lower()`
- `router.py:99`: Category code token `{'_\mcs_'}` checked in comparison: `'_mcs_' in s.lower()`
- `router.py:100`: Category code token `{'_\sta_'}` checked in comparison: `'_sta_' in s.lower()`
- `router.py:401`: Semantic token `{'clipping'}` tested against filename var `{'c_str'}`: `'clipping' in c_str`
- `router.py:401`: Semantic token `{'newspaper'}` tested against filename var `{'c_str'}`: `'newspaper' in c_str`
- `router.py:401`: Semantic token `{'newsprint'}` tested against filename var `{'c_str'}`: `'newsprint' in c_str`
- `router.py:408`: Semantic token `{'typed'}` tested against filename var `{'c_str'}`: `'typed' in c_str`
- `router.py:408`: Semantic token `{'typewriter'}` tested against filename var `{'c_str'}`: `'typewriter' in c_str`
- `router.py:408`: Semantic token `{'typewritten'}` tested against filename var `{'c_str'}`: `'typewritten' in c_str`
- `router.py:415`: Semantic token `{'damaged'}` tested against filename var `{'c_str'}`: `'damaged' in c_str`
- `router.py:415`: Semantic token `{'mixed'}` tested against filename var `{'c_str'}`: `'mixed' in c_str`
- `router.py:415`: Semantic token `{'stained'}` tested against filename var `{'c_str'}`: `'stained' in c_str`

---

## 6. Cross-Check Against Known Cheats

| Known Cheat Location & Description | Remediation WP | Detected By | Test Status |
|---|---|---|---|
| `extractor.py:125-193`: Manifest lookup tables & SHA256 index | WP-C1 | `test_no_answer_key_paths` (lines 126, 132, 133, 146, 190) | Verified |
| `extractor.py:773-901`: Keyword & canned recipes | WP-C1 | `test_no_filename_semantics` (lines 773, 815, 847, 873) & `test_no_fixture_content` (lines 831, 850, 857, 858, 876, 883, 885, 889) | Verified |
| `semantic.py:48-71,262-278`: Manifest cluster lookup | WP-C2 | `test_no_answer_key_paths` (lines 55, 56) | Verified |
| `router.py:35-58`: Manifest modality lookup | WP-C3 | `test_no_answer_key_paths` (lines 42, 43) | Verified |
| `router.py:77-107`: Modality normalization category codes | WP-C3 | `test_no_filename_semantics` (lines 78, 89, 91, 97-100) | Verified |
| `router.py:399-427`: Filename description fallback | WP-C3 | `test_no_filename_semantics` (lines 401, 408, 415) | Verified |
| `fingerprint.py:253-379`: Offline filename keywords & side cues | WP-C4 | `test_no_filename_semantics` (lines 320, 335-338, 356, 363, 368, 369) | Verified |
| `heuristics.py:512-567`: Filename regex & side progression | WP-C5 | `test_no_filename_semantics` (lines 517-519, 523-525, 533-566) | Verified |

100% of known eval cheats are detected. When Phase 2 (WP-C1..C6) removes these cheats, removing the `@pytest.mark.xfail` annotations will flip these tests to hard passes.
