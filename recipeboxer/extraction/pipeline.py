"""Multimodal recipe extraction pipeline (WP-XA, Plan §8.3).

Orchestrates the complete multi-pass extraction architecture:
1. prepare: image normalization, border crop, deskew, resolution capping (WP-X2)
2. transcribe: strict verbatim transcription with legibility & margin markers (WP-X4)
3. structure: Recipe Schema v2 assembly via structured output (WP-X5)
4. repair (optional): schema validation & retry loop (WP-XB, X8)
5. grounding (optional): fuzzy matching and local OCR verification (WP-XB, X6)
6. normalize (optional): domain-specific fraction/unit/ad normalization (WP-XC, X7)
7. self-consistency (optional): alternative read when flagged or low confidence (WP-XB, X9)

Emits an ExtractionResult containing the finalized Recipe v2, Transcript,
warnings, call counts, and execution passes.
Typed failures raise ExtractionUnavailable; BudgetExceeded propagates cleanly.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from recipeboxer.ai.client import LLMClient, LLMStage
from recipeboxer.config import Settings, get_settings
from recipeboxer.extraction.image_prep import PreparedFace, prepare_faces
from recipeboxer.extraction.structure import PROMPT_STRUCTURE, structure
from recipeboxer.extraction.transcribe import Transcript, transcribe
from recipeboxer.models.failures import (
    BudgetExceeded,
    ExtractionUnavailable,
    FailureReason,
    StageUnavailableError,
)
from recipeboxer.models.recipe import Provenance, Recipe, RecipeWarning
from recipeboxer.prompts import get_prompt

logger = logging.getLogger(__name__)


class ExtractionResult(BaseModel):
    """Container for the output of an extraction pipeline execution."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")

    recipe: Recipe = Field(..., description="Structured Recipe Schema v2")
    transcript: Transcript = Field(..., description="Verbatim multi-face transcript")
    warnings: List[RecipeWarning] = Field(
        default_factory=list, description="Grounding, confidence, and legibility warnings"
    )
    calls: int = Field(default=0, ge=0, description="Total LLM calls executed during the run")
    passes: List[str] = Field(
        default_factory=list, description="Sequential list of pipeline stages executed"
    )


class ExtractionPipeline:
    """Orchestrates multi-pass recipe card extraction."""

    def __init__(
        self,
        llm_client: LLMClient,
        settings: Optional[Settings] = None,
        *,
        ocr: Optional[Any] = None,
        normalizer: Optional[Callable[[Recipe], Recipe]] = None,
        grounding: Optional[Callable[..., Any]] = None,
        repairer: Optional[Callable[..., Any]] = None,
        consistency: Optional[Callable[..., Any]] = None,
    ) -> None:
        """Initializes the ExtractionPipeline.

        Optional components (ocr, normalizer, grounding, repairer, consistency)
        can be explicitly injected or resolved dynamically from sibling modules.
        """
        self.llm_client = llm_client
        self.settings = settings or get_settings()
        self.ocr = ocr
        self.normalizer = normalizer
        self.grounding = grounding
        self.repairer = repairer
        self.consistency = consistency

        # Attempt lazy resolution of optional components if not injected
        self._resolve_optional_components()

    def _resolve_optional_components(self) -> None:
        """Dynamically imports optional components if not explicitly injected."""
        if self.normalizer is None:
            try:
                from recipeboxer.extraction.normalize import normalize_recipe
                self.normalizer = normalize_recipe
            except ImportError:
                pass

        if self.grounding is None:
            try:
                from recipeboxer.extraction.grounding import ground
                self.grounding = ground
            except ImportError:
                pass

        if self.repairer is None:
            try:
                from recipeboxer.extraction.repair import validate_and_repair
                self.repairer = validate_and_repair
            except ImportError:
                pass

        if self.consistency is None:
            try:
                from recipeboxer.extraction.consistency import self_consistency
                self.consistency = self_consistency
            except ImportError:
                pass

        if self.ocr is None:
            try:
                from recipeboxer.vision.ocr import LocalOCR
                if LocalOCR.available():
                    self.ocr = LocalOCR()
            except ImportError:
                pass

    async def run(
        self,
        face_paths: Sequence[Union[Path, str, PreparedFace]],
        *,
        modality_hint: Optional[str] = None,
    ) -> ExtractionResult:
        """Executes the extraction pipeline across prepared card faces.

        Args:
            face_paths: Sequence of image file paths or pre-built PreparedFace objects.
            modality_hint: Optional modality classification for prompt routing.

        Returns:
            ExtractionResult with validated recipe, transcript, warnings, calls, and passes.

        Raises:
            BudgetExceeded: If spend or call limit is exceeded (never swallowed).
            ExtractionUnavailable: If any required stage cannot complete.
        """
        if not face_paths:
            raise ExtractionUnavailable(
                "No card face paths provided to extraction pipeline.",
                reason=FailureReason.INVALID_RESPONSE,
                retryable=False,
            )

        passes: List[str] = []
        skipped_passes: List[str] = []
        calls: int = 0
        all_warnings: List[RecipeWarning] = []
        models_used: List[str] = []
        prompt_versions: Dict[str, str] = {}

        # ---------------------------------------------------------------------
        # Pass 1: Image Preparation (WP-X2)
        # ---------------------------------------------------------------------
        if isinstance(face_paths[0], PreparedFace):
            prepared_faces = list(face_paths)  # type: ignore[arg-type]
        else:
            try:
                prepared_faces = prepare_faces(
                    paths=[Path(p) for p in face_paths],  # type: ignore[arg-type]
                    profile_params=self.settings.profile_params(),
                )
            except Exception as prep_err:
                raise ExtractionUnavailable(
                    f"Image preparation failed: {prep_err}",
                    reason=FailureReason.INPUT_UNREADABLE,
                    details={"error": str(prep_err)},
                ) from prep_err

        passes.append("prepare")

        # ---------------------------------------------------------------------
        # Pass 2: Verbatim Transcription (WP-X4)
        # ---------------------------------------------------------------------
        try:
            transcript = await transcribe(
                faces=prepared_faces,
                llm_client=self.llm_client,
                settings=self.settings,
                modality_hint=modality_hint,
            )
        except BudgetExceeded:
            raise
        except StageUnavailableError:
            raise
        except Exception as trans_err:
            raise ExtractionUnavailable(
                f"Transcription pass failed: {trans_err}",
                reason=FailureReason.MODEL_UNAVAILABLE,
                details={"error": str(trans_err)},
            ) from trans_err

        passes.append("transcribe")
        calls += 1
        if transcript.model not in models_used:
            models_used.append(transcript.model)
        prompt_versions[transcript.prompt_id] = transcript.prompt_version

        # ---------------------------------------------------------------------
        # Pass 3: Recipe Structuring (WP-X5)
        # ---------------------------------------------------------------------
        if getattr(transcript, "_embedded_recipe", None) is not None:
            recipe = transcript._embedded_recipe
            passes.append("structure")
        else:
            try:
                recipe = await structure(
                    transcript=transcript,
                    faces=prepared_faces,
                    llm_client=self.llm_client,
                    settings=self.settings,
                )
            except BudgetExceeded:
                raise
            except StageUnavailableError:
                raise
            except Exception as struct_err:
                raise ExtractionUnavailable(
                    f"Structuring pass failed: {struct_err}",
                    reason=FailureReason.MODEL_UNAVAILABLE,
                    details={"error": str(struct_err)},
                ) from struct_err

            passes.append("structure")
            calls += 1

        struct_model = self.settings.get_stage_model(LLMStage.STRUCTURE)
        if struct_model not in models_used:
            models_used.append(struct_model)
        try:
            struct_prompt_record = get_prompt(PROMPT_STRUCTURE)
            prompt_versions[PROMPT_STRUCTURE] = struct_prompt_record.version
        except KeyError:
            pass

        # ---------------------------------------------------------------------
        # Pass 4: Validation & Repair (WP-XB, X8)
        # ---------------------------------------------------------------------
        if self.repairer is not None:
            try:
                repair_res = self.repairer(recipe.model_dump(), self.llm_client, self.settings)
                if inspect.isawaitable(repair_res):
                    recipe = await repair_res
                else:
                    recipe = repair_res
                passes.append("repair")
            except BudgetExceeded:
                raise
            except StageUnavailableError:
                raise
            except Exception as repair_err:
                raise ExtractionUnavailable(
                    f"Schema validation and repair failed: {repair_err}",
                    reason=FailureReason.SCHEMA_VALIDATION,
                    details={"error": str(repair_err)},
                ) from repair_err

        # ---------------------------------------------------------------------
        # Pass 5: Grounding Verification (WP-XB, X6)
        # ---------------------------------------------------------------------
        grounding_enabled = self.settings.profile_params().get("grounding", True)
        if grounding_enabled and self.grounding is not None:
            try:
                ocr_lines = None
                if self.ocr is not None and hasattr(self.ocr, "read"):
                    try:
                        ocr_lines = [self.ocr.read(f.data) for f in prepared_faces]
                    except Exception as ocr_err:
                        logger.debug("OCR reading skipped during grounding: %s", ocr_err)

                ground_res = self.grounding(
                    recipe, transcript, self.llm_client, self.settings, ocr_lines=ocr_lines
                )
                if inspect.isawaitable(ground_res):
                    ground_res = await ground_res

                if isinstance(ground_res, tuple) and len(ground_res) == 2:
                    recipe, ground_warnings = ground_res
                    all_warnings.extend(ground_warnings)
                passes.append("grounding")
                calls += 1
            except BudgetExceeded:
                raise
            except StageUnavailableError:
                raise
            except Exception as ground_err:
                logger.warning("Optional grounding component raised: %s", ground_err)
                skipped_passes.append("grounding")
                all_warnings.append(
                    RecipeWarning(
                        code="VERIFICATION_SKIPPED",
                        message=f"Grounding verification skipped due to error: {type(ground_err).__name__}: {ground_err}",
                        field_path="grounding",
                    )
                )

        # ---------------------------------------------------------------------
        # Pass 6: Deterministic Normalization (WP-XC, X7)
        # ---------------------------------------------------------------------
        if self.normalizer is not None:
            try:
                norm_res = self.normalizer(recipe)
                if inspect.isawaitable(norm_res):
                    recipe = await norm_res
                else:
                    recipe = norm_res
                passes.append("normalize")
            except Exception as norm_err:
                logger.warning("Optional normalizer component raised: %s", norm_err)
                skipped_passes.append("normalize")
                all_warnings.append(
                    RecipeWarning(
                        code="NORMALIZATION_SKIPPED",
                        message=f"Normalization skipped due to error: {type(norm_err).__name__}: {norm_err}",
                        field_path="normalize",
                    )
                )

        # ---------------------------------------------------------------------
        # Pass 7: Self-Consistency Check (WP-XB, X9)
        # ---------------------------------------------------------------------
        consistency_mode = self.settings.profile_params().get("self_consistency", "off")
        needs_consistency = False
        if consistency_mode in ("flagged", "flagged_other_model"):
            needs_consistency = (
                recipe.needs_review()
                or recipe.flagged_count() > 0
                or bool(recipe.warnings)
                or bool(all_warnings)
                or (recipe.confidence is not None and recipe.confidence < 0.8)
            )

        if needs_consistency and self.consistency is not None:
            try:
                cons_res = self.consistency(
                    recipe, transcript, prepared_faces, self.llm_client, self.settings
                )
                if inspect.isawaitable(cons_res):
                    cons_res = await cons_res

                if isinstance(cons_res, tuple) and len(cons_res) == 2:
                    recipe, cons_warnings = cons_res
                    all_warnings.extend(cons_warnings)
                passes.append("consistency")
                calls += 1
            except BudgetExceeded:
                raise
            except StageUnavailableError:
                raise
            except Exception as cons_err:
                logger.warning("Optional consistency component raised: %s", cons_err)
                skipped_passes.append("consistency")
                all_warnings.append(
                    RecipeWarning(
                        code="VERIFICATION_SKIPPED",
                        message=f"Self-consistency check skipped due to error: {type(cons_err).__name__}: {cons_err}",
                        field_path="consistency",
                    )
                )

        # ---------------------------------------------------------------------
        # Finalize Warnings & Provenance
        # ---------------------------------------------------------------------
        # Combine existing recipe warnings and stage warnings
        all_warnings.extend(recipe.warnings)
        deduped_warnings: List[RecipeWarning] = []
        seen_warn_keys = set()
        for w in all_warnings:
            key = (w.code, w.message, w.field_path)
            if key not in seen_warn_keys:
                seen_warn_keys.add(key)
                deduped_warnings.append(w)
        recipe.warnings = deduped_warnings

        # Retrieve total USD cost if ledger is attached to LLM client
        total_cost: Optional[float] = None
        ledger = getattr(self.llm_client, "ledger", None)
        if ledger is not None:
            total_usd = getattr(ledger, "total_usd", None)
            if total_usd is not None:
                try:
                    total_cost = float(total_usd)
                except (ValueError, TypeError):
                    total_cost = None

        # Build provenance
        face_labels = [f.label for f in prepared_faces]
        recipe.provenance = Provenance(
            faces=face_labels,
            models=models_used,
            prompt_versions=prompt_versions,
            profile=self.settings.quality_profile,
            cost=total_cost,
            skipped_passes=skipped_passes,
        )

        return ExtractionResult(
            recipe=recipe,
            transcript=transcript,
            warnings=recipe.warnings,
            calls=calls,
            passes=passes,
        )


__all__ = [
    "ExtractionPipeline",
    "ExtractionResult",
]
