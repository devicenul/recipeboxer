"""Unit tests for Stage 1 Heuristic Visual Distance and Screening Engine."""

import math
import pytest
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


class TestColorMathAndSimilarity:
    """Tests for CIE L*a*b* conversion and Delta-E color distance formulas."""

    def test_rgb_to_lab_pure_white(self):
        l, a, b = rgb_to_lab(255, 255, 255)
        assert l >= 99.0  # Lightness of pure white is ~100
        assert abs(a) < 1.0
        assert abs(b) < 1.0

    def test_rgb_to_lab_pure_black(self):
        l, a, b = rgb_to_lab(0, 0, 0)
        assert l <= 1.0  # Lightness of pure black is 0
        assert abs(a) < 1.0
        assert abs(b) < 1.0

    def test_hex_to_lab_valid_and_fallback(self):
        lab = hex_to_lab("#FFFFFF")
        assert lab[0] >= 99.0

        lab_cream = hex_to_lab("F4E8C1")
        assert 85.0 <= lab_cream[0] <= 98.0

        # Invalid hex formats fallback to default
        fallback = hex_to_lab("INVALID")
        assert fallback == (100.0, 0.0, 0.0)

    def test_calculate_color_similarity_identical_colors(self):
        c1 = PaperColorInfo(tone_name="Cream", hex_approx="#F4E8C1", lab_l=90.0, lab_a=2.0, lab_b=18.0)
        c2 = PaperColorInfo(tone_name="Cream", hex_approx="#F4E8C1", lab_l=90.0, lab_a=2.0, lab_b=18.0)
        score = calculate_color_similarity(c1, c2)
        assert score == 1.0

    def test_calculate_color_similarity_moderate_distance(self):
        c1 = PaperColorInfo(lab_l=90.0, lab_a=0.0, lab_b=0.0)
        c2 = PaperColorInfo(lab_l=65.0, lab_a=0.0, lab_b=0.0)  # Delta-E = 25.0
        score = calculate_color_similarity(c1, c2)
        # Expected score = 1.0 - 25/50 = 0.50
        assert pytest.approx(score, abs=0.01) == 0.50

    def test_calculate_color_similarity_extreme_distance_clamps_to_zero(self):
        c1 = PaperColorInfo(lab_l=100.0, lab_a=0.0, lab_b=0.0)
        c2 = PaperColorInfo(lab_l=0.0, lab_a=0.0, lab_b=0.0)  # Delta-E = 100.0 >= 50.0
        score = calculate_color_similarity(c1, c2)
        assert score == 0.0

    def test_calculate_color_similarity_with_tuples_and_dicts(self):
        score1 = calculate_color_similarity((90.0, 2.0, 18.0), (90.0, 2.0, 18.0))
        assert score1 == 1.0

        score2 = calculate_color_similarity({"lab_l": 90.0, "lab_a": 0.0, "lab_b": 0.0}, {"lab_l": 80.0, "lab_a": 0.0, "lab_b": 0.0})
        # Delta-E = 10 -> score = 1.0 - 10/50 = 0.80
        assert pytest.approx(score2, abs=0.01) == 0.80


class TestScanProximityDecay:
    """Tests for exponential proximity decay formula."""

    def test_proximity_same_index(self):
        assert calculate_proximity_score(0, 0) == 1.0

    def test_proximity_immediate_consecutive(self):
        # S_prox = exp(-0.5 * 1) = exp(-0.5) ~ 0.6065
        score = calculate_proximity_score(0, 1)
        assert pytest.approx(score, abs=0.01) == 0.6065

    def test_proximity_two_cards_apart(self):
        # S_prox = exp(-0.5 * 2) = exp(-1.0) ~ 0.3679
        score = calculate_proximity_score(3, 5)
        assert pytest.approx(score, abs=0.01) == 0.3679

    def test_proximity_far_distance(self):
        # S_prox = exp(-0.5 * 10) = exp(-5.0) ~ 0.0067
        score = calculate_proximity_score(0, 10)
        assert score < 0.01
        assert score >= 0.0


class TestMirroredStainBleedThrough:
    """Tests for horizontal mirror artifact pairing logic."""

    def test_clean_cards_both_no_artifacts(self):
        score = calculate_stain_similarity([], [])
        assert score == 0.60  # Clean baseline

    def test_one_card_stained_one_clean(self):
        art = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_RIGHT)]
        score1 = calculate_stain_similarity(art, [])
        score2 = calculate_stain_similarity([], art)
        assert score1 == 0.30
        assert score2 == 0.30

    def test_horizontal_mirror_quadrant_pairs(self):
        # TOP_RIGHT <-> TOP_LEFT
        art_front = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_RIGHT)]
        art_back = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_LEFT)]
        assert calculate_stain_similarity(art_front, art_back) == 1.00

        # BOTTOM_LEFT <-> BOTTOM_RIGHT
        art_f2 = [PhysicalArtifact(artifact_type=ArtifactType.LIQUID_STAIN, quadrant=CardQuadrant.BOTTOM_LEFT)]
        art_b2 = [PhysicalArtifact(artifact_type=ArtifactType.OIL_STAIN, quadrant=CardQuadrant.BOTTOM_RIGHT)]
        assert calculate_stain_similarity(art_f2, art_b2) == 1.00

        # CENTER <-> CENTER
        art_f3 = [PhysicalArtifact(artifact_type=ArtifactType.COFFEE_TEA_RING, quadrant=CardQuadrant.CENTER)]
        art_b3 = [PhysicalArtifact(artifact_type=ArtifactType.COFFEE_TEA_RING, quadrant=CardQuadrant.CENTER)]
        assert calculate_stain_similarity(art_f3, art_b3) == 1.00

        # EDGE_LEFT <-> EDGE_RIGHT
        art_f4 = [PhysicalArtifact(artifact_type=ArtifactType.TEAR, quadrant=CardQuadrant.EDGE_LEFT)]
        art_b4 = [PhysicalArtifact(artifact_type=ArtifactType.TEAR, quadrant=CardQuadrant.EDGE_RIGHT)]
        assert calculate_stain_similarity(art_f4, art_b4) == 1.00

    def test_unmirrored_incompatible_quadrants(self):
        # Front top-left and Back top-left (not mirrored!)
        art_front = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_LEFT)]
        art_back = [PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_LEFT)]
        score = calculate_stain_similarity(art_front, art_back)
        assert score == 0.10


class TestRulingAndInkSimilarity:
    """Tests for ruling grid and ink instrument matching."""

    def test_ruling_both_unruled(self):
        r1 = PaperRulingInfo(has_ruling=False, ruling_type=PaperRulingType.UNRULED_BLANK)
        r2 = PaperRulingInfo(has_ruling=False, ruling_type=PaperRulingType.UNRULED_BLANK)
        assert calculate_ruling_similarity(r1, r2) == 0.80

    def test_ruling_one_ruled_one_unruled(self):
        r1 = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE)
        r2 = PaperRulingInfo(has_ruling=False, ruling_type=PaperRulingType.UNRULED_BLANK)
        assert calculate_ruling_similarity(r1, r2) == 0.00

    def test_ruling_matching_spacing(self):
        r1 = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=24.0)
        r2 = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=24.0)
        assert calculate_ruling_similarity(r1, r2) == 1.00

    def test_ruling_conflicting_types(self):
        r1 = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.GRID)
        r2 = PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE)
        assert calculate_ruling_similarity(r1, r2) == 0.10

    def test_ink_identical_type_and_color(self):
        i1 = InkInfo(ink_type=InkType.BLUE_BALLPOINT, ink_color=InkColor.BLUE)
        i2 = InkInfo(ink_type=InkType.BALLPOINT_PEN, ink_color=InkColor.BLUE)
        assert calculate_ink_similarity(i1, i2) == 1.00

    def test_ink_unknown_fallback(self):
        i1 = InkInfo(ink_type=InkType.UNKNOWN, ink_color=InkColor.UNKNOWN)
        i2 = InkInfo(ink_type=InkType.BLUE_BALLPOINT, ink_color=InkColor.BLUE)
        score = calculate_ink_similarity(i1, i2)
        assert score == 0.50


class TestRoleCompatibility:
    """Tests for CardRole pairwise compatibility matrix."""

    def test_front_and_back_role(self):
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.SEQUENCE_CONTINUATION) == 1.00

    def test_continuation_and_continuation_role(self):
        assert calculate_role_compatibility(CardRole.SEQUENCE_CONTINUATION, CardRole.SEQUENCE_CONTINUATION) == 0.90

    def test_front_and_unknown_role(self):
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.UNKNOWN) == 0.80
        assert calculate_role_compatibility(CardRole.UNKNOWN, CardRole.SEQUENCE_CONTINUATION) == 0.80

    def test_unknown_and_unknown(self):
        assert calculate_role_compatibility(CardRole.UNKNOWN, CardRole.UNKNOWN) == 0.50

    def test_standalone_cards_cannot_pair(self):
        assert calculate_role_compatibility(CardRole.STANDALONE, CardRole.SEQUENCE_CONTINUATION) == 0.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.STANDALONE) == 0.00
        assert calculate_role_compatibility(CardRole.STANDALONE, CardRole.STANDALONE) == 0.00

    def test_incompatible_front_front_or_back_back(self):
        assert calculate_role_compatibility(CardRole.SEQUENCE_START, CardRole.SEQUENCE_START) == 0.00
        assert calculate_role_compatibility(CardRole.SEQUENCE_CONTINUATION, CardRole.SEQUENCE_START) == 0.00


class TestCompositeHeuristicDistanceAndScreening:
    """Tests for composite scoring and pair screening."""

    def test_calculate_heuristic_distance_ideal_pair(self):
        fp_front = VisualFingerprint(
            card_id="card_01.jpg",
            image_path="/path/01.jpg",
            sha256_hash="abc",
            role=CardRole.SEQUENCE_START,
            paper_color=PaperColorInfo(tone_name="Cream", hex_approx="#F4E8C1", lab_l=90.0, lab_a=2.0, lab_b=18.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=24.0),
            ink=InkInfo(ink_type=InkType.BLUE_BALLPOINT, ink_color=InkColor.BLUE),
            artifacts=[PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_RIGHT)],
            boundary_text=BoundaryText(first_line="Title", last_line="Mix flour (over ->)", has_continuation_symbol=True),
        )

        fp_back = VisualFingerprint(
            card_id="card_02.jpg",
            image_path="/path/02.jpg",
            sha256_hash="def",
            role=CardRole.SEQUENCE_CONTINUATION,
            paper_color=PaperColorInfo(tone_name="Cream", hex_approx="#F4E8C1", lab_l=90.0, lab_a=2.0, lab_b=18.0),
            ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE, line_spacing_px=24.0),
            ink=InkInfo(ink_type=InkType.BLUE_BALLPOINT, ink_color=InkColor.BLUE),
            artifacts=[PhysicalArtifact(artifact_type=ArtifactType.OIL_GREASE_STAIN, quadrant=CardQuadrant.TOP_LEFT)],
            boundary_text=BoundaryText(first_line="(Directions cont)", last_line="Bake 20 mins."),
        )

        score = calculate_heuristic_distance(fp_front, fp_back, idx1=0, idx2=1)
        assert score >= 0.80

    def test_calculate_heuristic_distance_incompatible_role_pruned(self):
        fp1 = VisualFingerprint(
            card_id="c1.jpg",
            image_path="/path/c1.jpg",
            sha256_hash="111",
            role=CardRole.SEQUENCE_START,
        )
        fp2 = VisualFingerprint(
            card_id="c2.jpg",
            image_path="/path/c2.jpg",
            sha256_hash="222",
            role=CardRole.SEQUENCE_START,
        )
        assert calculate_heuristic_distance(fp1, fp2) == 0.00

    def test_screen_candidate_pairs_filters_and_sorts(self):
        fp_f1 = VisualFingerprint(card_id="01_front.jpg", image_path="/p/1", sha256_hash="1", role=CardRole.FRONT_ONLY)
        fp_b1 = VisualFingerprint(card_id="02_back.jpg", image_path="/p/2", sha256_hash="2", role=CardRole.BACK_ONLY)
        fp_standalone = VisualFingerprint(card_id="03_standalone.jpg", image_path="/p/3", sha256_hash="3", role=CardRole.STANDALONE)

        candidates = screen_candidate_pairs([fp_f1, fp_b1, fp_standalone], threshold=0.50)
        assert len(candidates) >= 1
        top_front, top_back, top_score = candidates[0]
        assert top_front.card_id == "01_front.jpg"
        assert top_back.card_id == "02_back.jpg"
        assert top_score >= 0.50

    def test_screen_candidate_pairs_empty_and_single(self):
        assert screen_candidate_pairs([]) == []
        fp_single = VisualFingerprint(card_id="01.jpg", image_path="/p/1", sha256_hash="1", role=CardRole.FRONT_ONLY)
        assert screen_candidate_pairs([fp_single]) == []
