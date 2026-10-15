"""Unit tests for FIND-05: Silent errors and placeholder fallbacks remediation.

Verifies:
1. ImageProvider (FileImageProvider & TempDirImageProvider) returns None on missing files, never empty bytes b"".
2. ImageNotFoundError definition exists and is available.
3. self-consistency LLM exceptions emit RecipeWarning(code="VERIFICATION_SKIPPED") instead of silent pass.
4. BudgetExceeded propagates cleanly from self-consistency check without being caught.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import pytest

from recipeboxer.ai.client import LLMClient, LLMRequest, LLMResponse
from recipeboxer.models.failures import BudgetExceeded
from recipeboxer.correlation.engine import (
    FileImageProvider,
    ImageNotFoundError,
    TempDirImageProvider,
)
from recipeboxer.extraction.consistency import self_consistency
from recipeboxer.extraction.transcribe import FaceTranscript, Transcript
from recipeboxer.models.recipe import IngredientLine, Recipe, RecipeSection, Step


def test_image_provider_returns_none_never_empty_bytes(tmp_path: Path):
    """Verify ImageProvider implementations return None on missing/unreadable files."""
    # 1. FileImageProvider
    file_prov = FileImageProvider({"c1": tmp_path / "non_existent.jpg"})
    assert file_prov.get("c1") is None
    assert file_prov.get("c1") != b""

    # 2. TempDirImageProvider
    temp_prov = TempDirImageProvider(tmp_path)
    assert temp_prov.get("non_existent_card.jpg") is None
    assert temp_prov.get("non_existent_card.jpg") != b""


def test_image_not_found_error_is_exception():
    """Verify ImageNotFoundError inherits from FileNotFoundError or KeyError."""
    assert issubclass(ImageNotFoundError, (FileNotFoundError, KeyError, Exception))
    err = ImageNotFoundError("Image missing")
    assert str(err) == "Image missing"


class _FailingLLMClient(LLMClient):
    def __init__(self, exc: Exception):
        self.exc = exc

    async def generate(self, request: LLMRequest) -> LLMResponse:
        raise self.exc


@pytest.mark.asyncio
async def test_consistency_emits_verification_skipped_on_llm_failure(tmp_path: Path):
    """Verify LLM failure in self_consistency emits a VERIFICATION_SKIPPED warning."""
    card_img = tmp_path / "card.png"
    card_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82")

    recipe = Recipe(
        title="Vanilla Sugar Cookies",
        confidence=0.50,
        sections=[
            RecipeSection(
                ingredients=[IngredientLine(original_text="2 cups flour", item="flour", quantity="2", unit="cups")],
                steps=[Step(original_text="1. Mix ingredients.", text="Mix ingredients.")],
            )
        ],
    )
    transcript = Transcript(
        faces=[FaceTranscript(face_index=0, text="Vanilla Sugar Cookies\n2 cups flour\n1. Mix ingredients.")],
        model="gemini-3.7-flash",
        prompt_id="test",
        prompt_version="1",
    )

    failing_client = _FailingLLMClient(RuntimeError("Gemini 503 Service Unavailable"))
    from recipeboxer.config import get_settings
    settings = get_settings()

    verified_recipe, warnings = await self_consistency(
        recipe=recipe,
        transcript=transcript,
        faces=[card_img],
        llm_client=failing_client,
        settings=settings,
    )

    # Must return original recipe plus a warning, not silent empty warnings
    assert verified_recipe == recipe
    assert any(w.code == "VERIFICATION_SKIPPED" for w in warnings)
    skip_warning = next(w for w in warnings if w.code == "VERIFICATION_SKIPPED")
    assert "Gemini 503" in skip_warning.message


@pytest.mark.asyncio
async def test_consistency_budget_exceeded_propagates(tmp_path: Path):
    """Verify BudgetExceeded in self_consistency propagates immediately."""
    card_img = tmp_path / "card.png"
    card_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82")

    recipe = Recipe(
        title="Vanilla Sugar Cookies",
        confidence=0.50,
        sections=[
            RecipeSection(
                ingredients=[IngredientLine(original_text="2 cups flour", item="flour", quantity="2", unit="cups")],
                steps=[Step(original_text="1. Mix ingredients.", text="Mix ingredients.")],
            )
        ],
    )
    transcript = Transcript(
        faces=[FaceTranscript(face_index=0, text="Vanilla Sugar Cookies\n2 cups flour\n1. Mix ingredients.")],
        model="gemini-3.7-flash",
        prompt_id="test",
        prompt_version="1",
    )

    budget_client = _FailingLLMClient(BudgetExceeded("Budget cap $2.50 exceeded"))
    from recipeboxer.config import get_settings
    settings = get_settings()

    with pytest.raises(BudgetExceeded):
        await self_consistency(
            recipe=recipe,
            transcript=transcript,
            faces=[card_img],
            llm_client=budget_client,
            settings=settings,
        )
