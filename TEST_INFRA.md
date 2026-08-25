# E2E Test Infra: RecipeBoxer React + TypeScript SPA & FastAPI Integration

## Test Philosophy
- Opaque-box, requirement-driven verification covering both FastAPI backend and Vite React SPA.
- Multi-tier testing methodology: Category-Partition, Boundary Value Analysis (BVA), Pairwise Combinatorial Testing, and Real-World Application Scenarios.
- Comprehensive test automation across Python pytest (ASGI TestClient) and Node.js Vitest / React Testing Library / E2E suites.

## Feature Inventory
| # | Feature | Source (Requirement) | Tier 1 (Feature) | Tier 2 (Boundary) | Tier 3 (Pairwise) | Tier 4 (Scenario) |
|---|---------|----------------------|:----------------:|:-----------------:|:-----------------:|:-----------------:|
| 1 | SPA Static Hosting & Catch-All Routing | ORIGINAL_REQUEST §R1 | ≥5 | ≥5 | ✓ | ✓ |
| 2 | Theme Subsystem (Light/Dark Mode) | ORIGINAL_REQUEST §R1 | ≥5 | ≥5 | ✓ | ✓ |
| 3 | SSE Real-Time Telemetry | ORIGINAL_REQUEST §R1 | ≥5 | ≥5 | ✓ | ✓ |
| 4 | Session Manager & Resumption | ORIGINAL_REQUEST §R2 | ≥5 | ≥5 | ✓ | ✓ |
| 5 | Ingestion Controller & Throttle Settings | ORIGINAL_REQUEST §R2 | ≥5 | ≥5 | ✓ | ✓ |
| 6 | Card Image Streaming Service | ORIGINAL_REQUEST §R3 | ≥5 | ≥5 | ✓ | ✓ |
| 7 | Rapid Queue Keyboard Shortcuts | ORIGINAL_REQUEST §R3 | ≥5 | ≥5 | ✓ | ✓ |
| 8 | Candidate Drawer & Multi-Card Grouping | ORIGINAL_REQUEST §R3 | ≥5 | ≥5 | ✓ | ✓ |
| 9 | Visual Inspection Toolbar (Pan/Zoom/Rotate/Filters) | ORIGINAL_REQUEST §R3 | ≥5 | ≥5 | ✓ | ✓ |
| 10 | Post-Extraction Split-Screen Recipe Editor | ORIGINAL_REQUEST §R4 | ≥5 | ≥5 | ✓ | ✓ |
| 11 | Inline Recipe CRUD & Persistence | ORIGINAL_REQUEST §R4 | ≥5 | ≥5 | ✓ | ✓ |
| 12 | Plan To Eat RFC 4180 CSV Export | ORIGINAL_REQUEST §R5 | ≥5 | ≥5 | ✓ | ✓ |
| 13 | Schema.org Recipe JSON-LD Export | ORIGINAL_REQUEST §R5 | ≥5 | ≥5 | ✓ | ✓ |
| 14 | Standard Recipe JSON Export | ORIGINAL_REQUEST §R5 | ≥5 | ≥5 | ✓ | ✓ |
| 15 | Full Integration Pipeline & Resilience | Acceptance Criteria | ≥5 | ≥5 | ✓ | ✓ |

## Test Architecture
- **Backend Test Runner**: Pytest using `.venv/bin/pytest` and `./test.sh` executing unit, API, SSE, static serving, and pipeline tests.
- **Frontend Test Runner**: Vitest + React Testing Library executing unit & component test cases (`npm test` in `frontend/`).
- **E2E Test Runner**: End-to-end integration suite verifying SPA static delivery, live SSE updates, keyboard actions, candidate drawer grouping, inline recipe edits, and file downloads.

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Vintage Heirloom Index Cards with Faded Cursive | Ingestion, SSE, Visual Filters (Contrast/Invert/Rotate), Recipe Editor, SQLite Save | High |
| 2 | High-Speed Keyboard-Only Triage of Large Deck | Ingestion, Rapid Queue shortcuts (Space/S/B/X/M), SSE, Resumption | High |
| 3 | Multi-Card 4-Part Thanksgiving Feast Recipe Assembly | Candidate Drawer, Multi-Card Grouping (>2 cards), Gemini Extraction, Editor, JSON-LD Export | High |
| 4 | Rate-Limited Slow Batch Run with Mid-Flight Resumption | Ingestion Throttle, SQLite Session Store, Server Crash/Resume, SSE Reconnect | High |
| 5 | Cross-Platform Multi-Format Export Interoperability | Ingestion, Extraction, Recipe Mutation, Plan To Eat CSV, Schema.org JSON-LD, Raw JSON | High |

## Coverage Thresholds
- Tier 1: ≥75 test cases (≥5 per feature across 15 features)
- Tier 2: ≥75 test cases (≥5 boundary and edge condition tests per feature)
- Tier 3: ≥10 pairwise interaction test cases
- Tier 4: ≥5 complex realistic application scenario workflows
- Total: ≥165 comprehensive test cases
