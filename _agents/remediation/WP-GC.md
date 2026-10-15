# Work Package Completion Report: WP-GC (G6 Set-Level Verification)

**Work Package:** WP-GC  
**Branch:** `remediation/phase0`  
**Status:** Completed & Verified  
**Contracts:** `_agents/remediation/WAVE5_CONTRACTS.md` §1.1, §1.2, §3; `_agents/remediation/WAVE3_CONTRACTS.md` §0; `REMEDIATION_PLAN.md` §9 (G6), §9.1  

---

## 1. Summary of Changes

In accordance with Wave 5 Contracts and the Remediation Plan, Stage 2 semantic continuity verification has been completely rewritten around multimodal set-level verification (`SetVerifier`):

1. **Prompt Template & Registry (`recipeboxer/prompts/set_verify_v1.md`)**:
   - Created official system prompt `set_verify_v1.md` registered in `recipeboxer/prompts/__init__.py`.
   - Requires verbatim quotes of visible text as evidence, permits `'none'` when no candidate continues the anchor, and requests candidate continuations in sequence order.
   - Provenance tracked via deterministic 8-character SHA-256 version hash (`d46540ed`).

2. **Multimodal Set-Level Verifier (`SetVerifier` in `recipeboxer/correlation/semantic.py`)**:
   - **Single Call per Anchor**: Evaluates anchor image plus its $k$ candidate images in one unified LLM call.
   - **Deterministic Randomized Candidate Ordering**: Shuffles candidate ordering per call seeded deterministically from the SHA-256 hash of the anchor content hash combined with sorted candidate content hashes. Eliminates position bias while preserving run-to-run reproducibility.
   - **Strict Neutral Labels**: All parts and prompts use only neutral identifiers (`'Anchor'`, `'Candidate 1..k'`). Zero filenames, paths, or card IDs are leaked to the model (INV-AC2).
   - **Bidirectional Label Mapping**: Maps model's candidate label decisions (`'Candidate i'`) back to domain `card_id`s, returning ordered `continuations` and remaining candidate IDs in `rejected`.
   - **Verbatim Quote Validation**: Validates model evidence quotes against the combined text corpus of the anchor and candidate fingerprints/OCR. Unverified quotes are logged and flagged (`flags.append("UNVERIFIED_QUOTE: ...")`) and reduce confidence by 50%.
   - **Deterministic Content Caching**: Caches `SetVerification` results keyed by `(anchor_hash, sorted_candidate_hashes, model, prompt_version)`.
   - **Typed Error Handling**: `BudgetExceeded` propagates unmodified; all other model/API failures raise `SemanticVerificationUnavailable` with typed `FailureReason`.
   - **Removal of Offline Heuristics**: Eliminated offline continuity scoring that fabricated numbers without a model (INV-AC4/AC6). Without a configured LLM client or API key, raises `SemanticVerificationUnavailable(FailureReason.NO_API_KEY)`.

3. **Deprecated Compatibility Shim (`GeminiSemanticVerifier`)**:
   - Retained thin shim delegating pairwise calls (`verify_pair`) to `SetVerifier` with $k=1$, converting results into `CorrelationProposal` for concurrent orchestrator integration (WP-GD). Emits `DeprecationWarning`.

4. **Model Extension (`recipeboxer/models/correlation.py`)**:
   - Added optional `flags: List[str] = Field(default_factory=list)` to `SetVerification` for recording quote verification flags without breaking schema compatibility.

5. **Comprehensive Test Suite (`tests/test_wp_gc_semantic.py`)**:
   - 14 automated unit tests using `ScriptedLLMClient` covering: request shape, neutral labels, registry prompt, deterministic randomization, label mapping, quote validation, hallucination confidence penalty, cache hits, typed failures, budget propagation, and compatibility shim.

---

## 2. Files Changed

### Created
- `recipeboxer/prompts/set_verify_v1.md` (Official set verification prompt)
- `tests/test_wp_gc_semantic.py` (14 unit tests for SetVerifier)
- `_agents/remediation/WP-GC.md` (This note)

### Modified (Owned)
- `recipeboxer/correlation/semantic.py` (Rewritten around SetVerifier with compatibility shim)

### Touched Outside Ownership
- `recipeboxer/models/correlation.py`: Added `flags: List[str] = Field(default_factory=list)` to `SetVerification`.
- `tests/test_wp_c2_semantic.py`: Updated 4 tests to reflect removal of offline heuristic numbers and error typing.
- `tests/test_correlation.py`: Updated 2 legacy tests (`test_semantic_verifier_custom_weights` uses `ScriptedLLMClient`, `test_semantic_verifier_offline_continuity_cues` asserts `SemanticVerificationUnavailable(NO_API_KEY)`).

---

## 3. Commands & Results

1. **Targeted WP-GC Test Suite**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" RECIPEBOXER_GEMINI_API_KEY="" GEMINI_API_KEY="" \
   timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_gc_semantic.py -v -p no:randomly
   # Result: 14 passed in 0.41s (100%)
   ```

2. **Combined Semantic & Correlation Test Suite**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" RECIPEBOXER_GEMINI_API_KEY="" GEMINI_API_KEY="" \
   timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" \
       tests/test_wp_gc_semantic.py \
       tests/test_wp_c2_semantic.py \
       tests/test_correlation.py \
       -v -p no:randomly
   # Result: 34 passed in 0.51s (100%)
   ```

3. **Anti-Cheat Static Invariants Suite**:
   ```bash
   PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" RECIPEBOXER_GEMINI_API_KEY="" GEMINI_API_KEY="" \
   timeout 60 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat/test_static_invariants.py -v -p no:randomly
   # Result: 4 passed in 1.79s (100% clean)
   ```

---

## 4. Contract Deviations

None. Implemented strictly to `WAVE5_CONTRACTS.md` §1.1 and §1.2.

---

## 5. Requests for Other WPs

- **WP-GD (`GroupingEngine`)**: `GroupingEngine` can instantiate `SetVerifier(llm_client=llm_client, settings=settings)` and call `await verifier.verify(anchor_fp, cand_fps, images=images)`.
- **WP-GE (`LinkCalibrator`)**: Set verification outcome (`set_verified=True/False/None`) is cleanly accessible via `b_id in ver.continuations` / `b_id in ver.rejected`.

---

## 6. Risks & Mitigations

- **Risk**: Position bias when presenting candidates to LLM.  
  **Mitigation**: Candidate order is shuffled with a deterministic PRNG seeded from the SHA-256 of the anchor hash and sorted candidate hashes.
- **Risk**: LLM hallucinates text quotes in evidence.  
  **Mitigation**: All cited quotes are cross-checked against the card fingerprint/OCR text corpus; unverified quotes are flagged and penalize confidence score.
