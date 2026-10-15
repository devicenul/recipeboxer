"""Comprehensive offline unit test suite for WP-XB (Grounding, Repair, Consistency).

Tests comply with INV-AC1..INV-AC6:
- 100% offline, zero Gemini API calls, no GEMINI_API_KEY.
- ScriptedLLMClient with strictly call-ordered responses.
- Fictitious hand-written recipes (Grandma's Blueberry Muffins, Aunt Martha's Scones, etc.).
- Never keys behaviour on filenames or image hashes.
- Never asserts offline accuracy numbers.
"""

from __future__ import annotations

import pytest

from recipeboxer.ai.client import LLMStage
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.config import Settings
from recipeboxer.extraction.consistency import diff_recipes, self_consistency
from recipeboxer.extraction.grounding import (
    GroundingBatchResponse,
    LineGroundingCheck,
    ground,
    levenshtein_distance,
    local_window_cer,
    semi_global_cer,
)
from recipeboxer.extraction.grounding import FaceTranscript, Transcript
from recipeboxer.extraction.repair import validate_and_repair
from recipeboxer.models.failures import BudgetExceeded, ExtractionUnavailable, FailureReason
from recipeboxer.models.recipe import IngredientLine, Recipe, RecipeSection, RecipeWarning, Step
from recipeboxer.vision.ocr import OCRLine


@pytest.fixture
def base_settings() -> Settings:
    """Settings fixture configured for offline testing."""
    return Settings(
        gemini_api_key=None,
        default_model="gemini-3.7-flash",
        quality_profile="balanced",
    )


def make_fictitious_recipe() -> Recipe:
    """Creates a canonical fictitious handwritten recipe: Grandma Clara's Blueberry Muffins."""
    return Recipe(
        recipe_id="rec_muffins_01",
        title="Grandma Clara's Blueberry Muffins",
        sections=[
            RecipeSection(
                name="Main",
                ingredients=[
                    IngredientLine(
                        original_text="2 cups all-purpose flour",
                        quantity="2",
                        unit="cups",
                        item="all-purpose flour",
                        source_face=0,
                    ),
                    IngredientLine(
                        original_text="1/2 cup white sugar",
                        quantity="1/2",
                        unit="cup",
                        item="white sugar",
                        source_face=0,
                    ),
                    IngredientLine(
                        original_text="1 cup fresh blueberries",
                        quantity="1",
                        unit="cup",
                        item="fresh blueberries",
                        source_face=0,
                    ),
                ],
                steps=[
                    Step(
                        original_text="Preheat oven to 375 degrees F.",
                        text="Preheat oven to 375 degrees F.",
                        source_face=0,
                    ),
                    Step(
                        original_text="Fold blueberries gently into the flour batter.",
                        text="Fold blueberries gently into the flour batter.",
                        source_face=0,
                    ),
                ],
            )
        ],
        confidence=0.85,
    )


def make_fictitious_transcript() -> Transcript:
    """Creates a matching card face transcript for Grandma Clara's Blueberry Muffins."""
    text_face_0 = (
        "Grandma Clara's Blueberry Muffins\n"
        "2 cups all-purpose flour\n"
        "1/2 cup white sugar\n"
        "1 cup fresh blueberries\n"
        "Preheat oven to 375 degrees F.\n"
        "Fold blueberries gently into the flour batter.\n"
        "Bake 20-25 mins until golden brown."
    )
    return Transcript(
        faces=[FaceTranscript(face_index=0, text=text_face_0)],
        model="gemini-3.7-flash",
        prompt_id="transcribe_v1",
        prompt_version="a1b2c3d4",
    )


# ============================================================================
# 1. GROUNDING VERIFIER (X6) TESTS
# ============================================================================


def test_levenshtein_and_cer_metrics() -> None:
    """Verifies Levenshtein distance and semi-global CER calculation helpers."""
    assert levenshtein_distance("", "") == 0
    assert levenshtein_distance("flour", "flour") == 0
    assert levenshtein_distance("flour", "four") == 1
    assert levenshtein_distance("cup", "cups") == 1

    # Exact match in target
    cer_exact = semi_global_cer("1 cup flour", "Ingredients: 1 cup flour, sifted")
    assert cer_exact == 0.0

    # Local window CER on multi-line text
    target_block = "Aunt Clara's Kitchen\n1 cup flour\n1/2 cup sugar\n"
    assert local_window_cer("1 cup flour", target_block) == 0.0
    assert local_window_cer("1 c. flour", target_block) <= 0.25


@pytest.mark.asyncio
async def test_grounding_all_lines_match_transcript(base_settings: Settings) -> None:
    """When all recipe lines match transcript with CER <= 0.25, zero LLM calls are made."""
    recipe = make_fictitious_recipe()
    transcript = make_fictitious_transcript()
    client = ScriptedLLMClient([])

    out_recipe, warnings = await ground(recipe, transcript, client, base_settings)

    assert client.call_count == 0
    assert len(warnings) == 0
    assert len(out_recipe.all_ingredients()) == 3
    assert len(out_recipe.all_steps()) == 2


@pytest.mark.asyncio
async def test_grounding_batched_llm_check_flags_unsupported_line(base_settings: Settings) -> None:
    """Unmatched line triggers batched LLM support check and gets GROUNDING_UNSUPPORTED warning."""
    recipe = make_fictitious_recipe()
    transcript = make_fictitious_transcript()

    # Add an ungrounded ingredient not present on card
    hallucinated_ing = IngredientLine(
        original_text="1 tsp ground nutmeg",
        quantity="1",
        unit="tsp",
        item="ground nutmeg",
        source_face=0,
    )
    recipe.sections[0].ingredients.append(hallucinated_ing)

    # Script LLM response marking nutmeg unsupported
    mock_batch_resp = GroundingBatchResponse(
        checks=[
            LineGroundingCheck(
                line_id="ing_0_3",
                supported=False,
                quote=None,
                reason="Card transcript contains no mention of nutmeg.",
            )
        ]
    )
    client = ScriptedLLMClient([mock_batch_resp])

    out_recipe, warnings = await ground(
        recipe, transcript, client, base_settings, remove_unsupported=False
    )

    assert client.call_count == 1
    assert client.requests[0].stage == LLMStage.GROUNDING
    assert len(warnings) == 1
    assert warnings[0].code == "GROUNDING_UNSUPPORTED"
    assert "nutmeg" in warnings[0].message
    # Line is kept when remove_unsupported=False
    assert len(out_recipe.all_ingredients()) == 4


@pytest.mark.asyncio
async def test_grounding_removes_unsupported_line_when_configured_and_absent_from_ocr(
    base_settings: Settings,
) -> None:
    """When configured and line is absent from transcript & OCR, unsupported line is removed."""
    recipe = make_fictitious_recipe()
    transcript = make_fictitious_transcript()

    # Add an invented step
    invented_step = Step(
        original_text="Microwave on high power for 3 minutes.",
        text="Microwave on high power for 3 minutes.",
        source_face=0,
    )
    recipe.sections[0].steps.append(invented_step)

    mock_batch_resp = GroundingBatchResponse(
        checks=[
            LineGroundingCheck(
                line_id="step_0_2",
                supported=False,
                quote=None,
                reason="Microwave instruction not found on vintage card.",
            )
        ]
    )
    client = ScriptedLLMClient([mock_batch_resp])

    # OCR lines do NOT contain microwave instruction
    ocr_lines = [
        OCRLine(text="Grandma Clara's Blueberry Muffins"),
        OCRLine(text="2 cups all-purpose flour"),
    ]

    out_recipe, warnings = await ground(
        recipe,
        transcript,
        client,
        base_settings,
        ocr_lines=ocr_lines,
        remove_unsupported=True,
    )

    assert client.call_count == 1
    assert len(warnings) == 1
    assert warnings[0].code == "GROUNDING_UNSUPPORTED"
    assert "Removed unsupported step line" in warnings[0].message
    # Invented step was removed
    assert len(out_recipe.all_steps()) == 2
    assert all("microwave" not in s.original_text.lower() for s in out_recipe.all_steps())


@pytest.mark.asyncio
async def test_grounding_keeps_unsupported_line_when_present_in_ocr_even_if_removal_enabled(
    base_settings: Settings,
) -> None:
    """Line absent from transcript is preserved if detected in local OCR evidence."""
    recipe = make_fictitious_recipe()
    transcript = make_fictitious_transcript()

    # Ingredient omitted by transcription pass but present on card
    vanilla_ing = IngredientLine(
        original_text="1 tsp pure vanilla extract",
        quantity="1",
        unit="tsp",
        item="pure vanilla extract",
        source_face=0,
    )
    recipe.sections[0].ingredients.append(vanilla_ing)

    # LLM says transcript does not support it
    mock_batch_resp = GroundingBatchResponse(
        checks=[
            LineGroundingCheck(
                line_id="ing_0_3",
                supported=False,
                quote=None,
                reason="Transcript omits vanilla.",
            )
        ]
    )
    client = ScriptedLLMClient([mock_batch_resp])

    # Local OCR lines contain vanilla extract!
    ocr_lines = [
        OCRLine(text="1 tsp pure vanilla extract", confidence=0.92),
    ]

    out_recipe, warnings = await ground(
        recipe,
        transcript,
        client,
        base_settings,
        ocr_lines=ocr_lines,
        remove_unsupported=True,  # Removal enabled, but OCR rescues line from removal
    )

    assert client.call_count == 1
    # Line is kept because it was present in OCR
    assert len(out_recipe.all_ingredients()) == 4
    # Flagged with GROUNDING_UNSUPPORTED warning rather than deleted
    assert any(w.code == "GROUNDING_UNSUPPORTED" for w in warnings)
    assert not any("Removed" in w.message for w in warnings)


@pytest.mark.asyncio
async def test_grounding_ocr_quantity_and_unit_disagreement_never_overwrites(
    base_settings: Settings,
) -> None:
    """OCR cross-check detects number/unit disagreement without modifying recipe values."""
    recipe = make_fictitious_recipe()
    transcript = make_fictitious_transcript()
    client = ScriptedLLMClient([])

    # Local OCR lines where flour says "3 cups" (vs recipe "2 cups") and sugar says "1/2 tbsp" (vs recipe "1/2 cup")
    ocr_lines = [
        OCRLine(text="3 cups all-purpose flour", confidence=0.95),
        OCRLine(text="1/2 tbsp white sugar", confidence=0.88),
    ]

    out_recipe, warnings = await ground(recipe, transcript, client, base_settings, ocr_lines=ocr_lines)

    disagreements = [w for w in warnings if w.code == "MODEL_DISAGREEMENT"]
    assert len(disagreements) >= 1

    # Invariant: recipe values are NEVER overwritten by OCR check
    flour_ing = out_recipe.all_ingredients()[0]
    assert flour_ing.quantity == "2"
    assert flour_ing.unit == "cups"

    sugar_ing = out_recipe.all_ingredients()[1]
    assert sugar_ing.quantity == "1/2"
    assert sugar_ing.unit == "cup"


@pytest.mark.asyncio
async def test_grounding_budget_exceeded_propagates(base_settings: Settings) -> None:
    """BudgetExceeded from LLMClient propagates without being swallowed."""
    recipe = make_fictitious_recipe()
    transcript = make_fictitious_transcript()

    # Add unmatched line to force LLM call
    recipe.sections[0].ingredients.append(
        IngredientLine(original_text="1 pinch saffron", item="saffron")
    )
    client = ScriptedLLMClient([BudgetExceeded("Budget cap exceeded")])

    with pytest.raises(BudgetExceeded):
        await ground(recipe, transcript, client, base_settings)


# ============================================================================
# 2. VALIDATE & REPAIR LOOP (X8) TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_validate_and_repair_valid_input_passes_round_0(base_settings: Settings) -> None:
    """Valid dict passes on round 0 without calling LLM or adding REPAIRED_OUTPUT warning."""
    valid_dict = {
        "title": "Aunt Martha's Lemon Drop Scones",
        "sections": [
            {
                "name": "Dough",
                "ingredients": [
                    {"original_text": "2 cups flour", "quantity": "2", "unit": "cups", "item": "flour"}
                ],
                "steps": [
                    {"original_text": "Bake at 400 F for 15 mins."}
                ],
            }
        ],
    }
    client = ScriptedLLMClient([])

    repaired = await validate_and_repair(valid_dict, client, base_settings, max_rounds=2)

    assert client.call_count == 0
    assert repaired.title == "Aunt Martha's Lemon Drop Scones"
    assert not any(w.code == "REPAIRED_OUTPUT" for w in repaired.warnings)


@pytest.mark.asyncio
async def test_validate_and_repair_success_in_round_1(base_settings: Settings) -> None:
    """Invalid input (missing required title) is repaired in Round 1 with REPAIRED_OUTPUT warning."""
    # Invalid input missing required title
    invalid_dict = {
        "title": "   ",  # Blank title fails validation
        "sections": [
            {
                "ingredients": [{"original_text": "1 cup oats", "item": "oats"}],
                "steps": [{"original_text": "Stir well."}],
            }
        ],
    }

    repaired_model = Recipe(
        title="Repaired Rolled Oats Porridge",
        sections=[
            RecipeSection(
                name="Main",
                ingredients=[IngredientLine(original_text="1 cup oats", item="oats")],
                steps=[Step(original_text="Stir well.")],
            )
        ],
    )
    client = ScriptedLLMClient([repaired_model])

    result = await validate_and_repair(invalid_dict, client, base_settings, max_rounds=2)

    assert client.call_count == 1
    assert client.requests[0].stage == LLMStage.REPAIR
    assert result.title == "Repaired Rolled Oats Porridge"
    assert any(w.code == "REPAIRED_OUTPUT" for w in result.warnings)


@pytest.mark.asyncio
async def test_validate_and_repair_success_in_round_2(base_settings: Settings) -> None:
    """Payload that fails Round 1 succeeds in Round 2 within max_rounds=2."""
    raw_broken_json = "{ broken json text without closing brace "

    # Round 1 returns still-invalid payload (empty title)
    still_broken = {"title": "", "sections": []}
    # Round 2 returns valid Recipe
    valid_repaired = Recipe(
        title="Grandpa Joe's Rye Bread",
        sections=[
            RecipeSection(
                name="Dough",
                ingredients=[IngredientLine(original_text="3 cups rye flour", item="rye flour")],
                steps=[Step(original_text="Knead 10 minutes.")],
            )
        ],
    )
    client = ScriptedLLMClient([still_broken, valid_repaired])

    result = await validate_and_repair(raw_broken_json, client, base_settings, max_rounds=2)

    assert client.call_count == 2
    assert result.title == "Grandpa Joe's Rye Bread"
    assert any(w.code == "REPAIRED_OUTPUT" for w in result.warnings)


@pytest.mark.asyncio
async def test_validate_and_repair_failure_after_exhausting_rounds(base_settings: Settings) -> None:
    """When repair attempts fail across all max_rounds, raises ExtractionUnavailable."""
    raw_invalid = {"title": ""}  # Empty title

    # Both rounds return invalid responses
    client = ScriptedLLMClient([
        {"title": ""},
        {"title": "   "},
    ])

    with pytest.raises(ExtractionUnavailable) as exc_info:
        await validate_and_repair(raw_invalid, client, base_settings, max_rounds=2)

    assert exc_info.value.reason == FailureReason.SCHEMA_VALIDATION
    assert "2 repair rounds" in str(exc_info.value)
    assert client.call_count == 2


@pytest.mark.asyncio
async def test_validate_and_repair_budget_exceeded_propagates(base_settings: Settings) -> None:
    """BudgetExceeded during repair loop propagates without being converted to ExtractionUnavailable."""
    raw_invalid = {"title": ""}
    client = ScriptedLLMClient([BudgetExceeded("Spend cap reached during repair")])

    with pytest.raises(BudgetExceeded):
        await validate_and_repair(raw_invalid, client, base_settings, max_rounds=2)


# ============================================================================
# 3. SELF-CONSISTENCY (X9) TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_self_consistency_disabled_when_off() -> None:
    """When quality profile self_consistency is 'off', returns immediately without LLM calls."""
    economy_settings = Settings(
        gemini_api_key=None,
        quality_profile="economy",  # self_consistency is 'off'
    )
    recipe = make_fictitious_recipe()
    transcript = make_fictitious_transcript()
    client = ScriptedLLMClient([])

    out_recipe, warnings = await self_consistency(recipe, transcript, [], client, economy_settings)

    assert client.call_count == 0
    assert len(warnings) == 0
    assert out_recipe.title == recipe.title


@pytest.mark.asyncio
async def test_self_consistency_agreement_raises_confidence(base_settings: Settings) -> None:
    """When secondary read agrees, no disagreement warnings are emitted and NO fabricated confidence
    boost is applied (WP-UP3: the former 0.85+0.10 prior violated INV-AC4; Recipe.confidence is set
    only by the fitted recipe-confidence model)."""
    recipe = make_fictitious_recipe()
    recipe.confidence = 0.80
    recipe.warnings.append(RecipeWarning(code="LOW_CONFIDENCE", message="Faded card face"))
    transcript = make_fictitious_transcript()

    # Second reading with agreeing title, ingredients, and steps
    second_reading = make_fictitious_recipe()
    client = ScriptedLLMClient([second_reading])

    out_recipe, warnings = await self_consistency(recipe, transcript, [], client, base_settings)

    assert client.call_count == 1
    assert len(warnings) == 0
    # Confidence is left untouched (no hard-coded boost)
    assert out_recipe.confidence == 0.80


@pytest.mark.asyncio
async def test_self_consistency_disagreement_generates_warnings_and_never_silently_picks(
    base_settings: Settings,
) -> None:
    """Field disagreements generate MODEL_DISAGREEMENT warnings without altering primary reading."""
    recipe = make_fictitious_recipe()
    recipe.confidence = 0.70  # Flagged recipe
    transcript = make_fictitious_transcript()

    # Second independent reading with conflicting title, different flour quantity, and fewer steps
    second_reading = Recipe(
        title="Blueberry Muffins (Quick Version)",  # Conflicting title
        sections=[
            RecipeSection(
                name="Main",
                ingredients=[
                    IngredientLine(
                        original_text="3 cups all-purpose flour",  # Conflicting quantity: 3 vs 2
                        quantity="3",
                        unit="cups",
                        item="all-purpose flour",
                    ),
                    IngredientLine(
                        original_text="1/2 cup white sugar",
                        quantity="1/2",
                        unit="cup",
                        item="white sugar",
                    ),
                    IngredientLine(
                        original_text="1 cup fresh blueberries",
                        quantity="1",
                        unit="cup",
                        item="fresh blueberries",
                    ),
                ],
                steps=[
                    Step(original_text="Preheat oven to 375 degrees F."),
                    # Second step missing
                ],
            )
        ],
    )
    client = ScriptedLLMClient([second_reading])

    out_recipe, warnings = await self_consistency(recipe, transcript, [], client, base_settings)

    assert client.call_count == 1
    disagreements = [w for w in warnings if w.code == "MODEL_DISAGREEMENT"]
    assert len(disagreements) >= 3

    # Check title disagreement warning
    title_warns = [w for w in disagreements if "Title disagreement" in w.message]
    assert len(title_warns) == 1
    assert "Grandma Clara's Blueberry Muffins" in title_warns[0].message
    assert "Quick Version" in title_warns[0].message

    # Check quantity disagreement warning
    qty_warns = [w for w in disagreements if "quantity disagreement" in w.message]
    assert len(qty_warns) == 1
    assert "'2'" in qty_warns[0].message and "'3'" in qty_warns[0].message

    # Check step count disagreement
    step_warns = [w for w in disagreements if "Step count disagreement" in w.message]
    assert len(step_warns) == 1

    # Invariant: primary reading is NEVER silently overwritten
    assert out_recipe.title == "Grandma Clara's Blueberry Muffins"
    assert out_recipe.all_ingredients()[0].quantity == "2"
    assert len(out_recipe.all_steps()) == 2


@pytest.mark.asyncio
async def test_self_consistency_flagged_other_model_profile() -> None:
    """Best profile (flagged_other_model) directs second read to an alternative model."""
    best_settings = Settings(
        gemini_api_key=None,
        quality_profile="best",
        default_model="gemini-3.7-flash",
        judge_model="gemini-3.7-pro",
    )
    recipe = make_fictitious_recipe()
    recipe.confidence = 0.70  # Flagged
    transcript = make_fictitious_transcript()

    agreeing_recipe = make_fictitious_recipe()
    client = ScriptedLLMClient([agreeing_recipe])

    out_recipe, warnings = await self_consistency(recipe, transcript, [], client, best_settings)

    assert client.call_count == 1
    # Request should target the other model (different from primary model)
    primary_model = best_settings.get_stage_model(LLMStage.STRUCTURE)
    req = client.requests[0]
    assert req.model != primary_model
    # When primary is pro, second read switches to flash
    assert "flash" in req.model.lower()

    # Now verify that when primary is flash, second read switches to pro
    flash_settings = Settings(
        gemini_api_key=None,
        default_model="gemini-3.7-flash",
        structure_model="gemini-3.7-flash",
        judge_model="gemini-3.7-pro",
        quality_profile="best",
    )
    recipe_flash = make_fictitious_recipe()
    recipe_flash.confidence = 0.70  # Flagged
    client_flash = ScriptedLLMClient([agreeing_recipe])
    await self_consistency(recipe_flash, transcript, [], client_flash, flash_settings)
    assert len(client_flash.requests) == 1
    assert "pro" in client_flash.requests[0].model.lower()


@pytest.mark.asyncio
async def test_self_consistency_budget_exceeded_propagates(base_settings: Settings) -> None:
    """BudgetExceeded from LLMClient during self-consistency propagates directly."""
    recipe = make_fictitious_recipe()
    recipe.confidence = 0.70  # Flagged
    transcript = make_fictitious_transcript()

    client = ScriptedLLMClient([BudgetExceeded("Budget exceeded in consistency")])

    with pytest.raises(BudgetExceeded):
        await self_consistency(recipe, transcript, [], client, base_settings)


@pytest.mark.asyncio
async def test_grounding_nested_ocr_lines_handled_cleanly(base_settings: Settings) -> None:
    """Per-face nested list of OCR lines (as produced by ExtractionPipeline) is handled seamlessly."""
    recipe = make_fictitious_recipe()
    transcript = make_fictitious_transcript()
    client = ScriptedLLMClient([])

    nested_ocr_lines = [
        [OCRLine(text="Grandma Clara's Blueberry Muffins"), OCRLine(text="2 cups all-purpose flour")],
        [OCRLine(text="1/2 cup white sugar"), OCRLine(text="1 cup fresh blueberries")],
    ]

    out_recipe, warnings = await ground(recipe, transcript, client, base_settings, ocr_lines=nested_ocr_lines)

    assert client.call_count == 0
    assert len(out_recipe.all_ingredients()) == 3

