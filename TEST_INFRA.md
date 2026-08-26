# E2E Test Infrastructure: RecipeBoxer Full-Stack Platform

## 1. Test Philosophy & Architecture

The RecipeBoxer E2E Test Suite provides an exhaustive, requirement-driven, opaque-box verification platform across both the React 18+ TypeScript SPA frontend and the FastAPI backend engine.

### Core Testing Principles
1. **Opaque-Box Verification**: Exercise platform functionality strictly through public REST API endpoints, Server-Sent Events (SSE), standard filesystem interfaces, and frontend DOM component trees / user events without accessing internal private states or facade mocks.
2. **Progressive 4-Tier Test Matrix**:
   - **Tier 1 (Feature Coverage)**: Validates nominal happy path functionality across all 15 platform features (≥5 tests per feature).
   - **Tier 2 (Boundary & Corner Cases)**: Stress tests limits, zero-states, extreme inputs, malformed structures, rate limits, and network anomalies (≥5 tests per feature).
   - **Tier 3 (Cross-Feature Combinations)**: Evaluates pairwise interaction dynamics across ingestion, HITL correlation, recipe review, multimodal extraction, and document exporters (≥10 comprehensive interaction tests).
   - **Tier 4 (Real-World Application Scenarios)**: Simulates complex multi-generational heirloom digitization workflows from raw multi-card scans to structured publishing (≥5 end-to-end multi-stage scenarios).
3. **Deterministic & Isolated Execution**: Every test runs in isolated temporary workspaces with unique session IDs, SQLite WAL instances, and self-contained mock responses.

---

## 2. Feature Inventory & Coverage Mapping

| # | Feature | Milestone | Description & Contract | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|---------|-----------|------------------------|:------:|:------:|:------:|:------:|
| **F1.1** | Directory Picker (Modern API) | M1 | `showDirectoryPicker()` integration with `<input webkitdirectory>` fallback in `SourceSelector.tsx` | ≥5 | ≥5 | T3.1 | T4.S1 |
| **F1.2** | SSE Stability & Debounce | M1 | Single EventSource instance, ref-stabilized callbacks, heartbeat handling, and reconnection backoff | ≥5 | ≥5 | T3.2, T3.9 | T4.S4 |
| **F1.3** | Batch Default Tags | M1 | Session-level batch tags in Ingestion Controller merged into extracted recipes | ≥5 | ≥5 | T3.4, T3.10 | T4.S1, T4.S5 |
| **F2.1** | Multi-Card Full-Size Modal | M2 | Full-resolution modal & split-canvas inspection for 3+ card recipe sets during HITL | ≥5 | ≥5 | T3.4 | T4.S3 |
| **F2.2** | Positional Drag/Reorder Controls | M2 | Interactive ordering of 3+ card sets with explicit positional indices (Front, Back, Continuation 1..N) | ≥5 | ≥5 | T3.3, T3.4 | T4.S3 |
| **F2.3** | Multi-Card Sequence Preservation | M2 | Backend preservation and ordered merging of reordered card sequences during multimodal extraction | ≥5 | ≥5 | T3.4, T3.7 | T4.S3 |
| **F3.1** | Card Images in Review Pane | M3 | High-fidelity card scan image rendering (`/api/cards/{id}/image`) with fallback loading states in Recipe Editor | ≥5 | ≥5 | T3.5 | T4.S1, T4.S3 |
| **F3.2** | Workflow Navigation Dialog | M3 | "Next" button on last recipe displays `"Go to export? [Yes] [Cancel]"` confirmation dialog routing to Export | ≥5 | ≥5 | T3.8 | T4.S1 |
| **F3.3** | Inline Tag Editing & State Sync | M3 | Inline tag addition, removal, keyboard triggers (Enter/comma/semicolon), and state synchronization | ≥5 | ≥5 | T3.8 | T4.S1, T4.S5 |
| **F4.1** | Recipe Title Guessing Flag | M3 | Inferred titles for untitled / low-confidence cards append ` [GUESS]` suffix for user verification | ≥5 | ≥5 | T3.10 | T4.S2 |
| **F4.2** | Anecdote & Story Preservation | M3 | Family stories, marginalia, and vintage notes preserved in `description`/`source_notes` without polluting ingredients/directions | ≥5 | ≥5 | T3.8 | T4.S1, T4.S5 |
| **F5.1** | Google Docs OAuth & Doc Creation | M4 | Google OAuth 2.0 Web flow and Google Docs/Drive API creation with embedded scan images and formatted typography | ≥5 | ≥5 | T3.8 | T4.S5 |
| **F5.2** | Embedded Images in PDF & DOCX | M4 | Printable PDF and Word DOCX cookbook generation embedding high-resolution card scan images with proportional aspect scaling | ≥5 | ≥5 | T3.4 | T4.S1, T4.S5 |
| **F5.3** | Customizable Export Filenames | M4 | Editable suggested filename modal presented before initiating download across all 9 export formats | ≥5 | ≥5 | T3.8, T3.10 | T4.S1..S5 |
| **F5.4** | Tag Serialization Across Formats | M4 | RFC 4180 Column 8 `Tags` in Plan To Eat CSV, Schema.org `keywords`, Markdown/Cooklang frontmatter, Paprika categories | ≥5 | ≥5 | T3.4, T3.10 | T4.S1, T4.S5 |

---

## 3. Test Tier Breakdown & Methodology

### Tier 1: Feature Coverage (Happy Path Verification)
- **Objective**: Verify nominal functionality of every feature under expected inputs and user interactions.
- **Backend Scope**: API route contract compliance, 200/201 response status, correct JSON schemas, SQLite session persistence, exporter byte streams.
- **Frontend Scope**: Component rendering, button clicks, form input dispatch, state transitions, modal opening/closing, tab switching.

### Tier 2: Boundary & Corner Cases (Adversarial & Stress Verification)
- **Objective**: Verify robustness against edge conditions, malformed payloads, rate limits, zero lengths, special characters, and network disruptions.
- **Scenarios**:
  - Empty directories, missing scan files, zero-byte corrupted images.
  - Rapid double-clicks, duplicate tag submissions, extreme string lengths (10,000+ characters).
  - Special characters, quotes, commas, semicolons, emoji in recipe titles, notes, and tags.
  - Multi-card collections up to 10+ continuation cards.
  - Filename sanitization protecting against path traversal (`../../etc/passwd`).
  - Google OAuth token expiration and permission denial handling.

### Tier 3: Cross-Feature Combinations (Pairwise Interaction Dynamics)
- **T3.1**: Directory Picker Ingestion + Dark Mode Theme UI state persistence.
- **T3.2**: Slow-batch Throttled Ingestion + SSE Telemetry Backoff & Progress streaming.
- **T3.3**: Rapid Queue Keyboard Navigation + Multi-Card Candidate Reordering.
- **T3.4**: 3+ Multi-Card Reordered Grouping + Sequence Preservation Extraction + PDF/DOCX Embedded Scans + Tag Serialization.
- **T3.5**: Multipart Card Upload + Card Image Streaming Service + Recipe Editor Review Pane.
- **T3.6**: Session Cache Hits + Heuristic Screening + Ingestion Throttle Controls.
- **T3.7**: HITL Candidate Rejection + Standalone Conversion + Recipe Sequence Linkage.
- **T3.8**: Inline Tag & Story Editing + Navigation Dialog Routing to Export + Google Docs Document Creation.
- **T3.9**: Mid-flight Crash & Resume + SSE Telemetry Recovery without duplicate events.
- **T3.10**: Batch Default Tags + Inferred `[GUESS]` Titles + Custom Filename Modal + Plan To Eat RFC 4180 CSV Export.

### Tier 4: Real-World Application Scenarios (Multi-Stage Workflows)
- **Scenario 1 (Vintage Heirloom Index Cards with Faded Cursive & Marginalia)**:
  Ingest faded multi-generational heirloom cards with batch tags `["Heirloom", "Grandma"]`, preserve handwritten marginalia in description/notes, review card images in split pane, edit tags, trigger navigation modal, and export to PDF with embedded scan images and sanitized custom filenames.
- **Scenario 2 (High-Speed Keyboard Triage with Title Guessing)**:
  Ingest large mixed deck, triage with rapid keyboard shortcuts, verify cards with missing titles receive `[GUESS]` flag, convert low-confidence pairings to standalone recipes.
- **Scenario 3 (4-Card Thanksgiving Holiday Feast Assembly & Reordering)**:
  Group 4 separate recipe cards (Roast Turkey, Sourdough Stuffing, Pan Gravy, Cranberry Relish) using multi-card modal, reorder continuation cards into sequence (Front, Back, Cont 1, Cont 2), verify backend multimodal extraction preserves sequence and links all 4 card images.
- **Scenario 4 (Rate-Limited Ingestion with Network Interruption & SSE Recovery)**:
  Launch slow-batch ingestion with interval throttling, simulate temporary network disconnect and SSE reconnection backoff, ensure zero dropped telemetry events and clean completion.
- **Scenario 5 (Universal Multi-Format Export Interoperability & Compliance)**:
  Take fully digitized recipe collection with complex fractions, multi-line instructions, anecdotes, and default tags; export across all 9 formats (Plan To Eat CSV, Schema.org JSON-LD, PDF, DOCX, Markdown, Cooklang, Paprika, Google Docs, Raw JSON); verify 100% tag serialization and RFC 4180 quoting compliance.

---

## 4. Execution Commands & Automation

### Backend Pytest Suite
```bash
# Run the complete platform E2E test suite
./test.sh tests/test_e2e_platform.py -v

# Run full project pytest suite
./test.sh
```

### Frontend Vitest Suite
```bash
# Run the platform frontend E2E test suite
cd frontend && npm test -- tests/e2e_platform.test.tsx --run

# Run all frontend tests
cd frontend && npm test -- --run
```

---

## 5. Coverage Goals & Verification Standards
- **Total E2E Test Cases**: ≥165 tests across Tiers 1–4.
- **Feature Coverage**: 100% of Features F1.1–F5.4 covered in both backend and frontend suites.
- **Pass Rate**: 100% passing rate with zero skips or failures.
