# Remediation Report: WP-XC (Domain Normalizers, No-Key State & SSE Spend Event)

**Work Package ID**: WP-XC  
**Authoritative Contracts**: `_agents/remediation/WAVE5_CONTRACTS.md` (§2, §3), `WAVE3_CONTRACTS.md` §0, `REMEDIATION_PLAN.md` §8.3 (X7, X12), §4.1 INV-AC6, notes WP-X3X11, WP-C7, WP-B2, WP-B1B3  
**Status**: COMPLETE  

---

## 1. Summary of Changes

Work package WP-XC delivers three key capabilities across normalization, offline developer experience, and frontend telemetry:

1. **Domain Normalizers (`recipeboxer/extraction/normalize.py`, X7)**:
   - Implemented `normalize_recipe(recipe: Recipe) -> Recipe`.
   - Normalizes unicode vulgar fractions (`'1½'` -> `'1 1/2'`) and fractions into standardized representations.
   - Normalizes archaic culinary units (`'gill'` -> `'1 gill (1/2 cup)'`, `'teacup'` -> `'1 teacup (3/4 cup)'`, `'butter the size of an egg'` -> `'1/4 cup butter (size of an egg)'`, `'oleo'`, `'saleratus'`) using adapted parsers from `recipeboxer/agent/skills/vintage_handwriting_ocr/parser.py`.
   - Parses quantity into unambiguous normalized numeric values (`normalized_quantity: Optional[float]`) only where unambiguous (`'1½'` -> `1.5`, `'1 1/2'` -> `1.5`, `'3/4'` -> `0.75`), leaving ambiguous or descriptive quantities (`'to taste'`, `'a pinch'`, ranges `'1-2'`) as `None`.
   - Populates `normalized_unit` with canonical units (`'cup'`, `'tbsp'`, `'tsp'`, `'oz'`, `'lb'`, `'gill'`, `'teacup'`, etc.).
   - Strictly preserves bit-for-bit verbatim card transcription in `original_text`, writing strictly to `normalized_*` attributes.
   - Detects advertisement copy, coupon expiration, and promotional clipping noise via `is_promotional_line` from `recipeboxer/agent/skills/print_clipping_extractor/parser.py`.
   - Moves promotional noise to `recipe.notes` with a `RecipeWarning(code="ADVERTISEMENT_NOISE")`, never deleting or dropping text.

2. **X12 No-Key State (`recipeboxer/api/routes.py`, `recipeboxer/models/pipeline.py`, X12)**:
   - Proves offline card scanning, thumbnail generation, deterministic pixel features extraction, and capture metadata extraction work completely without a Gemini API key.
   - Surfaces clear `analysis_blocked_reason: 'NO_API_KEY'` with actionable `onboarding_hint` pointing to `/api/settings/api-key` in `GET /api/status`, `GET /api/pipeline/status`, and session status endpoints.

3. **SSE 'Spend' Event (`recipeboxer/api/routes.py`, WP-B2)**:
   - Extends the Server-Sent Events (SSE) `/api/events` endpoint in `routes.py` with real-time `spend` events whenever `session.ledger` or `session.spend_usd` changes.
   - Emits structured spend telemetry including `session_id`, `spend_usd`, and `CostLedger.snapshot()` breakdown.

---

## 2. Files Owned & Modified
- `recipeboxer/extraction/normalize.py` (new domain normalizer module)
- `recipeboxer/agent/skills/vintage_handwriting_ocr/parser.py` (updated archaic regex patterns to match standalone & quantified expressions)
- `recipeboxer/agent/skills/print_clipping_extractor/parser.py` (extended promotional patterns)
- `recipeboxer/models/recipe.py` (additive `normalized_quantity`, `normalized_unit`, `normalized_minutes`)
- `recipeboxer/models/pipeline.py` (additive `analysis_blocked_reason`, `onboarding_hint` to `PipelineStatus`)
- `recipeboxer/models/session.py` (additive `analysis_blocked_reason`, `onboarding_hint` to `SessionSummary`, `SessionDetail`)
- `recipeboxer/api/routes.py` (status blocked reason & SSE spend event)
- `tests/test_wp_xc_normalizer_nokey_spend.py` (new comprehensive tests)

---

## 3. Test Verification

Tests executed in the VM environment using `timeout 30 .venv/bin/pytest`:

```
============================= test session starts ==============================
platform linux -- Python 3.13.5, pytest-9.1.1, pluggy-1.6.0
rootdir: /mnt/shared/AntiGravity/Workspaces/recipeboxer
configfile: pyproject.toml
plugins: asyncio-1.4.0, anyio-4.15.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=function, asyncio_default_test_loop_scope=function
collecting ... collected 24 items

tests/test_wp_xc_normalizer_nokey_spend.py ............                  [ 50%]
tests/test_fastapi_routes.py ............                                [100%]

============================== 24 passed in 2.26s ==============================
```

- Hand-written unit tests cover:
  - `test_unambiguous_quantity_parsing`: Vulgar fractions, mixed numbers, decimals vs. ambiguous descriptions and ranges.
  - `test_unambiguous_unit_parsing`: Canonical culinary units resolution.
  - `test_normalize_handwritten_fractions_and_archaic_measurements`: Hand-written cases `'1½'`, `'1 1/2'`, `'gill'`, `'teacup'`, `'butter the size of an egg'`, preserving `original_text`.
  - `test_filter_advertisement_noise_moved_to_notes_with_warning`: Relocation of coupon/promotional noise into notes with `ADVERTISEMENT_NOISE` warnings.
  - `test_offline_scanning_works_without_api_key`, `test_offline_thumbnails_work_without_api_key`, `test_offline_pixel_features_work_without_api_key`, `test_offline_capture_metadata_works_without_api_key`: Offline viability without Gemini key.
  - `test_status_endpoint_reports_no_api_key_and_onboarding_hint`, `test_session_status_with_no_key_failure_reports_clear_block`: Surfacing `analysis_blocked_reason: 'NO_API_KEY'` and hint `/api/settings/api-key`.
  - `test_sse_stream_emits_spend_event_on_cost_change`, `test_sse_stream_fallback_to_session_spend_usd_field`: SSE stream emitting `event: spend` with snapshot and spend amount using `TestClient`.
