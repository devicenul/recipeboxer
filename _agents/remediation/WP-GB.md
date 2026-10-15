# Work Package Report: WP-GB (G11 Content-Continuity Features)

**Work Package:** WP-GB  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  
**Contracts:** `_agents/remediation/WAVE5_CONTRACTS.md` §1.2, §3; `_agents/remediation/WAVE3_CONTRACTS.md` §0; `REMEDIATION_PLAN.md` §9.1 item 2  

---

## 1. Summary of Changes

1. **`recipeboxer/correlation/content.py` (New)**:
   - Pure, deterministic, offline content-continuity feature extractor (zero LLM calls, zero eval-corpus vocabulary).
   - Generic English cooking vocabulary tables (units, stopwords, verbs) without corpus-specific terms.
   - Core exported APIs:
     - `content_features(fp_a: VisualFingerprint, fp_b: VisualFingerprint, *, deck_idf: dict[str, float] | None = None, include_ocr_cue: bool = True) -> list[FeatureEvidence]`:
       Returns directed pairwise feature evidence (raw measurements only, `log_lr=None`):
       - `content_xref`: cross-reference token overlap between ingredients and directions, with light lemmatization and optional deck IDF weighting. Raw in `[0.0, 1.0]` or `None`.
       - `sentence_continuity`: detects cut-off mid-sentence endings and continuation starts, including cross-card hyphenated word completions (e.g. `pre-` + `heat` -> `preheat`). Raw in `[0.0, 1.0]` or `None`.
       - `step_continuity`: tracks numbered step sequence progression ($n \to n+1$). Raw in `[0.0, 1.0]` or `None`.
       - `title_position`: canonical title distribution (front only) and penalties for title clashes. Raw in `[0.0, 1.0]` or `None`.
       - `marker_evidence` (and mirrored `ocr_cue`): matches forward and reverse continuation markers ('over', 'cont\'d', 'continued', 'page 2'). Raw in `[0.0, 1.0]` or `None`.
       - `section_flow`: canonical section flow from ingredient-only fronts to directions backs. Raw in `[0.0, 1.0]` or `None`.
     - `completeness_prior(fp: VisualFingerprint) -> dict[str, float]`:
       Returns `{"expects_continuation": p, "is_complete": p, "is_start": p}` based on card face recipe completeness.
     - `content_tokens(fp: VisualFingerprint) -> list[str]`:
       Extracts unique, lemmatized, non-stopword tokens for candidate retrieval and top-k screening.
     - `light_lemmatize(word: str) -> str`:
       Light morphological stemming (plurals, past tense/participles).
   - Robust to real-world OCR noise and punctuation variants. Empty/missing fields safely yield `raw = None`.

2. **`tests/test_wp_gb_content.py` (New)**:
   - 20 targeted unit and integration tests using fictitious recipe data (zero eval corpus leaks).
   - Tests cover:
     - Empty fields returning `raw = None` across all features.
     - `content_xref` cue sensitivity vs control and deck IDF weighting.
     - `sentence_continuity` hyphenation, dangling conjunctions, and strict directionality.
     - `step_continuity` sequential steps vs reverse/restarts.
     - `title_position` canonical vs clash vs reverse.
     - `marker_evidence` matching cues, directionality, and OCR noise robustness.
     - `section_flow` canonical vs reverse vs standalone recipes.
     - `completeness_prior` for complete, continuation-seeking, and back cards.
     - `content_tokens` filtering and lemmatization.

---

## 2. Files Changed / Owned

### Owned
- `recipeboxer/correlation/content.py` (New, Owned)
- `tests/test_wp_gb_content.py` (New, Owned)
- `_agents/remediation/WP-GB.md` (This report)

### Touched Outside Ownership
None.

---

## 3. Commands & Verification

1. **Targeted WP-GB Unit Test Suite**:
   ```bash
   PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
   py="$PROJECT_CACHE_DIR/venv/bin/python"
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_gb_content.py -v -p no:randomly
   ```
   **Result:** 20 passed in 0.27s (100%).

2. **Combined WP-GB & WP-GA Integration Suite**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_gb_content.py tests/test_wp_ga_evidence_screening.py -v -p no:randomly
   ```
   **Result:** 30 passed in 0.96s (100%).

3. **Anti-Cheat Static Invariants**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat/test_static_invariants.py -v -p no:randomly
   ```
   **Result:** 4 passed in 1.43s (100% clean).

4. **Anti-Cheat Canaries**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat/test_canaries.py -k "not test_canary_poisoned_vault and not test_canary_prompt_leak_scan" -v -p no:randomly
   ```
   **Result:** 4 passed in 0.83s (100% clean).

---

## 4. Contract Deviations

None. Adheres strictly to `WAVE5_CONTRACTS.md` §1.2 and §3.

---

## 5. Requests for Other WPs

- **WP-GA (`recipeboxer/correlation/evidence.py`, `screening.py`)**: Can import `content_features`, `completeness_prior`, and `content_tokens` directly from `recipeboxer.correlation.content`. All interfaces tested and passing integration tests with WP-GA.
- **WP-GD (`recipeboxer/correlation/optimizer.py`, `engine.py`)**: Can use `completeness_prior` to assign node costs in the path-cover optimization.

---

## 6. Risks & Mitigations

- **Risk**: Variations in OCR punctuation (e.g. `(over ->)`, `- 2 -`, `cont.d`).  
  **Mitigation**: Normalizer strips non-alphanumeric noise while testing both spaced and unspaced representations (e.g. matching `pto` against `p.t.o.` and `p t o`).
- **Risk**: Cards lacking text or boundary metadata.  
  **Mitigation**: All features strictly return `raw: None` on uncomputable or empty inputs, preserving proper likelihood ratio calculation in `DeckBackground`.
