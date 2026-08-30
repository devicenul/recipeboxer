"""End-to-end verification of multi-card sequence correlation and 0 dropped cards on the 23-card sample corpus."""

import asyncio
from pathlib import Path
import pytest

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession
from recipeboxer.ingestion.scanner import scan_card_directory
from recipeboxer.models.pipeline import PipelineState


@pytest.mark.asyncio
async def test_sample_card_corpus_e2e_correlation_and_retention():
    """Verify all 23 cards in sample_cards are correlated into 12 proposals with 0 dropped cards."""
    scans_dir = Path("tests/fixtures/sample_cards")
    cards = scan_card_directory(scans_dir)
    assert len(cards) == 23, f"Expected 23 sample cards, found {len(cards)}"

    agent = RecipeBoxerAgent()
    session = PipelineSession(session_id="test_verify_corpus")
    session.cards = cards

    task = asyncio.create_task(agent.run_pipeline(session))

    # Wait for HITL pause
    for _ in range(200):
        if session.state == PipelineState.HITL_PENDING:
            break
        await asyncio.sleep(0.05)

    assert session.state == PipelineState.HITL_PENDING, f"Session failed to reach HITL_PENDING: {session.state}"

    # 1. Verify Fruitcake 4-card sequence
    fc_props = [
        p for p in session.proposals
        if any("wedding_fruitcake" in cid for cid in p.ordered_card_ids)
    ]
    assert len(fc_props) == 1, f"Expected exactly 1 Fruitcake proposal, found {len(fc_props)}"
    fc_prop = fc_props[0]
    assert fc_prop.part_count == 4
    assert len(fc_prop.ordered_card_ids) == 4
    assert fc_prop.ordered_card_ids == [
        "rec_mcs_wedding_fruitcake_1942_card1_front.jpg",
        "rec_mcs_wedding_fruitcake_1942_card1_back.jpg",
        "rec_mcs_wedding_fruitcake_1942_card2_front.jpg",
        "rec_mcs_wedding_fruitcake_1942_card2_back.jpg",
    ]

    # 2. Verify Turkey Stuffing 3-card sequence
    turkey_props = [
        p for p in session.proposals
        if any("turkey_stuffing" in cid for cid in p.ordered_card_ids)
    ]
    assert len(turkey_props) == 1, f"Expected exactly 1 Turkey Stuffing proposal, found {len(turkey_props)}"
    turkey_prop = turkey_props[0]
    assert turkey_prop.part_count == 3
    assert len(turkey_prop.ordered_card_ids) == 3
    assert turkey_prop.ordered_card_ids == [
        "rec_mcs_turkey_stuffing_feast_card1_front.jpg",
        "rec_mcs_turkey_stuffing_feast_card1_back.jpg",
        "rec_mcs_turkey_stuffing_feast_card2_front.jpg",
    ]

    # 3. Verify total cards accounted for = 23 with zero dropped cards
    all_accounted = [cid for p in session.proposals for cid in p.ordered_card_ids]
    assert len(all_accounted) == 23, f"Expected 23 accounted cards, got {len(all_accounted)}"
    assert len(set(all_accounted)) == 23, f"Duplicate card IDs in proposals: {all_accounted}"
    assert len(session.proposals) == 12, f"Expected 12 proposals, got {len(session.proposals)}"

    # 4. Verify 2-card pairs
    expected_pairs = [
        ("rec_vhc_apple_crisp_1948_front.jpg", "rec_vhc_apple_crisp_1948_back.jpg"),
        ("rec_vhc_rhubarb_pie_1965_front.jpg", "rec_vhc_rhubarb_pie_1965_back.jpg"),
        ("rec_typ_beef_stroganoff_1956_front.jpg", "rec_typ_beef_stroganoff_1956_back.jpg"),
        ("rec_typ_tomato_soup_cake_1952_front.jpg", "rec_typ_tomato_soup_cake_1952_back.jpg"),
        ("rec_clp_crisco_fried_chicken_front.jpg", "rec_clp_crisco_fried_chicken_back.jpg"),
        ("rec_dmg_moms_lasagna_1971_front.jpg", "rec_dmg_moms_lasagna_1971_back.jpg"),
    ]
    for front_id, back_id in expected_pairs:
        pair_prop = next(
            (p for p in session.proposals if front_id in p.ordered_card_ids),
            None,
        )
        assert pair_prop is not None, f"Missing proposal for pair {front_id}"
        assert pair_prop.ordered_card_ids == [front_id, back_id], f"Incorrect ordering for {front_id}: {pair_prop.ordered_card_ids}"

    # 5. Verify standalones
    expected_standalones = [
        "rec_clp_condensed_milk_pie_1962_standalone.jpg",
        "rec_dmg_butter_shortbread_standalone.jpg",
        "rec_sta_quick_chocolate_fudge_standalone.jpg",
        "rec_sta_southern_pecan_pie_standalone.jpg",
    ]
    for std_id in expected_standalones:
        std_prop = next(
            (p for p in session.proposals if std_id in p.ordered_card_ids),
            None,
        )
        assert std_prop is not None, f"Missing proposal for standalone {std_id}"
        assert std_prop.ordered_card_ids == [std_id]
        assert std_prop.part_count == 1

    session.cancel()
    try:
        await task
    except Exception:
        pass
