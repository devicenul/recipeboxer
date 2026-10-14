# Remediation Report: WP-X3 (Recipe Schema v2) & WP-X11 (Exporters to v2)

**Work Package IDs**: WP-X3, WP-X11  
**Authoritative Contracts**: `_agents/remediation/WAVE4_CONTRACTS.md` §1.9, `_agents/remediation/WAVE3_CONTRACTS.md` §0, `REMEDIATION_PLAN.md` §8.2, §8.3  
**Status**: COMPLETE  

---

## 1. Executive Summary

Work packages WP-X3 (Recipe Schema v2) and WP-X11 (Exporters to v2) have been fully implemented, verified, and integrated into the RecipeBoxer codebase. 

- **WP-X3 (Recipe Schema v2)** introduces a structured, Gemini-multimodal friendly recipe schema supporting multi-section recipes (e.g. Crust, Filling), verbatim transcription preservation (`original_text`) separate from normalized text (`normalized_text`), fine-grained legibility tracking (`Legibility.CLEAR`, `Legibility.UNCERTAIN`, `Legibility.ILLEGIBLE`) with character-range `IllegibleSpan` models, provenance tracking, and an authoritative `.to_scoring_dict()` representation conforming strictly to `evals/scoring`.
- **WP-X11 (Exporters to v2)** updates all 7 export formats (Markdown, DOCX OpenXML, PDF 1.4, Google Docs batchUpdate/HTML/JSON, Plan to Eat CSV, Cooklang, Schema.org JSON-LD, plus raw JSON) to support nested sections, `display_text` resolution (prioritizing `normalized_text` with fallback to `original_text`), ISO 8601 duration formatting, and an optional `include_transcription_notes: bool = False` flag across all exporters.
- **Consumer Updates**: Updated agent skill parsers (`vintage_handwriting_ocr/parser.py`), evaluation scoring bridges (`evals/scoring/evaluator.py`, `evals/metrics/extraction.py`), and frontend TypeScript type definitions (`frontend/src/types/recipe.ts`).
- **Test Coverage**: Added dedicated unit suites `tests/test_wp_x3_schema.py` and `tests/test_wp_x11_exporters.py` testing schema constraints and all exporters on the canonical fictitious Grandma's Strawberry Rhubarb Pie. All 287 targeted exporter, model, and anticheat tests pass with zero regressions.

---

## 2. WP-X3: Schema v2 Implementation Details

Implemented in `recipeboxer/models/recipe.py` and exported through `recipeboxer/models/__init__.py`.

### 2.1 Core Models & Classes

| Class | Description | Key Attributes |
|---|---|---|
| `Legibility` | Enum for transcription confidence | `CLEAR`, `UNCERTAIN`, `ILLEGIBLE` |
| `IllegibleSpan` | Sub-string illegibility span | `start: int`, `end: int`, `reason: Optional[str]` |
| `TextSpan` | General text span with confidence/legibility | `text: str`, `confidence: float`, `source_face: int`, `legibility: Legibility` |
| `IngredientLine` | Verbatim & normalized ingredient line | `original_text: str`, `normalized_text: Optional[str]`, `quantity`, `unit`, `item`, `preparation`, `confidence`, `source_face`, `legibility`, `illegible_spans` |
| `Step` | Sequential instruction step | `original_text: str`, `text: Optional[str]`, `normalized_text: Optional[str]`, `confidence`, `source_face`, `legibility`, `illegible_spans` |
| `RecipeSection` | Logical section container | `name: Optional[str]`, `title` property/setter alias, `ingredients: List[IngredientLine]`, `steps: List[Step]` |
| `TimeValue` | Verbatim and normalized duration | `original_text: Optional[str]`, `minutes: Optional[int]`, `normalized_iso: Optional[str]`, `display_text: str`, `__eq__` with str |
| `YieldValue` | Verbatim and normalized yield/servings | `original_text: Optional[str]`, `normalized_servings: Optional[float]`, `unit: Optional[str]`, `display_text: str`, `__eq__` with str |
| `Provenance` | Extraction lineage and cost metadata | `faces: List[str]`, `models: List[str]`, `prompt_versions: Dict[str, str]`, `profile`, `cost` |
| `RecipeWarning` | Extraction verification warning | `code: str`, `message: str`, `field_path: Optional[str]` |
| `Recipe` | Authoritative Recipe Schema v2 | `recipe_id`, `title`, `title_is_inferred`, `description`, `headnote`, `notes`, `sections`, `prep_time`, `cook_time`, `total_time`, `yield_value`, `tags`, `source_notes`, `provenance`, `warnings`, `confidence`, `card_ids`, `image_urls` |
| `RecipeSchema` | Direct alias to `Recipe` | For backwards compatibility with existing codebase imports |

### 2.2 Field Validators & Constraints

- **`confidence`**: Validated on `Recipe`, `IngredientLine`, and `Step` to ensure values are strictly in range $[0.0, 1.0]$.
- **`source_face`**: Validated on `IngredientLine` and `Step` to ensure values are non-negative ($\ge 0$).
- **`title`**: Validated to ensure non-empty, stripped text; rejects empty or whitespace-only titles.
- **`tags`**: Stripped and deduplicated case-insensitively, preserving canonical casing of first appearance.
- **Verbatim Text Integrity**: `original_text` preserves bit-for-bit verbatim card transcription. Any parsed or cleaned text resides strictly in `normalized_*` fields.

### 2.3 Helper Methods & Properties

- **`IngredientLine.display_text`**: Returns `normalized_text` if non-empty, else `original_text`, else joined `quantity + unit + item`.
- **`Step.display_text`**: Returns `normalized_text` if non-empty, else `text`, else `original_text`.
- **`Recipe.all_ingredients()`**: Flattened list of all `IngredientLine` objects across all sections.
- **`Recipe.all_steps()`**: Flattened list of all `Step` objects across all sections.
- **`Recipe.ingredients`**: Property returning a list of `str` display texts for all ingredients.
- **`Recipe.directions`**: Property returning a list of `str` display texts for all steps.
- **`Recipe.yield_servings`**: Property getter and setter bridging `yield_value.display_text`.
- **`Recipe.flagged_count()`**: Counts warnings, uncertain/illegible lines, illegible spans, and low-confidence items.
- **`Recipe.needs_review()`**: Returns `True` if `flagged_count() > 0` or overall `confidence < 0.8`.
- **`Recipe.to_scoring_dict()`**: Returns the exact dictionary shape expected by `evals/scoring`:
  ```json
  {
    "title": "Grandma's Strawberry Rhubarb Pie",
    "yield": "1 9-inch pie (8 slices)",
    "ingredients": [
      {
        "raw": "2 1/2 cups all-purpose flour",
        "quantity": "2 1/2",
        "unit": "cups",
        "item": "all-purpose flour",
        "section": "Crust"
      }
    ],
    "directions": ["1. Cut butter into flour..."],
    "notes": ["Family notes..."]
  }
  ```

### 2.4 Backwards-Compatible Coercion

An `@model_validator(mode="before")` on `Recipe` cleanly coerces legacy flat data into v2 sections without raising:
- Flat list of `ingredients` strings $\to$ `RecipeSection(name=None, ingredients=[...])`.
- Flat list of `directions` strings $\to$ `RecipeSection(name=None, steps=[...])`, filtering empty lines and normalizing step numbers.
- String durations for `prep_time`, `cook_time`, `total_time` $\to$ `TimeValue(original_text=val)`.
- String or numeric `yield_servings` $\to$ `YieldValue(original_text=val)`.
- String notes or headnotes $\to$ `TextSpan` models.

---

## 3. WP-X11: Exporters to v2 Implementation Details

All exporters support:
1. Multi-section recipes with distinct section headings.
2. `display_text` resolution prioritizing `normalized_text` with fallback to `original_text`.
3. `include_transcription_notes: bool = False` parameter (default `False`, enabled when requested).
4. Full preservation of metadata (`prep_time`, `cook_time`, `total_time`, `yield_value`).

| Exporter | File | Updates Made |
|---|---|---|
| **Markdown** | `recipeboxer/exporter/markdown_export.py` | Added section headings (`### Section`), YAML frontmatter times/yield, `## Transcription Notes & Verification` section when enabled. |
| **DOCX** | `recipeboxer/exporter/docx_export.py` | OpenXML Heading3 for sections, metadata table with times/yield, dedicated `Transcription Notes` section when enabled. |
| **PDF** | `recipeboxer/exporter/pdf_export.py` | PDF 1.4 typography with section subheadings, metadata box with times/yield, multi-page overflow protection, `TRANSCRIPTION NOTES` block when enabled. |
| **Google Docs** | `recipeboxer/exporter/google_docs.py` | BatchUpdate requests with HEADING_3 sections, HTML export with section classes, JSON export, and `Transcription Notes` blocks. |
| **Plan to Eat** | `recipeboxer/exporter/plan_to_eat.py` | RFC 4180 9-column CSV, multiline ingredients/directions, times/yield, transcription notes appended to Description when enabled. |
| **Cooklang** | `recipeboxer/exporter/cooklang.py` | `.cook` recipe text with section headers (`-- Section:`), ingredient markup (`@item{qty%unit}`), and `>> warning:`, `>> note:` headers. |
| **JSON-LD** | `recipeboxer/exporter/json_ld.py` | Schema.org `Recipe` JSON-LD, ISO 8601 durations (`prepTime`, `cookTime`, `totalTime`), and transcription notes in `comment`. |
| **Raw JSON** | `recipeboxer/exporter/json_export.py` | Verified full lossless serialization and validation against v2 schema. |

---

## 4. Consumer Updates

1. **Paleography / Agent Skills (`recipeboxer/agent/skills/vintage_handwriting_ocr/parser.py`)**:
   - Added `normalize_ingredient_line`, `normalize_step`, and `normalize_recipe_fields`.
   - Strictly enforces the contract: writes exclusively to `normalized_*` attributes; never alters or overwrites `original_text`, leaving verbatim card text intact.
2. **Evaluator & Scoring Metrics (`evals/scoring/evaluator.py`, `evals/metrics/extraction.py`)**:
   - Updated `score_recipe()` and `evaluate_deck()` to detect `Recipe` objects and call `.to_scoring_dict()`.
   - Supports dictionaries containing v2 `"sections"` by parsing into `Recipe` and calling `.to_scoring_dict()`.
3. **Frontend TypeScript Types (`frontend/src/types/recipe.ts`)**:
   - Added TypeScript interfaces: `Legibility`, `TextSpan`, `IllegibleSpan`, `RecipeWarning`, `Provenance`, `TimeValue`, `YieldValue`, `IngredientLine`, `Step`, `RecipeSection`, `Recipe`, and `RecipeSchema = Recipe`.

---

## 5. Verification & Test Results

### 5.1 New Test Suites

- **`tests/test_wp_x3_schema.py`** (8 tests):
  1. `test_schema_v2_fictitious_pie_construction`: Canonical Grandma's Strawberry Rhubarb Pie verification.
  2. `test_confidence_validation`: Strict bounds checking $[0.0, 1.0]$.
  3. `test_source_face_validation`: Non-negative face indices ($\ge 0$).
  4. `test_title_validation`: Non-empty stripped string enforcement.
  5. `test_tag_cleaning_and_deduplication`: Deduplication and trimming.
  6. `test_to_scoring_dict_shape`: Exact dictionary structure matching `evals/scoring`.
  7. `test_round_trip_json_serialization`: Lossless Pydantic and `JsonExporter` round-trip.
  8. `test_backward_compatibility_v1_coercion`: Flat string coercion into v2 sections.

- **`tests/test_wp_x11_exporters.py`** (8 tests):
  1. `test_markdown_exporter_v2`: Section headings, display text, transcription notes.
  2. `test_docx_exporter_v2`: OpenXML document structure, sections, transcription notes.
  3. `test_pdf_exporter_v2`: Binary PDF 1.4 layout, sections, transcription notes.
  4. `test_google_docs_exporter_v2`: BatchUpdate requests, HTML, JSON archives.
  5. `test_plan_to_eat_exporter_v2`: CSV columns, section ingredients, times.
  6. `test_cooklang_exporter_v2`: Cooklang markup, sections, warnings.
  7. `test_json_ld_exporter_v2`: Schema.org metadata, ISO 8601 durations, comments.
  8. `test_json_exporter_v2`: Lossless JSON schema validation.

### 5.2 Targeted Suite Execution

```
pytest tests/test_wp_x3_schema.py tests/test_wp_x11_exporters.py tests/test_models.py \
       tests/test_exporters.py tests/test_docx_export.py tests/test_pdf_export.py \
       tests/test_google_docs_export.py tests/test_cooklang_export.py \
       tests/test_markdown_export.py tests/test_plan_to_eat_csv.py \
       tests/test_paprika_export.py tests/test_export_curation.py \
       tests/test_export_endpoints.py tests/test_challenger_m3_curation_document_exporters.py \
       tests/test_challenger_2_sequence_exporters.py tests/test_eval_metrics.py \
       tests/test_challenger_m3_extraction.py tests/test_wp_e4_scorer.py \
       tests/test_wp_e5_edge_cases.py tests/anticheat
```
**Result**: **287 passed, 0 failed in 4.36s** (100% pass rate).

### 5.3 Full Repository Test Suite Execution

```
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 1800 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" -q -p no:randomly
```
- **Result**: **2092 passed, 9 skipped, 13 failed (all 13 pre-existing baseline failures: 6 zip-slip Windows path tests + 7 headless tkinter browse directory tests), 0 new regressions**.
- **Anticheat**: 10 passed, 0 failed.

---

## 6. Diffstat Summary

```
 evals/metrics/extraction.py                        |   9 +
 evals/scoring/evaluator.py                         |   9 +
 frontend/src/types/recipe.ts                       |  98 ++-
 recipeboxer/agent/skills/vintage_handwriting_ocr/__init__.py |   7 +
 recipeboxer/agent/skills/vintage_handwriting_ocr/parser.py   |  34 +-
 recipeboxer/exporter/cooklang.py                   |  94 ++-
 recipeboxer/exporter/docx_export.py                | 127 +++-
 recipeboxer/exporter/google_docs.py                | 402 +++++++++---
 recipeboxer/exporter/json_ld.py                    |  94 ++-
 recipeboxer/exporter/markdown_export.py            | 187 +++++-
 recipeboxer/exporter/paprika.py                    |  84 ++-
 recipeboxer/exporter/pdf_export.py                 | 160 ++++-
 recipeboxer/exporter/plan_to_eat.py                |  98 ++-
 recipeboxer/models/__init__.py                     |  22 +
 recipeboxer/models/recipe.py                       | 688 ++++++++++++++++++---
 tests/test_wp_x3_schema.py                         | 346 +++++++++++
 tests/test_wp_x11_exporters.py                     | 342 +++++++++++
 _agents/remediation/WP-X3X11.md                    | 180 ++++++
 18 files changed, 2627 insertions(+), 356 deletions(-)
```
