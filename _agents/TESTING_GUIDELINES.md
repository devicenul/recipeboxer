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

   > [!WARNING] Superseded by Writing Non-Cheating Tests (2026-10)
   > Offline tests must assert **system behavior only**, never extraction or correlation accuracy. Previous practices asserting 100% offline extraction or pairing on corpus images relied on prohibited manifest lookups and canned recipes (INV-AC1, INV-AC3). True accuracy is measured exclusively in live and scoring suites.
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

> [!WARNING] Superseded by Writing Non-Cheating Tests (2026-10)
> Production code must never import or read `manifest.json` or fixture ground truth (INV-AC1). Furthermore, tests must never assert offline pairing or extraction accuracy on `sample_card_corpus` / `sample_card_pairs`. Corpus fixtures are designated as Tier T0 (train) and may only be used for behavioral tests, not quality claims.

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

> [!WARNING] Superseded by Writing Non-Cheating Tests (2026-10)
> Legacy test suites that assert extraction or correlation accuracy offline, rely on manifest lookups, or parse filename conventions (e.g., `front`/`back` tokens) are marked for deletion or rewriting under Phase 2 triage (WP-C8). Reviewers must verify tests against the Non-Cheating Tests Checklist in Section 7.

---

## 7. Writing Non-Cheating Tests

Following the October 2026 evaluation audit, all tests across RecipeBoxer must strictly enforce anti-cheat invariants (INV-AC1 through INV-AC6). Ground truth is strictly segregated from production code, and tests must never synthesize answers or evaluate accuracy using offline shortcuts.

### 7.1 Test Execution Tiers & Assertion Boundaries

The test suite is partitioned into three distinct operational tiers. Each tier has strict boundaries regarding environment prerequisites and permitted assertions:

| Test Tier | Marker / Execution | Environment & Secrets | Permitted Assertions | Prohibited Assertions |
|---|---|---|---|---|
| **Offline** | Default (`pytest tests/`) | No API keys; offline execution; scrubbed environment | **Behavior only**: API contracts, schema validation, state transitions, exception handling, DAG path chaining, card retention, and determinism | **NEVER accuracy**: CER, WER, line-level F1, exact cluster accuracy, pairing precision, or canned recipe verification |
| **Live** | `@pytest.mark.live` | Requires `GEMINI_API_KEY`; adheres to spend budget & quality profile | **Live model interactions**: End-to-end inference, prompt formatting, multimodal response parsing, retry loops, and live API contracts | Unbounded spend; runs exceeding cost caps or rate limits |
| **Score** | `evals/score.py` (CLI) | Requires `age` private key from `RB_EVAL_AGE_KEY_FILE` outside repo | **Evaluation metrics**: B-Cubed F1, ARI, CER, direction F1, order $\tau$, cost ledger verification against decrypted vault GT | Any execution inside the production runtime or PR workflow |

> [!IMPORTANT]
> **Cardinal Rule**: *Offline tests assert behavior; only live and scorer runs assert accuracy.* An offline test must verify that an algorithm correctly processes mock outputs without asserting whether the outputs match historical ground truth.

### 7.2 Mocking Rules & Isolation

1. **Keying Mocks**:
   - Mocks (e.g., `MockGeminiClient`) must be keyed strictly by **call order** (invocation index) or an **explicit test-supplied mapping** passed directly inside the test body.
   - Mocks must **NEVER** be keyed by filename, file path, card label, or image content hash (SHA-256) of corpus images. Keying by filename or hash turns the mock into an illicit answer key lookup.
2. **Manifest Isolation**:
   - Production code (`recipeboxer/`) must **NEVER** import from `tests/fixtures/`, read `manifest.json`, access the ground-truth vault (`evals/vault/`), or parse benchmark answer keys (INV-AC1).
   - Any runtime attempt by production modules to access manifest or vault files triggers an immediate `CHEAT_DETECTED` termination via `sys.addaudithook`.
3. **No SUT Contamination**:
   - Tests must never feed ground-truth attributes, pre-grouped clusters, or de-obfuscation registries to the System Under Test (SUT).

### 7.3 Corpus Tiers (T0..T4) & Implementer Visibility

Evaluation datasets are divided into five distinct tiers to preserve evaluation integrity and prevent adaptive overfitting:

| Tier | Dataset Description | Images Visible to Implementers? | Ground Truth Visible? | Primary Purpose | Feedback Policy |
|---|---|---|---|---|---|
| **T0 Train** | Existing 48 synthetic fixtures + local synthetic decks | **Yes** | **Yes** | Unit tests, feature weight calibration, prompt iteration, and debugging | Full per-image diffs and diagnostics allowed (labeled "train") |
| **T1 Dev-Eval** | Curated synthetic decks from external recipe text with opaque names | **Yes** (enables integration runs) | **No** (encrypted in vault) | Daily honest development metrics | **Aggregate and per-category breakdown only**; never per-image diffs; rate-limited ($\le 10$ runs/day) |
| **T2 Held-Out** | Held-out recipes, seeds, homogeneous decks, and distractors | **No** (encrypted in vault) | **No** (encrypted in vault) | Gate A & Gate B qualification decisions; anti-overfitting barrier | Aggregate only; run exclusively at release gates |
| **T3 Real-Web** | Public-domain photos from Smithsonian, LoC, etc. with human transcriptions | **Yes** (public domain archives, opaque IDs in repo) | **No** (encrypted in vault) | Real-world handwritten & print extraction; standalone distractor resilience | Aggregate only |
| **T4 Real-User** | User-contributed physical photo collections with skew, glare, timestamps | Per user preference | **No** (encrypted in vault) | Ultimate benchmark headline metric | Aggregate only |

### 7.4 T1 Feedback Policy

To prevent "hill-climbing" on evaluation data (adaptive manual or automated overfitting):
- Scoring runs on Tier T1 return **only aggregate metrics** (e.g., overall B-Cubed F1, mean CER, stage failure counts) and per-category breakdowns.
- Individual recipe error diffs, failed image filenames, or per-card predictions are **never** disclosed in T1 scoring reports.
- If detailed per-image failure diagnostics are required, engineers must reproduce and debug the issue on **Tier T0** datasets.
- Scoring T1 is rate-limited in CI (e.g., $\le 10$ runs per PR per day).

### 7.5 Good vs. Bad Test Examples

#### Bad Test Example: Filename Cheats, Offline Pairing & Canned Extraction Assertions
```python
# BAD: Violates INV-AC1, INV-AC2, and INV-AC3
# 1. Names files with semantic role hints ("front", "back").
# 2. Asserts correlation accuracy offline without an LLM mock.
# 3. Asserts exact ground-truth recipe extraction offline (relying on manifest lookup cheats).
import pytest
from recipeboxer.models.card import ScannedCard
from recipeboxer.correlation.optimizer import correlate_cards
from recipeboxer.extraction.extractor import RecipeExtractor

def test_apple_crisp_pairing_and_extraction_BAD():
    # CHEAT: Filename provides front/back semantic cues
    card_front = ScannedCard(id="apple_crisp_front.jpg", path="tests/fixtures/sample_cards/apple_crisp_front.jpg")
    card_back = ScannedCard(id="apple_crisp_back.jpg", path="tests/fixtures/sample_cards/apple_crisp_back.jpg")

    # CHEAT: Relies on filename substring heuristics inside correlate_cards
    proposals = correlate_cards([card_front, card_back])
    assert len(proposals) == 1
    assert proposals[0].front_id == "apple_crisp_front.jpg"
    assert proposals[0].back_id == "apple_crisp_back.jpg"

    # CHEAT: Relies on offline manifest SHA lookup table returning canned recipe text
    extractor = RecipeExtractor()
    recipe = extractor.extract_from_cards([card_front, card_back])
    assert recipe.title == "Old-Fashioned Apple Crisp"  # Asserts GT accuracy offline!
    assert "4 cups sliced apples" in recipe.ingredients
```

#### Good Test Example: Opaque Identifiers, Injected Mock & Algorithmic Behavior Assertion
```python
# GOOD: Complies with INV-AC1, INV-AC2, and INV-AC3
# 1. Uses completely opaque identifiers without semantic cues.
# 2. Injects a mock LLM client with responses keyed strictly by call order.
# 3. Asserts optimizer algorithmic behavior (chaining logic), NOT corpus accuracy.
import pytest
from recipeboxer.models.card import ScannedCard
from recipeboxer.correlation.optimizer import SequenceOptimizer
from tests.conftest import MockGeminiClient

def test_optimizer_builds_chain_from_explicit_fingerprints_GOOD():
    # Opaque IDs - no semantic cues, numbers, or roles
    card_a = ScannedCard(id="card_01a8f9e2.jpg", path="/tmp/test_cards/card_01a8f9e2.jpg")
    card_b = ScannedCard(id="card_09c3d4b1.jpg", path="/tmp/test_cards/card_09c3d4b1.jpg")

    # Inject mock with explicit outputs keyed strictly by call order
    mock_client = MockGeminiClient()
    mock_client.set_ordered_responses([
        # Call 1 (card_a): Fingerprint indicating front with continuation
        {
            "visible_title": "Grandma's Sugar Cookies",
            "has_continuation_marker": True,
            "is_back_side": False,
            "ink_color": "blue",
        },
        # Call 2 (card_b): Fingerprint indicating back continuation
        {
            "visible_title": None,
            "has_continuation_marker": False,
            "is_back_side": True,
            "ink_color": "blue",
        },
    ])

    optimizer = SequenceOptimizer(client=mock_client)
    chains = optimizer.build_chains([card_a, card_b])

    # Assert behavior: optimizer correctly chains front -> back based on mock outputs
    assert len(chains) == 1
    assert chains[0].ordered_card_ids == ["card_01a8f9e2.jpg", "card_09c3d4b1.jpg"]
    assert chains[0].is_complete is True
```

### 7.6 Reviewer Anti-Cheat Checklist

When reviewing any pull request introducing or modifying tests, ensure the following checklist passes:

- [ ] **No Answer Keys (INV-AC1)**: Does the test or code touch `tests/fixtures/sample_cards/manifest.json`, `evals/vault/`, or reference `RB_EVAL_KEY` / `RB_EVAL_AGE_KEY_FILE`?
- [ ] **Opaque Identifiers (INV-AC2)**: Are all card IDs and test filenames opaque? Check that no test relies on keywords (`front`, `back`, `card1`, `part2`, `deck_`, recipe names) in filenames.
- [ ] **Behavior vs. Accuracy**: Does this offline test assert system behavior (contracts, routing, error handling, schema properties) rather than model extraction accuracy, CER, or pairing accuracy?
- [ ] **Mock Keying**: If `MockGeminiClient` or any simulator is used, is it keyed strictly by invocation order or an explicit in-test map? Verify it does NOT key by filename, image path, or SHA-256 hash.
- [ ] **No Harness Leaks (INV-AC5)**: Does the test avoid passing ground-truth clusters, pre-grouped card pairs, or decrypted attributes to production methods?
- [ ] **Proper Marking**: Are tests that call real LLMs marked with `@pytest.mark.live`? Are benchmark scoring utilities placed under `evals/` and restricted to the `score` tier?

