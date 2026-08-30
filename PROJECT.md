# Project: RecipeBoxer Integration Findings Resolution

## Architecture
RecipeBoxer is a multimodal recipe digitization system comprising:
- **Frontend**: React + TypeScript + Vite + Tailwind CSS (`frontend/`)
- **Backend Core**: Python package `recipeboxer` (correlation, segmentation, extraction, ingestion)
- **Evaluation & Benchmarks**: `recipeboxer/evals/` and `evals/run_benchmarks.py`
- **Fixtures Corpus**: `tests/fixtures/sample_cards/` (48 images, 24 ground-truth recipes across 6 historical dimensions)

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | OS-Adaptive Path Detection & Placeholder | Ingestion path placeholder dynamically detects client OS (Windows, macOS, Linux) | M1 | ORIGINAL_REQUEST §R1 |
| 2 | Model Prompt Filename Sanitization | Sanitize card filenames in multimodal prompts to prevent cheating on title/year/role | M2 | ORIGINAL_REQUEST §R2 |
| 3 | Semantic Verifier Prompt Anonymization | Obfuscate card IDs in semantic continuity evaluation | M2 | ORIGINAL_REQUEST §R2 |
| 4 | Offline Correlation Anti-Cheating | Remove filename substring/token matching from offline correlation fallbacks | M2 | ORIGINAL_REQUEST §R2 |
| 5 | Modality Router Feature-Based Classification | Classify card modalities from visual/CV image features rather than filename substrings | M2 | ORIGINAL_REQUEST §R2 |
| 6 | Obfuscation Ground-Truth Mapping | Bidirectional mapping between obfuscated IDs and original ground truth for scoring | M2 | ORIGINAL_REQUEST §R2 |
| 7 | Authentic Historical Clipping Corpus | Public domain magazine ads, box recipes, can labels in sample_cards | M3 | ORIGINAL_REQUEST §R3 |
| 8 | Clipping Manifest & Provenance Docs | Valid entries in manifest.json and historical CC0 provenance in README.md | M3 | ORIGINAL_REQUEST §R3 |
| 9 | Corpus Validation in Benchmarks | Existing and new clipping fixtures verified in test_sample_card_corpus.py & evals | M3 | ORIGINAL_REQUEST §R3 |
| 10 | Interactive GIS OAuth Popup (Docs Export) | google.accounts.oauth2.initTokenClient popup flow replacing manual token drawer | M1 | ORIGINAL_REQUEST §R4 |
| 11 | Interactive GIS Authorization (Drive/Photos) | Connect account flow in SourceSelector & AlbumPickerModal with error handling | M1 | ORIGINAL_REQUEST §R4 |
| 12 | GIS Token Client Mocking & Unit Tests | Comprehensive frontend test coverage for OAuth initialization, token grant, error | M1 | ORIGINAL_REQUEST §R4 |
| 13 | Full E2E Test Suite & Adversarial Hardening | End-to-end integration and anti-cheating verification across all 4 tiers | M4 | ORIGINAL_REQUEST Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Frontend: OS-Adaptive Path & GIS OAuth Flow | frontend UI, utils (platform, googleAuth), components, tests | none | DONE |
| M2 | Backend: Evaluation Filename Obfuscation | recipeboxer/evals/runner.py, obfuscation.py, models & evals tests | none | DONE |
| M3 | Corpus: Authentic Clipping Fixtures & Docs | tests/fixtures/sample_cards/ manifest.json, README.md, tests | none | DONE |
| M4 | Final E2E Suite & Regression | Frontend build/tests, backend pytest, benchmark evaluations, forensic audit | M1, M2, M3 | DONE |

## Code Layout
- `frontend/src/utils/platform.ts`: OS detection helper (`detectClientPlatform`, `getOsPathPlaceholder`)
- `frontend/src/utils/googleAuth.ts`: GIS token client helper (`requestGoogleAccessToken`, `hasValidToken`)
- `frontend/src/components/ingestion/SourceSelector.tsx`: Ingestion source selection UI with OS placeholder & GIS triggers
- `frontend/src/components/ingestion/AlbumPickerModal.tsx`: Container/album selector with in-modal GIS sign-in
- `frontend/src/components/export/GoogleDocsPreview.tsx`: Google Docs export preview with 1-click GIS auth
- `recipeboxer/evals/obfuscation.py`: Filename/metadata obfuscation and ground-truth mapping engine
- `recipeboxer/evals/runner.py`: Evaluation benchmark runner applying obfuscation layer
- `recipeboxer/evals/dataset.py`: Benchmark dataset loader supporting opaque card IDs
- `recipeboxer/ai/gemini.py` / `prompt_router.py`: Sanitized prompt generation and CV feature routing
- `tests/fixtures/sample_cards/`: Authentic historical clipping card images, `manifest.json`, `README.md`
- `tests/test_eval_obfuscation.py`: Anti-cheating verification test suite

## Interface Contracts
### `frontend/src/utils/platform.ts`
```typescript
export type ClientPlatform = 'windows' | 'macos' | 'linux';
export function detectClientPlatform(nav?: Navigator): ClientPlatform;
export function getOsPathPlaceholder(platform?: ClientPlatform): string;
```

### `frontend/src/utils/googleAuth.ts`
```typescript
export interface GoogleAuthOptions {
  clientId?: string;
  scope: string;
  prompt?: string;
}
export function requestGoogleAccessToken(options: GoogleAuthOptions): Promise<string>;
```

### `recipeboxer/evals/obfuscation.py`
```python
class ObfuscatedCardCorpus:
    def __init__(self, original_scan_dir: Path, target_dir: Optional[Path] = None): ...
    def obfuscate_id(self, original_id: str) -> str: ...
    def resolve_id(self, obfuscated_id: str) -> str: ...
    def get_obfuscated_path(self, original_id: str) -> Path: ...
    def map_proposals_to_ground_truth(self, proposals: List[PairingProposal]) -> List[PairingProposal]: ...
```
