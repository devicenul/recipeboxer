# Wave 5 Contracts (authoritative for all wave-5 agents)

Branch `remediation/phase0` (wave 4 committed at `f95e562`). Plan: `_agents/remediation/REMEDIATION_PLAN.md`
§8 (extraction), §9 + §9.1 (grouping, homogeneous collections — critical), §7 (budget/UI), §8.2 (F1 legibility UX).
Common rules: **identical to `WAVE3_CONTRACTS.md` §0** (timeout-wrapped pytest, no state-mutating git, never touch
`setup.sh`, caches outside workspace, no Gemini keys — everything offline with `ScriptedLLMClient`/explicit mocks,
no filename/hash semantics, never assert accuracy offline, never read other agents' logs, WP note
`_agents/remediation/WP-<id>.md`, final reply ≤ 250 words). Read the wave-4 notes you depend on
(`WP-G1/G2/G3/G4/G12/X2/X3X11/B1B3/B2/O1.md`) and `WAVE4_CONTRACTS.md` for existing interfaces.
Minimal fixes to tests outside your ownership that your change breaks are allowed; list them under
"Touched outside ownership". Orchestrator-only files: `evals/triage.yaml`, `requirements*.txt`, `pyproject.toml`.

**Frontend toolchain:** Node/npm from apt in the VM. Use
`npm ci --cache "$PROJECT_CACHE_DIR/npm"` (once, in `frontend/`), `timeout 900 npx vitest run`,
`timeout 600 npx tsc --noEmit -p .`. Never commit `node_modules`.

**Model IDs / LLM rules:** all calls via `LLMClient` (`recipeboxer/ai/client.py`), stage enum, model from
`settings.get_stage_model(stage)`, structured output with Pydantic response models (Gemini-schema friendly), prompts
in `recipeboxer/prompts/*.md` loaded through the registry (id + version hash recorded in provenance), images as
neutral-labelled parts (`Card A`, `Face 1 of 3`) — never filenames. Failures → `StageUnavailableError` subclasses.
Budget: calls are guarded by `BudgetGuard` inside `GenAIClient`; `BudgetExceeded` must propagate (never swallowed).

## 1. Grouping interfaces (Phase 3)

### 1.1 Evidence model (`recipeboxer/models/correlation.py` additions, owned by WP-GA)
```python
class FeatureEvidence(BaseModel):
    name: str                 # e.g. "paper_lab", "ruling", "ink_hue", "capture_gap", "sort_adjacency",
                              # "show_through", "mirrored_wear", "outline", "header_score",
                              # "content_xref", "sentence_continuity", "step_continuity", "title_position",
                              # "embedding_sim", "ocr_cue"
    raw: float | None         # raw similarity / measurement (None = not computable)
    log_lr: float | None      # log likelihood ratio vs this deck's background (G10); None if not computable
    weight: float = 1.0
    note: str | None = None
class PairEvidence(BaseModel):
    a: str; b: str            # ordered: a precedes b (directed successor hypothesis)
    features: list[FeatureEvidence]
    total_log_lr: float       # sum(weight*log_lr) over computable features
    p_link: float | None      # calibrated probability (G8) or None before calibration
    content_evidence_used: bool
class SetVerification(BaseModel):   # G6 output
    anchor: str
    continuations: list[str]   # ordered successors chosen from candidates (may be empty)
    rejected: list[str]
    evidence_quotes: list[str] # verbatim visible text cited by the model
    confidence: float | None
    model: str; prompt_id: str; prompt_version: str
```

### 1.2 Module APIs
```python
# recipeboxer/correlation/content.py  (WP-GB, G11) — pure, deterministic, no LLM
def content_features(fp_a: VisualFingerprint, fp_b: VisualFingerprint) -> list[FeatureEvidence]   # raw only, log_lr None
def completeness_prior(fp: VisualFingerprint) -> dict   # {"expects_continuation": p, "is_complete": p, "is_start": p}
def content_tokens(fp: VisualFingerprint) -> list[str]  # for retrieval / top-k
# recipeboxer/correlation/background.py  (WP-GA, G10)
class DeckBackground:
    @classmethod
    def fit(cls, fps: list[VisualFingerprint], raw_pair_fn) -> "DeckBackground"   # background distributions over random pairs of THIS deck
    def log_lr(self, name: str, raw: float) -> float | None
# recipeboxer/correlation/evidence.py  (WP-GA)
def pair_evidence(a, b, background: DeckBackground, *, images: "ImageProvider | None" = None) -> PairEvidence
# recipeboxer/correlation/screening.py  (WP-GA, G5)
def top_k_candidates(fps: list[VisualFingerprint], background: DeckBackground, k: int = 5, *, embedder=None) -> dict[str, list[PairEvidence]]
    # O(N*k) downstream; candidate generation must include content-based retrieval (tokens/embeddings),
    # capture-order neighbours, and physical evidence — not substrate only.
# recipeboxer/correlation/semantic.py  (WP-GC, G6)
class SetVerifier:
    def __init__(self, llm_client: LLMClient, settings, cache=None)
    async def verify(self, anchor: VisualFingerprint, candidates: list[VisualFingerprint], images: "ImageProvider") -> SetVerification
# recipeboxer/correlation/calibration.py  (WP-GE, G8)
class LinkCalibrator:   # isotonic on total_log_lr (+ set-verify outcome); identity-ish default when unfitted
    def fit(self, evidences: list[PairEvidence], labels: list[bool]) -> None
    def predict(self, ev: PairEvidence, set_verified: bool | None) -> float
    def save(self, path) / @classmethod load(cls, path)
DEFAULT_CALIBRATION_PATH = recipeboxer/correlation/calibration_default.json  # fitted ONLY on T0 + freshly generated cardforge decks (never T1–T4)
# recipeboxer/correlation/optimizer.py  (WP-GD, G7)
def path_cover(nodes: list[str], links: dict[tuple[str,str], float], node_costs: dict[str, dict]) -> list[list[str]]
    # min-cost path cover: Hungarian on -log p with "no successor"/"no predecessor" costs; constraints (≤1 title-bearing
    # start per chain; completeness prior as node costs); optional tiny ILP (scipy.optimize.milp) for ambiguous components
# recipeboxer/correlation/engine.py  (WP-GD) — the single entry used by the orchestrator
class GroupingEngine:
    async def group(self, fps: list[VisualFingerprint], images: "ImageProvider", *, llm_client, settings, embedder=None,
                    toggles: "FeatureToggles | None" = None) -> "GroupingResult"
class FeatureToggles(BaseModel): pixels: bool = True; capture: bool = True; physical: bool = True; content: bool = True;
    embeddings: bool = False; ocr: bool = False; set_verify: bool = True
class GroupingResult(BaseModel): chains: list[list[str]]; proposals: list[CorrelationProposal]; evidence: list[PairEvidence];
    verifications: list[SetVerification]; needs_review: list[str]; stats: dict
class ImageProvider(Protocol): def get(self, card_id: str) -> bytes   # (define in engine.py; implemented by orchestrator)
```
HITL routing: any link with p < `settings.grouping_review_threshold` (default 0.7), any chain with conflicting
evidence, or any degraded/unverified input → `needs_review` with cited evidence (quotes + top features).
`FeatureToggles` are also read from env `RB_FEATURE_TOGGLES` (JSON) by the engine so the eval harness can run ablations
without code changes.

## 2. Extraction interfaces (Phase 4)

```python
# recipeboxer/extraction/pipeline.py (WP-XA)
class ExtractionPipeline:
    def __init__(self, llm_client: LLMClient, settings, *, ocr=None, normalizer=None, grounding=None, repairer=None, consistency=None)
    async def run(self, face_paths: list[Path], *, modality_hint: str | None = None) -> "ExtractionResult"
class ExtractionResult(BaseModel): recipe: Recipe; transcript: "Transcript"; warnings: list[RecipeWarning]; calls: int; passes: list[str]
# recipeboxer/extraction/transcribe.py (WP-XA, X4)
class FaceTranscript(BaseModel): face_index: int; text: str; illegible_spans: list[IllegibleSpan]; margin_notes: list[str]; crossed_out: list[str]; confidence: float | None
class Transcript(BaseModel): faces: list[FaceTranscript]; model: str; prompt_id: str; prompt_version: str
async def transcribe(faces: list[PreparedFace], llm_client, settings, modality_hint=None) -> Transcript
# recipeboxer/extraction/structure.py (WP-XA, X5)
async def structure(transcript: Transcript, faces: list[PreparedFace], llm_client, settings) -> Recipe
# recipeboxer/extraction/grounding.py (WP-XB, X6)
async def ground(recipe: Recipe, transcript: Transcript, llm_client, settings, ocr_lines=None) -> tuple[Recipe, list[RecipeWarning]]
# recipeboxer/extraction/repair.py (WP-XB, X8)
async def validate_and_repair(raw: dict | str, llm_client, settings, max_rounds: int = 2) -> Recipe   # raises ExtractionUnavailable(SCHEMA_VALIDATION) after rounds
# recipeboxer/extraction/consistency.py (WP-XB, X9)
async def self_consistency(recipe: Recipe, transcript: Transcript, faces, llm_client, settings) -> tuple[Recipe, list[RecipeWarning]]  # MODEL_DISAGREEMENT warnings with both readings
# recipeboxer/extraction/normalize.py (WP-XC, X7)
def normalize_recipe(recipe: Recipe) -> Recipe   # writes ONLY normalized_* fields; wraps agent/skills parsers
```
Warning codes (RecipeWarning.code): `GROUNDING_UNSUPPORTED`, `LOW_CONFIDENCE`, `MODEL_DISAGREEMENT`, `ILLEGIBLE_CONTENT`,
`REPAIRED_OUTPUT`, `INFERRED_TITLE`, `CONTINUITY_GAP`.
`RecipeExtractor` (extractor.py) keeps its public API but delegates to `ExtractionPipeline`.

## 3. Ownership (wave 5)

| Agent | Owns |
|---|---|
| WP-GA (pro) — G5, G10 | `recipeboxer/correlation/background.py`, `evidence.py`, `screening.py` (new), `recipeboxer/models/correlation.py` (§1.1 additions — do these FIRST), `tests/test_wp_ga_*.py` |
| WP-GB (pro) — G11 | `recipeboxer/correlation/content.py` (new), `tests/test_wp_gb_*.py` |
| WP-GC (pro) — G6 | `recipeboxer/correlation/semantic.py`, `recipeboxer/prompts/set_verify*.md`, `tests/test_wp_gc_*.py` |
| WP-GD (pro) — G7 + engine + integration | `recipeboxer/correlation/optimizer.py`, `recipeboxer/correlation/engine.py` (new), `recipeboxer/correlation/__init__.py`, `recipeboxer/agent/orchestrator.py`, `recipeboxer/agent/session.py`, `recipeboxer/headless.py`, `tests/test_wp_gd_*.py` |
| WP-GE (flash) — G8, G9, G13 | `recipeboxer/correlation/calibration.py`, `calibration_default.json`, `evals/ablations.py` (new; runs harness with `RB_FEATURE_TOGGLES` variants, reports homogeneous vs mixed), `evals/stress.py` (new; 300–500-card synthetic homogeneous deck via cardforge, offline cost/latency/call-count curves with ScriptedLLMClient), `evals/calibration_fit.py` (fits on T0 + fresh cardforge decks only), `tests/test_wp_ge_*.py` |
| WP-XA (pro) — X4, X5, X10 | `recipeboxer/extraction/pipeline.py`, `transcribe.py`, `structure.py` (new), `recipeboxer/extraction/extractor.py`, `recipeboxer/extraction/__init__.py`, `recipeboxer/prompts/transcribe*.md`, `structure*.md`, `recipeboxer/prompts/__init__.py` (registry extensions only), `tests/test_wp_xa_*.py` |
| WP-XB (pro) — X6, X8, X9 | `recipeboxer/extraction/grounding.py`, `repair.py`, `consistency.py` (new), `recipeboxer/prompts/grounding*.md`, `repair*.md`, `consistency*.md`, `tests/test_wp_xb_*.py` |
| WP-XC (flash) — X7, X12 + SSE spend | `recipeboxer/extraction/normalize.py` (new), `recipeboxer/agent/skills/**`, `recipeboxer/api/routes.py`, `tests/test_wp_xc_*.py` |
| WP-F1 (pro) — legibility UX + HITL evidence | `frontend/src/types/recipe.ts`, `frontend/src/types/hitl.ts`, `frontend/src/components/recipe/**`, `frontend/src/components/hitl/**`, `frontend/src/components/viewer/**`, `frontend/src/pages/RecipeReviewPage.tsx`, `frontend/src/pages/HitlReviewPage.tsx`, their tests |
| WP-F2 (flash) — settings UI, onboarding, spend | `frontend/src/pages/SettingsPage.tsx` (new), `frontend/src/components/settings/**` (new), `frontend/src/components/layout/**`, `frontend/src/App.tsx`, `frontend/src/context/**`, `frontend/src/hooks/**`, `frontend/src/utils/**`, `frontend/src/types/pipeline.ts`, their tests; `frontend/package.json` + lockfile only if a dependency is truly needed (avoid) |

Integration order: GA/GB/GC/GE/XB/XC code against these contracts in parallel; GD and XA integrate. If a dependency
module is missing when you need to test integration, write local test doubles, finish, and then re-check near the end.
