# Work Package Report: WP-GA (Top-k Screening & Collection-Relative Weighting)

## Summary
Implemented G5 (top-k screening) and G10 (collection-relative weighting & background distribution) for RecipeBoxer Phase 3 remediation conforming to `WAVE5_CONTRACTS.md` §1.1, §1.2, §3 and `REMEDIATION_PLAN.md` §9 and §9.1:
1. **Correlation Models (`recipeboxer/models/correlation.py`)**:
   - Added `FeatureEvidence`: per-feature raw measurement, collection-relative `log_lr`, weight, and note.
   - Added `PairEvidence`: directed link hypothesis ($A \to B$), feature list, `total_log_lr`, calibrated `p_link`, and `content_evidence_used` flag.
   - Added `SetVerification`: anchor card, ordered continuations, rejected candidates, verbatim cited quotes, and prompt/model provenance.
2. **Collection-Relative Background Model (`recipeboxer/correlation/background.py`)**:
   - `DeckBackground.fit`: Models the background distribution of pairwise features across random pairs of THIS deck (sampled deterministically up to $M=500$ pairs).
   - Documented parametric same-recipe priors: default distributions for $P(\text{raw} \mid \text{same})$ across all feature types (folded normal for distances, exponential/beta for similarities, discrete for capture adjacency), ready for refitting by `evals/calibration_fit.py`.
   - Likelihood ratio: evaluates $\log\text{LR} = \ln P(\text{raw} \mid \text{same}) - \ln P(\text{raw} \mid \text{background})$ using Laplace-smoothed and kernel-smoothed binned distributions.
   - Homogeneous collection constraint (§9.1): Features with near-zero variance across the deck yield $|\log\text{LR}| \sim 0$ (mutual information is zero). Card-level substrate rarity ensures rare stock pairs in the deck yield high positive $\log\text{LR}$ ($> 1.5$), while common stock matches yield $\sim 0$.
3. **Directed Pairwise Evidence (`recipeboxer/correlation/evidence.py`)**:
   - `compute_raw_features`: Computes raw features:
     - pixel: paper LAB Euclidean distance $\Delta E$, ruling kind & spacing match, ink hue circular distance in $[0^\circ, 180^\circ]$, aspect ratio difference.
     - capture: signed capture time gap ($t_b - t_a$) in seconds, natural-sort index adjacency ($idx_b - idx_a$).
     - physical: show-through, mirrored wear, and outline silhouette IoU via `recipeboxer.vision.physical_pairing` when `ImageProvider` is provided (outline weighted at 0.2 as a weak signal per specification).
     - header_score: sequential continuation and header progression heuristics.
     - content features: lazy import from `recipeboxer.correlation.content.content_features` with graceful fallback when absent.
   - `pair_evidence`: Evaluates collection-relative $\log\text{LR}$ for all features, computes `total_log_lr = sum(weight * log_lr)`, and tracks `content_evidence_used`.
4. **Top-k Candidate Screening (`recipeboxer/correlation/screening.py`)**:
   - `top_k_candidates`: Candidate generation from the union of:
     - Content-token retrieval (TF-IDF over `content_tokens` or dense embeddings if embedder provided).
     - Capture-order neighbours ($\pm 2$).
     - Fast heuristic progression.
   - $O(N \cdot k)$ evidence evaluation: Candidate pools are screened using cheap vector ops and integer rank operations, evaluating full `pair_evidence` ONLY on the filtered union (at most $\approx 2k + 4$ candidates per card).
   - Fully deterministic and card_id permutation-invariant.

## Files Changed / Owned
- `recipeboxer/models/correlation.py` (Owned): Added `FeatureEvidence`, `PairEvidence`, and `SetVerification` (§1.1).
- `recipeboxer/correlation/background.py` (New, Owned): `DeckBackground` background distribution, parametric priors, and collection-relative likelihood ratios.
- `recipeboxer/correlation/evidence.py` (New, Owned): `ImageProvider` protocol, `compute_raw_features`, and `pair_evidence`.
- `recipeboxer/correlation/screening.py` (New, Owned): `top_k_candidates` with TF-IDF / embedding retrieval and $O(N \cdot k)$ complexity.
- `tests/test_wp_ga_evidence_screening.py` (New, Owned): 10 offline unit and integration tests.
- `_agents/remediation/WP-GA.md` (New, Owned): This work package report.

### Touched Outside Ownership
- `recipeboxer/models/__init__.py`: Re-exported `FeatureEvidence`, `PairEvidence`, `SetVerification` in `__all__` for cross-agent imports.

## Commands Run & Results
```bash
PROJECT_CACHE_DIR="$HOME/.cache/projects/$(basename "$PWD")-$(pwd -P | sha1sum | cut -c1-8)"
py="$PROJECT_CACHE_DIR/venv/bin/python"

# 1. Targeted WP-GA unit & integration tests
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_ga_evidence_screening.py -v -p no:randomly
# Result: 10 passed in 1.37s (100%)

# 2. Anti-cheat static invariants
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/anticheat/test_static_invariants.py -v -p no:randomly
# Result: 4 passed in 2.23s (100% clean)

# 3. Cross-agent compatibility: WP-GB content features
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_gb_content.py -v -p no:randomly
# Result: 20 passed in 0.21s (100%)

# 4. Cross-agent compatibility: WP-GC semantic verification
PYTHONPYCACHEPREFIX="$PROJECT_CACHE_DIR/pycache" timeout 600 $py -m pytest -o cache_dir="$PROJECT_CACHE_DIR/pytest" tests/test_wp_gc_semantic.py -v -p no:randomly
# Result: 14 passed in 0.34s (100%)
```

## Contract Deviations
None. All models, classes, and function signatures strictly match `WAVE5_CONTRACTS.md` §1.1 and §1.2.

## Requests for Other WPs
- **WP-GD (`recipeboxer/correlation/engine.py` & `optimizer.py`)**:
  - `DeckBackground.fit(fps, images=...)` and `top_k_candidates(fps, background, k=5, images=images, embedder=embedder)` are ready for the grouping engine.
  - Can consume `PairEvidence` directly for candidate links into `SetVerifier` and `path_cover`.
- **WP-GE (`recipeboxer/correlation/calibration.py` & `evals/calibration_fit.py`)**:
  - `DeckBackground` parametric priors are exposed in `DEFAULT_PRIORS` and can be refitted via `DeckBackground.fit(..., priors=...)`.

## Risks & Mitigations
- **Homogeneous Deck Sensitivity**: In collections where all cards share stock, stationery match could dominate without weighting.
  *Mitigation*: Near-zero background variance automatically dampens stock match $\log\text{LR}$ to $\sim 0$, allowing content-token retrieval, sentence/step continuity, and header cues to dominate top-k ranking.
- **Screening Latency**: Full physical pairing (FFT/convolution) across all pairs would scale $O(N^2)$.
  *Mitigation*: $O(N \cdot k)$ screening filters candidates via fast TF-IDF and capture-order index arithmetic first, evaluating full pairwise evidence only on the pruned candidate union ($\le 2k + 4$ per card).
