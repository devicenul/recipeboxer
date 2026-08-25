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
5. **Multi-Card Grouping Support (3+ Cards)**:
   - Multi-card recipe groups are represented as `CardPairApproval.card_ids: List[str]`.
   - Front card is `group[0]`, primary back is `group[1]`, and continuations are `group[2:]`. The backend extraction pipeline must merge text from all grouped cards into a single coherent `RecipeSchema`.

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
