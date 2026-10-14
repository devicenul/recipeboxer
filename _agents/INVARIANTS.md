# RecipeBoxer Non-Negotiable Architectural Invariants & Guardrails

This document establishes the non-negotiable architectural invariants and guardrails for the **RecipeBoxer** codebase. Any modifications made by AI agents or human contributors must preserve these rules without exception.

---

## 1. Invariant 1: SQLite WAL Concurrency & Thread Safety

### Rule
All SQLite database connections (`FingerprintCache` and `SessionStore`) must run in Write-Ahead Logging (WAL) mode, maintain thread synchronization via re-entrant locks (`threading.RLock()`), and offload synchronous blocking operations in asynchronous route handlers using `asyncio.to_thread`.

### Technical Specifications
1. **Mandatory Connection PRAGMAs**:
   Every SQLite connection opened in `recipeboxer/storage/` must execute:
   ```sql
   PRAGMA journal_mode = WAL;
   PRAGMA synchronous = NORMAL;
   PRAGMA busy_timeout = 5000;
   PRAGMA foreign_keys = ON;
   PRAGMA temp_store = MEMORY;
   PRAGMA cache_size = -64000; -- 64MB memory page cache
   ```
2. **Locking Discipline**:
   - Every read and write cursor block must be executed within `with self._lock:` where `self._lock = threading.RLock()`.
   - Direct concurrent cursor access without lock acquisition is strictly forbidden.
3. **Async Offloading**:
   - Asynchronous methods (`aget`, `aput`, `asave_session`, `alist_sessions`, `aremove_session`) must delegate synchronous database calls to thread pools via `await asyncio.to_thread(...)` to ensure the FastAPI ASGI event loop remains unblocked.
4. **Content-Addressable Keying**:
   - `FingerprintCache` must strictly key records by the lowercase 64-character SHA-256 hex digest of the raw image bytes.
   - Re-scanning an identical file must result in an immediate cache hit without invoking vision models or altering existing timestamps.

---

## 2. Invariant 2: Plan To Eat RFC 4180 CSV Escaping & Formatting

### Rule
The Plan To Eat CSV exporter (`recipeboxer/exporter/plan_to_eat.py` and `frontend/src/utils/exportPlanToEat.ts`) must generate CSV files conforming strictly to RFC 4180 and Plan To Eat's exact 9-column import specification.

### Technical Specifications
1. **Exact 9-Column Header**:
   The header row must contain exactly the following 9 columns in this exact sequence:
   ```csv
   Title,Description,Ingredients,Directions,Prep Time,Total Time,Yield,Tags,Source
   ```
2. **Record Line Terminators**:
   - Every record line (including the header and final row) **MUST** terminate with CRLF (`\r\n`), not bare LF (`\n`).
   - Python implementation must configure `lineterminator="\r\n"` in `csv.writer` / `csv.DictWriter`.
3. **Quoting & Escaping**:
   - Any field containing commas `,`, double quotes `"`, or line breaks (`\n` / `\r`) must be enclosed in double quotes `"..."`.
   - Embedded double quotes must be escaped by doubling them (`"` $\rightarrow$ `""`).
4. **Multi-line Field Formatting**:
   - `Ingredients`: Distinct ingredient items must be separated by standard line feeds (`\n`).
   - `Directions`: Numbered instruction steps must be separated by standard line feeds (`\n`) and formatted sequentially as `1. Step one\n2. Step two`.
   - `Tags`: Categorical tags must be formatted as a comma-delimited string (e.g. `Baking, Dessert, Vintage`).
5. **Encoding**:
   - Files and streams must be encoded in UTF-8 without Byte Order Mark (BOM).
6. **Validation Invariant**:
   - `validate_csv_format(csv_content)` must verify the 9-column header and ensure every data row parses to exactly 9 columns without delimiter corruption.

---

## 3. Invariant 3: HITL Async Barrier & Synchronization Semantics

### Rule
The Human-in-the-Loop review transition at Stage 4 must pause the background pipeline asynchronously without blocking worker threads, and the frontend must enforce input focus trapping to prevent hotkey misfires.

### Technical Specifications
1. **Cooperative Async Pause**:
   - When Stage 3 correlation completes, `RecipeBoxerAgent` must call `await session.pause_for_hitl()`.
   - `PipelineSession` clears `approval_event` and awaits:
     ```python
     done, _ = await asyncio.wait(
         [
             asyncio.create_task(self._approval_event.wait()),
             asyncio.create_task(self._cancellation_event.wait()),
         ],
         return_when=asyncio.FIRST_COMPLETED,
     )
     ```
   - No thread locks may be held, and no CPU spinning may occur while awaiting human input.
2. **Unblocking via Approval Submission**:
   - `POST /api/hitl/approve` validates that `session.state == PipelineState.HITL_PENDING`.
   - Sets `session.approvals`, transitions state to `PipelineState.EXTRACTING`, calls `self._approval_event.set()`, and returns `True`.
3. **Unblocking via Cancellation**:
   - `session.cancel()` sets `self._cancellation_event.set()`, cancels running tasks, transitions state to `PipelineState.CANCELLED`, and returns `False`.
4. **Input Focus Trapping (Frontend)**:
   - Global review shortcuts (`Space`, `Enter`, `S`, `B`, `X`, `M`, `Tab`, `Escape`) registered by `useKeyboard` **MUST** be suppressed whenever `document.activeElement` or `event.target` is an editable element (`INPUT`, `TEXTAREA`, `SELECT`, or `isContentEditable`).
   - Typing recipe notes, ingredient quantities, or search queries must never trigger unintended queue decisions.
5. **Ordered Sequence Approvals ("Part 1 ... N")**:
   - Recipe card sequences are represented canonically as `CardSequenceApproval.ordered_card_ids: List[str]` (`CardPairApproval` retained as alias).
   - Cards are indexed ordinally as Part 1 (`ordered_card_ids[0]`), Part 2 (`ordered_card_ids[1]`), ..., Part N. The backend multimodal extraction pipeline transcribes card images in strict positional sequence order and merges them into a single coherent `RecipeSchema`. Single-sided cards are represented as single-element sequences (`len(ordered_card_ids) == 1`).

---

## 4. Invariant 4: Vite SPA Static Bundle Contract & API Catch-All Fallback

### Rule
The frontend single-page application must compile directly into `recipeboxer/api/static`, and FastAPI routing must prevent missing API endpoints from returning HTML error responses.

### Technical Specifications
1. **Compilation Target**:
   - `frontend/vite.config.ts` must define `build.outDir` as `path.resolve(__dirname, '../recipeboxer/api/static')` and `build.assetsDir` as `'assets'`.
2. **Static Asset Mounting**:
   - FastAPI must mount `/assets` to `recipeboxer/api/static/assets/`.
3. **SPA Catch-All Route Guard (`GET /{full_path:path}`)**:
   - The catch-all route must strictly check the request path:
     ```python
     if full_path == "api" or full_path.startswith("api/"):
         raise HTTPException(status_code=404, detail=f"API endpoint not found: /{full_path}")
     ```
   - All non-API paths return `index.html` to support client-side HTML5 history navigation.
4. **Graceful Unbuilt Fallback**:
   - If `recipeboxer/api/static/index.html` is not present on disk, the backend must render `FALLBACK_SPA_HTML` providing instructions rather than crashing or throwing unhandled exceptions.

---

## 5. Invariant 5: Secure Card Image Streaming

### Rule
The image streaming endpoint (`GET /api/cards/{card_id}/image`) must strictly guard against path traversal attacks, enforce extension whitelists, and provide HTTP cache headers.

### Technical Specifications
1. **Path Traversal Prevention**:
   - Input `card_id` must be sanitized to strip path separators (`/`, `\`) and null bytes (`\0`).
   - Resolved filesystem paths must be verified to reside within authorized scan directories or upload storage folders.
2. **File Extension Whitelist**:
   - Only files with the following extensions are permitted for streaming:
     ```python
     ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
     ```
3. **HTTP Cache & MIME Headers**:
   - Responses must set the verified `media_type` (e.g. `image/jpeg`, `image/png`).
   - Responses must include `Cache-Control: public, max-age=86400` to prevent redundant network fetches during image pan and zoom operations.

---

## 6. Invariant 6: Ingestion Throttling & Cancellation Semantics

### Rule
The ingestion engine (`recipeboxer/ingestion/runner.py`) must respect execution bounds and cancellation signals in both Parallel and Slow-Batch modes.

### Technical Specifications
1. **Parallel Concurrency**:
   - Concurrency must be bounded by `asyncio.Semaphore(max_concurrency)` with configured bounds $1 \le \text{concurrency} \le 50$.
2. **Slow-Batch Inter-Chunk Delays**:
   - Slow-batch mode must pause between batch chunks using:
     ```python
     await asyncio.wait_for(cancel_token.wait_cancelled(), timeout=interval_seconds)
     ```
   - **INVARIANT: No Sleep After Final Chunk**: The runner must never pause or sleep after the final batch chunk has finished processing.
3. **Cancellation Token Responsiveness**:
   - Setting the cancellation token must immediately interrupt pending inter-batch sleeps and abort unstarted card fingerprinting tasks.

---

## 7. Invariant 7: Schema.org Recipe JSON-LD Structure

### Rule
The Schema.org JSON-LD exporter (`recipeboxer/exporter/json_ld.py` and `frontend/src/utils/exportJsonLd.ts`) must produce valid JSON-LD structured data conforming to the Schema.org `Recipe` specification.

### Technical Specifications
1. **Context & Graph Structure**:
   - Single recipe export must produce a root object with `@context: "https://schema.org"` and `@type: "Recipe"`.
   - Multi-recipe export must produce a root object with `@context: "https://schema.org"` and `@graph: [...]` array of recipe objects.
2. **Step Formatting**:
   - Directions must be exported as an array of `HowToStep` objects:
     ```json
     {
       "@type": "HowToStep",
       "position": 1,
       "text": "Preheat oven to 350°F (175°C)."
     }
     ```
3. **Duration Parser**:
   - Natural language duration strings must be converted to standard ISO 8601 duration notation via `human_duration_to_iso8601` (e.g. `"25 mins"` $\rightarrow$ `"PT25M"`, `"1 hr 30 mins"` $\rightarrow$ `"PT1H30M"`).

---

## 8. Invariant 8: Open Source License Compatibility & Dependency Vetting

### Rule
All third-party libraries, runtime packages, build tools, and dependencies added to RecipeBoxer **MUST** be licensed under GPLv3-compatible licenses (e.g., MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, PSF-2.0, or Public Domain). Incorporating libraries with restrictive, incompatible copyleft, or proprietary non-redistributable terms is strictly prohibited.

### Technical Specifications
1. **Permitted Dependency Licenses**:
   - `MIT`, `MIT-CMU`, `MIT-0`
   - `BSD-2-Clause`, `BSD-3-Clause`
   - `Apache-2.0`
   - `ISC`
   - `PSF-2.0` (Python Software Foundation License)
   - `Public Domain` / `Unlicense` / `CC0-1.0`
   - `MPL-2.0` (for standalone/dynamically linked components)
   - `GPLv3` / `GPL-compatible`
2. **Prohibited Dependency Licenses**:
   - Proprietary / Commercial-only licenses with anti-redistribution clauses.
   - Restrictive Commons Clause, SSPL, or non-commercial (NC) licenses that conflict with open source distribution.
3. **Agent & Contributor Verification Protocol**:
   - Before introducing any new Python dependency (`requirements.txt`, `pyproject.toml`) or npm package (`frontend/package.json`), the implementing agent must verify the package's license metadata against the approved list above.

---

## 9. Invariant 9: .recipeboxer Archive Integrity, Content-Addressing & ZipSlip Prevention

### Rule
The portable `.recipeboxer` project archive container (`recipeboxer/storage/archive.py`) must provide lossless round-trip serialization and deserialization of complete session state, preserve content-addressed SHA-256 asset immutability, and strictly enforce ZipSlip path traversal defenses during extraction.

### Technical Specifications
1. **Archive Container Structure**:
   - Every `.recipeboxer` archive is a standard compressed ZIP archive containing:
     - `manifest.json`: Complete serialized `RecipeBoxerArchiveManifest` storing session ID, creation timestamp, recipe card records, visual fingerprints, transcribed recipes (`RecipeSchema`), HITL review approvals, marginalia notes, and image-to-step bindings.
     - `assets/scans/<sha256>.<ext>`: Original card scan pixel buffers content-addressed by their lowercase 64-character SHA-256 digest.
2. **ZipSlip Defense & Canonical Path Verification**:
   - The extraction routine (`_safe_extract`) must verify that every resolved extracted target path begins with the canonical root extraction directory (`os.path.commonpath([target_dir, resolved_path]) == target_dir`).
   - Any member path attempting directory traversal (`../`, absolute paths, drive letters) must immediately raise a `ValueError` aborting extraction.
3. **Lossless Round-Trip Restore**:
   - Re-importing a `.recipeboxer` archive must reconstruct the exact sequence of cards, fingerprint feature descriptors, bounding boxes, recipe fields, and user notes with zero data degradation.
4. **Idempotent Merge & Deduplication**:
   - Merging an archive into an existing session must match incoming card assets by SHA-256 digest to avoid duplicating image files or creating redundant cards.

---

## 10. Anti-Cheat Invariants (INV-AC1..INV-AC6)

These non-negotiable invariants ensure all evaluation metrics, benchmarks, and model interactions reflect genuine machine intelligence operating solely from input pixels to model inference. Ground truth is strictly segregated from production systems and accessible only by trusted scorers.

### 10.1 Invariant AC1 (INV-AC1): No Answer Key
- **Statement**: Nothing under `recipeboxer/` may read `tests/`, `evals/`, any fixture or eval manifest, the ground-truth vault, or the scorer identity (an `age` key configured via `RB_EVAL_AGE_KEY_FILE` outside the workspace, historically referred to as `RB_EVAL_KEY`).
- **Rationale**: October 2026 audit findings revealed hardcoded manifest SHA and filename lookups in `extractor.py:125-193,680-770`, `semantic.py:48-71,262-278`, and `router.py:35-58,337-392` that bypassed model execution.
- **Enforced by**: `tests/anticheat/test_static_invariants.py`, canary tests in Phase 1 (import graph checks, audit-hook trip canary), and red-team review.

### 10.2 Invariant AC2 (INV-AC2): No Filename Meaning
- **Statement**: Filenames and card identifiers are strictly opaque keys; only their natural sort order may be used as a sequence prior. No substring, keyword matching, pattern parsing, or regex checks on names (e.g., `front`, `back`, `card\d`, category codes) are permitted in production code.
- **Rationale**: October 2026 audit findings revealed filename heuristics in `fingerprint.py:~253-379`, `heuristics.py:512-567`, and `router.py:77-107,399-427` that synthesized card roles, rulings, ink, stains, continuation text, and pairings from filenames.
- **Enforced by**: `tests/anticheat/test_static_invariants.py`, canary tests in Phase 1 (rename-invariance canary), and red-team review.

### 10.3 Invariant AC3 (INV-AC3): No Corpus Content
- **Statement**: No ground-truth recipe titles, distinctive recipe text phrases, or 64-hex SHA-256 corpus image hashes may appear as literals, lookup keys, or embedded assets anywhere within production code (`recipeboxer/`).
- **Rationale**: October 2026 audit findings uncovered canned recipes (cinnamon rolls, lemon bars, fudge, pecan in `extractor.py:773-901`) and direct SHA lookups returning pre-computed outputs without visual analysis.
- **Enforced by**: `tests/anticheat/test_static_invariants.py` (deny-list scanner generated from decrypted ground truth), canary tests in Phase 1 (prompt-leak scan), and red-team review.

### 10.4 Invariant AC4 (INV-AC4): No Silent Fabrication
- **Statement**: Any pipeline or model failure must produce an explicit, typed `StageFailure` routed directly to Human-in-the-Loop (HITL) review and session status; silent fallbacks to fabricated, placeholder, or synthetic recipes are strictly prohibited.
- **Rationale**: October 2026 audit findings revealed silent fallbacks to fake output and canned recipe structures in all three pipeline stages when calls failed or keys were absent.
- **Enforced by**: `tests/anticheat/test_static_invariants.py`, canary tests in Phase 1 (failure path propagation verification), and red-team review.

### 10.5 Invariant AC5 (INV-AC5): Black-Box Evals
- **Statement**: System Under Test (SUT) evaluation runs must execute in a scrubbed subprocess with an active runtime audit hook; ground truth is decrypted exclusively by the external scorer (`evals/score.py`) using the `age` identity from `RB_EVAL_AGE_KEY_FILE` (outside the workspace) strictly after the SUT completes execution.
- **Rationale**: October 2026 audit findings showed harness leaks including original SHA preservation across obfuscation (`obfuscation.py:271`), GT attributes passed to the SUT (`runner.py:467-522,718-722`), and a global de-obfuscation registry (`tests/fixtures/loader.py:195`).
- **Enforced by**: `tests/anticheat/test_static_invariants.py`, canary tests in Phase 1 (audit-hook trip canary, poisoned vault canary), and red-team review.

### 10.6 Invariant AC6 (INV-AC6): LLM Required
- **Statement**: An active LLM connection and valid API key are mandatory for recipe analysis and extraction; if no API key is provided, the pipeline enters a blocked onboarding state and executes no analysis. Local OCR may only provide complementary evidence (e.g. text cues or grounding checks), never stand in as a replacement model.
- **Rationale**: October 2026 audit findings showed mock judges enabled by default and offline extraction paths returning full recipes without an API key, masking model dependency and cost.
- **Enforced by**: `tests/anticheat/test_static_invariants.py`, canary tests in Phase 1 (blocked state verification on missing key), and red-team review.

