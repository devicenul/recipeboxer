# WP-B2 Remediation Report: Settings API & Persistence Store

## Summary
Implemented WP-B2 (Settings API and persistent settings storage) adhering strictly to WAVE 4 contract (§1.1, §2) and REMEDIATION_PLAN.md §7.2:
1. **JSON Settings Store** (`recipeboxer/storage/settings_store.py`):
   - Stores user preferences (`quality_profile`, `stage_models`, `spend_cap_usd`, `monthly_cap_usd`, `rpm_limit`, `extra_effort`, `embeddings_enabled`) in `settings.json` under the platform user configuration directory (`RECIPEBOXER_CONFIG_DIR`, `platformdirs`, or XDG/Darwin/Windows platform paths; never inside repository by default).
   - Atomic file persistence with strict `0o600` file permissions to ensure local credential confidentiality.
   - Credentials management: stores Gemini API key securely in OS keyring if available, falling back to `settings.json` with `0o600` permissions.
   - Public schema abstraction (`get_public_settings_dict`) guaranteeing the actual secret key is never leaked, only `has_api_key: bool` and `key_source`.
2. **FastAPI Settings Router** (`recipeboxer/api/settings_routes.py`):
   - `GET /api/settings`: Returns public settings, `has_api_key`, and `key_source`. Never returns the secret API key.
   - `PUT /api/settings`: Validates and updates user settings using Pydantic schemas (e.g. `quality_profile` in `["economy", "balanced", "best"]`, non-negative spend caps, `rpm_limit >= 1`).
   - `POST /api/settings/api-key`: Configures Gemini API key (attempts keyring first, falls back to 0o600 file).
   - `DELETE /api/settings/api-key`: Deletes Gemini API key from keyring and file store.
   - `GET /api/settings/estimate?n_cards=N`: Calculates pre-run cost estimation using `recipeboxer.ai.cost.estimate_run_cost` and `PriceTable.load()`, with fallback support for uncalibrated models.
   - `GET /api/settings/privacy-notice`: Serves static privacy notice cautioning regarding unpaid / free-tier Gemini API data usage policies versus paid accounts, with link placeholders to Google's terms.
3. **Session Telemetry Spend Fields** (`recipeboxer/models/session.py`):
   - Added `spend_usd`, `estimated_cost_usd` to `SessionSummary`.
   - Added `spend_usd`, `estimated_cost_usd`, and `spend_breakdown` to `SessionDetail`.
   - Added `SessionSpendStatus` model for real-time spend queries.
4. **Router Registration** (`recipeboxer/api/app.py`):
   - Registered `settings_router` under `/api` prefix and wired `settings_store` into application lifecycle state.
5. **Testing** (`tests/test_wp_b2_settings.py`):
   - 16 comprehensive unit and integration tests verifying store isolation (via `tmp_path`), atomic writes, 0o600 permissions, API key secrecy, input validation, endpoints, cost estimation, and model spend fields.

## Files Changed / Created
- `recipeboxer/storage/settings_store.py` (new)
- `recipeboxer/api/settings_routes.py` (new)
- `recipeboxer/models/session.py` (added spend fields and `SessionSpendStatus`)
- `recipeboxer/api/app.py` (settings router registration and settings_store injection)
- `tests/test_wp_b2_settings.py` (new tests)

## Commands Run & Results
```bash
# 1. Targeted WP-B2 test suite
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
py="$PROJECT_CACHE_DIR/venv/bin/python"
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_b2_settings.py -v -p no:randomly
# Result: 16 passed in 0.58s

# 2. Combined validation with regression suites and anticheat
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_b2_settings.py tests/test_fastapi_routes.py tests/anticheat -q -p no:randomly
# Result: 38 passed in 3.97s
```

## Contract Deviations
None. All contracts strictly honored.

## Requests for Other WPs / Orchestrator
- **Request for `recipeboxer/api/routes.py` owner**: The existing SSE streaming endpoint (`/api/events`) in `routes.py` has a closed generator without hook extension points. To expose real-time session spend over the SSE stream, please emit a `'spend'` event when `session.spend_usd` changes:
  ```python
  curr_spend = getattr(session, "spend_usd", 0.0)
  if curr_spend != last_spend:
      last_spend = curr_spend
      yield f"event: spend\ndata: {json.dumps({'session_id': session.session_id, 'spend_usd': curr_spend})}\n\n"
  ```
- **Optional dependency recommendation (for Orchestrator)**: Consider adding `keyring` and `platformdirs` to `requirements.txt` if desired for system-keychain integration; both are currently handled with graceful zero-dependency fallbacks.

## Risks & Mitigations
- *Headless / Linux VM Keyring absence*: Handled via fallback to storing the key in `settings.json` guarded with `0o600` file permissions (`chmod`).
- *Accidental API Key Leakage*: Endpoint response models (`SettingsResponse`) strictly exclude the API key, reporting only boolean presence `has_api_key` and `key_source`.
