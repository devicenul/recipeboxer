# Wave 4 Contracts (authoritative for all wave-4 agents)

Branch `remediation/phase0`. Plan: `_agents/remediation/REMEDIATION_PLAN.md` §7, §8, §9, §9.1, §12.
Common rules: **identical to `WAVE3_CONTRACTS.md` §0** (read it): timeout-wrapped pytest, no state-mutating git,
never touch `setup.sh`, no caches in workspace, no Gemini keys (all offline), ScriptedLLMClient / explicit mocks only,
no filename/hash semantics, never assert accuracy offline, never read other agents' logs, WP note
`_agents/remediation/WP-<id>.md`, final reply ≤ 250 words. Run only targeted tests; at the end run
`tests/anticheat` too. If an existing test outside your owned files breaks because of your change, you may make the
minimal fix and must list it in your note under "Touched outside ownership".

Context: Gate A (honest baseline) is pending a Gemini key; the pre-uplift code is tagged `gate-a-candidate`.
Wave 4 builds Phase 3/4 foundations offline. Production LLM calls go through `recipeboxer.ai.client.LLMClient`
(see `WP-X1.md`, `WP-RT1.md` for the `llm_client` injection pattern in fingerprint/semantic/extractor).
Every LLM-backed failure raises a `StageUnavailableError` subclass (INV-AC4); never fabricate.

## 1. Shared interfaces

### 1.1 Cost engine (`recipeboxer/ai/cost.py`, WP-B1)
```python
class PriceTable:                      # loaded from config/pricing.toml (user-editable)
    @classmethod
    def load(cls, path: Path | None = None) -> "PriceTable"
    def cost_usd(self, model: str, input_tokens: int, output_tokens: int, thinking_tokens: int = 0) -> float | None  # None if model unpriced
class CostLedger(UsageLedger):          # implements recipeboxer.ai.client.UsageLedger
    def __init__(self, prices: PriceTable, guard: "BudgetGuard | None" = None)
    total_usd: float; calls: int; def snapshot(self) -> dict    # per-stage/model breakdown
class BudgetGuard:
    def __init__(self, cap_usd: float | None, max_calls: int | None)
    def check_before_call(self, ledger: CostLedger, est_usd: float = 0.0) -> None  # raises BudgetExceeded
class BudgetExceeded(StageUnavailableError): reason = FailureReason.BUDGET_EXCEEDED, retryable=False
def estimate_run_cost(n_cards: int, profile: str, prices: PriceTable, calibration: dict | None = None) -> dict  # {low, expected, high, assumptions}
```
`FailureReason.BUDGET_EXCEEDED` is added to `recipeboxer/models/failures.py` by WP-B1. When the cap is hit the
pipeline PAUSES with a HITL item of kind budget (resume with a raised cap or stop) — never silently degrades.

### 1.2 Profile parameters (`recipeboxer/config.py`, WP-B1)
`Settings.profile_params() -> dict` for the active `quality_profile` with keys:
`image_long_edge` (economy 1280 / balanced 1600 / best 2000), `thinking_budget_transcribe` (0 / adaptive=-1 / 2048),
`self_consistency` ("off" | "flagged" | "flagged_other_model"), `grounding` (bool). Consumers use `.get()` with defaults.

### 1.3 Fingerprint v2 (`recipeboxer/models/domain.py`, WP-G1)
`VisualFingerprint` gains (all Optional, defaults neutral): `visible_title`, `first_lines: list[str]`,
`last_lines: list[str]`, `printed_markers: list[str]` (e.g. "2", "cont'd", "over" exactly as printed),
`text_cut_off_end: bool|None`, `text_cut_off_start: bool|None`, `sections_detected: list[str]`
(e.g. "ingredients","directions","notes"), `script_descriptor` (free text: handwriting/typography),
`orientation_deg`, `looks_like_back: bool|None`, `has_ingredients: bool|None`, `has_directions: bool|None`,
`pixel: PixelFeatures|None`, `capture: CaptureMeta|None`, `embedding_ref: str|None`. Keep existing fields.

### 1.4 Pixel features (`recipeboxer/vision/pixel_features.py`, WP-G2)
```python
class PixelFeatures(BaseModel):   # defined in recipeboxer/models/features.py (WP-G1 creates it FIRST); G2 computes them
    paper_lab: tuple[float,float,float]; paper_lab_std: tuple[float,float,float]
    ruling_spacing_px: float|None; ruling_angle_deg: float|None; ruling_kind: str|None   # none|lined|grid|double
    ink_hues: list[float]; aspect_ratio: float; long_edge_px: int
    deskew_angle_deg: float; crop_box: tuple[int,int,int,int]|None
    stain_mask_ref: str|None        # key into an in-memory/session cache, not a file path
def compute_pixel_features(image: PIL.Image.Image) -> PixelFeatures
```

### 1.5 Capture metadata (`recipeboxer/ingestion/capture.py`, WP-G3)
```python
class CaptureMeta(BaseModel):    # defined in recipeboxer/models/features.py (WP-G1 creates it FIRST); G3 computes
    taken_at: datetime|None; subsec: int|None; device: str|None; source: str   # "exif"|"mtime"|"none"
    natural_sort_index: int
def read_capture_meta(path: Path, natural_sort_index: int) -> CaptureMeta
```

### 1.6 Physical pairing evidence (`recipeboxer/vision/physical_pairing.py`, WP-G12)
```python
def show_through_score(face_a: Image, face_b: Image) -> float | None   # mirrored ghost of B's ink in A (and vice versa), 0..1
def mirrored_wear_score(face_a: Image, face_b: Image) -> float | None  # dog-ears/tears/stains/holes mirrored, 0..1
def outline_match_score(face_a: Image, face_b: Image) -> float | None  # card size/cut/corner shape
```
Pure image functions; return None when not computable. Validated on cardforge-generated pairs whose parameters are known.

### 1.7 Embeddings (`recipeboxer/ai/embeddings.py`, WP-G4, feature-flagged off by default)
```python
class Embedder(Protocol): async def embed_images(self, images: list[bytes]) -> list[list[float]]; async def embed_texts(self, texts: list[str]) -> list[list[float]]
def create_embedder(settings) -> Embedder | None    # None if flag off or no key
```

### 1.8 Image preparation (`recipeboxer/extraction/image_prep.py`, WP-X2)
```python
class PreparedFace(BaseModel): face_index: int; n_faces: int; data: bytes; mime_type: str; variant: str  # "standard"|"enhanced_clahe"; long_edge_px: int; label: str  # "Face i of N" (+ " (contrast-enhanced copy)")
def prepare_faces(paths: list[Path], profile_params: dict, enhance: bool | None = None) -> list[PreparedFace]
```

### 1.9 Recipe schema v2 (`recipeboxer/models/recipe.py`, WP-X3X11) — plan §8.2, no backward compatibility
Class names: `Recipe` (keep `RecipeSchema = Recipe` alias for import stability), `IngredientLine`, `Step`,
`RecipeSection`, `TextSpan`, `IllegibleSpan`, `Legibility` enum CLEAR|UNCERTAIN|ILLEGIBLE, `RecipeWarning`,
`Provenance`, `TimeValue`, `YieldValue`. Keep it Gemini-schema-friendly (flat, enums, Optionals, no unions/recursion).
Helper methods: `all_ingredients()`, `all_steps()`, `needs_review() -> bool`, `flagged_count() -> int`,
`to_scoring_dict() -> dict` producing the evals structured shape `{title, yield, ingredients:[{raw,quantity,unit,item,section}], directions:[...], notes:[...]}`.

## 2. Ownership (wave 4)

| Agent | Owns |
|---|---|
| WP-G1 (pro) | `recipeboxer/models/features.py` (new; create it as your FIRST action with PixelFeatures + CaptureMeta exactly per §1.4/§1.5), `recipeboxer/vision/fingerprint.py`, `recipeboxer/models/domain.py`, `recipeboxer/vision/heuristics.py`, `recipeboxer/prompts/__init__.py` + `recipeboxer/prompts/fingerprint*.md` (new prompt registry: load by id, version hash), `tests/test_wp_g1_*.py` |
| WP-G2 (pro) | `recipeboxer/vision/pixel_features.py` (new), `recipeboxer/vision/preprocessing.py`, `tests/test_wp_g2_*.py` |
| WP-G3 (flash) | `recipeboxer/ingestion/capture.py` (new), `recipeboxer/ingestion/scanner.py`, `tests/test_wp_g3_*.py` |
| WP-G4 (flash) | `recipeboxer/ai/embeddings.py` (new), `tests/test_wp_g4_*.py` |
| WP-G12 (pro) | `recipeboxer/vision/physical_pairing.py` (new), `tests/test_wp_g12_*.py` |
| WP-X2 (flash) | `recipeboxer/extraction/image_prep.py` (new), `tests/test_wp_x2_*.py` |
| WP-X3X11 (pro) | `recipeboxer/models/recipe.py`, `recipeboxer/exporter/**`, `recipeboxer/extraction/extractor.py` (v2 mapping only), `recipeboxer/agent/skills/**` (v2 adaptation only), `recipeboxer/storage/**` (if it persists recipes), `evals/scoring/**` (v2→scoring dict only), `frontend/src/types/recipe.ts` + minimal frontend changes to compile/tests pass, `tests/test_wp_x3_*.py`, `tests/test_wp_x11_*.py`, and existing exporter/recipe tests |
| WP-B1B3 (pro) | `recipeboxer/ai/cost.py` (new), `recipeboxer/ai/client.py` (guard hook only), `recipeboxer/models/failures.py`, `recipeboxer/config.py`, `config/pricing.toml`, `recipeboxer/headless.py`, `recipeboxer/cli.py`, `evals/harness/run.py` (`--budget-usd`, `--max-calls`), `recipeboxer/agent/orchestrator.py` (budget pause only), `tests/test_wp_b1_*.py`, `tests/test_wp_b3_*.py` |
| WP-B2 (flash) | `recipeboxer/api/settings_routes.py` (new), `recipeboxer/api/app.py` (router registration only), `recipeboxer/models/session.py`, `recipeboxer/storage/settings_store.py` (new), `tests/test_wp_b2_*.py` |
| WP-O1 (flash, spike) | `recipeboxer/vision/ocr.py` (new, optional import), `_agents/remediation/WP-O1.md`; deps installed only into the venv, NOT added to requirements |

Orchestrator only: `evals/triage.yaml`, `requirements*.txt`, `pyproject.toml` (request additions in your note).
