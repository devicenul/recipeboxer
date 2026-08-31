# RecipeBoxer Testing Architecture & Quality Assurance Guidelines

This document details the testing philosophy, multi-tier test pyramid, test execution procedures, and authoring guidelines for the **RecipeBoxer** platform.

---

## 1. Testing Philosophy

The RecipeBoxer quality assurance framework is built upon four core principles:

1. **Opaque-Box & Requirement-Driven**:
   Tests validate external behaviors, REST contracts, SSE event payloads, and export outputs against formal specifications rather than internal private method calls.
2. **Zero Mocking of Core Domain Logic**:
   Production code paths (heuristic calculations, CIELAB color math, greedy bipartite optimization, CSV serialization, SQLite WAL operations) are never mocked or replaced with synthetic shortcuts.
3. **100% Offline Testability**:
   All test suites run completely offline without an active internet connection or live `GEMINI_API_KEY`, using high-fidelity offline simulators (`MockGeminiClient` in `tests/conftest.py` and mock API handlers in `frontend/src/utils/api.ts`).
4. **Zero Regression Policy**:
   The repository maintains **2,205 passing automated tests** (1,848 Python pytest tests across Tiers 1–5 + 357 Frontend Vitest tests across 29 suites). Every code change must preserve 100% pass rates across all test suites.

---

## 2. The Multi-Tier Testing Architecture

```
                                    ▲
                                   / \
                                  /   \
                                 / T4  \     Tier 4: Real-World Scenarios (5 tests)
                                /-------\
                               /  T3     \    Tier 3: Pairwise Combinations (10 tests)
                              /-----------\
                             /    T2       \   Tier 2: Boundary & Corner Cases (75 tests)
                            /---------------\
                           /      T1         \  Tier 1: Isolated Feature Coverage (75 tests)
                          /-------------------\
                         /   Tier 5 Hardening  \ Tier 5: Adversarial Stress & Concurrency
                        +-----------------------+
```

### Tier 1: Feature Coverage (75 Tests)
- **Goal**: Verify core functional requirements for each of the 15 system features in isolation.
- **Coverage**: At least 5 distinct unit/component tests per feature.
- **Examples**: Fingerprint model validation, heuristic score calculations, CSV generation, SSE subscriber dispatch, route status responses.

### Tier 2: Boundary Value Analysis & Edge Cases (75 Tests)
- **Goal**: Verify system stability when subjected to edge inputs and malformed payloads.
- **Coverage**: At least 5 boundary/corner tests per feature.
- **Examples**: Empty strings, missing optional fields, zero concurrency values, corrupted image bytes, single-character recipe titles, non-ASCII Unicode tags, 10,000-character descriptions.

### Tier 3: Pairwise Subsystem Combinations (10 Tests)
- **Goal**: Test cross-cutting interactions between independent architectural subsystems.
- **Examples**:
  - Dark Theme + Invert Image Filter CSS synchronization.
  - Ingestion Throttling + Real-time SSE event streaming.
  - Multi-Card 3+ grouping + Split-screen Recipe Editor + Plan To Eat CSV export.

### Tier 4: Real-World Application Scenarios (5 Tests)
- **Goal**: Full lifecycle simulations mirroring end-user workflows.
- **Examples**:
  - 10-card vintage deck ingestion $\rightarrow$ correlation $\rightarrow$ HITL review with candidate replacement $\rightarrow$ inline editing $\rightarrow$ multi-format export download.

### Tier 5: Adversarial Hardening & Concurrency Stress
- **Goal**: Stress test database locks, connection failures, and serialization fuzzing.
- **Examples**:
  - SQLite WAL concurrent reader/writer race conditions.
  - SSE connection drops and exponential backoff auto-reconnects.
  - Strict RFC 4180 CSV fuzzing and non-English multi-byte character integrity.

---

## 3. Python Backend Testing Guide (`tests/`)

### 3.1 Pytest Configuration (`pyproject.toml`)
Pytest is configured with `asyncio_mode = "auto"` to seamlessly execute async test functions:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-v --tb=short"
filterwarnings = [
    "ignore::DeprecationWarning",
    "ignore::pytest.PytestCollectionWarning",
]
```

### 3.2 Core Fixtures (`tests/conftest.py`)

| Fixture Name | Type / Return | Description |
|---|---|---|
| `mock_gemini_client` | `MockGeminiClient` | High-fidelity offline simulator for Google Gemini 3.7 / 2.5 multimodal API calls. |
| `synthetic_card_deck` | `List[ScannedCard]` | Provides a standard synthetic test card collection (two-sided pairs, bleed-through stains, standalones, dividers). |
| `sample_card_corpus` | `List[ScannedCard]` | Sourced from `tests/fixtures/sample_cards/`, providing historical public domain recipe cards. |
| `sample_card_pairs` | `List[Tuple]` | Ground-truth front/back pairs verified against `manifest.json`. |
| `SQLiteFingerprintCache` / `test_cache` | `FingerprintCache` | Isolated, temporary SQLite WAL cache database created per test session. |
| `session_store` | `SessionStore` | Isolated SQLite WAL relational store for session state, proposals, and recipes. |
| `create_test_fastapi_app` | Helper Function | Factory producing an isolated FastAPI ASGI application instance with mocked background task runners. |
| `async_client` | `httpx.AsyncClient` | ASGI test client for executing asynchronous HTTP and SSE requests. |

### 3.3 Authoring Python Backend Tests

```python
import pytest
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.exporter.plan_to_eat import PlanToEatExporter

@pytest.mark.asyncio
async def test_plan_to_eat_csv_multiline_escaping():
    """Verify that multi-line ingredients and directions are properly quoted."""
    recipe = RecipeSchema(
        title="Grandma's Cinnamon Rolls",
        description="Rich dough with cinnamon-sugar swirl, \"Award Winning\"",
        ingredients=["2 cups flour", "1 tsp yeast", "1/2 cup milk\n(lukewarm)"],
        directions=["Step 1: Mix dough", "Step 2: Let rise", "Step 3: Bake at 375F"],
        prep_time="30 mins",
        cook_time="25 mins",
        yield_servings="12 rolls",
        tags=["Baking", "Breakfast", "Vintage"],
    )
    
    exporter = PlanToEatExporter()
    csv_out = exporter.export_to_csv_string([recipe])
    
    # Assert RFC 4180 invariants
    assert csv_out.endswith("\r\n")
    assert '""Award Winning""' in csv_out
    assert '"1. Mix dough\r\n2. Let rise\r\n3. Bake at 375F"' in csv_out or '"1. Mix dough\n2. Let rise\n3. Bake at 375F"' in csv_out
    assert exporter.validate_csv_format(csv_out) is True
```

---

## 4. Frontend Vitest Testing Guide (`frontend/`)

### 4.1 Vitest Configuration (`frontend/vite.config.ts`)
Vitest is configured with `jsdom` and `@testing-library/react`:
```typescript
test: {
  globals: true,
  environment: 'jsdom',
  setupFiles: ['./src/setupTests.ts'],
}
```

### 4.2 Polyfills & Setup Mocks (`frontend/src/setupTests.ts`)
`setupTests.ts` preconfigures:
- `@testing-library/jest-dom` matchers (`toBeInTheDocument()`, `toHaveClass()`, etc.).
- Mock `window.localStorage` store for theme persistence tests.
- Mock `window.matchMedia` for system dark mode detection.
- Mock `URL.createObjectURL` and `URL.revokeObjectURL` for file download tests.

### 4.3 Authoring Frontend Component Tests

```typescript
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { RapidQueueCard } from '../src/components/hitl/RapidQueueCard';
import { mockProposal } from './mocks';

describe('RapidQueueCard Component', () => {
  it('renders front and back cards with confidence score badge', () => {
    const onApprove = vi.fn();
    render(
      <RapidQueueCard
        proposal={mockProposal}
        onApprove={onApprove}
        onReject={vi.fn()}
        onStandalone={vi.fn()}
        onReverse={vi.fn()}
      />
    );

    expect(screen.getByText(/Apple Pie/i)).toBeInTheDocument();
    expect(screen.getByText(/85%/i)).toBeInTheDocument();

    const approveButton = screen.getByRole('button', { name: /approve/i });
    fireEvent.click(approveButton);
    expect(onApprove).toHaveBeenCalledTimes(1);
  });
});
```

---

## 5. Test Execution Commands & Regression Verification

### 5.1 Full Test Suite Execution
```bash
# 1. Run all 1,130 Python backend tests
./test.sh

# 2. Run all 102 Frontend Vitest tests
cd frontend && npm test -- --run

# 3. Combined execution string
./test.sh && cd frontend && npm test -- --run
```

### 5.2 Granular Pytest Target Execution
```bash
# Run 4-Tier E2E Integration Suite (165 tests)
pytest tests/test_e2e_4tier_suite.py -v

# Run specific tier by pattern
pytest -k "test_t1_" -v  # Tier 1 Feature Coverage
pytest -k "test_t2_" -v  # Tier 2 Boundary Cases
pytest -k "test_t3_" -v  # Tier 3 Pairwise Combinations
pytest -k "test_t4_" -v  # Tier 4 Real-World Scenarios

# Run Tier 5 Adversarial Hardening Suite (48 tests)
pytest tests/test_tier5_adversarial_coverage.py -v

# Run specific test modules
pytest tests/test_heuristics.py
pytest tests/test_plan_to_eat_csv.py
pytest tests/test_cache.py
pytest tests/test_fastapi_routes.py
```

### 5.3 Granular Vitest Target Execution
```bash
# Run specific frontend test suites
cd frontend
npx vitest run tests/tutorial.test.tsx
npx vitest run tests/sources.test.tsx
npx vitest run tests/ingestion.test.tsx
npx vitest run tests/app_routing.test.tsx
npx vitest run tests/hitl_queue.test.tsx
npx vitest run tests/image_viewer.test.tsx
npx vitest run tests/recipe_editor.test.tsx
npx vitest run tests/export.test.tsx
npx vitest run tests/theme.test.tsx
npx vitest run tests/challenger_m2.test.tsx
npx vitest run tests/tier5_adversarial_frontend.test.tsx
```

---

## 6. Pre-Commit Quality Checklist

Before committing code or submitting a handoff report, verify:

- [ ] `./test.sh` passes with **1,848 passing tests** (0 failures, 0 errors across Tiers 1–5).
- [ ] `cd frontend && npm test -- --run` passes with **357 passing tests** across 29 test files.
- [ ] No hardcoded bypasses or artificial mock logic were added to production source files.
- [ ] Any newly created feature is covered by corresponding Tier 1 and Tier 2 tests.
- [ ] All modified files conform to formatting standards.
