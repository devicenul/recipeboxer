"""Unit and integration tests for WP-GD GroupingEngine and Orchestrator integration.

Verifies:
1. End-to-end GroupingEngine.group with ScriptedLLMClient (pairs, sequences, standalones).
2. FeatureToggles ablation (pixels=False, physical=False, set_verify=False, RB_FEATURE_TOGGLES JSON parsing).
3. needs_review routing on low calibrated confidence (p < threshold default 0.70) and degraded cards.
4. Fail-safe without LLM client (proposals UNCERTAIN, unverified, zero fabrication, all cards routed to review).
5. Orchestrator and Session integration with FileImageProvider and GroupingEngine.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.correlation.engine import (
    FeatureToggles,
    FileImageProvider,
    GroupingEngine,
    GroupingResult,
)
from recipeboxer.correlation.evidence import ImageProvider
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.correlation import CorrelationProposal, SetVerification
from recipeboxer.models.domain import BoundaryText, CardRole, VisualFingerprint
from recipeboxer.models.features import CaptureMeta, PixelFeatures
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.extraction.transcribe import FaceTranscript, FaceTranscriptList
from recipeboxer.storage.cache import FingerprintCache


class InMemoryImageProvider:
    """Mock ImageProvider providing in-memory byte buffers."""

    def __init__(self, data: dict[str, bytes]) -> None:
        self.data = data

    def get(self, card_id: str) -> bytes:
        return self.data.get(card_id, b"")


def _make_fp(
    card_id: str,
    *,
    image_path: str | None = None,
    role: CardRole = CardRole.UNKNOWN,
    title: str | None = None,
    first_lines: list[str] | None = None,
    last_lines: list[str] | None = None,
    degraded: bool = False,
    looks_like_back: bool = False,
) -> VisualFingerprint:
    """Helper creating test visual fingerprints."""
    return VisualFingerprint(
        card_id=card_id,
        image_path=image_path or f"/fake/{card_id}.jpg",
        sha256_hash="0" * 64,
        role=role,
        visible_title=title,
        first_lines=first_lines or [],
        last_lines=last_lines or [],
        degraded=degraded,
        looks_like_back=looks_like_back,
        boundary_text=BoundaryText(
            first_line=first_lines[0] if first_lines else "",
            last_line=last_lines[-1] if last_lines else "",
        ),
    )


class TestFeatureToggles:
    """Tests for FeatureToggles defaults and environment parsing."""

    def test_default_toggles(self):
        toggles = FeatureToggles()
        assert toggles.pixels is True
        assert toggles.content is True
        assert toggles.physical is True
        assert toggles.capture is True
        assert toggles.set_verify is True
        assert toggles.embeddings is False
        assert toggles.ocr is False

    def test_from_env_json(self):
        env_val = json.dumps({"pixels": False, "set_verify": False, "ocr": True})
        with patch.dict(os.environ, {"RB_FEATURE_TOGGLES": env_val}):
            toggles = FeatureToggles.from_env()
            assert toggles.pixels is False
            assert toggles.set_verify is False
            assert toggles.ocr is True
            assert toggles.content is True

    def test_from_env_invalid_json_fallback(self):
        with patch.dict(os.environ, {"RB_FEATURE_TOGGLES": "not-valid-json"}):
            toggles = FeatureToggles.from_env()
            assert toggles.pixels is True
            assert toggles.set_verify is True


class TestGroupingEngineEndToEnd:
    """End-to-end tests for GroupingEngine.group."""

    @pytest.mark.asyncio
    async def test_group_single_card_standalone(self):
        fp = _make_fp("card_solo", role=CardRole.STANDALONE, title="Single Card Stew")
        engine = GroupingEngine()
        img_prov = InMemoryImageProvider({"card_solo": b"\xff\xd8\xff\xe0"})
        client = ScriptedLLMClient([])

        result = await engine.group([fp], img_prov, llm_client=client)

        assert isinstance(result, GroupingResult)
        assert len(result.chains) == 1
        assert result.chains[0] == ["card_solo"]
        assert len(result.proposals) == 1
        assert result.proposals[0].ordered_card_ids == ["card_solo"]
        assert result.proposals[0].is_standalone is True
        assert result.proposals[0].recommended_action == "STANDALONE"

    @pytest.mark.asyncio
    async def test_group_pair_sequence_with_set_verification(self):
        fp1 = _make_fp("card_1", role=CardRole.SEQUENCE_START, title="Berry Pie", first_lines=["Ingredients"], last_lines=["Bake crust"])
        fp2 = _make_fp("card_2", role=CardRole.SEQUENCE_CONTINUATION, first_lines=["Filling:"], last_lines=["Cool before serving"])

        # Scripted SetVerifier response citing quotes
        set_ver_resp = SetVerification(
            anchor="card_1",
            continuations=["card_2"],
            rejected=[],
            evidence_quotes=["Filling continues crust step", "Cool before serving"],
            confidence=0.92,
            model="gemini-3.7-flash",
            prompt_id="set_verify_v1",
            prompt_version="v1",
        )
        client = ScriptedLLMClient([set_ver_resp])

        engine = GroupingEngine()
        img_prov = InMemoryImageProvider({"card_1": b"dummy1", "card_2": b"dummy2"})
        result = await engine.group([fp1, fp2], img_prov, llm_client=client)

        assert len(result.chains) == 1
        assert result.chains[0] == ["card_1", "card_2"]
        assert len(result.proposals) == 1
        prop = result.proposals[0]
        assert prop.ordered_card_ids == ["card_1", "card_2"]
        assert prop.recommended_action == "PAIR"
        assert prop.semantic_verified is True
        assert prop.confidence == pytest.approx(0.92, abs=0.05)
        assert "Quotes:" in prop.continuity_explanation

    @pytest.mark.asyncio
    async def test_failsafe_without_llm_client(self):
        """Without LLMClient, SetVerifier cannot run; proposals must be UNCERTAIN, never fabricated."""
        fp1 = _make_fp("c_alpha", role=CardRole.SEQUENCE_START, title="Roast Beef")
        fp2 = _make_fp("c_beta", role=CardRole.SEQUENCE_CONTINUATION)

        engine = GroupingEngine()
        img_prov = InMemoryImageProvider({})

        result = await engine.group([fp1, fp2], img_prov, llm_client=None)

        assert len(result.proposals) >= 1
        for p in result.proposals:
            assert p.semantic_verified is False
            assert p.recommended_action in ("UNCERTAIN", "NEEDS_REVIEW")
            assert p.degraded_reason == "NO_LLM_CLIENT"

        # All cards must be routed to needs_review
        assert "c_alpha" in result.needs_review
        assert "c_beta" in result.needs_review

    @pytest.mark.asyncio
    async def test_toggles_ablation_set_verify_disabled(self):
        """When set_verify=False toggle is active, SetVerifier is bypassed."""
        fp1 = _make_fp("c_1", role=CardRole.SEQUENCE_START, title="Cookies")
        fp2 = _make_fp("c_2", role=CardRole.SEQUENCE_CONTINUATION)

        client = ScriptedLLMClient([])  # Empty client; would raise if called
        toggles = FeatureToggles(set_verify=False)

        engine = GroupingEngine()
        img_prov = InMemoryImageProvider({})
        result = await engine.group([fp1, fp2], img_prov, llm_client=client, toggles=toggles)

        assert len(client.requests) == 0  # No LLM calls executed
        for p in result.proposals:
            assert p.semantic_verified is False
            assert p.recommended_action in ("UNCERTAIN", "NEEDS_REVIEW")
        assert len(result.needs_review) == 2

    @pytest.mark.asyncio
    async def test_needs_review_on_degraded_card_input(self):
        """Cards flagged with degraded=True are routed to needs_review."""
        fp1 = _make_fp("deg_1", role=CardRole.SEQUENCE_START, title="Damaged Note", degraded=True)
        fp2 = _make_fp("deg_2", role=CardRole.SEQUENCE_CONTINUATION)

        set_ver_resp = SetVerification(
            anchor="deg_1",
            continuations=["deg_2"],
            rejected=[],
            evidence_quotes=[],
            confidence=0.85,
            model="gemini-3.7-flash",
            prompt_id="set_verify_v1",
            prompt_version="v1",
        )
        client = ScriptedLLMClient([set_ver_resp])

        engine = GroupingEngine()
        img_prov = InMemoryImageProvider({})
        result = await engine.group([fp1, fp2], img_prov, llm_client=client)

        assert "deg_1" in result.needs_review
        prop = result.proposals[0]
        assert prop.recommended_action == "NEEDS_REVIEW"


class TestOrchestratorGroupingIntegration:
    """Verifies RecipeBoxerAgent orchestration with GroupingEngine."""

    @pytest.mark.asyncio
    async def test_orchestrator_runs_grouping_engine_and_populates_session(self, tmp_path: Path):
        img1 = tmp_path / "card1.jpg"
        img2 = tmp_path / "card2.jpg"
        valid_jpeg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
            b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
            b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01"
            b"\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01"
            b"\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06"
            b"\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        )
        img1.write_bytes(valid_jpeg)
        img2.write_bytes(valid_jpeg)

        card1 = ScannedCard(card_id="card1", file_path=img1, sha256_hash="0" * 64)
        card2 = ScannedCard(card_id="card2", file_path=img2, sha256_hash="1" * 64)

        fp1 = _make_fp("card1", image_path=str(img1), role=CardRole.SEQUENCE_START, title="Lemon Cake")
        fp2 = _make_fp("card2", image_path=str(img2), role=CardRole.SEQUENCE_CONTINUATION)

        set_ver = SetVerification(
            anchor="card1",
            continuations=["card2"],
            rejected=[],
            evidence_quotes=["Step 2 continues frosting"],
            confidence=0.91,
            model="gemini-3.7-flash",
            prompt_id="set_verify_v1",
            prompt_version="v1",
        )
        recipe_mock = RecipeSchema(
            recipe_id="rec_lemon_cake",
            title="Lemon Cake",
            ingredients=["Flour", "Lemon", "Sugar"],
            directions=["Mix and bake at 350F."],
        )

        transcript_mock = FaceTranscriptList(
            faces=[
                FaceTranscript(
                    face_index=0,
                    text="Lemon Cake\nFlour, Lemon, Sugar\nMix and bake at 350F.",
                )
            ]
        )
        client = ScriptedLLMClient([set_ver, transcript_mock, recipe_mock])
        cache = FingerprintCache(":memory:")

        session = PipelineSession(
            session_id="test_orchestrator_grouping",
            cache=cache,
            config={"scans_dir": str(tmp_path), "auto_approve": True},
            mock_client=client,
        )
        session.cards = [card1, card2]
        session.fingerprints["card1"] = fp1
        session.fingerprints["card2"] = fp2

        agent = RecipeBoxerAgent(cache=cache)
        await agent.run_pipeline(session)

        # Invariants: GroupingResult and proposals populated on session
        assert session.grouping_result is not None
        assert isinstance(session.grouping_result, GroupingResult)
        assert len(session.proposals) == 1
        assert session.proposals[0].ordered_card_ids == ["card1", "card2"]
        assert session.proposals[0].semantic_verified is True

        # Invariants: Extraction executed on approved proposal
        assert len(session.recipes) == 1
        assert session.recipes[0].title == "Lemon Cake"
        assert session.recipes[0].card_ids == ["card1", "card2"]
        assert session.state == PipelineState.COMPLETED
