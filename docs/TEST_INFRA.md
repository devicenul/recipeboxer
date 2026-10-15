# Test Infrastructure & Anti-Cheat Verification

This document specifies the testing architecture, anti-cheat test suites, test double isolation, execution tiers, and frontend test workarounds for **RecipeBoxer**.

---

## 1. Test Suite Layout & Architecture

The RecipeBoxer repository maintains over 2,200 automated tests across backend and frontend environments, organized into strictly separated modules:

```
recipeboxer/
├── tests/
│   ├── anticheat/                         # Anti-cheat invariants & canary verification
│   │   ├── test_static_invariants.py      # AST analysis enforcing INV-AC1..INV-AC4
│   │   └── test_canaries.py               # 5 runtime canaries (rename, poison, audit, leak, import)
│   ├── fixtures/
│   │   ├── sample_cards/                  # Tier 0 (Train) synthetic recipe card images
│   │   │   ├── manifest.json              # Public ground truth manifest for T0 only
│   │   │   └── README.md                  # Provenance disclosure (explicitly synthetic)
│   │   └── loader.py                      # Fixture loaders for T0 datasets
│   ├── conftest.py                        # Hermetic API key stripper & test tier markers
│   ├── test_wp_*.py                       # Work package specific behavioral unit tests
│   ├── test_correlation.py                # Visual and physical correlation tests
│   ├── test_recipe_extractor.py           # Extraction pipeline tests
│   └── ...                                # Regression & platform E2E suites
├── frontend/
│   ├── tests/                             # 389 passing Vitest tests across 29 test suites
│   │   ├── wp_f1_legibility_hitl.test.tsx # Legibility UI & HITL evidence tests
│   │   ├── wp_f2_settings_spend.test.tsx  # Settings, onboarding, and spend telemetry tests
│   │   └── ...
├── cardforge/
│   └── tests/                             # Isolated synthetic generator test suites
└── evals/
    └── triage.yaml                        # Test remediation tracking catalog (2,000+ nodes)
```

---

## 2. Anti-Cheat Verification & Static Invariants

All tests enforcing benchmark honesty reside in [`tests/anticheat/`](../../../../../../storage/emulated/10/AntiGravity/Workspaces/recipeboxer/tests/anticheat):

### 2.1 Static AST Invariant Tests (`test_static_invariants.py`)
These tests inspect the Python Abstract Syntax Tree (AST) of the `recipeboxer/` production package to guarantee architectural invariants:
- **`test_no_answer_key_paths` (INV-AC1)**: Recursively parses all production source files. Asserts that string literals and AST nodes contain no forbidden path tokens: `tests/fixtures`, `evals/`, `manifest.json` (outside legitimate project archive modules), `*.gt.enc`, or `RB_EVAL_AGE_KEY_FILE`.
- **`test_no_filename_semantics` (INV-AC2)**: Scans pipeline stages (`vision`, `correlation`, `extraction`, `ai`, `agent`, `ingestion`). Detects comparisons or regex matches between filename-like variables (`card_id`, `fname`, `filename`, `path`) and semantic tokens (`front`, `back`, `card1`..`card9`, category codes `vhc`, `clp`, `dmg`, `mcs`, `sta`, `typ`).
- **`test_no_fixture_content` (INV-AC3)**: Ensures no ground-truth recipe titles, distinctive recipe text, or fixture image SHA-256 hashes are embedded as literals in production code.
- **`test_no_cardforge_or_evals_import` (INV-AC4 / INV-AC5)**: Verifies production code never imports `evals`, `cardforge`, or `tests`.
- **`test_no_test_double_sniffing`**: Verifies production code never inspects class names for `"Mock"` or checks attributes for `"mock"`.

### 2.2 The Five Anti-Cheat Canaries (`test_canaries.py`)
Implemented in accordance with `REMEDIATION_PLAN.md` §6.1 to actively verify system resilience against reintroduced cheats:
1. **Canary 1: Rename Invariance (`test_canary_rename_invariance`)**: Permuting filenames and input directory order produces byte-identical model inputs (up to opaque ID mapping).
2. **Canary 2: Poisoned Vault (`test_canary_poisoned_vault`)**: Swapping out evaluation ground truth produces byte-identical SUT output, proving the SUT cannot inspect the answer key.
3. **Canary 3: Audit-Hook Trip (`test_canary_audit_hook_trip`)**: Any attempt by the SUT to read paths under `evals/` or `tests/fixtures/` causes `sut_boot.py` to immediately terminate the process with exit code **86** and emit `CHEAT_DETECTED`.
4. **Canary 4: Prompt-Leak Scan (`test_canary_prompt_leak_scan`)**: Injected LLM clients inspect all production prompt parts and system instructions to verify no original filenames, fixture titles, or category codes appear in requests.
5. **Canary 5: Import Graph (`test_canary_import_graph`)**: Runtime dependency traversal verifies `recipeboxer.*` modules have zero reachability to `evals.*`, `cardforge.*`, or `tests.*`.

---

## 3. Hermetic API Key Isolation (`tests/conftest.py`)

A critical challenge during development is ensuring offline tests do not accidentally incur live model API spend or fail due to missing network credentials.

### The Autouse `_hermetic_api_key` Fixture
```python
@pytest.fixture(autouse=True)
def _hermetic_api_key(request, monkeypatch):
    """Keep offline tests hermetic: never pick up a developer's real API key.
    
    Settings reads .env from CWD, so without this a maintainer's key would
    turn offline tests into live, quota-burning API calls.
    """
    if request.node.get_closest_marker("live"):
        yield
        return
    from recipeboxer import config as rb_config

    monkeypatch.setitem(rb_config.Settings.model_config, "env_file", None)
    for var in ("GEMINI_API_KEY", "RECIPEBOXER_GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    if hasattr(rb_config.get_settings, "cache_clear"):
        rb_config.get_settings.cache_clear()
    yield
    if hasattr(rb_config.get_settings, "cache_clear"):
        rb_config.get_settings.cache_clear()
```

- **Mechanism**: For all tests not explicitly marked `@pytest.mark.live`, the fixture disables `.env` file loading in Pydantic Settings, unsets all Gemini environment variables, and purges the cached `Settings` singleton.
- **Guarantee**: Offline unit tests execute hermetically. Even if a maintainer has an active `.env` file or exported API key, offline tests will never make external network calls.

---

## 4. Test Doubles: `ScriptedLLMClient`

### 4.1 Strict Policy: Deprecation of `mock_gemini_client`
> [!CAUTION]
> **Never use the legacy conftest `mock_gemini_client` fixture in new or rewritten tests.**
> 
> The legacy fixture used duck-typed mock instances that were inspected by production code via `hasattr(client, "mock")` or `type(client).__name__.endswith("Mock")`. This practice violated clean dependency injection and created opportunities for test-double sniffing.

### 4.2 The `ScriptedLLMClient` Standard (`recipeboxer/ai/replay.py`)
New and remediated tests must use `ScriptedLLMClient` or inject concrete responses conforming to the `LLMClient` protocol:

```python
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.ai.client import LLMResponse, UsageTokens

# Create scripted client with canned responses keyed by call order or prompt match
scripted_client = ScriptedLLMClient([
    LLMResponse(
        text='{"visible_title": "Grandma\'s Sugar Cookies", "has_ingredients": true}',
        parsed={"visible_title": "Grandma's Sugar Cookies", "has_ingredients": True},
        usage=UsageTokens(input_tokens=150, output_tokens=30),
        latency_ms=120.0,
    ),
])

# Inject into extractor, fingerprint, or orchestrator
extractor = GeminiVisualExtractor(settings=settings, llm_client=scripted_client)
```

- **Behavior**: Pure, deterministic response sequencing.
- **Integrity**: Enforces zero inspection of filenames or hashes; responses are returned strictly by call sequence or explicit prompt criteria.

---

## 5. Test Tiers & Pytest Markers

RecipeBoxer partitions tests into three execution tiers:

1. **Offline Tier (Default)**:
   - Does not require API keys or vault credentials.
   - Evaluates behavior, error handling, state transitions, and schemas using test doubles.
   - **Never asserts accuracy numbers offline** (INV-AC4/INV-AC6).
   - Executed automatically on every pull request via `.github/workflows/ci.yml`.

2. **Live Tier (`@pytest.mark.live`)**:
   - Tests requiring genuine communication with the Google Gemini API.
   - Configured in `conftest.py`:
     ```python
     def pytest_runtest_setup(item):
         if item.get_closest_marker("live"):
             if not (os.getenv("GEMINI_API_KEY") or os.getenv("RECIPEBOXER_GEMINI_API_KEY")):
                 pytest.skip("Test requires GEMINI_API_KEY or RECIPEBOXER_GEMINI_API_KEY")
     ```
   - Automatically skipped if no live key is present in the environment.

3. **Score Tier (`@pytest.mark.score`)**:
   - Tests requiring ground-truth vault decryption (`evals/vault/*.gt.enc`).
   - Requires an age identity at `RB_EVAL_AGE_KEY_FILE` or `~/.config/recipeboxer/age.key`.
   - Automatically skipped in CI PR runs and local environments lacking maintainer age identities.

---

## 6. Test Remediation Catalog: `evals/triage.yaml`

During Phase 0 (WP-00d), an exhaustive triage of all 2,000+ test nodes was conducted, categorizing tests and recording their remediation actions:

- **Category A (Manifest Lookups & Canned Recipes)**: Tests that asserted exact outputs matching canned recipes (fudge, lemon bars, pecan pie) or verified manifest SHA lookups.
  - *Action*: Deleted or rewritten with `ScriptedLLMClient` to test pipeline plumbing rather than canned text.
- **Category B (Filename Heuristics)**: Tests asserting that cards named `..._front.jpg` or `..._back.jpg` were recognized as sequence starts/continuations.
  - *Action*: Rewritten with opaque random filenames (`secrets.token_hex(6)`) asserting behavior without filename reliance.
- **Category C (Harness Leaks)**: Tests relying on the global de-obfuscation registry or default mock judge.
  - *Action*: Excised; replaced with black-box runner tests.
- **Category D (Honest Invariant Tests)**: Anti-cheat canaries and static AST checks.
  - *Action*: Maintained as strict gatekeepers.

`evals/triage.yaml` is maintained as a read-only audit log of all test triage decisions.

---

## 7. Frontend Test Runner Workaround (Ext4 Mirroring)

### The Storage Limitation
In Android-hosted developer environments or containerized environments where the repository workspace resides on a shared storage volume (such as FAT32, exFAT, or Android emulated shared storage at `/storage/emulated/10`), the filesystem does not support standard POSIX symbolic links.

Because `npm` relies on symlinks inside `node_modules/.bin/`, running `npm ci` or `npx vitest` directly on the shared storage can result in `EPERM: operation not permitted, symlink` errors.

### The Mirror Workaround
To execute frontend tests cleanly, developers and CI environments mirror the frontend project to the Linux VM's local `ext4` filesystem (`$HOME/.cache/...`):

```bash
# 1. Define local ext4 mirror directory
EXT4_CACHE="$HOME/.cache/recipeboxer-frontend"
mkdir -p "$EXT4_CACHE"

# 2. Sync frontend source code (excluding node_modules) to ext4
rsync -av --delete --exclude 'node_modules' frontend/ "$EXT4_CACHE/"

# 3. Install dependencies and run tests on local ext4
cd "$EXT4_CACHE"
npm ci
timeout 900 npx vitest run
timeout 600 npx tsc --noEmit -p .

# 4. If building static assets, copy dist back to the repository
# cp -r dist/* /path/to/recipeboxer/recipeboxer/api/static/
cd -
```

This ensures full POSIX symlink compatibility and maximum I/O performance without polluting the shared workspace.

---

## 8. Command Execution Timeouts & Safety

Because `pytest-timeout` is not installed globally in the VM environment, all long-running commands must adhere to strict timeout wrapping:

1. **Shell-Level Timeouts**:
   Always wrap pytest and npm invocations in Linux `timeout`:
   ```bash
   # Wrapped backend test execution (10 minute limit)
   timeout 600 $py -m pytest tests/anticheat/ -q -p no:randomly

   # Wrapped frontend test execution (15 minute limit)
   timeout 900 npx vitest run
   ```

2. **Asyncio Safety**:
   Any asynchronous wait or event synchronization in tests must specify an explicit timeout:
   ```python
   # Correct: Async wait with explicit timeout
   await asyncio.wait_for(session.approval_event.wait(), timeout=10.0)
   ```
