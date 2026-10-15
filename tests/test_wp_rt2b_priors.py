"""Unit tests for FIND-04: Uncalibrated confidence priors remediation.

Verifies:
1. Single-card standalone proposals have confidence = None (uncalibrated priors removed).
2. Multi-card proposals report bottleneck link probability min(t_scores) or None.
3. Named structural prior constants defined in optimizer.py.
4. CorrelationProposal.confidence is Optional[float] with default None.
"""

from __future__ import annotations

import pytest

from recipeboxer.correlation.engine import GroupingEngine
from recipeboxer.correlation.optimizer import (
    STRUCTURAL_PRIOR_COMPLETE_DEFAULT,
    STRUCTURAL_PRIOR_COMPLETE_STANDALONE,
    STRUCTURAL_PRIOR_EXPECTS_CONT_DEFAULT,
    STRUCTURAL_PRIOR_EXPECTS_CONT_LOW,
    STRUCTURAL_PRIOR_EXPECTS_CONT_START,
    STRUCTURAL_PRIOR_START_DEFAULT,
    STRUCTURAL_PRIOR_START_TITLE,
    PairingOptimizer,
)
from recipeboxer.models.correlation import CorrelationProposal
from recipeboxer.models.domain import CardRole, VisualFingerprint


def test_correlation_proposal_confidence_default_none():
    """Verify that CorrelationProposal.confidence defaults to None."""
    prop = CorrelationProposal(
        proposal_id="test_prop",
        ordered_card_ids=["card1.jpg"],
        part_count=1,
    )
    assert prop.confidence is None


def test_structural_priors_named_constants():
    """Verify that structural priors are parameterized constants, not magic numbers."""
    assert STRUCTURAL_PRIOR_START_TITLE == 0.85
    assert STRUCTURAL_PRIOR_START_DEFAULT == 0.50
    assert STRUCTURAL_PRIOR_COMPLETE_STANDALONE == 0.90
    assert STRUCTURAL_PRIOR_COMPLETE_DEFAULT == 0.50
    assert STRUCTURAL_PRIOR_EXPECTS_CONT_START == 0.85
    assert STRUCTURAL_PRIOR_EXPECTS_CONT_LOW == 0.15
    assert STRUCTURAL_PRIOR_EXPECTS_CONT_DEFAULT == 0.50


def test_optimizer_standalone_confidence_derived_from_role_or_none():
    """Verify optimizer derives standalone confidence from role_confidence, or None if absent."""
    optimizer = PairingOptimizer()
    fp1 = VisualFingerprint(
        card_id="card_standalone.jpg",
        image_path="/dummy/path.jpg",
        sha256_hash="abc",
        role=CardRole.STANDALONE,
        role_confidence=0.95,
    )

    result1 = optimizer.optimize_proposals([fp1], proposals=[])
    assert len(result1.proposals) == 1
    # WP-RT3FIX (RT3-03): role_confidence is a classification prior, not grouping evidence -> None
    assert result1.proposals[0].confidence is None

    # When role_confidence is None
    fp2 = VisualFingerprint(
        card_id="card_standalone_2.jpg",
        image_path="/dummy/path.jpg",
        sha256_hash="abc2",
        role=CardRole.STANDALONE,
    )
    fp2.role_confidence = None  # type: ignore[assignment]
    result2 = optimizer.optimize_proposals([fp2], proposals=[])
    assert len(result2.proposals) == 1
    assert result2.proposals[0].confidence is None


def test_optimizer_multicard_confidence_reports_bottleneck_link():
    """Verify optimizer sets multi-card proposal confidence to bottleneck link score min(t_scores)."""
    optimizer = PairingOptimizer()
    fp1 = VisualFingerprint(card_id="c1.jpg", image_path="/p1", sha256_hash="h1", role=CardRole.SEQUENCE_START)
    fp2 = VisualFingerprint(card_id="c2.jpg", image_path="/p2", sha256_hash="h2", role=CardRole.SEQUENCE_CONTINUATION)

    pairwise_prop = CorrelationProposal(
        proposal_id="p12",
        ordered_card_ids=["c1.jpg", "c2.jpg"],
        part_count=2,
        transition_scores=[0.88],
        confidence=0.88,
        recommended_action="PAIR",
    )

    result = optimizer.optimize_proposals([fp1, fp2], proposals=[pairwise_prop])
    assert len(result.proposals) == 1
    seq_prop = result.proposals[0]
    assert seq_prop.ordered_card_ids == ["c1.jpg", "c2.jpg"]
    assert seq_prop.confidence == 0.88


@pytest.mark.asyncio
async def test_grouping_engine_single_card_proposal_confidence_none(tmp_path):
    """Verify GroupingEngine single card proposals output confidence = None."""
    from recipeboxer.correlation.engine import TempDirImageProvider

    engine = GroupingEngine()
    fp = VisualFingerprint(
        card_id="card_solo.jpg",
        image_path="/dummy/path.jpg",
        sha256_hash="xyz",
        role=CardRole.STANDALONE,
        role_confidence=0.92,
    )

    res = await engine.group([fp], images=TempDirImageProvider(tmp_path))
    assert len(res.proposals) == 1
    assert res.proposals[0].confidence is None
