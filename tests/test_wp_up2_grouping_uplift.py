"""Tests for WP-UP2 Grouping Uplift (Round 2).

Verifies offline grouping recall restoration, continuation cue preservation,
calibrated link probabilities, and multi-card recipe reconstruction.
Uses fictitious test cards only (no corpus text).
"""

from __future__ import annotations

import pytest

from recipeboxer.correlation.background import DeckBackground
from recipeboxer.correlation.calibration import DEFAULT_CALIBRATION_PATH, LinkCalibrator
from recipeboxer.correlation.content import content_features
from recipeboxer.correlation.engine import FeatureToggles, GroupingEngine
from recipeboxer.models.correlation import SetVerification
from recipeboxer.models.domain import CardRole, VisualFingerprint


def _make_fp(
    card_id: str,
    *,
    visible_title: str | None = None,
    printed_markers: list[str] | None = None,
    first_lines: list[str] | None = None,
    last_lines: list[str] | None = None,
    role: CardRole = CardRole.SEQUENCE_START,
    looks_like_back: bool | None = None,
) -> VisualFingerprint:
    return VisualFingerprint(
        card_id=card_id,
        image_path=f"/fake/{card_id}",
        sha256_hash=f"hash_{card_id}",
        role=role,
        visible_title=visible_title,
        printed_markers=printed_markers or [],
        first_lines=first_lines or [],
        last_lines=last_lines or [],
        looks_like_back=looks_like_back,
    )


def test_title_position_continuation_recognition():
    """Card A back (no title) followed by Card B front (continuation title) is recognized."""
    fp_a = _make_fp("card1_back.jpg", visible_title=None, role=CardRole.SEQUENCE_CONTINUATION, looks_like_back=True)
    fp_b = _make_fp("card2_front.jpg", visible_title="Card 2: Traditional Panettone (Part 2)", role=CardRole.SEQUENCE_CONTINUATION, looks_like_back=False)

    feats = content_features(fp_a, fp_b)
    title_feat = next(f for f in feats if f.name == "title_position")

    assert title_feat.raw is not None
    assert title_feat.raw >= 0.90
    assert "continuation title" in title_feat.note


def test_marker_evidence_equal_page_continuation():
    """Card 1 Back ('2' for page 2) followed by Card 2 Front ('Card 2') does not trigger reverse penalty."""
    fp_a = _make_fp("card1_back.jpg", printed_markers=["2"], role=CardRole.SEQUENCE_CONTINUATION)
    fp_b = _make_fp("card2_front.jpg", printed_markers=["Card 2"], visible_title="Card 2: Heirloom Sourdough")

    feats = content_features(fp_a, fp_b)
    marker_feat = next(f for f in feats if f.name == "marker_evidence")

    assert marker_feat.raw is not None
    assert marker_feat.raw >= 0.80
    assert "Reverse page markers" not in marker_feat.note


def test_background_cue_neutral_and_positive_preservation():
    """Neutral cues have log_lr == 0.0, positive cues have log_lr >= 0.0, and ruling penalty is bounded."""
    fp_a = _make_fp("card_a.jpg")
    fp_b = _make_fp("card_b.jpg")
    bg = DeckBackground.fit([fp_a, fp_b])

    # Neutral sentence continuity must not penalize
    llr_neutral = bg.log_lr("sentence_continuity", 0.50, fp_a=fp_a, fp_b=fp_b)
    assert llr_neutral == 0.0

    # Positive sentence continuity must be non-negative
    llr_pos = bg.log_lr("sentence_continuity", 0.75, fp_a=fp_a, fp_b=fp_b)
    assert llr_pos is not None and llr_pos >= 0.20

    # Positive step continuity must be non-negative
    llr_step = bg.log_lr("step_continuity", 1.0, fp_a=fp_a, fp_b=fp_b)
    assert llr_step is not None and llr_step >= 0.10

    # Ruling mismatch penalty is bounded
    llr_ruling_mismatch = bg.log_lr("ruling", 0.0, fp_a=fp_a, fp_b=fp_b)
    assert llr_ruling_mismatch is not None and llr_ruling_mismatch >= -1.5


def test_link_calibrator_verified_continuation():
    """SetVerifier verified true continuation produces high probability across realistic log_lr ranges."""
    calibrator = LinkCalibrator.load(DEFAULT_CALIBRATION_PATH)

    # Across slightly negative to positive total_log_lr, verified positive edges maintain high p
    for test_log_lr in [-2.0, -1.0, 0.0, 1.0, 2.5]:
        p = calibrator.predict(test_log_lr, set_verified=True)
        assert p >= 0.85, f"p={p} at total_log_lr={test_log_lr} is too low for verified true link"

    # SetVerifier rejected link produces low probability
    for test_log_lr in [-2.0, 0.0, 2.0]:
        p_rej = calibrator.predict(test_log_lr, set_verified=False)
        assert p_rej <= 0.05, f"p={p_rej} at total_log_lr={test_log_lr} is too high for rejected link"


class MockMemorySetVerifyCache:
    """Mock cache providing pre-verified decisions for offline test cards."""

    def __init__(self, verifications: list[SetVerification]):
        self._by_anchor = {v.anchor: v for v in verifications}

    def get(self, key: str):
        return None

    def get_by_anchor(self, anchor_id: str):
        return self._by_anchor.get(anchor_id)


@pytest.mark.asyncio
async def test_grouping_engine_offline_multi_card_reconstruction():
    """GroupingEngine correctly reconstructs multi-card chains offline using cached set verifications."""
    # 3-card recipe: card1_front -> card1_back -> card2_front, plus 1 standalone
    fp1_f = _make_fp(
        "c1_f.jpg",
        visible_title="Grandma's Cinnamon Rolls",
        first_lines=["Grandma's Cinnamon Rolls", "Ingredients:"],
        last_lines=["Mix dough until elastic."],
        looks_like_back=False,
        role=CardRole.SEQUENCE_START,
    )
    fp1_b = _make_fp(
        "c1_b.jpg",
        visible_title=None,
        first_lines=["(Directions continued)", "Let rise 1 hour."],
        last_lines=["Roll out dough."],
        looks_like_back=True,
        role=CardRole.SEQUENCE_CONTINUATION,
    )
    fp2_f = _make_fp(
        "c2_f.jpg",
        visible_title="Card 2: Grandma's Cinnamon Rolls (Filling & Icing)",
        first_lines=["Card 2: Filling and Icing", "Spread butter and cinnamon."],
        last_lines=["Bake at 375 for 25 mins."],
        looks_like_back=False,
        role=CardRole.SEQUENCE_CONTINUATION,
    )
    fp_standalone = _make_fp(
        "c_solo.jpg",
        visible_title="Quick Lemonade",
        first_lines=["Quick Lemonade", "Squeeze lemons into pitcher."],
        last_lines=["Stir with ice and serve."],
        looks_like_back=False,
        role=CardRole.STANDALONE,
    )

    fps = [fp1_f, fp1_b, fp2_f, fp_standalone]

    mock_ver = SetVerification(
        anchor="c1_f.jpg",
        continuations=["c1_b.jpg", "c2_f.jpg"],
        rejected=["c_solo.jpg"],
        confidence=0.98,
        evidence_quotes=["Grandma's Cinnamon Rolls", "Let rise 1 hour"],
        model="gemini-3.8-flash",
        prompt_id="set_verify_v1",
        prompt_version="b3e96a5c",
    )
    mock_cache = MockMemorySetVerifyCache([mock_ver])

    class MockImageProvider:
        def get(self, card_id: str) -> bytes | None:
            return b"fake_bytes"

    from recipeboxer.correlation.semantic import SetVerifier

    verifier = SetVerifier(llm_client=None, cache=mock_cache)
    calibrator = LinkCalibrator.load(DEFAULT_CALIBRATION_PATH)
    engine = GroupingEngine(
        calibrator=calibrator,
        set_verifier=verifier,
    )

    result = await engine.group(fps, images=MockImageProvider(), toggles=FeatureToggles(set_verify=True))

    # Should find 2 chains: cinnamon rolls [c1_f, c1_b, c2_f] and standalone [c_solo]
    cinnamon_chain = next((ch for ch in result.chains if "c1_f.jpg" in ch), None)
    assert cinnamon_chain is not None
    assert cinnamon_chain == ["c1_f.jpg", "c1_b.jpg", "c2_f.jpg"]

    solo_chain = next((ch for ch in result.chains if "c_solo.jpg" in ch), None)
    assert solo_chain is not None
    assert solo_chain == ["c_solo.jpg"]
