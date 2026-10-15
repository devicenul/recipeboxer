# Work Package Completion Report: WP-F1 (Legibility UX + HITL Evidence)

**Work Package:** WP-F1  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  
**Contracts:** `_agents/remediation/WAVE5_CONTRACTS.md` §3 row WP-F1; `_agents/remediation/WAVE3_CONTRACTS.md` §0; `REMEDIATION_PLAN.md` §8.2 (F1), §9 (HITL evidence); Notes `WP-X3X11.md`, `WP-XB.md`, `WP-GC.md`  

---

## 1. Summary of Changes

In accordance with Wave 5 Contracts and the Remediation Plan (§8.2 & §9), WP-F1 delivers Schema v2 legibility markers, interactive model disagreement resolution, card face viewer highlighting, and calibrated HITL evidence with multimodal set-level verification quotes:

### 1. Schema v2 Types & Helpers (`frontend/src/types/recipe.ts` & `frontend/src/types/hitl.ts`)
- **Exact Schema v2 Mirror**:
  - Added `Legibility` (`'CLEAR' | 'UNCERTAIN' | 'ILLEGIBLE'`), `IllegibleSpan`, `TextSpan`, `RecipeWarning`, `Provenance`.
  - Added structured `TimeValue` (`original_text`, `normalized_iso`, `minutes`, `normalized_minutes`) and `YieldValue` (`original_text`, `normalized_servings`, `unit`).
  - Added `IngredientLine` and `Step` with `original_text`, `normalized_text`, `legibility`, `source_face`, `confidence`, and `illegible_spans`.
  - Extended `Recipe` with `title_is_inferred`, `sections`, `warnings`, `provenance`.
  - Exported standard helpers:
    - `displayText(line)`: Returns `original_text` or falls back to clean `normalized_text` / `text`.
    - `formatTimeValue(tv)`: Formats minutes or ISO duration gracefully.
    - `formatYieldValue(yv)`: Formats servings with optional unit or raw text.
    - `getAllIngredients(recipe)` & `getAllSteps(recipe)`: Flattens sectioned or unsectioned recipe content.
    - `parseModelDisagreement(warning)`: Parses `MODEL_DISAGREEMENT: <field> (p1: "...", p2: "...")` into structured readings.
- **HITL Evidence & Calibration Types**:
  - Added `FeatureEvidence` (`name`, `raw`, `log_lr`, `weight`, `note`), `PairEvidence`, and `SetVerification` (`anchor`, `continuations`, `rejected`, `evidence_quotes`, `confidence`, `flags`, `model`, `prompt_id`, `prompt_version`).
  - Extended `CorrelationProposal` with `p_link`, `semantic_verified`, `degraded_reason`, `part_count`, `evidence`, `set_verification`, `evidence_quotes`, `flags`, `is_low_confidence`.
  - Added `HitlItemType` enum and `HitlItem` supporting `kind: 'budget'`.

### 2. Legibility UX (`frontend/src/components/recipe/**`, `frontend/src/components/viewer/**`)
- **Visual & Accessible Legibility Markers**:
  - `IngredientListInput` & `DirectionListInput`: Clear visual styling for `UNCERTAIN` (amber wavy border + badge), `ILLEGIBLE` (red dashed border + badge), and granular `illegible_spans` chips with reason/guess tooltips.
  - Accessible via `aria-label` attributes (`"Legibility: uncertain"`, `"Legibility: illegible"`).
  - Uncertain quantities rendered with subtle `[Qty: null]` indicator. Crossed-out segments rendered with strikethrough styling and a `Crossed-out` tag.
- **Verbatim vs. Normalized Transcription Toggle**:
  - `RecipeForm` features a toggle switch between *Normalized Text* and *Original Transcription* (verbatim).
- **Source Face Highlighting & Navigation**:
  - Lines with `source_face` render an interactive `Face N` button.
  - Clicking a face button highlights the face in `CardViewerCanvas` (`highlighted={true}` with ring/badge) and auto-selects that card in the viewer carousel.
- **Interactive Disagreement Resolution (Never Auto-Pick)**:
  - `MODEL_DISAGREEMENT` warnings surface as actionable decision cards displaying Reading 1, Reading 2, and Custom options. The user explicitly picks or edits the resolved value.
- **Inferred Title & Review Badges**:
  - Recipes with `title_is_inferred: true` show an `Inferred Title` badge with explanatory tooltip.
  - "Needs Review" counter badge displays count of uncertain/illegible lines and warnings.
  - `RecipeEditorView` adds a quick-toggle button to filter down to cards needing review.
- **Canvas Preservations**:
  - `CardViewerCanvas` maintains exact text fallbacks (`"No Card Scan Image Available"`, `"Failed to load card scan image"`).

### 3. HITL Evidence & Budget Handling (`frontend/src/components/hitl/**`, `frontend/src/pages/HitlReviewPage.tsx`)
- **Calibrated Confidence & Verification State**:
  - `ConfidenceMeter` renders calibrated `p_link` percentage (e.g. `92% Match`), Set-Level Verification badge (`Verified (Set Verification)`), or Unverified/Degraded badge with reason.
  - Prominent alert banner warns when a link is low-confidence (`< 70%`).
- **Verbatim Evidence Quotes**:
  - Displays verbatim quotes cited by multimodal set verification (`evidence_quotes`), with visual caution if flagged as unverified.
- **Pipeline Budget Pausing**:
  - `HitlWorkspace` detects budget pause events (`kind: 'budget'` / `HitlItemType.BUDGET`).
  - Renders a prominent "Budget Cap Reached — Pipeline Paused" banner with "Raise Cap & Resume" (calls `resumeSession`) and "Stop Pipeline" action controls.

---

## 2. Files Changed (Owned Files per §3)

### Created
- `frontend/tests/wp_f1_legibility_hitl.test.tsx` (17 comprehensive unit tests on fictitious data)
- `_agents/remediation/WP-F1.md` (This note)

### Modified (Owned)
- `frontend/src/types/recipe.ts` (Schema v2 models and helper functions)
- `frontend/src/types/hitl.ts` (Calibrated evidence, set verification, budget types)
- `frontend/src/components/recipe/RecipeForm.tsx` (Legibility UX, verbatim toggle, disagreement UI, face selection)
- `frontend/src/components/recipe/IngredientListInput.tsx` (Legibility badges, illegible spans, crossed out cues, face buttons)
- `frontend/src/components/recipe/DirectionListInput.tsx` (Legibility badges, illegible spans, face buttons)
- `frontend/src/components/recipe/RecipeEditorView.tsx` (Review filter, face selection forwarding)
- `frontend/src/components/viewer/CardViewerCanvas.tsx` (Source face highlight indicator)
- `frontend/src/components/hitl/ConfidenceMeter.tsx` (Calibrated confidence, verification badge, quotes, low-confidence banner)
- `frontend/src/components/hitl/RapidQueueCard.tsx` (Forwarding evidence, quotes, and verification status)
- `frontend/src/components/hitl/HitlWorkspace.tsx` (Budget cap paused banner with resume/stop actions)
- `frontend/src/pages/HitlReviewPage.tsx` (Forwarding initial budget item)

---

## 3. Verification & Tests

1. **WP-F1 Test Suite (`frontend/tests/wp_f1_legibility_hitl.test.tsx`)**:
   - 17 unit tests using testing-library and Vitest, strictly on fictitious recipe data:
     - Schema v2 helpers (`displayText`, `formatTimeValue`, `formatYieldValue`, `getAllIngredients`, `getAllSteps`, `parseModelDisagreement`).
     - Inferred title badge rendering.
     - Legibility styling (`UNCERTAIN`, `ILLEGIBLE`, illegible spans, crossed-out cues, null quantity).
     - Verbatim transcription toggle (`showOriginal` vs `normalized`).
     - Face button clicks triggering `onSelectFace`.
     - Model disagreement resolution cards allowing explicit selection between Reading 1 and Reading 2.
     - Review filter button in `RecipeEditorView`.
     - Calibrated confidence and low-confidence alert banners in `ConfidenceMeter`.
     - Multimodal set verification quotes and unverified quote alert.
     - Budget cap pause banner and resume session handler.
   - **Result:** 17 passed (100%).

2. **HITL Queue Existing Suite (`frontend/tests/hitl_queue.test.tsx`)**:
   - **Result:** 6 passed (100%).

3. **TypeScript Typecheck (`tsc --noEmit`)**:
   - `tsc` error count across entire project: **0 errors** (`tsc_errors=0`).

---

## 4. Requests for Concurrent WP-F2

- **Helpers Available for Consumption**:
  - `types/recipe.ts` exports `displayText(line)`, `formatTimeValue(timeValue)`, `formatYieldValue(yieldValue)`, `getAllIngredients(recipe)`, `getAllSteps(recipe)`, and `parseModelDisagreement(warning)`.
  - WP-F2's `useRecipeForm.ts`, `exportCuration.ts`, `formatters.ts`, and settings pages can safely consume these helpers directly.
- **Non-Breaking Types**:
  - Schema v2 types in `recipe.ts` maintain optional backward compatibility fields (`text?: string`, `time?: RecipeTime`, `yield?: string | number`) so that legacy serializers and un-migrated utility functions compile cleanly with zero errors.

---

## 5. Anti-Cheat & Common Rules Compliance

- Fictitious test data only (no corpus recipes, titles, ingredients, or directions).
- No hardcoded Gemini API keys or credentials.
- No caches placed in shared workspace storage (mirrored to VM ext4 cache).
- No modifications to `setup.sh` or Git state.
