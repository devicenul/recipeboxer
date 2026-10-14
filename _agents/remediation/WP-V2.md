# Work Package Report: WP-V2 — Extract Synthetic Card Generator into `cardforge/`

## Executive Summary

Work Package **WP-V2** extracted the procedural synthetic recipe card generation engine and its 24 T0 templates out of the shipped `recipeboxer` application into an independent, top-level project: **`cardforge/`**.

`cardforge` contains its own `pyproject.toml`, documentation, CLI entrypoint, domain models, template catalogue, and comprehensive test suite. It has zero runtime or static dependencies on `recipeboxer`.

---

## Changes Implemented

### 1. New Standalone Package `cardforge/`
- **`cardforge/pyproject.toml`**: Configured build system (`setuptools`), metadata, and dependencies (`pillow>=11.0.0`, `pydantic>=2.0.0`), mapping CLI executable `cardforge = "cardforge.__main__:main"`.
- **`cardforge/README.md`**: Explains role as the synthetic recipe card generator used by RecipeBoxer evals, noting that it is intended to become its own repository and must never be imported by `recipeboxer`.
- **`cardforge/models.py`**: Self-contained data models and enums:
  - `CardModality`, `PaperSubstrate`, `CardRole`, `InkType`, `DegradationLevel` (all with `__str__` returning `value` for seamless cross-package string coercion).
  - `DegradationConfig`: Includes both `from_level` and `for_level` constructors.
  - `RecipeSchema`: Minimal standalone Pydantic model carrying ground-truth fields (`recipe_id`, `title`, `description`, `yield_servings`, `prep_time`, `cook_time`, `total_time`, `ingredients`, `directions`, `tags`, `source_notes`, `card_ids`, `image_urls`, `created_at`, `updated_at`).
  - `GeneratedCard`: Image scan and metadata container.
- **`cardforge/templates_t0.py`**: Extracted all 24 historical T0 archival recipe templates across 6 modalities.
- **`cardforge/generator.py`**: Procedural generation engine (paper substrates, ink rendering, degradation effects, front/back coordinate pairing, multi-card sets, and manifest generation including `dimensions` metadata).
- **`cardforge/__main__.py` & `cardforge/__init__.py`**: CLI entry point with identical flags (`-o`, `-n`, `-m`, `-s`, `-d`, `--seed`, `--format`, `--no-manifest`).

### 2. Removal from `recipeboxer`
- **Deleted**: `recipeboxer/tools/card_generator.py`.
- **Updated `recipeboxer/tools/__init__.py`**: Converted to docstring-only module.
- **Updated `recipeboxer/cli.py`**: Intercepts `generate`, `card-generator`, `generate-cards`, or `cardforge` subcommands and prints a redirect notice pointing to `python -m cardforge`.
- **Verified `recipeboxer.spec`**: Contains no references to `card_generator` or tools package.

### 3. Dependencies & Build Artifacts
- **`requirements-dev.txt`**: Added `-e ./cardforge`.
- **`.gitignore`**: Added `cardforge.egg-info/`.
- **Installed**: `cardforge` installed in editable mode (`pip install -e cardforge`) inside the project venv.

### 4. Test Migration & Updated Imports
- **`cardforge/tests/test_card_generator.py`**: Migrated from `tests/test_card_generator.py` (39 unit/integration tests).
- **Determinism Test**: Moved `test_generator_deterministic_reproducibility` from `tests/test_challenger_m2_empirical_rigor.py` into `cardforge/tests/test_card_generator.py`.
- **Updated Test Imports**:
  - `tests/test_sample_card_corpus.py`: `from cardforge import CardModality`
  - `tests/test_challenger_m2_empirical_rigor.py`: `from cardforge import CardModality`
  - `tests/test_eval_runner.py`: `from cardforge import CardModality`
  - `tests/test_challenger_m2_obfuscation_deep.py`: `from cardforge import CardModality`
  - `tests/test_card_generator.py`: Deleted (migrated to `cardforge/tests/`).
  - Preserved `tests/fixtures/loader.py` and `recipeboxer/evals/*` untouched for concurrent agents.

### 5. Isolation Enforcement & Invariants
- **`cardforge/tests/test_isolation.py`**: Added 3 static & runtime tests:
  1. `test_cardforge_source_never_imports_recipeboxer`: AST walk of all `cardforge/*.py` ensuring 0 imports of `recipeboxer`.
  2. `test_cardforge_runtime_import_cleanliness`: Subprocess check verifying `recipeboxer` is not in `sys.modules` upon importing `cardforge`.
  3. `test_recipeboxer_production_never_imports_cardforge`: AST scan across `recipeboxer/` ensuring no production modules import `cardforge`.
- **`tests/anticheat/test_static_invariants.py`**: Removed `recipeboxer/tools/card_generator.py` and `recipeboxer/tools/__init__.py` from `TEMPORARY_EXCLUSIONS`.

---

## Verification Results

| Suite / Check | Command | Result |
|---|---|---|
| CardForge Generator Tests | `pytest cardforge/tests/test_card_generator.py` | 39 passed |
| CardForge Isolation Tests | `pytest cardforge/tests/test_isolation.py` | 3 passed |
| Empirical Rigor Tests | `pytest tests/test_challenger_m2_empirical_rigor.py` | 9 passed |
| Sample Corpus Tests | `pytest tests/test_sample_card_corpus.py` | 32 passed |
| T0 Manifest Structure Diff | `python -m cardforge -o <tmpdir>` & structural diff | 48/48 cards match keys, schema, and manifest structure |
| Anticheat Invariant Scanning | AST scan of `recipeboxer/tools/` | Clean (docstring-only `__init__.py`) |
