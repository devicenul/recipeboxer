# Work Package Completion Report: WP-M1 (Default Model IDs & Pricing Validation)

**Agent ID:** WP-M1 (Wave 6 Remediation)  
**Branch / HEAD:** `remediation/phase0` @ `0882b4c`  
**Date:** 2026-10-15  
**Owned Files Written / Updated:**
1. [`recipeboxer/config.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/config.py)
2. [`recipeboxer/models/session.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/models/session.py)
3. [`recipeboxer/models/pipeline.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/models/pipeline.py)
4. [`recipeboxer/api/routes.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/api/routes.py)
5. [`recipeboxer/api/settings_routes.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/api/settings_routes.py)
6. [`recipeboxer/ai/cost.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/ai/cost.py)
7. [`recipeboxer/extraction/consistency.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/extraction/consistency.py)
8. [`recipeboxer/ai/embeddings.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/ai/embeddings.py)
9. [`frontend/src/App.tsx`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/frontend/src/App.tsx)
10. [`frontend/src/components/ingestion/SessionLauncher.tsx`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/frontend/src/components/ingestion/SessionLauncher.tsx)
11. [`frontend/src/components/ingestion/ConfigPanel.tsx`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/frontend/src/components/ingestion/ConfigPanel.tsx)
12. [`frontend/src/components/layout/StatusBar.tsx`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/frontend/src/components/layout/StatusBar.tsx)
13. [`frontend/src/components/settings/SpendCapsSection.tsx`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/frontend/src/components/settings/SpendCapsSection.tsx)
14. [`tests/test_config.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/tests/test_config.py)
15. [`tests/test_challenger_r1_r2.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/tests/test_challenger_r1_r2.py)
16. [`tests/test_recipe_extractor.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/tests/test_recipe_extractor.py)
17. [`tests/test_wp_b1_cost.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/tests/test_wp_b1_cost.py)
18. [`tests/test_wp_m1_model_pricing.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/tests/test_wp_m1_model_pricing.py) (new)
19. [`_agents/remediation/WP-M1.md`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/_agents/remediation/WP-M1.md) (this report)

---

## 1. Summary of Model Default Remediation

WP-M1 eliminated all deprecated, unpriced, and nonexistent model identifiers (`gemini-3.7-flash`, `gemini-3.7-pro`, `gemini-2.5-pro`, `gemini-embedding-001`) from defaults in backend and frontend code. All defaults now resolve to single-source-of-truth constants in [`recipeboxer/config.py`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/recipeboxer/config.py), each verified and priced in [`config/pricing.toml`](file:///storage/emulated/10/AntiGravity/Workspaces/recipeboxer/config/pricing.toml).

### Single Source of Truth Constants
- `DEFAULT_FLASH_MODEL = "gemini-3.8-flash"` (priced $0.75/$3.75 per MTok)
- `DEFAULT_FLASH_LITE_MODEL = "gemini-3.5-flash-lite"` (priced $0.30/$2.50 per MTok)
- `DEFAULT_PRO_MODEL = "gemini-3.1-pro-preview"` (priced $2.00/$12.00 per MTok)
- `DEFAULT_CONSISTENCY_MODEL = "gemini-3.6-flash"` (priced $0.75/$3.75 per MTok)
- `DEFAULT_EMBEDDING_MODEL = "gemini-embedding-2"` (priced $0.20 per MTok; supports multimodal images)

### Quality Profile Mappings (`QUALITY_PROFILES`)
1. **`economy`**:
   - `TRANSCRIBE`: `gemini-3.8-flash`
   - `STRUCTURE`: `gemini-3.8-flash`
   - `FINGERPRINT`, `SET_VERIFY`, `GROUNDING`, `REPAIR`, `JUDGE`, `MODALITY`: `gemini-3.5-flash-lite`
   - **Trade-off documented:** Verbatim OCR transcription and structured schema parsing suffer severe extraction degradation on `gemini-3.5-flash-lite`. Routing those two critical extraction stages to `gemini-3.8-flash` maintains recipe quality and valid output schemas, while routing visual classification, heuristic screening, grounding, and repairs to `gemini-3.5-flash-lite` keeps per-card cost under ~$0.003.
2. **`balanced`**:
   - SUT stages (`FINGERPRINT`, `SET_VERIFY`, `TRANSCRIBE`, `STRUCTURE`, `GROUNDING`, `REPAIR`, `MODALITY`): `gemini-3.8-flash`
   - Evaluation judge (`JUDGE`): `gemini-3.1-pro-preview`
   - Self-consistency alternative model: `gemini-3.6-flash` (distinct priced model family providing independent consensus without expensive Pro overhead)
3. **`best`**:
   - `FINGERPRINT`: `gemini-3.8-flash`
   - `SET_VERIFY`, `TRANSCRIBE`, `STRUCTURE`, `GROUNDING`, `REPAIR`, `JUDGE`: `gemini-3.1-pro-preview`
   - `MODALITY`: `gemini-3.8-flash`

---

## 2. Verification Commands & Results

1. **Targeted Pytest Suite:**
   ```bash
   PCD=$HOME/.cache/projects/recipeboxer-ba754a91; PYTHONPYCACHEPREFIX="$PCD/pycache" timeout 120 "$PCD/venv/bin/python" -m pytest -o cache_dir="$PCD/pytest" tests/test_wp_m1_model_pricing.py tests/test_config.py tests/test_challenger_r1_r2.py tests/test_recipe_extractor.py tests/test_wp_b1_cost.py tests/test_wp_xb_grounding_repair_consistency.py tests/test_wp_x1_client.py -q -p no:randomly
   ```
   **Result:** `106 passed, 1 skipped in 21.31s` (0 failures).

2. **Anti-Cheat Invariant Verification:**
   ```bash
   PCD=$HOME/.cache/projects/recipeboxer-ba754a91; PYTHONPYCACHEPREFIX="$PCD/pycache" timeout 120 "$PCD/venv/bin/python" -m pytest -o cache_dir="$PCD/pytest" tests/anticheat -q -p no:randomly
   ```
   **Result:** `10 passed in 33.66s` (0 failures).

3. **Frontend Vitest & TypeScript Suite:**
   ```bash
   bash /storage/emulated/10/AntiGravity/.gemini/antigravity/brain/965c2b98-1aca-490d-a2ab-1d1fd16aea5f/scratch/frontend_check.sh test
   ```
   **Result:**
   - Vitest: `32 passed (32 test files), 389 passed (389 tests)`
   - TSC: `tsc_errors=0`

4. **Full Test Suite:**
   ```bash
   PCD=$HOME/.cache/projects/recipeboxer-ba754a91; PYTHONPYCACHEPREFIX="$PCD/pycache" timeout 1800 "$PCD/venv/bin/python" -m pytest -o cache_dir="$PCD/pytest" tests cardforge/tests -q -p no:randomly --continue-on-collection-errors
   ```
   **Result:** `13 failed, 2345 passed, 9 skipped in 350.88s` (0:05:50).
   All 13 failures are the exact pre-existing known failures:
   - 6 zip-slip path escaping tests (`tests/test_archive_empirical_challenger.py`)
   - 7 headless tkinter GUI tests (`tests/test_browse_directory.py`)

---

## 3. Contract Deviations
None. All models match validated entries in `config/pricing.toml`.

## 4. Requests for Other WPs
None. Changes are isolated to model defaults and pricing alignment.

## 5. Risks
- Promotional rates for `gemini-3.8-flash` ($0.75/$3.75) and `gemini-3.6-flash` expire 2026-12-31, increasing to standard rates ($1.50/$7.50) as documented in `config/pricing.toml`.
