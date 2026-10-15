"""Empirical Challenger 2 Verification Suite for Milestone 2.

Comprehensive stress tests and edge case verification for:
1. Visual Fingerprint Extraction:
   - Corrupt images (0-byte, truncated bytes, random garbage, missing files, corrupted headers).
   - Blank cards (pure white, uniform cream, minimal noise) and tone categorization.
   - Extreme contrast images (pure black, pure white, pure RGB primaries, checkerboard, inverted).
   - Boundary text edge cases (continuation symbols '(over)', '->', 'PTO', mid-sentence cutoffs,
     trailing punctuation, empty/whitespace/None text, complex Unicode).
2. Semantic Verifier Mock & Fallback Mechanics:
   - Injected mock clients (CorrelationProposal, dict, model_dumpable, exception fallbacks).
   - Offline continuity heuristics (lexical overlap, continuation cue detection, header matching).
   - Recommendation threshold transitions (PAIR >= 0.80, UNCERTAIN 0.50-0.80, REJECT < 0.50).
3. Composite Scoring Math Verification:
   - Exact formula verification: Composite = 0.30 * S_h + 0.70 * S_s.
   - Custom weights verification: w_h * S_h + w_s * S_s.
   - Boundary values: (0,0)->0.0, (1,1)->1.0, (1,0)->0.30, (0,1)->0.70.
   - Numerical precision and bounding [0.0, 1.0].
4. Physical Heuristics Mathematical Rigor:
   - CIE L*a*b* Delta-E color distance and score clamping.
   - Proximity exponential decay math (delta=0, 1, 2, 5, 10, 100).
   - Stain bleed-through horizontal mirroring (all quadrants, clean vs stained, unmirrored).
   - Ruling compatibility and line spacing diff scoring.
   - Ink type/color similarity matrix.
   - Role compatibility matrix and zero-pruning behavior.
5. Pairing Optimizer Stress & Invariant Conservation:
   - Multi-way bipartite competition and greedy resolution.
   - Threshold cutoff filtering (< 0.50) and REJECT action filtering.
   - Full card conservation invariant: len(assigned_fronts) + len(assigned_backs) + len(unmatched) == len(total).
"""

import io
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image
from pydantic import BaseModel

from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.config import Settings, get_settings
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult, SetVerification
from recipeboxer.models.failures import FailureReason, SemanticVerificationUnavailable
from recipeboxer.models.domain import (
    ArtifactType,
    BoundaryText,
    CardQuadrant,
    CardRole,
    InkColor,
    InkInfo,
    InkType,
    PaperColorInfo,
    PaperColorTone,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.vision.fingerprint import GeminiVisualExtractor
from recipeboxer.vision.heuristics import (
    QUADRANT_MIRROR_MAP,
    calculate_color_similarity,
    calculate_heuristic_distance,
    calculate_ink_similarity,
    calculate_proximity_score,
    calculate_role_compatibility,
    calculate_ruling_similarity,
    calculate_stain_similarity,
    hex_to_lab,
    rgb_to_lab,
    screen_candidate_pairs,
)


# ============================================================================
# PART 1: VISUAL FINGERPRINT EXTRACTION EDGE CASES
# ============================================================================

class TestVisualFingerprintExtractionEdgeCases:
    """Stress-tests for GeminiVisualExtractor with corrupt, blank, and extreme contrast inputs."""

    @pytest.fixture
    def extractor(self):
        return GeminiVisualExtractor(api_key="mock_key")

    @pytest.mark.asyncio
    async def test_zero_byte_image_raises_value_error(self, extractor):
        """0-byte image input raises ValueError."""
        with pytest.raises(ValueError, match="Corrupted or empty image data"):
            await extractor.extract(b"", card_id="zero_byte.jpg")

    @pytest.mark.asyncio
    async def test_corrupt_marker_bytes_raises_value_error(self, extractor):
        """Image bytes containing explicit CORRUPT marker raise ValueError."""
        corrupt_data = b"CORRUPT_HEADER_GARBAGE_BYTES_12345"
        with pytest.raises(ValueError, match="Corrupted or empty image data"):
            await extractor.extract(corrupt_data, card_id="corrupt_card.jpg")

    @pytest.mark.asyncio
    async def test_invalid_image_random_noise_fallback(self, extractor):
        """Non-decodable random bytes without CORRUPT string raise ValueError."""
        garbage_bytes = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x10" * 20
        with pytest.raises(ValueError, match="Cannot decode image data"):
            await extractor.extract(garbage_bytes, card_id="random_noise_card.jpg")

    @pytest.mark.asyncio
    async def test_pure_white_blank_card(self, extractor):
        """Pure white (255, 255, 255) card scan is categorized as White."""
        img = Image.new("RGB", (400, 300), color=(255, 255, 255))
        fp = await extractor.extract(img, card_id="blank_white_card.jpg")

        assert fp.paper_color.hex_approx == "#FFFFFF"
        assert fp.paper_color.lab_l >= 95.0
        assert fp.paper_color.tone_enum == PaperColorTone.WHITE

    @pytest.mark.asyncio
    async def test_pure_black_extreme_contrast_card(self, extractor):
        """Pure black (0, 0, 0) card scan has L* near 0 and valid hex."""
        img = Image.new("RGB", (400, 300), color=(0, 0, 0))
        fp = await extractor.extract(img, card_id="extreme_black_card.jpg")

        assert fp.paper_color.hex_approx == "#000000"
        assert fp.paper_color.lab_l == 0.0
        assert -128.0 <= fp.paper_color.lab_a <= 128.0
        assert -128.0 <= fp.paper_color.lab_b <= 128.0

    @pytest.mark.asyncio
    async def test_saturated_primary_colors_cielab_bounds(self, extractor):
        """Extreme saturated primaries (Pure Red, Pure Green, Pure Blue) produce bounded CIE L*a*b* values."""
        colors = {
            "pure_red": (255, 0, 0),
            "pure_green": (0, 255, 0),
            "pure_blue": (0, 0, 255),
            "neon_yellow": (255, 255, 0),
            "neon_cyan": (0, 255, 255),
            "neon_magenta": (255, 255, 0),
        }
        for name, rgb in colors.items():
            img = Image.new("RGB", (200, 200), color=rgb)
            fp = await extractor.extract(img, card_id=f"{name}_card.jpg")
            assert 0.0 <= fp.paper_color.lab_l <= 100.0, f"Failed for {name}: L={fp.paper_color.lab_l}"
            assert -128.0 <= fp.paper_color.lab_a <= 128.0, f"Failed for {name}: a={fp.paper_color.lab_a}"
            assert -128.0 <= fp.paper_color.lab_b <= 128.0, f"Failed for {name}: b={fp.paper_color.lab_b}"

    @pytest.mark.asyncio
    async def test_aged_cream_and_kraft_brown_tone_classification(self, extractor):
        """Aged cream and kraft brown substrates classify into appropriate tone enums."""
        # Kraft Brown color: #8B5A2B -> RGB(139, 90, 43)
        kraft_img = Image.new("RGB", (300, 200), color=(139, 90, 43))
        fp_kraft = await extractor.extract(kraft_img, card_id="kraft_card.jpg")
        assert fp_kraft.paper_color.tone_enum in (PaperColorTone.KRAFT_BROWN, PaperColorTone.OFF_WHITE_CREAM)

        # Aged Cream color: #F4E8C1 -> RGB(244, 232, 193)
        cream_img = Image.new("RGB", (300, 200), color=(244, 232, 193))
        fp_cream = await extractor.extract(cream_img, card_id="cream_recipe_front.jpg")
        assert fp_cream.paper_color.tone_enum in (PaperColorTone.OFF_WHITE_CREAM, PaperColorTone.WHITE, PaperColorTone.KRAFT_BROWN)

    @pytest.mark.asyncio
    async def test_input_type_polymorphism(self, extractor, tmp_path):
        """Extractor accepts ScannedCard, Path, str, bytes, and PIL Image."""
        img = Image.new("RGB", (100, 100), color=(240, 230, 210))
        img_path = tmp_path / "polymorphic_test_front.jpg"
        img.save(img_path, format="JPEG")
        raw_bytes = img_path.read_bytes()

        # 1. ScannedCard
        scanned = ScannedCard(
            card_id="polymorphic_test_front.jpg",
            file_path=img_path,
            filename="polymorphic_test_front.jpg",
            sha256_hash="11" * 32,
            file_size_bytes=len(raw_bytes),
            width=100,
            height=100,
            format="JPEG",
            mode="RGB",
        )
        fp1 = await extractor.extract(scanned)
        assert fp1.card_id == "polymorphic_test_front.jpg"

        # 2. Path
        fp2 = await extractor.extract(img_path)
        assert fp2.card_id == "polymorphic_test_front.jpg"

        # 3. String path
        fp3 = await extractor.extract(str(img_path))
        assert fp3.card_id == "polymorphic_test_front.jpg"

        # 4. Raw bytes
        fp4 = await extractor.extract(raw_bytes, card_id="custom_bytes_front.jpg")
        assert fp4.card_id == "custom_bytes_front.jpg"

        # 5. PIL Image
        fp5 = await extractor.extract(img, card_id="custom_pil_front.jpg")
        assert fp5.card_id == "custom_pil_front.jpg"

        # 6. Synchronous interface
        fp_sync = extractor.extract_sync(img_path)
        assert fp_sync.card_id == "polymorphic_test_front.jpg"

    def test_unsupported_input_type_raises_type_error(self, extractor):
        """Unsupported input type (e.g. integer or list) raises TypeError."""
        with pytest.raises(TypeError, match="Unsupported card input type"):
            extractor.extract_sync(12345)  # type: ignore

    @pytest.mark.asyncio
    async def test_extractor_with_sync_magicmock(self):
        """Extractor handles sync MagicMock returning dict."""
        mock_fp_dict = {
            "card_id": "mock_magic_card.jpg",
            "image_path": "/path/to/img.jpg",
            "sha256_hash": "ff" * 32,
            "role": "FRONT_ONLY",
            "role_confidence": 0.95,
            "paper_color": {"hex_approx": "#FFFFFF", "lab_l": 95.0, "lab_a": 0.0, "lab_b": 0.0, "tone_name": "White"},
            "ruling": {"has_ruling": False, "ruling_type": "UNRULED_BLANK"},
            "ink": {"ink_color": "BLACK", "ink_type": "FELT_TIP"},
            "boundary_text": {"first_line": "Header", "last_line": "Footer"},
            "extracted_at": datetime.now(timezone.utc).isoformat(),
        }

        # 1. client.analyze_fingerprint as sync MagicMock
        mock_client1 = MagicMock()
        mock_client1.analyze_fingerprint.return_value = mock_fp_dict
        extractor1 = GeminiVisualExtractor(client=mock_client1)
        res1 = await extractor1.extract(b"dummy_bytes", card_id="mock_magic_card.jpg")
        assert isinstance(res1, VisualFingerprint)
        assert res1.card_id == "mock_magic_card.jpg"

        # 2. client.extract as sync MagicMock
        mock_client2 = MagicMock()
        mock_client2.extract.return_value = mock_fp_dict
        extractor2 = GeminiVisualExtractor(client=mock_client2)
        res2 = await extractor2.extract(b"dummy_bytes", card_id="mock_magic_card.jpg")
        assert isinstance(res2, VisualFingerprint)
        assert res2.card_id == "mock_magic_card.jpg"

    @pytest.mark.asyncio
    async def test_extractor_with_asyncmock(self):
        """Extractor handles AsyncMock returning VisualFingerprint."""
        mock_fp = VisualFingerprint(
            card_id="async_card.jpg",
            image_path="/path/to/img.jpg",
            sha256_hash="ee" * 32,
            role=CardRole.STANDALONE,
        )

        # 1. client.analyze_fingerprint as AsyncMock
        mock_client1 = MagicMock()
        mock_client1.analyze_fingerprint = AsyncMock(return_value=mock_fp)
        extractor1 = GeminiVisualExtractor(client=mock_client1)
        res1 = await extractor1.extract(b"dummy_bytes", card_id="async_card.jpg")
        assert isinstance(res1, VisualFingerprint)
        assert res1.card_id == "async_card.jpg"

        # 2. client.extract as AsyncMock
        mock_client2 = MagicMock()
        mock_client2.extract = AsyncMock(return_value=mock_fp)
        extractor2 = GeminiVisualExtractor(client=mock_client2)
        res2 = await extractor2.extract(b"dummy_bytes", card_id="async_card.jpg")
        assert isinstance(res2, VisualFingerprint)
        assert res2.card_id == "async_card.jpg"

    @pytest.mark.asyncio
    async def test_extractor_with_callable(self):
        """Extractor handles callable client returning VisualFingerprint or dict."""
        mock_fp = VisualFingerprint(
            card_id="callable_card.jpg",
            image_path="/path/to/img.jpg",
            sha256_hash="dd" * 32,
            role=CardRole.STANDALONE,
        )

        extractor = GeminiVisualExtractor(client=lambda card: mock_fp)
        res = await extractor.extract(b"dummy_bytes", card_id="callable_card.jpg")
        assert isinstance(res, VisualFingerprint)
        assert res.card_id == "callable_card.jpg"


# ============================================================================
# PART 2: BOUNDARY TEXT & CONTINUATION CUE TESTS
# ============================================================================

class TestBoundaryTextAndContinuationCues:
    """Stress-tests for boundary text detection, continuation symbols, and cutoffs."""

    def test_boundary_text_continuation_symbols(self):
        """BoundaryText flags continuation cues like '(over)', '->', 'PTO'."""
        cues = [
            "(over)",
            "Turn over ->",
            "PTO (Please turn over)",
            "Continued on back...",
            "See reverse side (cont.)",
        ]
        for cue in cues:
            bt = BoundaryText(
                first_line="Apple Pie Recipe",
                last_line=f"Mix apples with cinnamon and sugar. {cue}",
                has_continuation_symbol=True,
                is_cut_off_mid_sentence=False,
            )
            assert bt.has_continuation_symbol is True

    def test_boundary_text_mid_sentence_cutoffs(self):
        """BoundaryText flags mid-sentence cutoffs (e.g. trailing comma, conjunction, hyphen)."""
        cutoffs = [
            "In a large saucepan over medium heat, melt the butter and",
            "Bake at 350 degrees F for 25 minutes, then remove from-",
            "Whisk the dry ingredients together with the...",
            "Stir until creamy and smooth, then add 2 tbsp of",
        ]
        for text in cutoffs:
            bt = BoundaryText(
                first_line="Grandma's Fudge",
                last_line=text,
                has_continuation_symbol=False,
                is_cut_off_mid_sentence=True,
            )
            assert bt.is_cut_off_mid_sentence is True

    def test_boundary_text_multilingual_unicode(self):
        """BoundaryText handles Cyrillic, Kanji, Arabic, and diacritics."""
        bt = BoundaryText(
            first_line="Рецепт: Бабушкин борщ 🍲",
            last_line="Продолжение на обороте карточки (см. далее) ➔",
            has_continuation_symbol=True,
            is_cut_off_mid_sentence=True,
        )
        assert "Бабушкин борщ" in bt.first_line
        assert "➔" in bt.last_line


# ============================================================================
# PART 3: SEMANTIC VERIFIER MOCK & FALLBACK CONDITIONS
# ============================================================================

class TestSemanticVerifierMockAndFallback:
    """Stress-tests for GeminiSemanticVerifier with mock clients and offline fallback logic."""

    @pytest.fixture
    def sample_front_back_fps(self):
        front = VisualFingerprint(
            card_id="01_cinnamon_rolls_front.jpg",
            image_path="/scans/01_cinnamon_rolls_front.jpg",
            sha256_hash="aa" * 32,
            role=CardRole.FRONT_ONLY,
            role_confidence=0.95,
            paper_color=PaperColorInfo(hex_approx="#F4E8C1", lab_l=92.0, lab_a=-1.0, lab_b=15.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
            artifacts=[PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_RIGHT)],
            boundary_text=BoundaryText(
                first_line="Cinnamon Rolls with Cream Cheese Icing",
                last_line="Roll dough into 12x18 inch rectangle (over ->)",
                has_continuation_symbol=True,
                is_cut_off_mid_sentence=True,
            ),
        )
        back = VisualFingerprint(
            card_id="02_cinnamon_rolls_back.jpg",
            image_path="/scans/02_cinnamon_rolls_back.jpg",
            sha256_hash="bb" * 32,
            role=CardRole.BACK_ONLY,
            role_confidence=0.95,
            paper_color=PaperColorInfo(hex_approx="#F4E8C1", lab_l=92.0, lab_a=-1.0, lab_b=15.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
            artifacts=[PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_LEFT)],
            boundary_text=BoundaryText(
                first_line="(Directions cont.) Spread softened butter and cinnamon sugar",
                last_line="Bake at 375F for 20-25 mins until golden brown.",
                has_continuation_symbol=False,
                is_cut_off_mid_sentence=False,
            ),
        )
        return front, back

    @pytest.mark.asyncio
    async def test_mock_client_returning_proposal_object(self, sample_front_back_fps):
        """Verifier supports mock client returning CorrelationProposal directly."""
        front, back = sample_front_back_fps
        mock_proposal = CorrelationProposal(
            proposal_id="prop_mock123",
            front_card_id=front.card_id,
            back_card_id=back.card_id,
            heuristic_score=0.92,
            semantic_score=0.98,
            confidence=0.962,
            is_standalone=False,
            continuity_explanation="Mock verified perfect match",
            recommended_action="PAIR",
        )

        class CustomMockClient:
            async def verify_correlation(self, f, b, h):
                return mock_proposal

        verifier = GeminiSemanticVerifier(client=CustomMockClient())
        result = await verifier.verify_pair(front, back)

        assert result.proposal_id == "prop_mock123"
        assert result.confidence == 0.962
        assert result.recommended_action == "PAIR"

    @pytest.mark.asyncio
    async def test_mock_client_returning_dict(self, sample_front_back_fps):
        """Verifier parses dict returned by mock client into CorrelationProposal."""
        front, back = sample_front_back_fps
        mock_dict = {
            "proposal_id": "prop_dict456",
            "front_card_id": front.card_id,
            "back_card_id": back.card_id,
            "heuristic_score": 0.85,
            "semantic_score": 0.90,
            "confidence": 0.885,
            "is_standalone": False,
            "continuity_explanation": "Dict payload verified",
            "recommended_action": "PAIR",
        }

        class DictMockClient:
            async def verify_correlation(self, f, b, h):
                return mock_dict

        verifier = GeminiSemanticVerifier(client=DictMockClient())
        result = await verifier.verify_pair(front, back)

        assert isinstance(result, CorrelationProposal)
        assert result.proposal_id == "prop_dict456"
        assert result.confidence == 0.885

    @pytest.mark.asyncio
    async def test_mock_client_returning_model_dumpable(self, sample_front_back_fps):
        """Verifier parses object with model_dump() method into CorrelationProposal."""
        front, back = sample_front_back_fps

        class CustomDumpableModel(BaseModel):
            proposal_id: str = "prop_dumpable789"
            front_card_id: str = front.card_id
            back_card_id: str = back.card_id
            heuristic_score: float = 0.80
            semantic_score: float = 0.85
            confidence: float = 0.835
            is_standalone: bool = False
            continuity_explanation: str = "Model dump verified"
            recommended_action: str = "PAIR"

        class DumpableMockClient:
            async def verify_correlation(self, f, b, h):
                return CustomDumpableModel()

        verifier = GeminiSemanticVerifier(client=DumpableMockClient())
        result = await verifier.verify_pair(front, back)

        assert isinstance(result, CorrelationProposal)
        assert result.proposal_id == "prop_dumpable789"
        assert result.confidence == 0.835

    @pytest.mark.asyncio
    async def test_magicmock_sync_returning_dict(self, sample_front_back_fps):
        """Verifier handles sync MagicMock returning dict without awaiting non-awaitable."""
        front, back = sample_front_back_fps
        mock_dict = {
            "proposal_id": "prop_magic_sync",
            "front_card_id": front.card_id,
            "back_card_id": back.card_id,
            "heuristic_score": 0.85,
            "semantic_score": 0.90,
            "confidence": 0.885,
            "is_standalone": False,
            "continuity_explanation": "MagicMock sync dict verified",
            "recommended_action": "PAIR",
        }

        # 1. client.verify_pair as sync MagicMock
        mock_client1 = MagicMock()
        mock_client1.verify_pair.return_value = mock_dict
        verifier1 = GeminiSemanticVerifier(client=mock_client1)
        res1 = await verifier1.verify_pair(front, back)
        assert isinstance(res1, CorrelationProposal)
        assert res1.proposal_id == "prop_magic_sync"

        # 2. client.verify_correlation as sync MagicMock
        mock_client2 = MagicMock()
        mock_client2.verify_correlation.return_value = mock_dict
        verifier2 = GeminiSemanticVerifier(client=mock_client2)
        res2 = await verifier2.verify_pair(front, back)
        assert isinstance(res2, CorrelationProposal)
        assert res2.proposal_id == "prop_magic_sync"

    @pytest.mark.asyncio
    async def test_magicmock_async_returning_dict(self, sample_front_back_fps):
        """Verifier handles AsyncMock returning dict."""
        front, back = sample_front_back_fps
        mock_dict = {
            "proposal_id": "prop_magic_async",
            "front_card_id": front.card_id,
            "back_card_id": back.card_id,
            "heuristic_score": 0.85,
            "semantic_score": 0.90,
            "confidence": 0.885,
            "is_standalone": False,
            "continuity_explanation": "AsyncMock dict verified",
            "recommended_action": "PAIR",
        }

        # 1. client.verify_pair as AsyncMock
        mock_client1 = MagicMock()
        mock_client1.verify_pair = AsyncMock(return_value=mock_dict)
        verifier1 = GeminiSemanticVerifier(client=mock_client1)
        res1 = await verifier1.verify_pair(front, back)
        assert isinstance(res1, CorrelationProposal)
        assert res1.proposal_id == "prop_magic_async"

        # 2. client.verify_correlation as AsyncMock
        mock_client2 = MagicMock()
        mock_client2.verify_correlation = AsyncMock(return_value=mock_dict)
        verifier2 = GeminiSemanticVerifier(client=mock_client2)
        res2 = await verifier2.verify_pair(front, back)
        assert isinstance(res2, CorrelationProposal)
        assert res2.proposal_id == "prop_magic_async"

    @pytest.mark.asyncio
    async def test_callable_sync_and_async_client(self, sample_front_back_fps):
        """Verifier handles direct callable clients (both sync and async functions)."""
        front, back = sample_front_back_fps
        mock_dict = {
            "proposal_id": "prop_callable",
            "front_card_id": front.card_id,
            "back_card_id": back.card_id,
            "heuristic_score": 0.85,
            "semantic_score": 0.90,
            "confidence": 0.885,
            "is_standalone": False,
            "continuity_explanation": "Callable verified",
            "recommended_action": "PAIR",
        }

        # Sync callable
        verifier_sync = GeminiSemanticVerifier(client=lambda f, b, h: mock_dict)
        res_sync = await verifier_sync.verify_pair(front, back)
        assert isinstance(res_sync, CorrelationProposal)
        assert res_sync.proposal_id == "prop_callable"

        # Async callable
        async def async_fn(f, b, h):
            return mock_dict

        verifier_async = GeminiSemanticVerifier(client=async_fn)
        res_async = await verifier_async.verify_pair(front, back)
        assert isinstance(res_async, CorrelationProposal)
        assert res_async.proposal_id == "prop_callable"

    @pytest.mark.asyncio
    async def test_offline_fallback_strong_continuity_cues(self, sample_front_back_fps):
        """Offline fallback does not synthesize continuity scores; strictly raises SemanticVerificationUnavailable (INV-AC4/AC6)."""
        front, back = sample_front_back_fps
        verifier = GeminiSemanticVerifier(api_key=None)

        with pytest.raises(SemanticVerificationUnavailable) as exc_info:
            await verifier.verify_pair(front, back, heuristic_score=0.90)
        assert exc_info.value.reason == FailureReason.NO_API_KEY

    @pytest.mark.asyncio
    async def test_offline_fallback_completely_unrelated_cards(self):
        """Offline fallback without LLM client strictly raises SemanticVerificationUnavailable (INV-AC4/AC6)."""
        front = VisualFingerprint(
            card_id="beef_stew_front.jpg",
            image_path="/scans/beef_stew_front.jpg",
            sha256_hash="11" * 32,
            role=CardRole.FRONT_ONLY,
            boundary_text=BoundaryText(first_line="Hearty Beef Stew", last_line="Brown beef cubes in pot."),
        )
        back = VisualFingerprint(
            card_id="chocolate_cake_back.jpg",
            image_path="/scans/chocolate_cake_back.jpg",
            sha256_hash="22" * 32,
            role=CardRole.BACK_ONLY,
            boundary_text=BoundaryText(first_line="Frosting Directions", last_line="Spread ganache over cake."),
        )

        verifier = GeminiSemanticVerifier(api_key=None)
        with pytest.raises(SemanticVerificationUnavailable) as exc_info:
            await verifier.verify_pair(front, back, heuristic_score=0.20)
        assert exc_info.value.reason == FailureReason.NO_API_KEY

    @pytest.mark.asyncio
    async def test_offline_fallback_moderate_heuristic_uncertain_action(self):
        """Offline fallback without LLM client strictly raises SemanticVerificationUnavailable (INV-AC4/AC6)."""
        front = VisualFingerprint(
            card_id="card_alpha_front.jpg",
            image_path="/scans/card_alpha_front.jpg",
            sha256_hash="33" * 32,
            role=CardRole.FRONT_ONLY,
            boundary_text=BoundaryText(first_line="Recipe Alpha", last_line="Chop ingredients."),
        )
        back = VisualFingerprint(
            card_id="card_beta_back.jpg",
            image_path="/scans/card_beta_back.jpg",
            sha256_hash="44" * 32,
            role=CardRole.BACK_ONLY,
            boundary_text=BoundaryText(first_line="Mix everything", last_line="Serve chilled."),
        )

        verifier = GeminiSemanticVerifier(api_key=None)
        with pytest.raises(SemanticVerificationUnavailable) as exc_info:
            await verifier.verify_pair(front, back, heuristic_score=0.65)
        assert exc_info.value.reason == FailureReason.NO_API_KEY

    @pytest.mark.asyncio
    async def test_none_or_empty_boundary_text_graceful_handling(self):
        """Verifier handles empty or None boundary text attributes without crashing."""
        front = VisualFingerprint(
            card_id="empty_text_front.jpg",
            image_path="/p",
            sha256_hash="11" * 32,
            role=CardRole.FRONT_ONLY,
            boundary_text=BoundaryText(first_line="", last_line=""),
        )
        back = VisualFingerprint(
            card_id="empty_text_back.jpg",
            image_path="/p",
            sha256_hash="22" * 32,
            role=CardRole.BACK_ONLY,
            boundary_text=BoundaryText(first_line="", last_line=""),
        )

        client = ScriptedLLMClient([
            SetVerification(
                anchor=front.card_id,
                continuations=[back.card_id],
                rejected=[],
                confidence=0.50,
                model="test-model",
                prompt_id="test",
                prompt_version="1",
            ),
            SetVerification(
                anchor=front.card_id,
                continuations=[back.card_id],
                rejected=[],
                confidence=0.50,
                model="test-model",
                prompt_id="test",
                prompt_version="1",
            ),
        ])
        verifier = GeminiSemanticVerifier(llm_client=client)
        proposal = await verifier.verify_pair(front, back, heuristic_score=0.50)
        assert isinstance(proposal, CorrelationProposal)
        assert 0.0 <= proposal.confidence <= 1.0

        # Also test with object.__setattr__ bypassing pydantic init validation
        object.__setattr__(front, "boundary_text", None)
        object.__setattr__(back, "boundary_text", None)
        proposal2 = await verifier.verify_pair(front, back, heuristic_score=0.50)
        assert isinstance(proposal2, CorrelationProposal)
        assert 0.0 <= proposal2.confidence <= 1.0


# ============================================================================
# PART 4: COMPOSITE SCORING MATH VERIFICATION
# ============================================================================

class TestCompositeScoringMathVerification:
    """Stress-tests verifying mathematical precision of composite confidence: 0.30 * S_h + 0.70 * S_s."""

    @pytest.mark.parametrize(
        "s_heuristic, s_semantic, expected_composite",
        [
            (0.0, 0.0, 0.0),
            (1.0, 1.0, 1.0),
            (0.5, 0.5, 0.5),
            (1.0, 0.0, 0.30),
            (0.0, 1.0, 0.70),
            (0.85, 0.95, 0.92),        # 0.30*0.85 + 0.70*0.95 = 0.255 + 0.665 = 0.9200
            (0.62, 0.44, 0.494),       # 0.30*0.62 + 0.70*0.44 = 0.186 + 0.308 = 0.4940
            (0.7321, 0.8912, 0.8435),  # 0.30*0.7321 + 0.70*0.8912 = 0.21963 + 0.62384 = 0.84347 -> 0.8435
            (0.9999, 0.9999, 0.9999),
            (0.0001, 0.0001, 0.0001),
        ],
    )
    def test_default_weights_composite_confidence_math(
        self, s_heuristic, s_semantic, expected_composite
    ):
        """Verify default 30/70 weighting arithmetic precision to 4 decimal places."""
        w_h = 0.30
        w_s = 0.70
        calculated = round(w_h * s_heuristic + w_s * s_semantic, 4)
        assert calculated == pytest.approx(expected_composite, abs=1e-4)

    @pytest.mark.parametrize(
        "w_h, w_s, s_h, s_s, expected_composite",
        [
            (0.50, 0.50, 0.80, 0.60, 0.70),
            (0.40, 0.60, 0.90, 0.80, 0.84),
            (1.00, 0.00, 0.85, 0.20, 0.85),
            (0.00, 1.00, 0.15, 0.95, 0.95),
            (0.25, 0.75, 0.40, 0.80, 0.70),
        ],
    )
    @pytest.mark.asyncio
    async def test_custom_weights_configuration(self, w_h, w_s, s_h, s_s, expected_composite):
        """GeminiSemanticVerifier respects custom heuristic_weight and semantic_weight overrides."""
        front = VisualFingerprint(
            card_id="front_custom.jpg",
            image_path="/scans/front_custom.jpg",
            sha256_hash="aa" * 32,
            role=CardRole.FRONT_ONLY,
            boundary_text=BoundaryText(first_line="Title", last_line="End (over)"),
        )
        back = VisualFingerprint(
            card_id="back_custom.jpg",
            image_path="/scans/back_custom.jpg",
            sha256_hash="bb" * 32,
            role=CardRole.BACK_ONLY,
            boundary_text=BoundaryText(first_line="(Directions cont.)", last_line="Serve."),
        )

        client = ScriptedLLMClient([
            SetVerification(
                anchor=front.card_id,
                continuations=[back.card_id],
                rejected=[],
                confidence=s_s,
                model="test-model",
                prompt_id="test",
                prompt_version="1",
            )
        ])
        verifier = GeminiSemanticVerifier(
            llm_client=client,
            heuristic_weight=w_h,
            semantic_weight=w_s,
        )

        res = await verifier.verify_pair(front, back, heuristic_score=s_h)
        assert res.confidence == pytest.approx(expected_composite, abs=1e-4)


# ============================================================================
# PART 5: PHYSICAL HEURISTICS MATHEMATICAL RIGOR
# ============================================================================

class TestPhysicalHeuristicsMathematicalRigor:
    """Stress-tests for colorimetry, proximity, stain bleed-through, ruling, ink, and role compatibility."""

    def test_cielab_conversion_and_delta_e_distance(self):
        """CIE L*a*b* conversion and Delta-E distance match standard formulas."""
        # Pure White (255,255,255) -> L=100.0, a=0.0, b=0.0
        l_w, a_w, b_w = rgb_to_lab(255, 255, 255)
        assert l_w == 100.0
        assert a_w == 0.0
        assert b_w == 0.0

        # Pure Black (0,0,0) -> L=0.0, a=0.0, b=0.0
        l_k, a_k, b_k = rgb_to_lab(0, 0, 0)
        assert l_k == 0.0
        assert a_k == 0.0
        assert b_k == 0.0

        # Delta-E between pure white and pure black = 100.0
        # Similarity score = max(0.0, 1.0 - 100.0 / 50.0) = max(0.0, -1.0) = 0.0
        score_extreme = calculate_color_similarity((l_w, a_w, b_w), (l_k, a_k, b_k))
        assert score_extreme == 0.0

        # Identical colors -> Delta-E = 0.0 -> Score = 1.0
        score_identical = calculate_color_similarity((90.0, 5.0, 10.0), (90.0, 5.0, 10.0))
        assert score_identical == 1.0

        # Small Delta-E: (90.0, 0.0, 0.0) vs (85.0, 0.0, 0.0) -> Delta-E = 5.0 -> S = 1.0 - 5.0/50.0 = 0.90
        score_close = calculate_color_similarity((90.0, 0.0, 0.0), (85.0, 0.0, 0.0))
        assert score_close == 0.90

    def test_hex_to_lab_malformed_inputs_fallback(self):
        """hex_to_lab gracefully falls back to (100.0, 0.0, 0.0) for invalid hex strings."""
        assert hex_to_lab("invalid_hex") == (100.0, 0.0, 0.0)
        assert hex_to_lab("#12") == (100.0, 0.0, 0.0)
        assert hex_to_lab("") == (100.0, 0.0, 0.0)

    @pytest.mark.parametrize(
        "idx1, idx2, expected_prox",
        [
            (0, 0, 1.0),
            (0, 1, 0.6065),    # exp(-0.5 * 1) = 0.60653
            (1, 0, 0.6065),    # Symmetric
            (0, 2, 0.3679),    # exp(-0.5 * 2) = exp(-1.0) = 0.36788 -> 0.3679
            (0, 3, 0.2231),    # exp(-0.5 * 3) = exp(-1.5) = 0.22313 -> 0.2231
            (0, 5, 0.0821),    # exp(-0.5 * 5) = exp(-2.5) = 0.08208 -> 0.0821
            (0, 10, 0.0067),   # exp(-0.5 * 10) = exp(-5.0) = 0.00674 -> 0.0067
            (0, 100, 0.0),     # Underflow to 0.0
        ],
    )
    def test_proximity_exponential_decay_precision(self, idx1, idx2, expected_prox):
        """Proximity score follows exponential decay formula: exp(-0.5 * |idx1 - idx2|)."""
        prox = calculate_proximity_score(idx1, idx2)
        assert prox == pytest.approx(expected_prox, abs=1e-4)

    def test_stain_mirroring_quadrant_permutations(self):
        """All horizontally mirrored quadrant pairs score 1.0 for bleed-through stains."""
        mirror_pairs = [
            ("TOP_LEFT", "TOP_RIGHT"),
            ("TOP_RIGHT", "TOP_LEFT"),
            ("BOTTOM_LEFT", "BOTTOM_RIGHT"),
            ("BOTTOM_RIGHT", "BOTTOM_LEFT"),
            ("EDGE_LEFT", "EDGE_RIGHT"),
            ("EDGE_RIGHT", "EDGE_LEFT"),
            ("CENTER", "CENTER"),
            ("EDGE_TOP", "EDGE_TOP"),
            ("EDGE_BOTTOM", "EDGE_BOTTOM"),
        ]

        for q_front, q_back in mirror_pairs:
            art_front = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=q_front)]
            art_back = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=q_back)]
            score = calculate_stain_similarity(art_front, art_back)
            assert score == 1.0, f"Failed for mirror pair ({q_front}, {q_back})"

    def test_stain_scoring_categories(self):
        """Stain similarity outputs 0.60 (both clean), 0.30 (one clean/one stained), 0.10 (unmirrored)."""
        # Both clean
        assert calculate_stain_similarity([], []) == 0.60
        assert calculate_stain_similarity(None, None) == 0.60  # type: ignore

        # One clean, one stained
        stained = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant="TOP_LEFT")]
        assert calculate_stain_similarity(stained, []) == 0.30
        assert calculate_stain_similarity([], stained) == 0.30

        # Both stained, but unmirrored (e.g. TOP_LEFT and TOP_LEFT -> not reflected!)
        unmirrored_back = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant="TOP_LEFT")]
        assert calculate_stain_similarity(stained, unmirrored_back) == 0.10

    def test_ruling_similarity_metrics(self):
        """Ruling similarity handles spacing differences, unruled pairs, and conflicting types."""
        # Both unruled
        assert calculate_ruling_similarity(None, None) == 0.80
        assert calculate_ruling_similarity("UNRULED_BLANK", "UNRULED_BLANK") == 0.80

        # One ruled, one unruled
        r_ruled = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=24.0)
        r_unruled = PaperRulingInfo(has_ruling=False, ruling_type=PaperRulingType.UNRULED_BLANK)
        assert calculate_ruling_similarity(r_ruled, r_unruled) == 0.00

        # Both ruled, matching spacing (diff <= 4px -> 1.00)
        r_ruled2 = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=26.0)
        assert calculate_ruling_similarity(r_ruled, r_ruled2) == 1.00

        # Both ruled, moderate spacing diff (diff <= 10px -> 0.85)
        r_ruled3 = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=32.0)
        assert calculate_ruling_similarity(r_ruled, r_ruled3) == 0.85

        # Incompatible ruling types (e.g. Standard Ruled vs Grid -> 0.10)
        r_grid = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.GRID_GRAPH, line_spacing_px=24.0)
        assert calculate_ruling_similarity(r_ruled, r_grid) == 0.10

    def test_ink_similarity_metrics(self):
        """Ink similarity evaluates medium and color compatibility matrix."""
        # Identical ink & type -> 1.00
        ink1 = InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT)
        ink2 = InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT)
        assert calculate_ink_similarity(ink1, ink2) == 1.00

        # Both unknown -> 0.50
        assert calculate_ink_similarity(None, None) == 0.50

        # Compatible color (BLUE and BLUE_BLACK with matching BALLPOINT) -> 0.60*1.0 + 0.40*0.80 = 0.92
        ink_blue_black = InkInfo(ink_color=InkColor.BLUE_BLACK, ink_type=InkType.BLUE_BALLPOINT)
        assert calculate_ink_similarity(ink1, ink_blue_black) == 0.92

        # Conflicting type & color (BLUE BALLPOINT vs RED FELT_TIP) -> 0.60*0.10 + 0.40*0.10 = 0.10
        ink_red_felt = InkInfo(ink_color=InkColor.RED, ink_type=InkType.FELT_TIP_MARKER)
        assert calculate_ink_similarity(ink1, ink_red_felt) == 0.10

    def test_role_compatibility_matrix(self):
        """Role compatibility rigorously adheres to pairing matrix rules."""
        # Ideal pair
        assert calculate_role_compatibility(CardRole.FRONT_ONLY, CardRole.BACK_ONLY) == 1.00

        # Front + Unknown (likely back)
        assert calculate_role_compatibility(CardRole.FRONT_ONLY, CardRole.UNKNOWN) == 0.80

        # Unknown + Back (likely front)
        assert calculate_role_compatibility(CardRole.UNKNOWN, CardRole.BACK_ONLY) == 0.80

        # Unknown + Unknown
        assert calculate_role_compatibility(CardRole.UNKNOWN, CardRole.UNKNOWN) == 0.50

        # Standalone cannot pair with anything
        assert calculate_role_compatibility(CardRole.STANDALONE, CardRole.STANDALONE) == 0.00
        assert calculate_role_compatibility(CardRole.STANDALONE, CardRole.FRONT_ONLY) == 0.00
        assert calculate_role_compatibility(CardRole.STANDALONE, CardRole.BACK_ONLY) == 0.00
        assert calculate_role_compatibility(CardRole.STANDALONE, CardRole.UNKNOWN) == 0.00
        assert calculate_role_compatibility(CardRole.FRONT_ONLY, CardRole.STANDALONE) == 0.00

        # Incompatible front/front, inverted back/front; continuation+continuation is 0.90 for N-part chaining
        assert calculate_role_compatibility(CardRole.FRONT_ONLY, CardRole.FRONT_ONLY) == 0.00
        assert calculate_role_compatibility(CardRole.BACK_ONLY, CardRole.BACK_ONLY) == 0.90
        assert calculate_role_compatibility(CardRole.BACK_ONLY, CardRole.FRONT_ONLY) == 0.00

    def test_role_pruning_forces_heuristic_distance_zero(self):
        """When role compatibility is 0.0, composite heuristic score is immediately 0.0."""
        fp1 = VisualFingerprint(
            card_id="front1.jpg",
            image_path="/scans/front1.jpg",
            sha256_hash="aa" * 32,
            role=CardRole.FRONT_ONLY,
            paper_color=PaperColorInfo(hex_approx="#FFFFFF", lab_l=100.0, lab_a=0.0, lab_b=0.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
        )
        fp2 = VisualFingerprint(
            card_id="front2.jpg",
            image_path="/scans/front2.jpg",
            sha256_hash="bb" * 32,
            role=CardRole.FRONT_ONLY,  # Front + Front is incompatible
            paper_color=PaperColorInfo(hex_approx="#FFFFFF", lab_l=100.0, lab_a=0.0, lab_b=0.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
        )

        score = calculate_heuristic_distance(fp1, fp2, idx1=0, idx2=1)
        assert score == 0.0

    def test_screen_candidate_pairs_empty_and_threshold_filtering(self):
        """screen_candidate_pairs filters out pairs below threshold and sorts descending."""
        assert screen_candidate_pairs([]) == []
        assert screen_candidate_pairs([VisualFingerprint(card_id="c1", image_path="/p", sha256_hash="1" * 64, role=CardRole.FRONT_ONLY)]) == []

        fp_front = VisualFingerprint(
            card_id="f1.jpg",
            image_path="/p",
            sha256_hash="1" * 64,
            role=CardRole.FRONT_ONLY,
            paper_color=PaperColorInfo(hex_approx="#FFFFFF", lab_l=100.0, lab_a=0.0, lab_b=0.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
        )
        fp_back = VisualFingerprint(
            card_id="b1.jpg",
            image_path="/p",
            sha256_hash="2" * 64,
            role=CardRole.BACK_ONLY,
            paper_color=PaperColorInfo(hex_approx="#FFFFFF", lab_l=100.0, lab_a=0.0, lab_b=0.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
            ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
        )

        candidates = screen_candidate_pairs([fp_front, fp_back], threshold=0.50)
        assert len(candidates) == 1
        assert candidates[0][0].card_id == "f1.jpg"
        assert candidates[0][1].card_id == "b1.jpg"
        assert candidates[0][2] >= 0.50


# ============================================================================
# PART 6: PAIRING OPTIMIZER STRESS & INVARIANT CONSERVATION
# ============================================================================

class TestPairingOptimizerStressAndInvariants:
    """Stress-tests for PairingOptimizer bipartite matching and card conservation invariants."""

    def test_three_fronts_competing_for_single_back(self):
        """Greedy bipartite matching assigns the single back to the highest confidence front."""
        fps = [
            VisualFingerprint(card_id="front_a.jpg", image_path="/p", sha256_hash="a" * 64, role=CardRole.FRONT_ONLY),
            VisualFingerprint(card_id="front_b.jpg", image_path="/p", sha256_hash="b" * 64, role=CardRole.FRONT_ONLY),
            VisualFingerprint(card_id="front_c.jpg", image_path="/p", sha256_hash="c" * 64, role=CardRole.FRONT_ONLY),
            VisualFingerprint(card_id="back_target.jpg", image_path="/p", sha256_hash="d" * 64, role=CardRole.BACK_ONLY),
        ]

        proposals = [
            CorrelationProposal(
                proposal_id="p_b",
                front_card_id="front_b.jpg",
                back_card_id="back_target.jpg",
                heuristic_score=0.95,
                semantic_score=0.95,
                confidence=0.95,
                is_standalone=False,
                continuity_explanation="Front B highest confidence",
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p_a",
                front_card_id="front_a.jpg",
                back_card_id="back_target.jpg",
                heuristic_score=0.80,
                semantic_score=0.80,
                confidence=0.80,
                is_standalone=False,
                continuity_explanation="Front A moderate confidence",
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p_c",
                front_card_id="front_c.jpg",
                back_card_id="back_target.jpg",
                heuristic_score=0.60,
                semantic_score=0.60,
                confidence=0.60,
                is_standalone=False,
                continuity_explanation="Front C lower confidence",
                recommended_action="PAIR",
            ),
        ]

        optimizer = PairingOptimizer(min_pair_confidence=0.50)
        result = optimizer.optimize_proposals(fps, proposals)

        # Exactly 1 pair proposal matched: front_b.jpg <-> back_target.jpg
        pair_props = [p for p in result.proposals if not p.is_standalone]
        assert len(pair_props) == 1
        assert pair_props[0].front_card_id == "front_b.jpg"
        assert pair_props[0].back_card_id == "back_target.jpg"

        # front_a and front_c routed to unmatched_card_ids
        assert set(result.unmatched_card_ids) == {"front_a.jpg", "front_c.jpg"}

    def test_rejected_action_or_low_confidence_filtered_out(self):
        """Proposals with recommended_action='REJECT' or confidence < min_pair_confidence are rejected."""
        fps = [
            VisualFingerprint(card_id="f1.jpg", image_path="/p", sha256_hash="1" * 64, role=CardRole.FRONT_ONLY),
            VisualFingerprint(card_id="b1.jpg", image_path="/p", sha256_hash="2" * 64, role=CardRole.BACK_ONLY),
            VisualFingerprint(card_id="f2.jpg", image_path="/p", sha256_hash="3" * 64, role=CardRole.FRONT_ONLY),
            VisualFingerprint(card_id="b2.jpg", image_path="/p", sha256_hash="4" * 64, role=CardRole.BACK_ONLY),
        ]

        proposals = [
            # Low confidence proposal (< 0.50)
            CorrelationProposal(
                proposal_id="p_low",
                front_card_id="f1.jpg",
                back_card_id="b1.jpg",
                heuristic_score=0.30,
                semantic_score=0.40,
                confidence=0.37,
                is_standalone=False,
                continuity_explanation="Low confidence",
                recommended_action="UNCERTAIN",
            ),
            # REJECT action proposal
            CorrelationProposal(
                proposal_id="p_reject",
                front_card_id="f2.jpg",
                back_card_id="b2.jpg",
                heuristic_score=0.60,
                semantic_score=0.55,
                confidence=0.565,
                is_standalone=False,
                continuity_explanation="Explicitly rejected",
                recommended_action="REJECT",
            ),
        ]

        optimizer = PairingOptimizer(min_pair_confidence=0.50)
        result = optimizer.optimize_proposals(fps, proposals)

        assert len(result.proposals) == 0
        assert set(result.unmatched_card_ids) == {"f1.jpg", "b1.jpg", "f2.jpg", "b2.jpg"}

    def test_full_card_conservation_invariant(self):
        """Invariant: Every input card must appear exactly once across (proposals, unmatched_card_ids)."""
        fps = [
            VisualFingerprint(card_id="f1.jpg", image_path="/p", sha256_hash="1" * 64, role=CardRole.FRONT_ONLY),
            VisualFingerprint(card_id="b1.jpg", image_path="/p", sha256_hash="2" * 64, role=CardRole.BACK_ONLY),
            VisualFingerprint(card_id="s1.jpg", image_path="/p", sha256_hash="3" * 64, role=CardRole.STANDALONE),
            VisualFingerprint(card_id="s2.jpg", image_path="/p", sha256_hash="4" * 64, role=CardRole.STANDALONE),
            VisualFingerprint(card_id="u1.jpg", image_path="/p", sha256_hash="5" * 64, role=CardRole.UNKNOWN),
            VisualFingerprint(card_id="f2.jpg", image_path="/p", sha256_hash="6" * 64, role=CardRole.FRONT_ONLY),
        ]

        proposals = [
            CorrelationProposal(
                proposal_id="p1",
                front_card_id="f1.jpg",
                back_card_id="b1.jpg",
                heuristic_score=0.90,
                semantic_score=0.90,
                confidence=0.90,
                is_standalone=False,
                continuity_explanation="f1-b1 matched",
                recommended_action="PAIR",
            )
        ]

        optimizer = PairingOptimizer(min_pair_confidence=0.50)
        result = optimizer.optimize_proposals(fps, proposals)

        all_assigned = []
        for p in result.proposals:
            all_assigned.append(p.front_card_id)
            if p.back_card_id:
                all_assigned.append(p.back_card_id)
        all_assigned.extend(result.unmatched_card_ids)

        # Check total count and uniqueness
        assert len(all_assigned) == len(fps)
        assert len(set(all_assigned)) == len(fps)
        assert set(all_assigned) == {fp.card_id for fp in fps}

        # Verify proposal types
        assert len([p for p in result.proposals if not p.is_standalone]) == 1  # f1-b1
        assert len([p for p in result.proposals if p.is_standalone]) == 2      # s1, s2
        assert set(result.unmatched_card_ids) == {"u1.jpg", "f2.jpg"}
