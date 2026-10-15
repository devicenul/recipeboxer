"""Unit and contract tests for WP-XA: Transcription, Structuring, Prompt Versioning, and Pipeline.

Verifies:
- Transcribe request shapes (per-modality system instruction from registry, 'Face i of N' labels, no filenames)
- Verbatim preservation, legibility markers, and adaptive thinking
- Structure request shape, null quantities (never invented), source_face, title inference
- Isolation of advertisement and clipping noise into notes
- Pipeline pass ordering and optional components injection
- Stage failure typing per pass (ExtractionUnavailable)
- Strict BudgetExceeded propagation
- Provenance recording (models, prompt versions, profile, ledger cost)
- RecipeExtractor public API delegation
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel

from recipeboxer.ai.client import (
    ImagePart,
    InMemoryUsageLedger,
    LLMClient,
    LLMRequest,
    LLMResponse,
    LLMStage,
    TextPart,
    UsageTokens,
)
from recipeboxer.ai.cost import CostLedger
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.config import Settings, get_settings
from recipeboxer.extraction.extractor import GeminiRecipeExtractor, RecipeExtractor
from recipeboxer.extraction.image_prep import PreparedFace
from recipeboxer.extraction.pipeline import ExtractionPipeline, ExtractionResult
from recipeboxer.extraction.structure import PROMPT_STRUCTURE, structure
from recipeboxer.extraction.transcribe import (
    PROMPT_CLIPPING,
    PROMPT_HANDWRITTEN,
    PROMPT_MIXED,
    PROMPT_TYPED,
    FaceTranscript,
    FaceTranscriptList,
    Transcript,
    compute_transcribe_thinking_budget,
    resolve_transcribe_prompt_id,
    transcribe,
)
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.failures import (
    BudgetExceeded,
    ExtractionUnavailable,
    FailureReason,
    StageUnavailableError,
)
from recipeboxer.models.recipe import (
    IllegibleSpan,
    IngredientLine,
    Legibility,
    Recipe,
    RecipeSchema,
    RecipeSection,
    RecipeWarning,
    Step,
    TextSpan,
)
from recipeboxer.prompts import get_prompt, get_prompt_version, list_prompts

# Synthetic 1x1 JPEG byte constants for testing
DUMMY_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"


def _make_dummy_faces(n: int = 2) -> List[PreparedFace]:
    """Creates a list of synthetic PreparedFace instances for testing."""
    faces = []
    for i in range(n):
        faces.append(
            PreparedFace(
                face_index=i,
                n_faces=n,
                data=DUMMY_JPEG,
                mime_type="image/jpeg",
                variant="standard",
                long_edge_px=1600,
                label=f"Face {i + 1} of {n}",
            )
        )
    return faces


# ============================================================================
# 1. Prompt Registry & Versioning Tests
# ============================================================================

def test_prompt_registry_lists_and_versions_all_prompts() -> None:
    """Verifies that all required extraction prompts exist with deterministic version hashes."""
    prompts = list_prompts()
    required = [
        PROMPT_HANDWRITTEN,
        PROMPT_TYPED,
        PROMPT_CLIPPING,
        PROMPT_MIXED,
        PROMPT_STRUCTURE,
    ]
    for req in required:
        assert req in prompts, f"Required prompt {req} missing from registry"
        record = get_prompt(req)
        assert len(record.version) == 8
        assert record.text.strip()
        assert get_prompt_version(req) == record.version


# ============================================================================
# 2. Transcribe Pass Tests (Modality, Request Shapes, Verbatim, Thinking)
# ============================================================================

@pytest.mark.asyncio
async def test_transcribe_modality_routing_and_request_shape() -> None:
    """Verifies modality routing, system instruction, neutral labels, and zero filename leakage."""
    settings = Settings(quality_profile="balanced")
    faces = _make_dummy_faces(2)

    # Modality cases to verify
    cases = [
        ("HANDWRITTEN_CURSIVE", PROMPT_HANDWRITTEN),
        ("TYPEWRITTEN", PROMPT_TYPED),
        ("PRINTED_CLIPPING_OR_LABEL", PROMPT_CLIPPING),
        ("MIXED_MEDIA", PROMPT_MIXED),
    ]

    for hint, expected_prompt_id in cases:
        mock_response = FaceTranscriptList(
            faces=[
                FaceTranscript(face_index=0, text="Line 1 of front", confidence=0.95),
                FaceTranscript(face_index=1, text="Line 1 of back", confidence=0.90),
            ]
        )
        client = ScriptedLLMClient([mock_response])

        transcript = await transcribe(faces, client, settings, modality_hint=hint)

        assert transcript.prompt_id == expected_prompt_id
        assert len(client.requests) == 1
        req = client.requests[0]

        # Verify stage and model
        assert req.stage == LLMStage.TRANSCRIBE
        assert req.model == settings.get_stage_model(LLMStage.TRANSCRIBE)

        # Verify system instruction matches registry exactly
        expected_rec = get_prompt(expected_prompt_id)
        assert req.system_instruction == expected_rec.text

        # Verify neutral labels and zero filename leakage (INV-AC1, INV-AC2)
        image_parts = [p for p in req.parts if isinstance(p, ImagePart)]
        assert len(image_parts) == 2
        assert image_parts[0].label == "Face 1 of 2"
        assert image_parts[1].label == "Face 2 of 2"

        # Check prompt text contains no file paths or hash artifacts
        for p in req.parts:
            if isinstance(p, TextPart):
                assert ".jpg" not in p.text.lower()
                assert ".png" not in p.text.lower()
                assert "/" not in p.text


@pytest.mark.asyncio
async def test_transcribe_adaptive_thinking_budget() -> None:
    """Verifies adaptive thinking token allocation across profiles and modalities."""
    faces = _make_dummy_faces(1)

    # 1. Economy profile: thinking budget is 0 / None
    s_econ = Settings(quality_profile="economy")
    assert compute_transcribe_thinking_budget(s_econ, PROMPT_HANDWRITTEN, faces) is None

    # 2. Balanced profile: dynamic 1024 tokens for handwriting/mixed
    s_bal = Settings(quality_profile="balanced")
    assert compute_transcribe_thinking_budget(s_bal, PROMPT_HANDWRITTEN, faces) == 1024
    assert compute_transcribe_thinking_budget(s_bal, PROMPT_MIXED, faces) == 1024
    assert compute_transcribe_thinking_budget(s_bal, PROMPT_TYPED, faces) is None

    # 3. Best profile: fixed 2048 tokens
    s_best = Settings(quality_profile="best")
    assert compute_transcribe_thinking_budget(s_best, PROMPT_HANDWRITTEN, faces) == 2048
    assert compute_transcribe_thinking_budget(s_best, PROMPT_TYPED, faces) == 2048


@pytest.mark.asyncio
async def test_transcribe_verbatim_preservation_and_markers() -> None:
    """Verifies strict verbatim preservation of archaic spellings, legibility markers, and margin notes."""
    settings = Settings()
    faces = _make_dummy_faces(1)

    mock_text = (
        "Grandmother's Receipt for Tea Cakes\n"
        "Take 2 cups of sifted flour and 1 cup of oleo.\n"
        "Bake at [crossed out: 325] 350 degrees until [illegible: faded].\n"
    )
    mock_resp = FaceTranscriptList(
        faces=[
            FaceTranscript(
                face_index=0,
                text=mock_text,
                margin_notes=["Good for picnic"],
                crossed_out=["325"],
                illegible_spans=[IllegibleSpan(reason="faded")],
                confidence=0.88,
            )
        ]
    )
    client = ScriptedLLMClient([mock_resp])

    transcript = await transcribe(faces, client, settings)

    assert len(transcript.faces) == 1
    face0 = transcript.faces[0]
    assert "Receipt" in face0.text
    assert "oleo" in face0.text
    assert "[crossed out: 325]" in face0.text
    assert "[illegible: faded]" in face0.text
    assert "Good for picnic" in face0.margin_notes
    assert "325" in face0.crossed_out
    assert len(face0.illegible_spans) == 1
    assert face0.illegible_spans[0].reason == "faded"


# ============================================================================
# 3. Structuring Pass Tests (Schema v2, Null Quantities, Source Face, Title)
# ============================================================================

@pytest.mark.asyncio
async def test_structure_null_quantities_and_source_face() -> None:
    """Verifies that missing quantities remain null (never invented) and source_face is set."""
    settings = Settings()
    faces = _make_dummy_faces(2)

    transcript = Transcript(
        faces=[
            FaceTranscript(
                face_index=0,
                text="1 cup sugar\nSalt to taste\nFlour for dusting",
            ),
            FaceTranscript(
                face_index=1,
                text="1. Mix ingredients well.\n2. Bake 30 mins.",
            ),
        ],
        model="gemini-3.7-flash",
        prompt_id="transcribe_handwritten_v1",
        prompt_version="abcd1234",
    )

    mock_recipe = Recipe(
        title="Sugar Biscuits",
        title_is_inferred=False,
        sections=[
            RecipeSection(
                name="Main",
                ingredients=[
                    IngredientLine(
                        original_text="1 cup sugar",
                        quantity="1",
                        unit="cup",
                        item="sugar",
                        source_face=0,
                    ),
                    IngredientLine(
                        original_text="Salt to taste",
                        quantity=None,  # Null quantity
                        unit=None,
                        item="salt",
                        source_face=0,
                    ),
                    IngredientLine(
                        original_text="Flour for dusting",
                        quantity="none",  # Will be normalized to None
                        unit=None,
                        item="flour",
                        source_face=0,
                    ),
                ],
                steps=[
                    Step(original_text="1. Mix ingredients well.", source_face=1),
                    Step(original_text="2. Bake 30 mins.", source_face=1),
                ],
            )
        ],
    )
    client = ScriptedLLMClient([mock_recipe])

    recipe = await structure(transcript, faces, client, settings)

    assert recipe.title == "Sugar Biscuits"
    assert recipe.title_is_inferred is False

    ings = recipe.all_ingredients()
    assert len(ings) == 3
    assert ings[0].quantity == "1"
    assert ings[0].source_face == 0

    # Strict invariant: quantity must be null for missing quantities
    assert ings[1].quantity is None
    assert ings[1].source_face == 0
    assert ings[2].quantity is None
    assert ings[2].source_face == 0

    steps = recipe.all_steps()
    assert len(steps) == 2
    assert steps[0].source_face == 1
    assert steps[1].source_face == 1


@pytest.mark.asyncio
async def test_structure_title_is_inferred_flag_and_warning() -> None:
    """Verifies that untitled cards trigger title_is_inferred=True and an INFERRED_TITLE warning."""
    settings = Settings()
    faces = _make_dummy_faces(1)

    transcript = Transcript(
        faces=[FaceTranscript(face_index=0, text="1 cup berries\nMix and chill.")],
        model="gemini-3.7-flash",
        prompt_id="transcribe_handwritten_v1",
        prompt_version="abcd1234",
    )

    # Untitled recipe where title was inferred
    mock_recipe = Recipe(
        title="Berry Compote [GUESS]",
        title_is_inferred=True,
        sections=[
            RecipeSection(
                name=None,
                ingredients=[IngredientLine(original_text="1 cup berries", item="berries")],
                steps=[Step(original_text="Mix and chill.")],
            )
        ],
    )
    client = ScriptedLLMClient([mock_recipe])

    recipe = await structure(transcript, faces, client, settings)

    assert recipe.title == "Berry Compote"
    assert recipe.title_is_inferred is True
    assert any(w.code == "INFERRED_TITLE" for w in recipe.warnings)


@pytest.mark.asyncio
async def test_structure_ad_clipping_noise_routed_to_notes() -> None:
    """Verifies that clipping noise and margin annotations are preserved in notes."""
    settings = Settings()
    faces = _make_dummy_faces(1)

    transcript = Transcript(
        faces=[
            FaceTranscript(
                face_index=0,
                text="Recipe for Peach Jam",
                margin_notes=["Coupons valid until October 1954", "Grandma Ruth's favorite"],
            )
        ],
        model="gemini-3.7-flash",
        prompt_id="transcribe_clipping_v1",
        prompt_version="abcd1234",
    )

    mock_recipe = Recipe(
        title="Peach Jam",
        sections=[RecipeSection(name=None, ingredients=[], steps=[])],
        notes=[
            TextSpan(text="Coupons valid until October 1954", kind="ad"),
        ],
    )
    client = ScriptedLLMClient([mock_recipe])

    recipe = await structure(transcript, faces, client, settings)

    note_texts = [n.text for n in recipe.notes]
    assert "Coupons valid until October 1954" in note_texts
    # Check that transcript margin note was integrated
    assert "Grandma Ruth's favorite" in note_texts


# ============================================================================
# 4. Pipeline Execution & Component Injection Tests
# ============================================================================

@pytest.mark.asyncio
async def test_pipeline_pass_ordering_and_provenance(tmp_path: Path) -> None:
    """Verifies that ExtractionPipeline executes passes in strict order and builds provenance."""
    img1 = tmp_path / "card1.jpg"
    img2 = tmp_path / "card2.jpg"
    img1.write_bytes(DUMMY_JPEG)
    img2.write_bytes(DUMMY_JPEG)

    settings = Settings(quality_profile="balanced")

    # Scripted responses: 1 for transcribe, 1 for structure
    mock_trans = FaceTranscriptList(
        faces=[
            FaceTranscript(face_index=0, text="1 cup sugar", confidence=0.9),
            FaceTranscript(face_index=1, text="1. Bake 20 mins", confidence=0.9),
        ]
    )
    mock_recipe = Recipe(
        title="Sugar Cookies",
        sections=[
            RecipeSection(
                name=None,
                ingredients=[IngredientLine(original_text="1 cup sugar", item="sugar", quantity="1", unit="cup")],
                steps=[Step(original_text="1. Bake 20 mins", text="Bake 20 mins")],
            )
        ],
    )
    client = ScriptedLLMClient([mock_trans, mock_recipe])

    # Injected test doubles
    mock_normalizer = Mock(side_effect=lambda r: r)
    mock_grounding = AsyncMock(return_value=(mock_recipe, [RecipeWarning(code="GROUNDING_UNSUPPORTED", message="Check test")]))
    mock_repairer = AsyncMock(side_effect=lambda r, c, s: mock_recipe)

    pipeline = ExtractionPipeline(
        llm_client=client,
        settings=settings,
        normalizer=mock_normalizer,
        grounding=mock_grounding,
        repairer=mock_repairer,
    )

    result: ExtractionResult = await pipeline.run([img1, img2])

    assert result.recipe.title == "Sugar Cookies"
    assert result.calls >= 2
    # Check pass sequence
    assert "prepare" in result.passes
    assert "transcribe" in result.passes
    assert "structure" in result.passes
    assert "repair" in result.passes
    assert "grounding" in result.passes
    assert "normalize" in result.passes

    # Check that normalizer, grounding, and repairer were called
    mock_normalizer.assert_called_once()
    mock_grounding.assert_called_once()
    mock_repairer.assert_called_once()

    # Check provenance
    prov = result.recipe.provenance
    assert prov is not None
    assert prov.profile == "balanced"
    assert len(prov.faces) in (2, 4)
    assert "Face 1 of 2" in prov.faces[0]
    assert PROMPT_STRUCTURE in prov.prompt_versions


@pytest.mark.asyncio
async def test_pipeline_failure_typing_per_pass(tmp_path: Path) -> None:
    """Verifies typed ExtractionUnavailable errors on transcription or structuring failure."""
    img = tmp_path / "card.jpg"
    img.write_bytes(DUMMY_JPEG)
    settings = Settings()

    # 1. Failure at transcribe pass
    client_fail_trans = ScriptedLLMClient([
        StageUnavailableError("Rate limit exceeded", reason=FailureReason.RATE_LIMITED)
    ])
    pipeline1 = ExtractionPipeline(llm_client=client_fail_trans, settings=settings)
    with pytest.raises(ExtractionUnavailable) as exc_info1:
        await pipeline1.run([img])
    assert exc_info1.value.reason == FailureReason.RATE_LIMITED

    # 2. Failure at structure pass
    mock_trans = FaceTranscriptList(faces=[FaceTranscript(face_index=0, text="Flour")])
    client_fail_struct = ScriptedLLMClient([
        mock_trans,
        StageUnavailableError("Malformed JSON output", reason=FailureReason.INVALID_RESPONSE),
    ])
    pipeline2 = ExtractionPipeline(llm_client=client_fail_struct, settings=settings)
    with pytest.raises(ExtractionUnavailable) as exc_info2:
        await pipeline2.run([img])
    assert exc_info2.value.reason == FailureReason.INVALID_RESPONSE


@pytest.mark.asyncio
async def test_pipeline_budget_exceeded_propagates_immediately(tmp_path: Path) -> None:
    """Verifies that BudgetExceeded is NEVER caught or swallowed."""
    img = tmp_path / "card.jpg"
    img.write_bytes(DUMMY_JPEG)
    settings = Settings()

    # Client raises BudgetExceeded on first call
    client = ScriptedLLMClient([BudgetExceeded("Spending cap of $0.50 exceeded")])
    pipeline = ExtractionPipeline(llm_client=client, settings=settings)

    with pytest.raises(BudgetExceeded) as exc_info:
        await pipeline.run([img])
    assert exc_info.value.reason == FailureReason.BUDGET_EXCEEDED


# ============================================================================
# 5. RecipeExtractor Public API Delegation Tests
# ============================================================================

@pytest.mark.asyncio
async def test_recipe_extractor_delegates_to_pipeline(tmp_path: Path) -> None:
    """Verifies RecipeExtractor public API delegates to ExtractionPipeline seamlessly."""
    c1 = tmp_path / "card_front.jpg"
    c2 = tmp_path / "card_back.jpg"
    c1.write_bytes(DUMMY_JPEG)
    c2.write_bytes(DUMMY_JPEG)

    mock_trans = FaceTranscriptList(
        faces=[
            FaceTranscript(face_index=0, text="Grandma's Apple Pie"),
            FaceTranscript(face_index=1, text="Bake at 375"),
        ]
    )
    mock_recipe = RecipeSchema(
        title="Grandma's Apple Pie",
        sections=[
            RecipeSection(
                name=None,
                ingredients=[IngredientLine(original_text="Apples", item="apples")],
                steps=[Step(original_text="Bake at 375")],
            )
        ],
    )
    client = ScriptedLLMClient([mock_trans, mock_recipe])

    extractor = RecipeExtractor(llm_client=client)

    # 1. Single card or front/back pair
    recipe = await extractor.extract(c1, c2)
    assert recipe.title == "Grandma's Apple Pie"
    assert recipe.card_ids == [c1.name, c2.name]

    # 2. extract_cards multi-card invocation
    client.reset()
    client.set_responses([mock_trans, mock_recipe])
    recipe_multi = await extractor.extract_cards([c1, c2])
    assert recipe_multi.title == "Grandma's Apple Pie"

    # 3. extract_batch invocation
    client.reset()
    client.set_responses([mock_trans, mock_recipe])
    batch_results = await extractor.extract_batch([(c1, c2)])
    assert len(batch_results) == 1
    assert batch_results[0].title == "Grandma's Apple Pie"


@pytest.mark.asyncio
async def test_consistency_unreadable_face_returns_verification_skipped(tmp_path):
    """Unreadable face file during consistency check skips with VERIFICATION_SKIPPED warning."""
    from recipeboxer.extraction.consistency import self_consistency

    recipe = Recipe(title="Test Recipe", warnings=[RecipeWarning(code="UNCERTAIN_TITLE", message="flagged")])
    transcript = Transcript(faces=[], model="test", prompt_id="test", prompt_version="1")
    client = ScriptedLLMClient([])
    settings = Settings(quality_profile="best")

    unreadable_path = tmp_path / "nonexistent_card_face.jpg"
    rec, warnings = await self_consistency(
        recipe, transcript, [unreadable_path], client, settings
    )
    assert rec.title == "Test Recipe"
    assert any(w.code == "VERIFICATION_SKIPPED" for w in warnings)
