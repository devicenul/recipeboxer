# Work Package Completion Report: WP-F2 (Settings UI, Onboarding, Spend Meter)

**Work Package:** WP-F2  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  
**Contracts:** `_agents/remediation/WAVE5_CONTRACTS.md` §3 row WP-F2; `_agents/remediation/WAVE3_CONTRACTS.md` §0; `REMEDIATION_PLAN.md` §7.2; Notes `WP-B2.md`, `WP-XC.md`, `WP-B1B3.md`; `config/pricing.toml`  

---

## 1. Summary of Changes

In accordance with Wave 5 Contracts, Remediation Plan §7.2, and Notes WP-B2 / WP-XC / WP-B1B3, WP-F2 implements user-facing settings, onboarding banners for missing API keys, real-time spend metering via SSE, budget pause indicators, and pre-run cost estimation:

### 1. Types & Settings API Client (`frontend/src/types/pipeline.ts` & `frontend/src/utils/api.ts`)
- **Pipeline Types Extended**:
  - Added `BUDGET_EXCEEDED` to `PipelineState`.
  - Extended `PipelineStatus`, `SessionSummary`, and `SessionDetail` with `spend_usd`, `estimated_cost_usd`, `analysis_blocked_reason`, `onboarding_hint`, and `failures`.
  - Added full settings domain types: `SettingsResponse`, `SettingsUpdateRequest`, `ApiKeyRequest`, `ApiKeyResponse`, `ApiKeyDeleteResponse`, `CostEstimateResponse`, `PrivacyNoticeResponse`, and `SpendEventPayload`.
- **API Client Methods Added**:
  - `getSettings()`: Fetches current profile, caps, features, and key status.
  - `updateSettings(req)`: Updates configuration parameters.
  - `setApiKey(key)`: Persists Gemini API key securely.
  - `deleteApiKey()`: Removes stored API key.
  - `getCostEstimate(nCards)`: Computes pre-run cost estimate bounds.
  - `getPrivacyNotice()`: Retrieves privacy and data-use disclosures.

### 2. Live Telemetry & Settings Context (`frontend/src/hooks/useSSE.ts` & `frontend/src/context/SettingsContext.tsx`)
- **SSE Telemetry**:
  - Added `spend` event listener in `useSSE` with `onSpend` callback emitting running USD spend, call count, and per-stage metrics.
- **Settings Context**:
  - Implemented `SettingsProvider` managing preferences, API key status, draft state, and error handling.
  - Provided a defensive `DEFAULT_SETTINGS_CONTEXT` so standalone components and tests mounted without `SettingsProvider` do not throw or fail.

### 3. Settings UI Components (`frontend/src/components/settings/**` & `frontend/src/pages/SettingsPage.tsx`)
- **QualityProfileSelector**:
  - Interactive selection between Economy (Flash 2.5/Lite), Balanced, and Best (Pro models).
  - Clear plain-language trade-offs and specs.
  - Paid API key requirement alert when `Best` is selected (explaining Pro models are unavailable on Google's free tier).
- **SpendCapsSection**:
  - Numerical inputs for `spend_cap_usd_per_run` and `spend_cap_usd_monthly`.
  - RPM rate limit control, extra-effort reasoning toggle, embeddings toggle.
  - Collapsible per-stage model override panel.
- **ApiKeySection**:
  - Displays key presence (`has_api_key`) and storage source (`key_source`), strictly never rendering the secret key.
  - Masked key input field and key deletion button.
  - Free Tier vs. Paid Key explainer: clarifies ~20 requests/day free quota, lack of Pro model access, Google's terms regarding free-tier inputs used to improve products, and recommendation for paid key with spend caps for sensitive family photos. Links to official Google terms and privacy notices.
- **CostEstimateCalculator**:
  - Pre-run estimator with preset card count buttons (1, 10, 50, 100) and custom input.
  - Displays low, expected, and high estimates.
  - Strictly displays `'unknown - model not priced'` whenever low/expected/high estimates are `null` (never showing `$0` or `$0.00`).
- **SettingsPage**:
  - Orchestrates all settings sections in a clean, responsive layout.

### 4. Layout, Onboarding, and Spend Meter (`frontend/src/components/layout/**` & `frontend/src/App.tsx`)
- **OnboardingBanner**:
  - Prominently shown when `analysisBlockedReason === 'NO_API_KEY'` or when status matches `NO_API_KEY`.
  - Informs the user that card scanning and thumbnail generation work offline, but analysis requires an API key, providing a direct link button to Settings.
- **SpendMeter & StatusBar**:
  - Integrated into layout and status bar.
  - Shows real-time spend (`$X.XX`), call count, and a visual progress bar toward the per-run spend cap.
  - Emits clear badge `[Budget Exceeded — Paused]` when budget cap is hit or status is `BUDGET_EXCEEDED`.
- **Navigation & App Routing**:
  - Added `Settings` tab with Lucide `Settings` icon to main navigation bar.
  - App root wraps routes in `SettingsProvider` and handles live SSE `onSpend` updates.

### 5. Schema v2 Alignment in Owned Utilities (`frontend/src/utils/**` & `frontend/src/hooks/**`)
- Resolved all schema v2 TypeScript discrepancies in WP-F2 owned files:
  - `useRecipeForm.ts`: safely guarded `recipe.ingredients || []` and `recipe.directions || []`.
  - `formatters.ts`: added `formatTimeValue` and `displayText` helpers; enabled `toISO8601Duration` to accept `TimeValue`.
  - `exportPlanToEat.ts`, `exportGoogleDocs.ts`, `exportJsonLd.ts`, `exportCooklang.ts`, `exportMarkdown.ts`: cleanly format `prep_time`, `cook_time`, and `total_time`.

---

## 2. Files Changed (Owned Files per §3)

### Created
- `frontend/src/pages/SettingsPage.tsx`
- `frontend/src/components/settings/QualityProfileSelector.tsx`
- `frontend/src/components/settings/SpendCapsSection.tsx`
- `frontend/src/components/settings/ApiKeySection.tsx`
- `frontend/src/components/settings/CostEstimateCalculator.tsx`
- `frontend/src/components/layout/OnboardingBanner.tsx`
- `frontend/src/components/layout/SpendMeter.tsx`
- `frontend/src/context/SettingsContext.tsx`
- `frontend/tests/wp_f2_settings_spend.test.tsx` (20 comprehensive tests)
- `_agents/remediation/WP-F2.md` (This report)

### Modified (Owned)
- `frontend/src/types/pipeline.ts`
- `frontend/src/utils/api.ts`
- `frontend/src/hooks/useSSE.ts`
- `frontend/src/components/layout/Navigation.tsx`
- `frontend/src/components/layout/StatusBar.tsx`
- `frontend/src/App.tsx`
- `frontend/src/hooks/useRecipeForm.ts`
- `frontend/src/utils/formatters.ts`
- `frontend/src/utils/exportCooklang.ts`
- `frontend/src/utils/exportGoogleDocs.ts`
- `frontend/src/utils/exportJsonLd.ts`
- `frontend/src/utils/exportMarkdown.ts`
- `frontend/src/utils/exportPlanToEat.ts`

---

## 3. Verification & Tests

1. **WP-F2 Test Suite (`frontend/tests/wp_f2_settings_spend.test.tsx`)**:
   - 20 unit and integration tests covering:
     - Quality Profile selector and Best profile paid-key warning.
     - Spend caps, rate limits, effort toggle, and stage overrides.
     - API key status display, secret non-exposure, delete/save operations, free vs. paid disclosures.
     - Pre-run cost estimator bounds and `'unknown - model not priced'` handling.
     - Onboarding banner visibility and navigation on `NO_API_KEY`.
     - Live spend meter telemetry, cap progress, and budget-exceeded alerts.
     - Full settings page lifecycle.
   - **Result:** 20/20 passed.

2. **TypeScript Compilation (`tsc --noEmit -p .`)**:
   - **Result:** 0 errors across the repository.

3. **Full Vitest Test Suite**:
   - **Result:** 32 test files passed (388/388 tests passed).

4. **Schema v2 TypeScript Errors**:
   - **Result:** 0 errors (down from 15 baseline errors in owned files).
