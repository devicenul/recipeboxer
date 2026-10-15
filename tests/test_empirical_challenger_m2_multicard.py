"""Empirical Challenger Deep Adversarial Test Suite for Milestone 2 (Multi-Card >2 Cards HITL & Extraction Ordering).

Verifies:
1. Multi-card sets (3, 4, 5, 6, 8+ cards) sequential extraction and ordering preservation without page inversion.
2. Gemini API multimodal payload generation with exact sequential page numbering (Front, Continuation 1, 2, ... N).
3. Deterministic offline multi-card direction generation preserving explicit input permutation order.
4. PipelineSession end-to-end integration: 4-card custom sequence approval, tag propagation, image URL generation.
5. Detachment, heterogeneous input types (PIL.Image, bytes, Path, ScannedCard, VisualFingerprint), and batch execution.
6. Multi-proposal batch session with mixed multi-card, standalone, and rejected cards.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from recipeboxer.agent.orchestrator import AgentOrchestrator, PipelineSession
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.correlation import CorrelationProposal
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import IngestionMode, PipelineStartRequest, PipelineState
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.cache import FingerprintCache


def _make_dummy_fingerprint(card_id: str, role: CardRole = CardRole.STANDALONE) -> VisualFingerprint:
    return VisualFingerprint(
        card_id=card_id,
        image_path=f"/tmp/scans/{card_id}",
        sha256_hash=hashlib.sha256(card_id.encode("utf-8")).hexdigest(),
        role=role,
    )


class MultiCardExtractorClient:
    """Mock client returning deterministic recipes matching explicit card ordering."""

    async def extract_cards(self, cards: List[Any]) -> RecipeSchema:
        cids = []
        for c in cards:
            if isinstance(c, VisualFingerprint):
                cids.append(c.card_id)
            elif isinstance(c, ScannedCard) or hasattr(c, "card_id"):
                cids.append(getattr(c, "card_id", "card"))
            elif isinstance(c, (Path, str)):
                cids.append(Path(c).name)
            elif isinstance(c, bytes):
                h = hashlib.sha256(c).hexdigest()[:8]
                cids.append(f"card_{h}.jpg")
            elif isinstance(c, Image.Image):
                buf = io.BytesIO()
                c.save(buf, format="JPEG")
                h = hashlib.sha256(buf.getvalue()).hexdigest()[:8]
                cids.append(f"card_{h}.jpg")
            else:
                cids.append(f"card_{len(cids)+1}")

        directions = [
            f"1. Front preparation and mixing ({cids[0]})",
            *(f"{i+2}. Subsequent directions for card {cid}" for i, cid in enumerate(cids[1:]))
        ]
        return RecipeSchema(
            recipe_id="rec_multicard",
            title="Multi-Card Recipe",
            ingredients=["ingredient 1", "ingredient 2"],
            directions=directions,
            card_ids=cids,
            image_urls=[f"/api/cards/{cid}/image" for cid in cids],
        )

    async def extract_recipe(self, front: Any, back: Optional[Any] = None) -> RecipeSchema:
        cards = [front]
        if back is not None:
            cards.append(back)
        return await self.extract_cards(cards)

    async def extract(self, cards: Any) -> RecipeSchema:
        if isinstance(cards, list):
            return await self.extract_cards(cards)
        return await self.extract_cards([cards])


class TestMultiCardExtractionOrdering:
    """Stress tests for GeminiRecipeExtractor with 3, 4, 5, 6, 8 card sequences."""

    @pytest.fixture
    def extractor(self):
        return GeminiRecipeExtractor(client=MultiCardExtractorClient())

    @pytest.mark.asyncio
    async def test_extract_empty_list_raises_value_error(self, extractor):
        """Empty input list raises ValueError."""
        with pytest.raises(ValueError, match="No card inputs provided"):
            await extractor.extract([])

    @pytest.mark.asyncio
    async def test_extract_cards_5_card_custom_permutation_preserves_order(self, extractor):
        """5-card custom permutation is preserved in recipe.card_ids, image_urls, and sequential directions."""
        card_ids = ["page_5.jpg", "page_2.jpg", "page_4.jpg", "page_1.jpg", "page_3.jpg"]
        fps = [_make_dummy_fingerprint(cid) for cid in card_ids]

        recipe = await extractor.extract_cards(fps)
        assert isinstance(recipe, RecipeSchema)
        assert recipe.card_ids == card_ids
        assert len(recipe.image_urls) == 5
        for idx, cid in enumerate(card_ids):
            assert recipe.image_urls[idx] == f"/api/cards/{cid}/image"

        # Check directions order matches input card permutation
        assert len(recipe.directions) == 5
        assert recipe.directions[0].startswith("1. Front preparation")
        assert "card page_2.jpg" in recipe.directions[1]
        assert "card page_4.jpg" in recipe.directions[2]
        assert "card page_1.jpg" in recipe.directions[3]
        assert "card page_3.jpg" in recipe.directions[4]

    @pytest.mark.asyncio
    async def test_extract_cards_8_card_large_recipe_sequence(self, extractor):
        """8-card large multi-card recipe sequence extraction preserves all 8 cards without truncation."""
        card_ids = [f"card_part_{i:02d}.jpg" for i in range(1, 9)]
        fps = [_make_dummy_fingerprint(cid) for cid in card_ids]

        recipe = await extractor.extract(fps)
        assert recipe.card_ids == card_ids
        assert len(recipe.image_urls) == 8
        assert len(recipe.directions) == 8
        for i in range(1, 9):
            assert recipe.directions[i - 1].startswith(f"{i}. ")

    @pytest.mark.asyncio
    async def test_extract_heterogeneous_input_sequence_types(self, extractor, tmp_path):
        """extract() supports heterogeneous sequence types: VisualFingerprint, Path, ScannedCard, bytes, PIL.Image."""
        p1 = tmp_path / "card_01.jpg"
        p1.write_bytes(b"image_bytes_1")

        fp = _make_dummy_fingerprint("card_02.jpg")
        sc = ScannedCard(card_id="card_03.jpg", file_path=p1, sha256_hash="hash1", file_size_bytes=100)

        img = Image.new("RGB", (100, 100), color="white")
        raw_b = b"dummy_jpeg_bytes_4"

        heterogeneous_cards = [fp, p1, sc, img, raw_b]
        recipe = await extractor.extract(heterogeneous_cards)

        assert isinstance(recipe, RecipeSchema)
        assert len(recipe.card_ids) == 5
        assert len(recipe.image_urls) == 5
        assert len(recipe.directions) == 5
        assert recipe.directions[0].startswith("1. ")

    @pytest.mark.asyncio
    async def test_gemini_api_payload_6_cards_sequential_labels_and_instructions(self):
        """Gemini API payload structure contains neutral sequential labels for 6 cards."""
        from recipeboxer.ai.client import LLMResponse, UsageTokens
        from recipeboxer.config import get_settings
        from recipeboxer.extraction.image_prep import PreparedFace
        from recipeboxer.extraction.transcribe import (
            FaceTranscript,
            FaceTranscriptList,
            transcribe,
        )

        card_ids = [f"deck_card_{i}.jpg" for i in range(1, 7)]
        valid_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
        faces = [
            PreparedFace(
                face_index=i,
                n_faces=6,
                data=valid_jpeg,
                mime_type="image/jpeg",
                variant="standard",
                long_edge_px=100,
                label=f"Face {i+1} of 6",
            )
            for i in range(6)
        ]

        fake_transcripts = FaceTranscriptList(
            faces=[
                FaceTranscript(face_index=i, text=f"Step {i+1} text")
                for i in range(6)
            ]
        )
        fake_resp = LLMResponse(
            model="gemini-test",
            text="{}",
            parsed=fake_transcripts,
            usage=UsageTokens(input_tokens=100, output_tokens=50),
        )

        class RecordingClient:
            def __init__(self, resp):
                self.resp = resp
                self.requests = []

            async def generate(self, req):
                self.requests.append(req)
                return self.resp

        client = RecordingClient(fake_resp)
        settings = get_settings()

        transcript = await transcribe(faces=faces, llm_client=client, settings=settings)

        assert len(client.requests) == 1
        req = client.requests[0]

        # Verify neutral prompt instructions
        text_parts = [p.text for p in req.parts if hasattr(p, "text") and p.text]
        assert any("each of the 6 recipe card faces" in t for t in text_parts)
        assert any("verbatim in order" in t for t in text_parts)

        # Verify neutral face labels (INV-AC2: zero filename leakage)
        image_parts = [p for p in req.parts if hasattr(p, "label") and p.label]
        assert len(image_parts) == 6
        for idx, p in enumerate(image_parts):
            assert p.label == f"Face {idx + 1} of 6"
            for cid in card_ids:
                assert cid not in p.label

        assert len(transcript.faces) == 6


class LocalPipelineMockClient:
    """Explicit injected mock client for PipelineSession multi-card orchestration."""

    async def analyze_fingerprint(self, image_path: Path, sha256_hash: str) -> VisualFingerprint:
        cid = image_path.name
        return VisualFingerprint(
            card_id=cid,
            image_path=str(image_path),
            sha256_hash=sha256_hash,
            role=CardRole.STANDALONE,
            paper_tone="aged_cream",
            dominant_colors=["#FFF8DC"],
            card_width_px=800,
            card_height_px=500,
            aspect_ratio=1.6,
            confidence=0.9,
        )

    async def verify_correlation(
        self, front_fp: VisualFingerprint, back_fp: VisualFingerprint, heuristic_score: float
    ) -> CorrelationProposal:
        return CorrelationProposal(
            proposal_id=f"prop_{front_fp.card_id}_{back_fp.card_id}",
            front_card_id=front_fp.card_id,
            back_card_id=back_fp.card_id,
            ordered_card_ids=[front_fp.card_id, back_fp.card_id],
            part_count=2,
            transition_scores=[0.8],
            heuristic_score=0.8,
            semantic_score=0.8,
            confidence=0.8,
            is_standalone=False,
            continuity_explanation="Matches well",
            recommended_action="PAIR",
            semantic_verified=True,
        )

    async def extract_cards(self, cards: List[Any]) -> RecipeSchema:
        cids = []
        for c in cards:
            if isinstance(c, VisualFingerprint):
                cids.append(c.card_id)
            elif isinstance(c, ScannedCard) or hasattr(c, "card_id"):
                cids.append(getattr(c, "card_id", "card"))
            elif isinstance(c, (Path, str)):
                cids.append(Path(c).name)
            else:
                cids.append(f"card_{len(cids)+1}")
        return RecipeSchema(
            recipe_id=f"rec_{hashlib.sha256('_'.join(cids).encode()).hexdigest()[:8]}",
            title="Extracted Multi-Card Recipe",
            ingredients=["1 cup sugar", "2 cups flour"],
            directions=[f"Step {i+1} for {cid}" for i, cid in enumerate(cids)],
            card_ids=cids,
            image_urls=[f"/api/cards/{cid}/image" for cid in cids],
            tags=["Dessert"],
        )

    async def extract_recipe(self, front: Any, back: Optional[Any] = None) -> RecipeSchema:
        cards = [front]
        if back is not None:
            cards.append(back)
        return await self.extract_cards(cards)

    async def extract(self, cards: Any) -> RecipeSchema:
        if isinstance(cards, list):
            return await self.extract_cards(cards)
        return await self.extract_cards([cards])


class TestPipelineSessionMultiCardOrchestration:
    """End-to-end PipelineSession integration tests with multi-card sequences."""

    @pytest.fixture
    def local_scans_dir(self, tmp_path: Path) -> Path:
        scans = tmp_path / "scans"
        scans.mkdir(parents=True, exist_ok=True)
        for i in range(1, 7):
            img = Image.new("RGB", (200, 150), color=(i * 35, (7 - i) * 35, i * 40))
            img.save(scans / f"card_{i:02d}.jpg", format="JPEG")
        return scans

    @pytest.fixture
    def local_cache(self, tmp_path: Path) -> FingerprintCache:
        return FingerprintCache(tmp_path / "cache" / "fingerprints_test.db")

    @pytest.fixture
    def local_mock_client(self) -> LocalPipelineMockClient:
        return LocalPipelineMockClient()

    @pytest.mark.asyncio
    async def test_session_multi_card_4_cards_approval_with_batch_tags(
        self,
        local_scans_dir: Path,
        local_mock_client: LocalPipelineMockClient,
        local_cache: FingerprintCache,
    ):
        """PipelineSession executes 4-card sequence approval, preserving order and propagating default batch tags."""
        session = PipelineSession(
            session_id="sess_multi_4card_tags",
            config=PipelineStartRequest(
                scans_dir=str(local_scans_dir),
                mode=IngestionMode.PARALLEL,
                default_tags=["Vintage Holiday", "Heirloom"],
                max_concurrency=4,
            ),
            cache=local_cache,
            extractor=local_mock_client,
            mock_client=local_mock_client,
        )

        task = asyncio.create_task(session.run())

        # Wait until HITL_PENDING
        for _ in range(100):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        all_card_ids = list(session.fingerprints.keys())
        assert len(all_card_ids) >= 4

        # Select 4 cards in inverted order
        selected_4 = [all_card_ids[3], all_card_ids[1], all_card_ids[0], all_card_ids[2]]

        approval = CardPairApproval(
            proposal_id="prop_4card_inverted",
            front_card_id=selected_4[0],
            back_card_id=selected_4[1],
            card_ids=selected_4,
            decision=HITLDecision.APPROVE,
        )
        session.submit_approval([approval])

        await task
        assert session.state == PipelineState.COMPLETED
        assert len(session.recipes) == 1
        rec = session.recipes[0]
        assert rec.card_ids == selected_4
        assert len(rec.image_urls) == 4
        for cid in selected_4:
            assert f"/api/cards/{cid}/image?session_id=sess_multi_4card_tags" in rec.image_urls

        # Verify batch tags attached
        assert "Vintage Holiday" in rec.tags
        assert "Heirloom" in rec.tags

    @pytest.mark.asyncio
    async def test_session_multi_card_reversal_fallback_when_card_ids_empty(
        self,
        local_scans_dir: Path,
        local_mock_client: LocalPipelineMockClient,
        local_cache: FingerprintCache,
    ):
        """PipelineSession handles reverse_roles=True when card_ids is empty, reversing front and back."""
        session = PipelineSession(
            session_id="sess_multi_rev_fallback",
            config=PipelineStartRequest(
                scans_dir=str(local_scans_dir),
                mode=IngestionMode.PARALLEL,
            ),
            cache=local_cache,
            extractor=local_mock_client,
            mock_client=local_mock_client,
        )

        task = asyncio.create_task(session.run())

        for _ in range(100):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        all_card_ids = list(session.fingerprints.keys())

        c_front = all_card_ids[0]
        c_back = all_card_ids[1]

        # Submit approval with reverse_roles=True and no explicit card_ids list
        approval = CardPairApproval(
            proposal_id="prop_rev_roles",
            front_card_id=c_front,
            back_card_id=c_back,
            reverse_roles=True,
            decision=HITLDecision.APPROVE,
        )
        session.submit_approval([approval])

        await task
        assert session.state == PipelineState.COMPLETED
        assert len(session.recipes) == 1
        rec = session.recipes[0]
        # Should be [c_back, c_front]
        assert rec.card_ids == [c_back, c_front]

    @pytest.mark.asyncio
    async def test_session_mixed_multi_card_standalone_and_rejection_batch(
        self,
        local_scans_dir: Path,
        local_mock_client: LocalPipelineMockClient,
        local_cache: FingerprintCache,
    ):
        """PipelineSession processes a batch containing 3-card group, standalone card, and rejected card."""
        session = PipelineSession(
            session_id="sess_mixed_batch",
            config=PipelineStartRequest(
                scans_dir=str(local_scans_dir),
                mode=IngestionMode.PARALLEL,
            ),
            cache=local_cache,
            extractor=local_mock_client,
            mock_client=local_mock_client,
        )

        task = asyncio.create_task(session.run())

        for _ in range(100):
            if session.state == PipelineState.HITL_PENDING:
                break
            await asyncio.sleep(0.02)

        assert session.state == PipelineState.HITL_PENDING
        all_card_ids = list(session.fingerprints.keys())
        assert len(all_card_ids) >= 5

        # 1. 3-card group
        app1 = CardPairApproval(
            proposal_id="prop_3card",
            front_card_id=all_card_ids[0],
            back_card_id=all_card_ids[1],
            card_ids=[all_card_ids[0], all_card_ids[1], all_card_ids[2]],
            decision=HITLDecision.APPROVE,
        )

        # 2. Standalone card
        app2 = CardPairApproval(
            proposal_id="prop_standalone",
            front_card_id=all_card_ids[3],
            back_card_id=None,
            card_ids=[all_card_ids[3]],
            is_standalone=True,
            decision=HITLDecision.APPROVE,
        )

        # 3. Rejected proposal
        app3 = CardPairApproval(
            proposal_id="prop_rejected",
            front_card_id=all_card_ids[4],
            back_card_id=None,
            is_rejected=True,
            decision=HITLDecision.REJECT,
        )

        session.submit_approval([app1, app2, app3], rejected_ids=[all_card_ids[4]])

        await task
        assert session.state == PipelineState.COMPLETED
        assert len(session.recipes) == 2
        assert session.recipes[0].card_ids == [all_card_ids[0], all_card_ids[1], all_card_ids[2]]
        assert session.recipes[1].card_ids == [all_card_ids[3]]
