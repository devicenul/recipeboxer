# E2E Test Infra: RecipeBoxer Backend Architecture

## Test Philosophy
- **Opaque-box & Requirement-driven**: All E2E tests exercise RecipeBoxer as an external client (via FastAPI REST endpoints, CLI/session interfaces, and Plan To Eat CSV imports). Tests make zero assumptions about internal private module functions.
- **Methodology**: Category-Partition + Boundary Value Analysis (BVA) + Pairwise Combinatorial Testing + Real-World Workload Testing.
- **Independence**: All test tiers can run in offline mock mode (using simulated multimodal responses) as well as live Gemini mode when `GEMINI_API_KEY` is provided.

## Feature Inventory
| # | Feature | Source (requirement) | Tier 1 | Tier 2 | Tier 3 |
|---|---------|---------------------|:------:|:------:|:------:|
| 1 | `config-management` | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| 2 | `domain-schemas` | ORIGINAL_REQUEST §R1-R4 | 5 | 5 | ✓ |
| 3 | `parallel-ingestion` | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| 4 | `slow-batch-ingestion` | ORIGINAL_REQUEST §R1 | 5 | 5 | ✓ |
| 5 | `fingerprint-cache` | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 6 | `visual-fingerprinting` | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 7 | `heuristic-screening` | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 8 | `semantic-correlation` | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 9 | `pairing-optimizer` | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ |
| 10| `hitl-async-bridge` | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 11| `fastapi-pipeline-endpoints` | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 12| `fastapi-hitl-endpoints` | ORIGINAL_REQUEST §R3 | 5 | 5 | ✓ |
| 13| `recipe-extraction` | ORIGINAL_REQUEST §R4 | 5 | 5 | ✓ |
| 14| `plan-to-eat-csv-export` | ORIGINAL_REQUEST §R4 | 5 | 5 | ✓ |
| 15| `fastapi-export-endpoint` | ORIGINAL_REQUEST §R4 | 5 | 5 | ✓ |

## Test Architecture
- **Test Runner**: `pytest -v tests/` (supports pytest-asyncio and httpx `ASGITransport`).
- **Pass / Fail Semantics**: Zero assertion errors, 100% passing tests, zero unhandled coroutine exceptions, exit code 0.
- **Directory Layout**:
  - `tests/conftest.py`: Test fixtures (synthetic card image generator with PIL, mock Antigravity Gemini responses, test client).
  - `tests/test_*.py`: Unit & integration tests for each module.
  - `tests/e2e/test_e2e_pipeline.py`: Comprehensive Tiers 1-4 opaque-box pipeline tests.
  - `tests/e2e/test_adversarial.py`: Tier 5 adversarial stress tests (corrupted images, missing card backs, non-recipe scans, ambiguous pairings, extreme timeouts).

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | **Vintage Handwritten Index Cards**: Stained, yellowed index cards with cursive front and continuing back directions. | F1-F15 | High |
| 2 | **Mixed Batch Ingestion**: Unsorted pile of 10 cards (3 two-sided recipes, 2 standalone single-sided recipes, 2 blank/noise cards). | F3, F5-F10, F12-F15 | High |
| 3 | **Rate-Limited Slow Batch with Resumption**: Ingesting 20 cards in slow-batch mode (batch size 2, interval 0.1s), simulating mid-flight crash, and verifying instant cache resumption without duplicate API calls. | F4, F5, F10-F12 | High |
| 4 | **HITL Override & Pairing Correction**: Human user corrects an ambiguous pair (reassigns back card to another front) via `/api/hitl/approve` before triggering extraction. | F10-F14 | High |
| 5 | **Plan To Eat Import Engine Validation**: Verifying that multi-line ingredients, numbered steps, commas in titles, and quote characters in descriptions import cleanly without breaking CSV column boundaries. | F2, F13, F14, F15 | High |

## Coverage Thresholds
- **Tier 1 (Feature Coverage)**: ≥5 test cases per feature (15 features × 5 = 75 test cases minimum across suite).
- **Tier 2 (Boundary & Corner Cases)**: ≥5 boundary test cases per feature (empty inputs, single-card batches, huge batches, special characters, unicode, missing backs).
- **Tier 3 (Pairwise Cross-Feature Combinations)**: Multi-feature interactions (e.g. slow-batch + cache hit + HITL rejection; parallel mode + override + CSV export).
- **Tier 4 (Real-World Application Scenarios)**: ≥5 realistic multi-stage scenarios (vintage index cards, mixed piles, crash resumption, HITL correction, Plan To Eat CSV RFC 4180 parsing).
- **Tier 5 (Adversarial Stress Testing)**: Adversarial fuzzing, corrupted files, memory leaks, and timeout recovery.
