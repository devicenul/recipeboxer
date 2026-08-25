"""Integration and unit tests for Two-Stage Correlation and Pairing Optimizer."""

import pytest
from pathlib import Path
from PIL import Image

from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import (
    ArtifactType,
    BoundaryText,
    CardQuadrant,
    CardRole,
    InkColor,
    InkInfo,
    InkType,
    PaperColorInfo,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.vision.fingerprint import GeminiVisualExtractor
from recipeboxer.vision.heuristics import screen_candidate_pairs
from tests.conftest import MockGeminiClient, SyntheticDeckInfo


class TestGeminiVisualExtractor:
    """Tests for GeminiVisualExtractor with both mock client and offline image analysis."""

    @pytest.mark.asyncio
    async def test_extractor_with_mock_client(self, tmp_path: Path):
        img_path = tmp_path / "test_front.jpg"
        img = Image.new("RGB", (600, 400), color=(246, 236, 205))
        img.save(img_path)

        mock_client = MockGeminiClient()
        extractor = GeminiVisualExtractor(client=mock_client)

        fp = await extractor.extract(img_path)
        assert isinstance(fp, VisualFingerprint)
        assert fp.card_id == "test_front.jpg"
        assert fp.role == CardRole.FRONT_ONLY
        assert mock_client.call_count == 1

    @pytest.mark.asyncio
    async def test_extractor_offline_pixel_analysis(self, tmp_path: Path):
        # Create an aged cream image
        img_path = tmp_path / "01_cinnamon_front.jpg"
        img = Image.new("RGB", (400, 300), color=(246, 236, 205))
        img.save(img_path)

        extractor = GeminiVisualExtractor(api_key=None)
        fp = await extractor.extract(img_path)

        assert fp.role == CardRole.FRONT_ONLY
        assert fp.paper_color.hex_approx.upper().startswith("#F6")
        assert fp.paper_color.lab_l > 80.0
        assert fp.ruling.has_ruling is True

    @pytest.mark.asyncio
    async def test_extractor_corrupted_image_raises_error(self, tmp_path: Path):
        corrupt_path = tmp_path / "corrupt.jpg"
        corrupt_path.write_bytes(b"CORRUPT_BYTES_NOT_AN_IMAGE")

        extractor = GeminiVisualExtractor(api_key=None)
        with pytest.raises(ValueError, match="Corrupted or empty image data"):
            await extractor.extract(corrupt_path)

    @pytest.mark.asyncio
    async def test_extractor_scanned_card_input(self, tmp_path: Path):
        img_path = tmp_path / "single_recipe.jpg"
        img = Image.new("RGB", (300, 200), color=(255, 255, 255))
        img.save(img_path)

        scanned_card = ScannedCard(
            card_id="single_recipe.jpg",
            file_path=img_path,
            filename="single_recipe.jpg",
            file_size_bytes=img_path.stat().st_size,
            sha256_hash="dummy_hash_12345",
            width=300,
            height=200,
            format="JPEG",
            mode="RGB",
        )

        extractor = GeminiVisualExtractor(api_key=None)
        fp = await extractor(scanned_card)
        assert fp.card_id == "single_recipe.jpg"
        assert fp.sha256_hash == "dummy_hash_12345"

    def test_extractor_sync_interface(self, tmp_path: Path):
        img_path = tmp_path / "sync_card.jpg"
        img = Image.new("RGB", (200, 200), color=(255, 255, 255))
        img.save(img_path)

        extractor = GeminiVisualExtractor(api_key=None)
        fp = extractor.extract_sync(img_path)
        assert isinstance(fp, VisualFingerprint)
        assert fp.card_id == "sync_card.jpg"


class TestGeminiSemanticVerifier:
    """Tests for Stage 2 semantic continuity verifier."""

    @pytest.mark.asyncio
    async def test_semantic_verifier_with_mock_client(self):
        mock_client = MockGeminiClient()
        verifier = GeminiSemanticVerifier(client=mock_client)

        fp_f = VisualFingerprint(
            card_id="01_cinnamon_rolls_front.jpg",
            image_path="/dummy/01.jpg",
            sha256_hash="111",
            role=CardRole.FRONT_ONLY,
        )
        fp_b = VisualFingerprint(
            card_id="02_cinnamon_rolls_back.jpg",
            image_path="/dummy/02.jpg",
            sha256_hash="222",
            role=CardRole.BACK_ONLY,
        )

        proposal = await verifier.verify_pair(fp_f, fp_b, heuristic_score=0.90)
        assert proposal.front_card_id == "01_cinnamon_rolls_front.jpg"
        assert proposal.back_card_id == "02_cinnamon_rolls_back.jpg"
        assert proposal.semantic_score > 0.90
        # Composite confidence = 0.30 * 0.90 + 0.70 * 0.96 = 0.27 + 0.672 = 0.942
        assert proposal.confidence >= 0.85
        assert proposal.recommended_action == "PAIR"

    @pytest.mark.asyncio
    async def test_semantic_verifier_custom_weights(self):
        verifier = GeminiSemanticVerifier(api_key=None, heuristic_weight=0.50, semantic_weight=0.50)

        fp_f = VisualFingerprint(
            card_id="03_lemon_bars_front.jpg",
            image_path="/dummy/03.jpg",
            sha256_hash="333",
            role=CardRole.FRONT_ONLY,
            boundary_text=BoundaryText(
                first_line="Lemon Bars",
                last_line="Filling: 4 eggs (over ->)",
                has_continuation_symbol=True,
            ),
        )
        fp_b = VisualFingerprint(
            card_id="04_lemon_bars_back.jpg",
            image_path="/dummy/04.jpg",
            sha256_hash="444",
            role=CardRole.BACK_ONLY,
            boundary_text=BoundaryText(
                first_line="(Directions cont)",
                last_line="Bake 25 mins.",
            ),
        )

        proposal = await verifier.verify_pair(fp_f, fp_b, heuristic_score=0.80)
        # 0.50 * 0.80 + 0.50 * 0.95 = 0.40 + 0.475 = 0.875
        assert pytest.approx(proposal.confidence, abs=0.01) == 0.875
        assert proposal.recommended_action == "PAIR"

    @pytest.mark.asyncio
    async def test_semantic_verifier_offline_continuity_cues(self):
        verifier = GeminiSemanticVerifier(api_key=None)

        fp_f = VisualFingerprint(
            card_id="03_lemon_bars_front.jpg",
            image_path="/dummy/03.jpg",
            sha256_hash="333",
            role=CardRole.FRONT_ONLY,
            boundary_text=BoundaryText(
                first_line="Luscious Lemon Glaze Bars",
                last_line="Filling: 4 large eggs (over ->)",
                has_continuation_symbol=True,
                is_cut_off_mid_sentence=True,
            ),
        )
        fp_b = VisualFingerprint(
            card_id="04_lemon_bars_back.jpg",
            image_path="/dummy/04.jpg",
            sha256_hash="444",
            role=CardRole.BACK_ONLY,
            boundary_text=BoundaryText(
                first_line="1. Preheat oven to 350F (Directions continued)",
                last_line="Bake 25 mins until set.",
            ),
        )

        proposal = await verifier.verify_pair(fp_f, fp_b, heuristic_score=0.85)
        assert proposal.recommended_action == "PAIR"
        assert proposal.semantic_score >= 0.90
        assert proposal.confidence >= 0.85

    @pytest.mark.asyncio
    async def test_semantic_verifier_mismatched_pair_rejected(self):
        verifier = GeminiSemanticVerifier(api_key=None)

        fp_f = VisualFingerprint(
            card_id="chocolate_cake_front.jpg",
            image_path="/dummy/f.jpg",
            sha256_hash="555",
            role=CardRole.FRONT_ONLY,
            boundary_text=BoundaryText(first_line="Chocolate Cake", last_line="2 cups sugar"),
        )
        fp_b = VisualFingerprint(
            card_id="beef_stew_back.jpg",
            image_path="/dummy/b.jpg",
            sha256_hash="666",
            role=CardRole.BACK_ONLY,
            boundary_text=BoundaryText(first_line="Brown beef chunks in pot", last_line="Simmer 2 hours."),
        )

        proposal = await verifier.verify_pair(fp_f, fp_b, heuristic_score=0.20)
        assert proposal.semantic_score <= 0.15
        assert proposal.confidence < 0.50
        assert proposal.recommended_action == "REJECT"


class TestPairingOptimizer:
    """Tests for bipartite global pairing assignment and result generation."""

    def test_optimizer_empty_input(self):
        optimizer = PairingOptimizer()
        result = optimizer.optimize_proposals([], proposals=[])
        assert len(result.proposals) == 0
        assert len(result.unmatched_card_ids) == 0

    def test_optimizer_standalone_cards(self):
        optimizer = PairingOptimizer()

        fp_standalone = VisualFingerprint(
            card_id="05_fudge_standalone.jpg",
            image_path="/p/5",
            sha256_hash="555",
            role=CardRole.STANDALONE,
            role_confidence=0.95,
        )

        result = optimizer.optimize_proposals([fp_standalone], proposals=[])
        assert len(result.proposals) == 1
        prop = result.proposals[0]
        assert prop.front_card_id == "05_fudge_standalone.jpg"
        assert prop.back_card_id is None
        assert prop.is_standalone is True
        assert prop.recommended_action == "STANDALONE"
        assert len(result.unmatched_card_ids) == 0

    def test_optimizer_bipartite_greedy_conflict_resolution(self):
        """When Card A matches Card B with conf=0.95 and Card C with conf=0.70, A pairs with B."""
        optimizer = PairingOptimizer()

        fp_a = VisualFingerprint(card_id="card_A_front.jpg", image_path="/a", sha256_hash="a", role=CardRole.FRONT_ONLY)
        fp_b = VisualFingerprint(card_id="card_B_back.jpg", image_path="/b", sha256_hash="b", role=CardRole.BACK_ONLY)
        fp_c = VisualFingerprint(card_id="card_C_back.jpg", image_path="/c", sha256_hash="c", role=CardRole.BACK_ONLY)

        prop_ab = CorrelationProposal(
            proposal_id="p1",
            front_card_id="card_A_front.jpg",
            back_card_id="card_B_back.jpg",
            confidence=0.95,
            recommended_action="PAIR",
        )
        prop_ac = CorrelationProposal(
            proposal_id="p2",
            front_card_id="card_A_front.jpg",
            back_card_id="card_C_back.jpg",
            confidence=0.70,
            recommended_action="UNCERTAIN",
        )

        result = optimizer.optimize_proposals([fp_a, fp_b, fp_c], [prop_ab, prop_ac])
        assert len(result.proposals) == 1
        assert result.proposals[0].front_card_id == "card_A_front.jpg"
        assert result.proposals[0].back_card_id == "card_B_back.jpg"
        # Card C was not matched to anything
        assert "card_C_back.jpg" in result.unmatched_card_ids

    def test_optimizer_unmatched_noise_cards(self):
        optimizer = PairingOptimizer()

        fp_noise = VisualFingerprint(
            card_id="07_divider_tab.jpg",
            image_path="/p/7",
            sha256_hash="777",
            role=CardRole.UNKNOWN,
            role_confidence=0.50,
        )

        result = optimizer.optimize_proposals([fp_noise], proposals=[])
        assert len(result.proposals) == 0
        assert "07_divider_tab.jpg" in result.unmatched_card_ids

    @pytest.mark.asyncio
    async def test_end_to_end_synthetic_deck_correlation(self, synthetic_card_deck: SyntheticDeckInfo):
        """Runs full two-stage correlation and optimization across the synthetic test deck."""
        mock_client = MockGeminiClient()
        extractor = GeminiVisualExtractor(client=mock_client)
        verifier = GeminiSemanticVerifier(client=mock_client)
        optimizer = PairingOptimizer(verifier=verifier)

        # 1. Extract fingerprints for all valid synthetic cards
        valid_files = [f for f in synthetic_card_deck.all_files if "corrupt" not in f.name]
        fps = []
        for f in valid_files:
            fp = await extractor.extract(f)
            fps.append(fp)

        # 2. Run end-to-end optimizer
        result = await optimizer.optimize(fps)
        assert isinstance(result, CorrelationResult)

        # Check paired proposals
        paired_proposals = [p for p in result.proposals if not p.is_standalone]
        standalone_proposals = [p for p in result.proposals if p.is_standalone]

        assert len(paired_proposals) == 2
        # Check Cinnamon Rolls pair
        cinnamon_pair = next(p for p in paired_proposals if "cinnamon" in p.front_card_id)
        assert "cinnamon" in cinnamon_pair.back_card_id
        assert cinnamon_pair.recommended_action == "PAIR"
        assert cinnamon_pair.confidence >= 0.85

        # Check Lemon Bars pair
        lemon_pair = next(p for p in paired_proposals if "lemon" in p.front_card_id)
        assert "lemon" in lemon_pair.back_card_id
        assert lemon_pair.recommended_action == "PAIR"

        # Check Standalones (Fudge, Pecan Pie)
        assert len(standalone_proposals) == 2
        standalone_ids = {p.front_card_id for p in standalone_proposals}
        assert any("fudge" in cid for cid in standalone_ids)
        assert any("pecan" in cid for cid in standalone_ids)

        # Check Unmatched noise tab
        assert any("divider" in cid for cid in result.unmatched_card_ids)
