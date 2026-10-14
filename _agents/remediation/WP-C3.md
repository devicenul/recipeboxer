# Work Package Completion Report: WP-C3 (Modality Router Remediation)

**Package Owner:** WP-C3 (flash)  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  

---

## 1. Summary of Remediation

In accordance with `_agents/remediation/REMEDIATION_PLAN.md` (§2, §4.1, §10), `_agents/INVARIANTS.md` (§10, INV-AC1..INV-AC4), and `_agents/TESTING_GUIDELINES.md` (§7), WP-C3 eliminated all answer-key lookups, category-code shortcuts, and filename heuristics from the document modality classifier and dynamic prompt router.

### Key Changes in `recipeboxer/vision/router.py`:

1. **Answer Key & SHA Lookup Elimination (INV-AC1, INV-AC3)**:
   - Deleted `_MANIFEST_MODALITY_CACHE` and `_get_manifest_modality_lookup()`.
   - Removed all SHA-256 calculation and manifest lookup branches across `ModalityClassifier.classify()`:
     - `card_input.sha256_hash` lookup branch removed.
     - `card_input.image_path` file SHA digest computation and lookup branch removed.
     - Raw bytes SHA digest computation and lookup branch removed.
   - Removed unused `json`, `hashlib`, `math`, and `re` imports.

2. **Category Code Removal & Semantic Normalization (INV-AC2)**:
   - Refactored `CardModality.normalize()`:
     - Stripped out all benchmark category codes: `'CLP'`, `'DMG'`, `'TYP'`, `'VHC'`, `'MCS'`, `'STA'`, `'_vhc_'`, `'_mcs_'`, `'_sta_'`.
     - Preserved legitimate semantic parsing for model/user output: `CLIPPING`, `NEWSPRINT`, `LABEL`, `MAGAZINE`, `CUTOUT`, `NEWSLETTER`, `TYPEWRITTEN`, `TYPE`, `TYPEWRITER`, `MONOSPACE`, `HANDWRITTEN`, `CURSIVE`, `SCRIPT`, `HANDWRITING`, `MIXED`, `DAMAGED`, `STAIN`, `DEGRADED`, `PRINT+HAND` (and `HANDWRITTEN_PRINT`, `HAND_PRINT`, `MANUSCRIPT_PRINT`).
     - Ensured removing `TYP` substring matching does not impact `TYPEWRITTEN`, `TYPEWRITER`, or `TYPE` recognition.

3. **Removal of Filename/'Mock Description' Cheats (INV-AC2, INV-AC4)**:
   - Deleted the `c_str` fallback in `classify()` that inspected string identifiers for `'clipping'`, `'newspaper'`, `'newsprint'`, `'typewriter'`, `'typewritten'`, `'typed'`, `'mixed'`, `'stained'`, and `'damaged'`.
   - Replaced fallback on failure to load or analyze pixels with an explicit degraded response:
     - `modality=CardModality.HANDWRITTEN_CURSIVE`
     - `confidence=0.0`
     - `reasoning="Modality unavailable: <reason>"`
     - `features={"fallback": True}`
   - Any nonexistent path (such as `clipping_typewriter.jpg`) completely ignores filename tokens and returns confidence `0.0`.

4. **Elimination of Semantic Filename Use in Vision Functions**:
   - Inspected `_classify_from_image_features(im, card_id)`: confirmed `card_id` is only used for optional debug logging (`logger.debug`) and never influences classification logic or decision branches.
   - Hardened `_resolve_image()` to validate file existence and throw clear `FileNotFoundError` or `ValueError` rather than silently swallowing missing inputs.

5. **Preservation of Domain VisualFingerprint Routing**:
   - Preserved genuine physical artifact routing (e.g. `OIL_STAIN`, `TEAR` -> `MIXED_MEDIA`, confidence `0.95`).
   - Preserved real ink characteristics routing (e.g. `TYPEWRITER_RIBBON` -> `TYPEWRITTEN`, confidence `0.96`; `PRINTED_TYPESET` -> `PRINTED_CLIPPING_OR_LABEL`, confidence `0.96`).

---

## 2. Test Verification

### Targeted Test Suite (`tests/test_wp_c3_router.py`)

A comprehensive, non-cheating test suite was authored covering all WP-C3 acceptance criteria:

```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" "$PROJECT_CACHE_DIR/venv/bin/python" -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_c3_router.py -v
```

**Results:**
```
tests/test_wp_c3_router.py::TestWP3CardModalityNormalization::test_normalize_category_codes_no_longer_mapped PASSED [ 10%]
tests/test_wp_c3_router.py::TestWP3CardModalityNormalization::test_normalize_semantic_words_still_map PASSED [ 20%]
tests/test_wp_c3_router.py::TestWP3CardModalityNormalization::test_normalize_typ_substring_removal_preserves_typewritten PASSED [ 30%]
tests/test_wp_c3_router.py::TestWP3ModalityClassification::test_synthetic_pil_image_classification_without_manifest PASSED [ 40%]
tests/test_wp_c3_router.py::TestWP3ModalityClassification::test_nonexistent_path_ignores_filename_semantics_and_returns_fallback PASSED [ 50%]
tests/test_wp_c3_router.py::TestWP3ModalityClassification::test_module_has_no_manifest_or_sha_lookups PASSED [ 60%]
tests/test_wp_c3_router.py::TestWP3ModalityClassification::test_raw_bytes_classification PASSED [ 70%]
tests/test_wp_c3_router.py::TestWP3ModalityClassification::test_client_override_in_modality_classifier PASSED [ 80%]
tests/test_wp_c3_router.py::TestWP3VisualFingerprintRouting::test_visual_fingerprint_routing_with_real_domain_fields PASSED [ 90%]
tests/test_wp_c3_router.py::TestWP3PromptRouter::test_prompt_router_configurations PASSED [100%]

============================== 10 passed in 0.07s ==============================
```

### Anti-Cheat Static Invariants Verification

Executed `tests/anticheat/test_static_invariants.py` with `--runxfail`:
- **Owned files (`recipeboxer/vision/router.py`, `recipeboxer/vision/__init__.py`, `tests/test_wp_c3_router.py`)**: **0 offending hits** for INV-AC1 (no answer keys/manifest references), INV-AC2 (no filename semantics/category codes), and INV-AC3 (no corpus fixtures/hashes).

---

## 3. Observed Test Failures in External Suites

As expected during Phase 2 cheat removal, legacy tests that depended on manifest tables or filename tokens now fail as documented in `evals/triage.yaml`:

1. **`tests/test_modality_router.py`**:
   - `TestCardModalityEnum::test_normalize_modalities`: Fails asserting category codes (`TYP`, `CLP`, `DMG`) resolve to specialized modalities. (Scheduled for deletion under WP-C8; `evals/triage.yaml` line 78).
   - `TestModalityClassifierBenchmark::test_classification_accuracy_on_benchmark_corpus`: Fails asserting $\ge 90\%$ accuracy offline across 48 synthetic cards. (Scheduled to move to live/scorer tier under WP-C8; `evals/triage.yaml` line 190).
   - `TestModalityClassifierBenchmark::test_classify_from_various_input_types`: Fails asserting unmocked offline classification on fixture card. (Scheduled for rewrite with mock under WP-C8; `evals/triage.yaml` line 176).

2. **`tests/test_tier5_adversarial_hardening.py`**:
   - `TestRecipeEditorAndExtractionIntelligenceAdversarial::test_prompt_router_modality_classification_and_routing`: Fails asserting filename tokens (`newspaper_clipping_recipe.png`) determine modality offline. (Scheduled for rewrite with mock under WP-C8; `evals/triage.yaml` line 183).

3. **`tests/test_agent_skills.py`**:
   - `TestAntigravityToolWrappers::test_classify_modality_tool`: Newly observed failure. Asserts `classify_card_modality_tool("rec_typ_beef_stroganoff_1956_front.jpg")["modality"] == "TYPEWRITTEN"`. Without the manifest SHA lookup cheat, unmocked offline deterministic CV returns default cursive, causing assertion failure.

---

## 4. Requests for Other WPs

- **WP-C8 (Test Triage & Rewrites)**:
  - Add `tests/test_agent_skills.py::TestAntigravityToolWrappers::test_classify_modality_tool` to triage list: rewrite using an injected mock classifier or mark as live, since offline tests must assert behavior rather than fixture classification accuracy.
  - Execute scheduled triage for `tests/test_modality_router.py` (deleting category code checks and rewriting input polymorphism tests with mocks).

---

## 5. Owned Files Manifest

- `recipeboxer/vision/router.py` (Remediated)
- `recipeboxer/vision/__init__.py` (Verified)
- `tests/test_wp_c3_router.py` (New targeted test suite)
- `_agents/remediation/WP-C3.md` (Remediation report)
