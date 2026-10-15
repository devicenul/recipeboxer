"""Integration and unit tests for Two-Stage Correlation and Pairing Optimizer."""

import pytest
from pathlib import Path
from PIL import Image

from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
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
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.vision.fingerprint import GeminiVisualExtractor
from recipeboxer.vision.heuristics import screen_candidate_pairs


class LocalMockVisualClient:
    def __init__(self, role: CardRole = CardRole.SEQUENCE_START):
        self.call_count = 0
        self.role = role

    def analyze_fingerprint(self, image_path, sha256_hash=None):
        self.call_count += 1
        cid = image_path.name if hasattr(image_path, "name") else str(image_path)
        return VisualFingerprint(
            card_id=cid,
            image_path=str(image_path),
            sha256_hash=sha256_hash or "test_hash",
            role=self.role,
            paper_color=PaperColorInfo(tone_name="Aged", hex_approx="#F6ECD1", lab_l=93.5, lab_a=-1.2, lab_b=15.4),
        )


class LocalMockSemanticClient:
    def verify_pair(
        self, front_fp: VisualFingerprint, back_fp: VisualFingerprint, heuristic_score: float
    ) -> CorrelationProposal:
        return CorrelationProposal(
            proposal_id="prop_mock_pair",
            ordered_card_ids=[front_fp.card_id, back_fp.card_id],
            part_count=2,
            heuristic_score=heuristic_score,
            semantic_score=0.96,
            confidence=round(0.30 * heuristic_score + 0.70 * 0.96, 4),
            recommended_action="PAIR",
            semantic_verified=True,
        )


class TestGeminiVisualExtractor:
    """Tests for GeminiVisualExtractor with both mock client and offline image analysis."""

    @pytest.mark.asyncio
    async def test_extractor_with_mock_client(self, tmp_path: Path):
        img_path = tmp_path / "test_front.jpg"
        img = Image.new("RGB", (600, 400), color=(246, 236, 205))
        img.save(img_path)

        mock_client = LocalMockVisualClient(role=CardRole.SEQUENCE_START)
        extractor = GeminiVisualExtractor(client=mock_client)

        fp = await extractor.extract(img_path)
        assert isinstance(fp, VisualFingerprint)
        assert fp.card_id == "test_front.jpg"
        assert fp.role == CardRole.SEQUENCE_START
        assert mock_client.call_count == 1

    @pytest.mark.asyncio
    async def test_extractor_offline_pixel_analysis(self, tmp_path: Path):
        # Create an aged cream image
        img_path = tmp_path / "01_cinnamon_front.jpg"
        img = Image.new("RGB", (400, 300), color=(246, 236, 205))
        img.save(img_path)

        extractor = GeminiVisualExtractor(api_key=None)
        fp = await extractor.extract(img_path)

        assert fp.role == CardRole.UNKNOWN
        assert fp.degraded is True
        assert fp.paper_color.hex_approx.upper().startswith("#F6")
        assert fp.paper_color.lab_l > 80.0
        assert fp.ruling.has_ruling is False
        assert fp.ruling.ruling_type == PaperRulingType.UNRULED_BLANK

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
        mock_client = LocalMockSemanticClient()
        verifier = GeminiSemanticVerifier(client=mock_client)

        fp_f = VisualFingerprint(
            card_id="01_cinnamon_rolls_front.jpg",
            image_path="/dummy/01.jpg",
            sha256_hash="111",
            role=CardRole.SEQUENCE_START,
        )
        fp_b = VisualFingerprint(
            card_id="02_cinnamon_rolls_back.jpg",
            image_path="/dummy/02.jpg",
            sha256_hash="222",
            role=CardRole.SEQUENCE_CONTINUATION,
        )

        proposal = await verifier.verify_pair(fp_f, fp_b, heuristic_score=0.90)
        assert proposal.ordered_card_ids == ["01_cinnamon_rolls_front.jpg", "02_cinnamon_rolls_back.jpg"]
        assert proposal.semantic_score > 0.90
        # Composite confidence = 0.30 * 0.90 + 0.70 * 0.96 = 0.27 + 0.672 = 0.942
        assert proposal.confidence >= 0.85
        assert proposal.recommended_action in ("PAIR", "SEQUENCE")

    @pytest.mark.asyncio
    async def test_semantic_verifier_custom_weights(self):
        from recipeboxer.ai.replay import ScriptedLLMClient
        from recipeboxer.correlation.semantic import SetVerificationResponse

        client = ScriptedLLMClient([
            SetVerificationResponse(continuations=["Candidate 1"], evidence_quotes=["Lemon Bars"], confidence=0.85)
        ])
        verifier = GeminiSemanticVerifier(llm_client=client, heuristic_weight=0.50, semantic_weight=0.50)

        fp_f = VisualFingerprint(
            card_id="03_lemon_bars_front.jpg",
            image_path="/dummy/03.jpg",
            sha256_hash="333",
            role=CardRole.SEQUENCE_START,
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
            role=CardRole.SEQUENCE_CONTINUATION,
            boundary_text=BoundaryText(
                first_line="(Directions cont)",
                last_line="Bake 25 mins.",
            ),
        )

        proposal = await verifier.verify_pair(fp_f, fp_b, heuristic_score=0.80)
        assert proposal.semantic_score == 0.85
        assert proposal.confidence == 0.825
        assert proposal.recommended_action in ("PAIR", "SEQUENCE")

    @pytest.mark.asyncio
    async def test_semantic_verifier_offline_continuity_cues(self):
        """Without a client, offline heuristic scoring is removed and raises NO_API_KEY (INV-AC4/AC6)."""
        verifier = GeminiSemanticVerifier(api_key=None)

        fp_f = VisualFingerprint(
            card_id="03_lemon_bars_front.jpg",
            image_path="/dummy/03.jpg",
            sha256_hash="333",
            role=CardRole.SEQUENCE_START,
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
            role=CardRole.SEQUENCE_CONTINUATION,
            boundary_text=BoundaryText(
                first_line="1. Preheat oven to 350F (Directions continued)",
                last_line="Bake 25 mins until set.",
            ),
        )

        with pytest.raises(SemanticVerificationUnavailable) as exc_info:
            await verifier.verify_pair(fp_f, fp_b, heuristic_score=0.85)

        assert exc_info.value.reason == FailureReason.NO_API_KEY

    @pytest.mark.asyncio
    async def test_semantic_verifier_mismatched_pair_rejected(self):
        class RejectMockClient:
            def verify_pair(self, front_fp, back_fp, heuristic_score):
                return CorrelationProposal(
                    proposal_id="prop_reject",
                    ordered_card_ids=[front_fp.card_id, back_fp.card_id],
                    part_count=2,
                    heuristic_score=heuristic_score,
                    semantic_score=0.10,
                    confidence=0.15,
                    recommended_action="REJECT",
                    semantic_verified=True,
                )

        verifier = GeminiSemanticVerifier(client=RejectMockClient())

        fp_f = VisualFingerprint(
            card_id="chocolate_cake_front.jpg",
            image_path="/dummy/f.jpg",
            sha256_hash="555",
            role=CardRole.SEQUENCE_START,
            boundary_text=BoundaryText(first_line="Chocolate Cake", last_line="2 cups sugar"),
        )
        fp_b = VisualFingerprint(
            card_id="beef_stew_back.jpg",
            image_path="/dummy/b.jpg",
            sha256_hash="666",
            role=CardRole.SEQUENCE_CONTINUATION,
            boundary_text=BoundaryText(first_line="Brown beef chunks in pot", last_line="Simmer 2 hours."),
        )

        proposal = await verifier.verify_pair(fp_f, fp_b, heuristic_score=0.20)
        assert proposal.semantic_score <= 0.15
        assert proposal.confidence < 0.50
        assert proposal.recommended_action == "REJECT"


class TestPairingOptimizer:
    """Tests for open-ended directed sequence correlation and clustering."""

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
        assert prop.ordered_card_ids == ["05_fudge_standalone.jpg"]
        assert prop.part_count == 1
        assert prop.is_standalone is True
        assert prop.recommended_action == "STANDALONE"
        assert len(result.unmatched_card_ids) == 0

    def test_optimizer_bipartite_greedy_conflict_resolution(self):
        """When Card A matches Card B with conf=0.95 and Card C with conf=0.70, A pairs with B."""
        optimizer = PairingOptimizer()

        fp_a = VisualFingerprint(card_id="card_A_front.jpg", image_path="/a", sha256_hash="a", role=CardRole.SEQUENCE_START)
        fp_b = VisualFingerprint(card_id="card_B_back.jpg", image_path="/b", sha256_hash="b", role=CardRole.SEQUENCE_CONTINUATION)
        fp_c = VisualFingerprint(card_id="card_C_back.jpg", image_path="/c", sha256_hash="c", role=CardRole.SEQUENCE_CONTINUATION)

        prop_ab = CorrelationProposal(
            proposal_id="p1",
            ordered_card_ids=["card_A_front.jpg", "card_B_back.jpg"],
            confidence=0.95,
            recommended_action="SEQUENCE",
        )
        prop_ac = CorrelationProposal(
            proposal_id="p2",
            ordered_card_ids=["card_A_front.jpg", "card_C_back.jpg"],
            confidence=0.70,
            recommended_action="UNCERTAIN",
        )

        result = optimizer.optimize_proposals([fp_a, fp_b, fp_c], [prop_ab, prop_ac])
        assert len(result.proposals) == 1
        assert result.proposals[0].ordered_card_ids == ["card_A_front.jpg", "card_B_back.jpg"]
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
    async def test_end_to_end_synthetic_deck_correlation(self):
        """Runs full two-stage correlation and optimization across synthetic test fingerprints."""
        class DeckVerifierClient:
            def verify_pair(self, front_fp, back_fp, heuristic_score):
                # Verify if front and back belong to the same recipe pair
                f_name = front_fp.card_id.split("_")[1]
                b_name = back_fp.card_id.split("_")[1]
                if f_name == b_name and front_fp.role == CardRole.SEQUENCE_START and back_fp.role == CardRole.SEQUENCE_CONTINUATION:
                    return CorrelationProposal(
                        proposal_id=f"prop_{f_name}",
                        ordered_card_ids=[front_fp.card_id, back_fp.card_id],
                        part_count=2,
                        heuristic_score=heuristic_score,
                        semantic_score=0.96,
                        confidence=round(0.30 * heuristic_score + 0.70 * 0.96, 4),
                        recommended_action="PAIR",
                        semantic_verified=True,
                    )
                return CorrelationProposal(
                    proposal_id=f"prop_reject_{f_name}_{b_name}",
                    ordered_card_ids=[front_fp.card_id, back_fp.card_id],
                    part_count=2,
                    heuristic_score=heuristic_score,
                    semantic_score=0.10,
                    confidence=0.15,
                    recommended_action="REJECT",
                    semantic_verified=True,
                )

        verifier = GeminiSemanticVerifier(client=DeckVerifierClient())
        optimizer = PairingOptimizer(verifier=verifier)

        aged_paper = PaperColorInfo(tone_name="Aged", hex_approx="#F6ECD1", lab_l=93.5, lab_a=-1.2, lab_b=15.4)
        white_paper = PaperColorInfo(tone_name="White", hex_approx="#FFFFFF", lab_l=100.0, lab_a=0.0, lab_b=0.0)
        ruled = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=24.0)
        blue_ink = InkInfo(ink_type=InkType.BLUE_BALLPOINT, ink_color=InkColor.BLUE)

        fps = [
            VisualFingerprint(
                card_id="01_cinnamon_rolls_front.jpg", image_path="/p/1f", sha256_hash="h1f",
                role=CardRole.SEQUENCE_START, paper_color=aged_paper, ruling=ruled, ink=blue_ink,
                boundary_text=BoundaryText(first_line="Cinnamon Rolls", last_line="turn over ->", has_continuation_symbol=True)
            ),
            VisualFingerprint(
                card_id="02_cinnamon_rolls_back.jpg", image_path="/p/1b", sha256_hash="h1b",
                role=CardRole.SEQUENCE_CONTINUATION, paper_color=aged_paper, ruling=ruled, ink=blue_ink,
                boundary_text=BoundaryText(first_line="Cinnamon Rolls Cont.", last_line="Bake 20 min.")
            ),
            VisualFingerprint(
                card_id="03_lemon_bars_front.jpg", image_path="/p/2f", sha256_hash="h2f",
                role=CardRole.SEQUENCE_START, paper_color=white_paper, ruling=ruled, ink=blue_ink,
                boundary_text=BoundaryText(first_line="Lemon Bars", last_line="see back ->", has_continuation_symbol=True)
            ),
            VisualFingerprint(
                card_id="04_lemon_bars_back.jpg", image_path="/p/2b", sha256_hash="h2b",
                role=CardRole.SEQUENCE_CONTINUATION, paper_color=white_paper, ruling=ruled, ink=blue_ink,
                boundary_text=BoundaryText(first_line="Lemon Bars Cont.", last_line="Cool completely.")
            ),
            VisualFingerprint(
                card_id="05_fudge_standalone.jpg", image_path="/p/3", sha256_hash="h3",
                role=CardRole.STANDALONE, role_confidence=0.95,
            ),
            VisualFingerprint(
                card_id="06_pecan_pie_standalone.jpg", image_path="/p/4", sha256_hash="h4",
                role=CardRole.STANDALONE, role_confidence=0.90,
            ),
            VisualFingerprint(
                card_id="07_divider_tab.jpg", image_path="/p/5", sha256_hash="h5",
                role=CardRole.UNKNOWN, role_confidence=0.20,
            ),
        ]

        result = await optimizer.optimize(fps)
        assert isinstance(result, CorrelationResult)

        # Check paired proposals
        paired_proposals = [p for p in result.proposals if not p.is_standalone]
        standalone_proposals = [p for p in result.proposals if p.is_standalone]

        assert len(paired_proposals) == 2
        # Check Cinnamon Rolls pair
        cinnamon_pair = next(p for p in paired_proposals if any("cinnamon" in cid for cid in p.ordered_card_ids))
        assert any("back" in cid for cid in cinnamon_pair.ordered_card_ids)
        assert cinnamon_pair.recommended_action in ("PAIR", "SEQUENCE")
        assert cinnamon_pair.confidence >= 0.85

        # Check Lemon Bars pair
        lemon_pair = next(p for p in paired_proposals if any("lemon" in cid for cid in p.ordered_card_ids))
        assert any("back" in cid for cid in lemon_pair.ordered_card_ids)
        assert lemon_pair.recommended_action in ("PAIR", "SEQUENCE")

        # Check Standalones (Fudge, Pecan Pie)
        assert len(standalone_proposals) == 2
        standalone_ids = {p.ordered_card_ids[0] for p in standalone_proposals}
        assert any("fudge" in cid for cid in standalone_ids)
        assert any("pecan" in cid for cid in standalone_ids)

        # Check Unmatched noise tab
        assert any("divider" in cid for cid in result.unmatched_card_ids)
