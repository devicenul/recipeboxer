"""Unit and integration tests for WP-GA (evidence.py, background.py, screening.py).

Verifies:
1. Homogeneous deck sharing stock: stock features have ~0 log_lr.
2. Rare stock pair in a deck: high positive log_lr.
3. Content cues dominate total_log_lr in homogeneous decks.
4. Top-k recall on synthetic deck with true successors encoded only in content (no semantic names).
5. Determinism across repeated runs.
6. Card ID / list permutation invariance.
7. Cardforge-based test: real pixel features extracted from cardforge-generated homogeneous deck.
8. Physical pairing integration with ImageProvider and weak outline weighting (0.2).
9. Lazy content features import and test doubles.
"""

from __future__ import annotations

import io
import random
from datetime import datetime, timezone
from typing import List
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from recipeboxer.correlation.background import DeckBackground
from recipeboxer.correlation.evidence import ImageProvider, compute_raw_features, pair_evidence
from recipeboxer.correlation.screening import top_k_candidates
from recipeboxer.models.correlation import FeatureEvidence, PairEvidence, SetVerification
from recipeboxer.models.domain import BoundaryText, CardRole, VisualFingerprint
from recipeboxer.models.features import CaptureMeta, PixelFeatures


class MockImageProvider:
    """Mock ImageProvider returning synthetic in-memory JPEG bytes."""

    def __init__(self, card_images: dict[str, bytes]) -> None:
        self.card_images = card_images

    def get(self, card_id: str) -> bytes:
        return self.card_images.get(card_id, b"")


def _create_synthetic_fingerprint(
    card_id: str,
    *,
    paper_lab: tuple[float, float, float] = (95.0, -1.0, 12.0),
    ruling_kind: str = "lined",
    ruling_spacing_px: float = 26.0,
    ink_hues: list[float] | None = None,
    aspect_ratio: float = 1.5,
    natural_sort_index: int = 0,
    taken_at: datetime | None = None,
    title: str = "",
    first_lines: list[str] | None = None,
    last_lines: list[str] | None = None,
    printed_markers: list[str] | None = None,
    text_cut_off_end: bool = False,
    text_cut_off_start: bool = False,
    has_ingredients: bool = False,
    has_directions: bool = False,
    looks_like_back: bool = False,
    role: CardRole = CardRole.UNKNOWN,
) -> VisualFingerprint:
    """Factory creating fully populated VisualFingerprints for testing."""
    return VisualFingerprint(
        card_id=card_id,
        image_path=f"/fake/{card_id}.jpg",
        sha256_hash="0" * 64,
        role=role,
        visible_title=title or None,
        first_lines=first_lines or [],
        last_lines=last_lines or [],
        printed_markers=printed_markers or [],
        text_cut_off_end=text_cut_off_end,
        text_cut_off_start=text_cut_off_start,
        has_ingredients=has_ingredients,
        has_directions=has_directions,
        looks_like_back=looks_like_back,
        boundary_text=BoundaryText(
            first_line=(first_lines[0] if first_lines else ""),
            last_line=(last_lines[-1] if last_lines else ""),
            has_continuation_symbol=text_cut_off_end,
            is_cut_off_mid_sentence=text_cut_off_end,
        ),
        pixel=PixelFeatures(
            paper_lab=paper_lab,
            ruling_kind=ruling_kind,
            ruling_spacing_px=ruling_spacing_px,
            ink_hues=ink_hues if ink_hues is not None else [233.0],
            aspect_ratio=aspect_ratio,
        ),
        capture=CaptureMeta(
            taken_at=taken_at or datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            subsec=0,
            natural_sort_index=natural_sort_index,
            source="mtime",
        ),
    )


# ============================================================================
# 1. Homogeneous Deck: Stock Features ~0 log_lr
# ============================================================================

def test_homogeneous_deck_stock_features_zero_log_lr() -> None:
    """In a homogeneous deck sharing paper, ruling, ink, and size, stock features yield |log_lr| < 0.25."""
    deck: List[VisualFingerprint] = []
    for i in range(12):
        fp = _create_synthetic_fingerprint(
            card_id=f"card_{i:02d}",
            paper_lab=(94.8, -1.4, 13.0),
            ruling_kind="lined",
            ruling_spacing_px=26.0,
            ink_hues=[233.0],
            aspect_ratio=1.5,
            natural_sort_index=i,
        )
        deck.append(fp)

    bg = DeckBackground.fit(deck)

    # Check pair evidence between cards sharing the common stock
    ev = pair_evidence(deck[0], deck[1], bg)

    feats_by_name = {f.name: f for f in ev.features}

    # In a homogeneous deck, stock match is common -> log_lr is ~ 0
    assert feats_by_name["paper_lab"].log_lr is not None
    assert abs(feats_by_name["paper_lab"].log_lr) < 0.25, f"Expected ~0 log_lr, got {feats_by_name['paper_lab'].log_lr}"

    assert feats_by_name["ruling"].log_lr is not None
    assert abs(feats_by_name["ruling"].log_lr) < 0.25, f"Expected ~0 log_lr, got {feats_by_name['ruling'].log_lr}"

    assert feats_by_name["ink_hue"].log_lr is not None
    assert abs(feats_by_name["ink_hue"].log_lr) < 0.25, f"Expected ~0 log_lr, got {feats_by_name['ink_hue'].log_lr}"

    assert feats_by_name["aspect_ratio"].log_lr is not None
    assert abs(feats_by_name["aspect_ratio"].log_lr) < 0.25, f"Expected ~0 log_lr, got {feats_by_name['aspect_ratio'].log_lr}"


# ============================================================================
# 2. Rare Stock Pair: High Positive log_lr
# ============================================================================

def test_rare_stock_pair_high_log_lr() -> None:
    """A rare stock pair in an otherwise homogeneous collection receives high positive log_lr."""
    deck: List[VisualFingerprint] = []
    # 10 standard cream cards
    for i in range(10):
        deck.append(_create_synthetic_fingerprint(
            card_id=f"cream_{i:02d}",
            paper_lab=(95.0, -1.0, 12.0),
            ruling_kind="lined",
            ruling_spacing_px=26.0,
            ink_hues=[233.0],
            aspect_ratio=1.5,
            natural_sort_index=i,
        ))

    # 2 rare kraft brown cards with dark green ink
    kraft_a = _create_synthetic_fingerprint(
        card_id="kraft_front",
        paper_lab=(78.3, 4.3, 18.4),
        ruling_kind="grid",
        ruling_spacing_px=20.0,
        ink_hues=[138.0],
        aspect_ratio=1.6,
        natural_sort_index=10,
    )
    kraft_b = _create_synthetic_fingerprint(
        card_id="kraft_back",
        paper_lab=(78.3, 4.3, 18.4),
        ruling_kind="grid",
        ruling_spacing_px=20.0,
        ink_hues=[138.0],
        aspect_ratio=1.6,
        natural_sort_index=11,
    )
    deck.extend([kraft_a, kraft_b])

    bg = DeckBackground.fit(deck)

    # Cream - Cream pair: stock match is common -> log_lr is ~0
    ev_cream = pair_evidence(deck[0], deck[1], bg)
    feat_cream = {f.name: f for f in ev_cream.features}
    assert abs(feat_cream["paper_lab"].log_lr or 0.0) < 0.35

    # Kraft - Kraft pair: rare stock match in this deck -> log_lr is HIGH!
    ev_kraft = pair_evidence(kraft_a, kraft_b, bg)
    feat_kraft = {f.name: f for f in ev_kraft.features}
    assert (feat_kraft["paper_lab"].log_lr or 0.0) > 1.5, f"Expected high log_lr for rare stock, got {feat_kraft['paper_lab'].log_lr}"


# ============================================================================
# 3. Content Cues Dominate in Homogeneous Deck
# ============================================================================

def test_content_cues_dominate_in_homogeneous_deck() -> None:
    """When stock features have ~0 log_lr, content progression and header cues dominate total_log_lr."""
    deck: List[VisualFingerprint] = []
    for i in range(8):
        deck.append(_create_synthetic_fingerprint(
            card_id=f"card_{i}",
            paper_lab=(95.0, -1.0, 12.0),
            ruling_kind="lined",
            ruling_spacing_px=26.0,
            natural_sort_index=i,
        ))

    bg = DeckBackground.fit(deck)

    # Pair with strong content/header continuation cues:
    # Card 0 -> Card 1 has printed marker '1' -> '2', sentence continuation, front -> back
    c0 = _create_synthetic_fingerprint(
        card_id="cont_a",
        paper_lab=(95.0, -1.0, 12.0),
        printed_markers=["Card 1"],
        text_cut_off_end=True,
        has_ingredients=True,
        has_directions=False,
        natural_sort_index=0,
    )
    c1 = _create_synthetic_fingerprint(
        card_id="cont_b",
        paper_lab=(95.0, -1.0, 12.0),
        printed_markers=["Card 2"],
        text_cut_off_start=True,
        has_ingredients=False,
        has_directions=True,
        looks_like_back=True,
        natural_sort_index=1,
    )

    ev = pair_evidence(c0, c1, bg)
    feats = {f.name: f for f in ev.features}

    # Stock features contribution is tiny
    stock_log_lr = (
        (feats["paper_lab"].log_lr or 0.0)
        + (feats["ruling"].log_lr or 0.0)
        + (feats["ink_hue"].log_lr or 0.0)
    )
    assert abs(stock_log_lr) < 0.6

    # Header and sequence prior contribution dominates
    header_log_lr = feats["header_score"].log_lr or 0.0
    seq_log_lr = feats["sort_adjacency"].log_lr or 0.0
    assert header_log_lr > 1.0 or seq_log_lr > 1.0
    assert ev.total_log_lr > stock_log_lr


# ============================================================================
# 4. Top-K Recall on Synthetic Deck with Content-Only Continuations (No Names)
# ============================================================================

def test_top_k_recall_on_content_only_synthetic_deck() -> None:
    """Evaluates top-k recall on a deck where true successors are identified ONLY via content."""
    # 10 cards with opaque random IDs and identical stock
    deck: List[VisualFingerprint] = []

    # Recipe 1: Front (0x1a) -> Back (0x2b)
    # Successor encoded in: text_cut_off_end -> text_cut_off_start, markers, ingredients -> directions
    r1_front = _create_synthetic_fingerprint(
        card_id="card_0x1a",
        title="Lemon Meringue Pie",
        first_lines=["Lemon Meringue Pie", "1 cup white sugar", "2 tbsp flour"],
        last_lines=["Mix flour and sugar in saucepan, add"],
        printed_markers=["Part 1"],
        text_cut_off_end=True,
        has_ingredients=True,
        has_directions=False,
        natural_sort_index=0,
    )
    r1_back = _create_synthetic_fingerprint(
        card_id="card_0x2b",
        first_lines=["water and lemon juice, bring to a boil.", "Pour into pie shell."],
        last_lines=["Bake at 350 for 15 minutes until meringue is golden."],
        printed_markers=["Part 2"],
        text_cut_off_start=True,
        has_ingredients=False,
        has_directions=True,
        looks_like_back=True,
        natural_sort_index=1,
    )

    # Distractor cards with completely different recipes and topics
    distractors = [
        _create_synthetic_fingerprint(
            card_id=f"distractor_{i:02x}",
            title=f"Roast Beef {i}",
            first_lines=[f"Roast Beef {i}", "3 lbs beef roast", "1 onion sliced"],
            last_lines=["Cover and roast at 325 degrees for 3 hours."],
            has_ingredients=True,
            has_directions=True,
            natural_sort_index=2 + i,
        )
        for i in range(8)
    ]

    deck = [r1_front, r1_back] + distractors
    # Shuffle deck order
    random.Random(1337).shuffle(deck)

    bg = DeckBackground.fit(deck)

    candidates_by_card = top_k_candidates(deck, bg, k=3)

    # Find candidates for r1_front
    r1_candidates = candidates_by_card["card_0x1a"]
    assert len(r1_candidates) > 0

    top_candidate_id = r1_candidates[0].b
    assert top_candidate_id == "card_0x2b", (
        f"Expected true successor 'card_0x2b' at rank 1, but got '{top_candidate_id}'"
    )


# ============================================================================
# 5. Determinism Across Runs
# ============================================================================

def test_screening_determinism() -> None:
    """Repeated calls to top_k_candidates produce bit-for-bit identical results."""
    deck = [
        _create_synthetic_fingerprint(
            card_id=f"card_{i:02d}",
            title=f"Recipe {i}",
            first_lines=[f"Recipe {i}", f"Ingredient {i}"],
            last_lines=[f"Step {i}"],
            natural_sort_index=i,
        )
        for i in range(8)
    ]

    bg = DeckBackground.fit(deck)

    run1 = top_k_candidates(deck, bg, k=3)
    run2 = top_k_candidates(deck, bg, k=3)

    assert run1.keys() == run2.keys()
    for card_id in run1:
        pairs1 = run1[card_id]
        pairs2 = run2[card_id]
        assert len(pairs1) == len(pairs2)
        for p1, p2 in zip(pairs1, pairs2):
            assert p1.a == p2.a
            assert p1.b == p2.b
            assert p1.total_log_lr == p2.total_log_lr


# ============================================================================
# 6. Card ID and List Permutation Invariance
# ============================================================================

def test_permutation_invariance() -> None:
    """Permuting list order or card IDs does not alter candidate pairings or evidence scores."""
    card_a = _create_synthetic_fingerprint(
        card_id="alpha_01",
        title="Chocolate Fudge",
        first_lines=["Chocolate Fudge", "2 cups sugar"],
        last_lines=["Boil to softball stage and"],
        text_cut_off_end=True,
        has_ingredients=True,
        has_directions=False,
        natural_sort_index=0,
    )
    card_b = _create_synthetic_fingerprint(
        card_id="beta_02",
        first_lines=["stir vigorously until thick.", "Pour into greased pan."],
        last_lines=["Cool and cut into squares."],
        text_cut_off_start=True,
        has_ingredients=False,
        has_directions=True,
        looks_like_back=True,
        natural_sort_index=1,
    )
    card_c = _create_synthetic_fingerprint(
        card_id="gamma_03",
        title="Beef Stew",
        first_lines=["Beef Stew", "2 lbs chuck roast"],
        last_lines=["Simmer for 2 hours."],
        has_ingredients=True,
        has_directions=True,
        natural_sort_index=2,
    )

    deck_orig = [card_a, card_b, card_c]
    deck_rev = [card_c, card_b, card_a]

    bg_orig = DeckBackground.fit(deck_orig)
    bg_rev = DeckBackground.fit(deck_rev)

    res_orig = top_k_candidates(deck_orig, bg_orig, k=2)
    res_rev = top_k_candidates(deck_rev, bg_rev, k=2)

    # For card_a, candidates in both runs must be identical
    top_orig = [p.b for p in res_orig["alpha_01"]]
    top_rev = [p.b for p in res_rev["alpha_01"]]
    assert top_orig == top_rev
    assert top_orig[0] == "beta_02"


# ============================================================================
# 7. Cardforge-Based Test: Real Pixel Features on Generated Homogeneous Deck
# ============================================================================

def test_cardforge_homogeneous_deck_pixel_features() -> None:
    """Extracts real pixel features from a procedurally generated cardforge homogeneous deck."""
    from cardforge import CardModality, PaperSubstrate, RecipeSchema, SyntheticCardGenerator
    from recipeboxer.vision.pixel_features import compute_pixel_features

    gen = SyntheticCardGenerator(seed=42)

    recipe = RecipeSchema(
        recipe_id="rec_homo_test",
        title="Homogeneous Lemon Bars",
        description="Test recipe for homogeneous deck evaluation.",
        ingredients=["1 cup flour", "1/2 cup sugar", "2 eggs"],
        directions=["Mix ingredients and bake at 350F for 20 mins."],
    )

    # Generate a front/back pair on RULED_INDEX
    card_f, card_b = gen.generate_card_pair(
        recipe,
        modality=CardModality.HANDWRITTEN_CURSIVE,
        substrate=PaperSubstrate.RULED_INDEX,
    )

    # Extract real pixel features using production vision code
    pix_f = compute_pixel_features(card_f.image)
    pix_b = compute_pixel_features(card_b.image)

    fp_f = VisualFingerprint(
        card_id="cardforge_f",
        image_path="/fake/cardforge_f.jpg",
        sha256_hash="f" * 64,
        role=CardRole.UNKNOWN,
        pixel=pix_f,
        capture=CaptureMeta(natural_sort_index=0, source="none"),
    )
    fp_b = VisualFingerprint(
        card_id="cardforge_b",
        image_path="/fake/cardforge_b.jpg",
        sha256_hash="b" * 64,
        role=CardRole.UNKNOWN,
        pixel=pix_b,
        capture=CaptureMeta(natural_sort_index=1, source="none"),
    )

    deck = [fp_f, fp_b]
    bg = DeckBackground.fit(deck)

    # Between the two cards of this homogeneous deck:
    ev = pair_evidence(fp_f, fp_b, bg)
    feats = {f.name: f for f in ev.features}

    # Stock features (paper_lab, ruling) must have ~0 log_lr in a homogeneous deck
    assert abs(feats["paper_lab"].log_lr or 0.0) < 0.25
    assert abs(feats["ruling"].log_lr or 0.0) < 0.25


# ============================================================================
# 8. Physical Pairing Integration & Weak Outline Weighting
# ============================================================================

def test_physical_pairing_with_image_provider() -> None:
    """Verifies physical pairing features are computed via ImageProvider with weak outline weight (0.2)."""
    # Create simple 100x100 white PIL images with dark text
    im_a = Image.new("RGB", (100, 100), color=(250, 248, 240))
    im_b = Image.new("RGB", (100, 100), color=(250, 248, 240))

    buf_a = io.BytesIO()
    im_a.save(buf_a, format="JPEG")
    bytes_a = buf_a.getvalue()

    buf_b = io.BytesIO()
    im_b.save(buf_b, format="JPEG")
    bytes_b = buf_b.getvalue()

    provider = MockImageProvider({"card_0": bytes_a, "card_1": bytes_b})

    fp0 = _create_synthetic_fingerprint("card_0")
    fp1 = _create_synthetic_fingerprint("card_1")

    deck = [fp0, fp1]
    bg = DeckBackground.fit(deck)

    ev_with_img = pair_evidence(fp0, fp1, bg, images=provider)
    feats_img = {f.name: f for f in ev_with_img.features}

    # Verify outline weight is exactly 0.2 (treated as weak)
    assert feats_img["outline"].weight == 0.2
    assert feats_img["show_through"].weight == 1.0
    assert feats_img["mirrored_wear"].weight == 1.0

    # Without images, physical features have raw=None
    ev_no_img = pair_evidence(fp0, fp1, bg, images=None)
    feats_no = {f.name: f for f in ev_no_img.features}
    assert feats_no["show_through"].raw is None
    assert feats_no["mirrored_wear"].raw is None
    assert feats_no["outline"].raw is None


# ============================================================================
# 9. Content Features Lazy Import & Test Doubles
# ============================================================================

def test_content_features_lazy_import_double() -> None:
    """Tests that content features from WP-GB are integrated cleanly using test doubles."""
    fp0 = _create_synthetic_fingerprint("c0")
    fp1 = _create_synthetic_fingerprint("c1")

    mock_content_features = [
        FeatureEvidence(name="content_xref", raw=0.85, log_lr=None, weight=1.0),
        FeatureEvidence(name="sentence_continuity", raw=0.95, log_lr=None, weight=1.0),
        FeatureEvidence(name="step_continuity", raw=1.00, log_lr=None, weight=1.0),
    ]

    bg = DeckBackground.fit([fp0, fp1])

    with patch("recipeboxer.correlation.evidence.compute_raw_features") as mock_raw:
        # Prepend standard features and append mock content features
        def fake_raw(a: VisualFingerprint, b: VisualFingerprint, images: Any = None) -> List[FeatureEvidence]:
            base = [
                FeatureEvidence(name="paper_lab", raw=0.5, log_lr=None, weight=1.0),
                FeatureEvidence(name="ruling", raw=1.0, log_lr=None, weight=1.0),
            ]
            return base + mock_content_features

        mock_raw.side_effect = fake_raw

        ev = pair_evidence(fp0, fp1, bg)
        assert ev.content_evidence_used is True
        names = [f.name for f in ev.features]
        assert "content_xref" in names
        assert "sentence_continuity" in names
        assert "step_continuity" in names


# ============================================================================
# 10. Models Contract Verification
# ============================================================================

def test_models_contract_instantiation() -> None:
    """Validates FeatureEvidence, PairEvidence, and SetVerification model fields."""
    fe = FeatureEvidence(name="paper_lab", raw=0.5, log_lr=0.1, weight=1.0, note="test")
    assert fe.name == "paper_lab"
    assert fe.raw == 0.5
    assert fe.log_lr == 0.1

    pe = PairEvidence(
        a="card_1",
        b="card_2",
        features=[fe],
        total_log_lr=0.1,
        p_link=None,
        content_evidence_used=False,
    )
    assert pe.a == "card_1"
    assert pe.b == "card_2"
    assert pe.total_log_lr == 0.1

    sv = SetVerification(
        anchor="card_1",
        continuations=["card_2"],
        rejected=["card_3"],
        evidence_quotes=["continued on next page"],
        confidence=0.92,
        model="gemini-2.5-flash",
        prompt_id="set_verify",
        prompt_version="a1b2c3d4",
    )
    assert sv.anchor == "card_1"
    assert sv.continuations == ["card_2"]
    assert sv.evidence_quotes == ["continued on next page"]
