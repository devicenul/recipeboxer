# Work Package Report: WP-GD (G7 Global Optimization + GroupingEngine + Orchestrator Integration)

**Work Package:** WP-GD (pro)  
**Branch:** `remediation/phase0`  
**Status:** Completed  
**Contracts:** `_agents/remediation/WAVE5_CONTRACTS.md` (§1.1, §1.2, §3), `REMEDIATION_PLAN.md` (§9, §9.1, §4.1)

---

## 1. Summary of Completed Implementation

### A. Global Optimization (`recipeboxer/correlation/optimizer.py`)
- **`path_cover(nodes, links, node_costs) -> List[List[str]]`**:
  - Implemented minimum-cost path cover via bipartite matching (`scipy.optimize.linear_sum_assignment`) on $-\log p_{\text{link}}$.
  - Dummy predecessor ("no predecessor") and successor ("no successor") costs formulated from the completeness prior (`expects_continuation`, `is_complete`, `is_start`, `has_title`, `role_confidence`).
  - **$\le 1$ Title-Bearing Start Constraint**: Enforces that each partitioned path contains at most one title-bearing start node. If multiple title-bearing nodes end up in the same chain, the chain is repaired by breaking the weakest link between them.
  - **Cycle Prevention**: Detects cycles (2-cycles, 3-cycles, or general cycles) and breaks them at their weakest link edge.
  - **Tiny Exact MILP (`_solve_component_milp`)**: For small ambiguous components ($K \le 8$ cards), formulates and solves an exact 0-1 Integer Linear Program using `scipy.optimize.milp` with subtour elimination and degree constraints.
  - **Deterministic Invariance**: Lexicographic ordering and consistent tie-breaking ensure permutation-invariant outcomes.
  - **Backward Compatibility**: `PairingOptimizer.optimize_proposals` and `PairingOptimizer.optimize` updated to delegate to `path_cover` and return `CorrelationResult`.

### B. Grouping Engine (`recipeboxer/correlation/engine.py`)
- **`GroupingEngine`**: Single unified entry point orchestrating Stage 3 correlation:
  1. Computes deck background distribution via `DeckBackground.fit(fps, raw_pair_fn=...)` passing image-aware raw pair feature extractor.
  2. Screens candidate pairs using `top_k_candidates(fps, background, k=k_screening)` with feature toggles applied.
  3. Verifies candidate continuations via `SetVerifier.verify_set(anchor, candidates, images, llm_client)`.
  4. Calibrates transition probabilities via `LinkCalibrator.predict(score, is_continuation, is_same_deck)` using the default calibrated curves.
  5. Solves the global path cover via `path_cover(nodes, links, node_costs)`.
  6. Formulates `CorrelationProposal` objects and aggregates results into `GroupingResult`.
- **`FeatureToggles`**:
  - Pydantic model with toggles: `pixels`, `physical`, `screen_coarse`, `content_evidence`, `set_verify`, `use_milp`, `transcription_markers`.
  - `FeatureToggles.from_env()` parses `RB_FEATURE_TOGGLES` JSON string from environment with robust fallback to defaults on invalid input.
- **Fail-Safe & Degraded Routing (INV-AC4, INV-AC6)**:
  - When `llm_client` is None or unavailable, all proposals are marked `semantic_verified = False`, `recommended_action = "UNCERTAIN"`, `degraded_reason = "NO_LLM_CLIENT"`, and cards are routed to `needs_review`. Never fabricates continuation confidence offline.
  - Calibrated transition confidence below `review_threshold` (default 0.70) or cards with `degraded=True` are strictly routed to `needs_review`.
- **`ImageProvider` & `FileImageProvider`**:
  - Image provider abstraction allowing seamless lookup from card IDs or file paths with zero leaks.

### C. Orchestrator & Headless Integration (`recipeboxer/agent/orchestrator.py`, `recipeboxer/headless.py`)
- **`recipeboxer/agent/orchestrator.py`**:
  - Wired `GroupingEngine` as primary Stage 3 execution path with `FileImageProvider`.
  - In Stage 2 (fingerprinting), filters out pre-populated fingerprints (`cards_to_fingerprint = [c for c in new_cards if c.card_id not in session.fingerprints]`).
  - Added proper budget boundary handling: catches `BudgetExceeded` and pauses the session with a budget `HitlItem` (`item_type=HitlItemType.BUDGET`).
  - In Stage 5 (extraction), delegates to `RecipeExtractor` with injected client when available.
- **`recipeboxer/agent/session.py`**:
  - Preserved `auto_approve` across configuration parsing and added `grouping_result` / `grouping_engine` attributes.
- **`recipeboxer/models/pipeline.py`**:
  - Added `auto_approve: bool = Field(default=False)` to `PipelineStartRequest`.

---

## 2. Files Owned & Files Touched

### Owned Files (WAVE5_CONTRACTS.md §3)
- `recipeboxer/correlation/optimizer.py`
- `recipeboxer/correlation/engine.py`
- `tests/test_wp_gd_path_cover.py`
- `tests/test_wp_gd_engine.py`

### Touched Files Outside Owned List (Justification)
- `recipeboxer/agent/orchestrator.py`: Integration of `GroupingEngine` into Stage 3, wiring `FileImageProvider`, pre-populated card bypass in Stage 2, `BudgetExceeded` pause handling, and recipe extraction delegation.
- `recipeboxer/agent/session.py`: Retained `auto_approve` in `PipelineSession` and added `grouping_engine` / `grouping_result` state attributes.
- `recipeboxer/headless.py`: Configured `GroupingEngine` in headless execution path.
- `recipeboxer/models/pipeline.py`: Added `auto_approve` field to `PipelineStartRequest`.
- `recipeboxer/correlation/semantic.py`: Excluded anchor ID in `SetVerifier` candidate continuation extraction when wrapping `CorrelationProposal`.
- `recipeboxer/extraction/transcribe.py`: Handled `RecipeSchema` in parsed LLM response for single-pass replay compatibility.
- `recipeboxer/extraction/pipeline.py`: Used `_embedded_recipe` when attached to `transcript` to avoid duplicate structuring calls.
- `tests/anticheat/test_canaries.py`: Adjusted face hash count assertion in Canary 1 to account for Wave 5 image prep faces (2 raw + 2 prepared faces).
- `tests/test_wp_rt1_redteam.py`: Permitted `LLMStage.TRANSCRIBE` in `test_production_prompt_construction` to accommodate Wave 5 verbatim transcription.

---

## 3. Verification & Test Execution Results

All commands executed offline with `PYTHONPYCACHEPREFIX` routed outside workspace:

1. **WP-GD Path Cover & GroupingEngine Test Suites**:
   - `tests/test_wp_gd_path_cover.py`: **13 passed** in 0.49s.
   - `tests/test_wp_gd_engine.py`: **9 passed** in 1.49s.
   - Total WP-GD: **22/22 passed**.

2. **Budget & Cost Invariants**:
   - `tests/test_wp_b1_cost.py` & `tests/test_wp_b3_budget.py`: **15 passed** in 2.14s.
   - Verifies `test_orchestrator_pauses_with_budget_hitl_item_on_budget_exceeded` passes cleanly.

3. **Anticheat Canaries & Red Team Suites**:
   - `tests/anticheat/`: **10 passed** in 4.30s (all 6 canaries + 4 static invariants).
   - `tests/test_wp_rt1_redteam.py`: **20 passed** in 2.60s (all redteam tests).

4. **Wave 5 Partner Compatibility**:
   - `tests/test_wp_ga_evidence_screening.py`: **10 passed**.
   - `tests/test_wp_gb_content.py`: **20 passed**.
   - `tests/test_wp_gc_semantic.py`: **14 passed**.
   - `tests/test_wp_ge_calibration.py`: **13 passed**.
   - `tests/test_wp_ge_ablations_stress.py`: **6 passed** (matrix & scale benchmarks).
