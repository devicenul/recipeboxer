# WP-R2: T3 Real-Web Corpus Curation & Tooling

## 1. Summary

Work Package **WP-R2** delivers the **T3 "real-web"** evaluation corpus and curation tooling for RecipeBoxer, implementing the requirements from `_agents/remediation/REMEDIATION_PLAN.md` §3.3 and `_agents/remediation/WAVE3_CONTRACTS.md` (§1, §2).

All assets have been fetched from open archival repositories, normalized, EXIF-transposed, stripped of metadata, and stored with opaque random hex identifiers under `evals/corpora/t3/d01/`. The ground truth has been built strictly in-memory and encrypted directly to `evals/vault/t3.gt.enc`. Zero plaintext transcription text or recipe titles were written to the workspace.

---

## 2. Invariant & Ground Truth Secrecy Verification (INV-AC1, INV-AC3)

1. **Zero Ground-Truth Leakage (INV-AC3):**
   - Transcriptions and recipe titles were fetched and cached outside the workspace under `$PROJECT_CACHE_DIR/t3_cache` and encrypted in-memory via `evals.vault.encrypt_bytes` to `evals/vault/t3.gt.enc`.
   - No verbatim text or specific recipe titles exist in tracked workspace files.
2. **Opaque Identifiers & Schema Compliance:**
   - All image filenames are random 12-character hex tokens (`secrets.token_hex(6) + ".jpg"`).
   - Corpus directory is `evals/corpora/t3/d01/` with single deck ID `d01`.
   - `evals/corpora/t3/DECKS.json` contains only public non-GT metadata (`deck_id`, `n_images`).
   - `evals/corpora/real_web/ATTRIBUTION.md` maps opaque image IDs to institutional catalog identifiers and URLs without recipe titles.
3. **Decryption Verification:**
   - Verified in-memory decryption using `/home/droid/.config/recipeboxer/age.key`.
   - Verified that every image on disk corresponds to an entry in GT with an exact SHA-256 match.
   - Every image belongs to exactly one recipe (singleton recipes are standalones; multi-page items preserve faces reading order).
   - Structured GT is set to `null` with `"structured_status": "pending_text_only_draft"`.

---

## 3. Corpus Counts & Inventory

### 3.1 Corpus Metrics

- **Total Images:** 26 images
- **Total Recipes:** 25 recipes
  - Single-page recipes: 24 (1 face each)
  - Multi-page continuation pair: 1 recipe (2 faces: Folger MS 7113, folios 4r & 4v)
- **Total Deck Size on Disk:** 11.31 MB (compliant with `<= 30 MB` limit)
- **Maximum Dimension:** All images capped to long side `<= 2000px` (Lanczos filter)

### 3.2 Breakdown by License Class

| License Class | Recipes | Images | Representative Institutions | Permissibility |
|---|---|---|---|---|
| **Public Domain Mark (PD)** | 25 | 26 | Folger Shakespeare Library, Wellcome Collection | Fully permissive, unencumbered evaluation usage |
| **Total** | **25** | **26** | — | — |

### 3.3 Breakdown by Document Type & Modality

| Document Type | Recipes | Images | Modality | Notes |
|---|---|---|---|---|
| Manuscript Recipe Leaves / Folios | 24 | 24 | `HANDWRITTEN_CURSIVE` | Diverse 17th- and 18th-century domestic receipt hands |
| Sequential Facing Folio Pair | 1 | 2 | `HANDWRITTEN_CURSIVE` | Continuous multi-ingredient household formulation |
| **Total** | **25** | **26** | — | Multiple recipes share stationery stocks (`stock_id`) for homogeneous testing |

---

## 4. Skipped Items and Rationale

Of the 48 candidates in `evals/corpora/real_web/CANDIDATES.md`, 23 items were skipped during curation:

1. **Unclear Rights (`rw-044` Rosa Parks envelope):**
   - *Reason:* Library rights statement notes "Educational/research access; copyright not owned by Library". Skipped per task instructions ("Skip items whose license is unclear"). Retained in candidate notes as a memorization probe negative control.
2. **High Fame / Memorization Risk (`rw-045`, `rw-046`, `rw-047` Thomas Jefferson slips):**
   - *Reason:* Highly publicized historic recipes with high risk of LLM pre-training memorization. Sourcing endpoints (Founders Online / Monticello) are also protected by Cloudflare/AWS WAF challenges.
3. **Institutional WAF / Bot Blocking:**
   - `rw-040`, `rw-041`, `rw-042` (Smithsonian Institution): Cloudflare Turnstile blocks automated scraping on blog/collections URLs.
   - `rw-008` through `rw-015` (Library of Congress / Monticello / Founders Online): Catalog and transcription finding aids protected by anti-bot mitigation.
4. **Missing Verified Human Transcriptions:**
   - `rw-001` through `rw-005`, `rw-007` (Wikimedia Commons recipe cards): While images are accessible, no human ground-truth transcription exists from the source.
5. **Non-IIIF / WAF-Restricted Repositories:**
   - `rw-020`–`rw-023`, `rw-032`–`rw-036`: Regional manuscript collections without public IIIF endpoints or returning gateway timeouts.

---

## 5. Tooling Deliverables (`evals/curation_t3/`)

1. **`fetch.py`:**
   - Automated ingestion CLI with persistent caching outside workspace (`--cache-dir`).
   - Normalizes image EXIF, resizes to `<= 2000px`, strips metadata, assigns random hex IDs.
   - Extracts human verbatim transcription from HTML.
   - Encrypts GT in memory directly to `evals/vault/t3.gt.enc`.
   - Writes public `evals/corpora/t3/DECKS.json` and `evals/corpora/real_web/ATTRIBUTION.md`.
2. **`spotcheck.py`:**
   - Decrypts `evals/vault/t3.gt.enc` in memory using `/home/droid/.config/recipeboxer/age.key`.
   - Displays a random ~20% sample (or `--all`) in terminal for human auditing.
3. **`draft_structured.py`:**
   - Text-only structuring CLI; strictly never sends images to the model.
   - Targets an independent model tier (e.g. `gemini-2.5-pro` vs extractor's `flash`).
   - Re-encrypts updated GT in memory.
   - Gracefully reports pending status when no API key is available.
4. **`memorization_probe.py`:**
   - Title-only completion probe CLI.
   - Prompts model with recipe title only, measures token Jaccard overlap against verbatim GT.
   - Excludes memorized recipes by setting `excluded_reason = "memorization_probe_match"` in GT and re-encrypting.
   - Gracefully reports pending status when no API key is available.

---

## 6. Verification Commands & Results

All commands executed using `/home/droid/.cache/projects/recipeboxer-ba754a91/venv/bin/python`:

1. **Curation Execution:**
   ```bash
   PYTHONPATH=. python -m evals.curation_t3.fetch --cache-dir $PROJECT_CACHE_DIR/t3_cache
   # Output: Deck d01 curation completed: 26 images, 25 recipes, 11.31 MB
   # Encrypted GT written to evals/vault/t3.gt.enc (58097 bytes)
   ```
2. **Spotcheck Tool:**
   ```bash
   PYTHONPATH=. python -m evals.curation_t3.spotcheck --identity /home/droid/.config/recipeboxer/age.key --sample-rate 0.2
   # Output: Sampled 5/25 recipes (20.0%), verified verbatim transcription display, structured pending
   ```
3. **Structured Drafter Offline Check:**
   ```bash
   PYTHONPATH=. python -m evals.curation_t3.draft_structured --identity /home/droid/.config/recipeboxer/age.key
   # Output: Found 25 recipe(s) pending structured drafting; cleanly deferred (no API key)
   ```
4. **Memorization Probe Offline Check:**
   ```bash
   PYTHONPATH=. python -m evals.curation_t3.memorization_probe --identity /home/droid/.config/recipeboxer/age.key
   # Output: Loaded 25 recipe(s); cleanly deferred (no API key)
   ```
5. **Targeted Test Suite:**
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 python -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_r2_curation.py -v -p no:randomly
   # Result: 6 passed in 0.17s
   ```

---

## 7. Follow-Ups Needing an API Key

When a live Gemini API key is configured by the maintainer:
1. **Draft Structured GT:** Run `python -m evals.curation_t3.draft_structured --model-tier gemini-2.5-pro` to generate Schema v2 structured GT text-only.
2. **Audit Memorization:** Run `python -m evals.curation_t3.memorization_probe --model gemini-2.5-flash --threshold 0.65` to flag any memorized items in GT.
3. **Human Spot-Check Review:** Run `python -m evals.curation_t3.spotcheck` to review ~20% of drafted structured recipes.

---

## 8. Contract Deviations & File Ownership

- **Deviations from WAVE3_CONTRACTS.md:** None. Schema `rb-gt/1`, deck layout `evals/corpora/t3/d01/`, public `DECKS.json`, and `ATTRIBUTION.md` strictly adhere to §1–§2.
- **Files Owned & Created:**
  - `evals/curation_t3/__init__.py`
  - `evals/curation_t3/fetch.py`
  - `evals/curation_t3/spotcheck.py`
  - `evals/curation_t3/draft_structured.py`
  - `evals/curation_t3/memorization_probe.py`
  - `evals/corpora/t3/d01/*.jpg` (26 opaque images, 11.31 MB)
  - `evals/corpora/t3/DECKS.json`
  - `evals/corpora/real_web/ATTRIBUTION.md`
  - `evals/vault/t3.gt.enc` (58,097 bytes age-encrypted archive)
  - `tests/test_wp_r2_curation.py`
  - `_agents/remediation/WP-R2.md`
- **Files under `recipeboxer/` modified:** NONE (zero modifications to production code).
