"""Tests for WP-C2: Honest Semantic Continuity Verifier (Non-cheating).

Enforces:
1. INV-AC1: No manifest / fixture lookups.
2. INV-AC2: Opaque identifiers without filename semantics.
3. INV-AC4: Honest degradation on absent model or API errors.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from recipeboxer.ai.client import TextPart
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.correlation.semantic import GeminiSemanticVerifier, SetVerificationResponse
from recipeboxer.models.correlation import CorrelationProposal
from recipeboxer.models.domain import BoundaryText, CardRole, VisualFingerprint
from recipeboxer.models.failures import FailureReason, SemanticVerificationUnavailable


@pytest.fixture
def opaque_fingerprints():
    """Returns two opaque visual fingerprints with no boundary cues."""
    fp_a = VisualFingerprint(
        card_id="card_01a8f9e2",
        image_path="/tmp/nonexistent/01a8f9e2.jpg",
        sha256_hash="a" * 64,
        role=CardRole.UNKNOWN,
    )
    fp_b = VisualFingerprint(
        card_id="card_09c3d4b1",
        image_path="/tmp/nonexistent/09c3d4b1.jpg",
        sha256_hash="b" * 64,
        role=CardRole.UNKNOWN,
    )
    return fp_a, fp_b


@pytest.fixture
def boundary_cue_fingerprints():
    """Returns two opaque visual fingerprints with explicit boundary cues."""
    fp_a = VisualFingerprint(
        card_id="card_33f7a1b0",
        image_path="/tmp/nonexistent/33f7a1b0.jpg",
        sha256_hash="c" * 64,
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(
            first_line="Title Line",
            last_line="Mix 2 cups flour (over ->)",
            has_continuation_symbol=True,
            is_cut_off_mid_sentence=True,
        ),
    )
    fp_b = VisualFingerprint(
        card_id="card_77e2d9c4",
        image_path="/tmp/nonexistent/77e2d9c4.jpg",
        sha256_hash="d" * 64,
        role=CardRole.UNKNOWN,
        boundary_text=BoundaryText(
            first_line="(Directions cont.) Preheat oven to 375F",
            last_line="Cool on wire rack.",
        ),
    )
    return fp_a, fp_b


@pytest.mark.asyncio
async def test_no_key_degraded_proposal(opaque_fingerprints):
    """When no API key is provided and no client injected, raises SemanticVerificationUnavailable(NO_API_KEY)."""
    fp_a, fp_b = opaque_fingerprints
    verifier = GeminiSemanticVerifier(api_key=None, client=None)

    with pytest.raises(SemanticVerificationUnavailable) as exc_info:
        await verifier.verify_pair(fp_a, fp_b, heuristic_score=0.62)

    assert exc_info.value.reason == FailureReason.NO_API_KEY
    assert fp_a.card_id in exc_info.value.card_ids


@pytest.mark.asyncio
async def test_boundary_cue_fingerprints_higher_but_capped_scores(opaque_fingerprints, boundary_cue_fingerprints):
    """Without a model client, offline heuristics do not fabricate numbers (INV-AC4/AC6)."""
    fp_cue_a, fp_cue_b = boundary_cue_fingerprints
    verifier = GeminiSemanticVerifier(api_key=None, client=None)

    with pytest.raises(SemanticVerificationUnavailable) as exc_info:
        await verifier.verify_pair(fp_cue_a, fp_cue_b, heuristic_score=0.55)

    assert exc_info.value.reason == FailureReason.NO_API_KEY


@pytest.mark.asyncio
async def test_live_api_exception_handled_as_degraded_with_reason(opaque_fingerprints):
    """Live API errors raise SemanticVerificationUnavailable with typed failure reason."""
    fp_a, fp_b = opaque_fingerprints
    client = ScriptedLLMClient([httpx.ConnectError("Connection refused")])
    verifier = GeminiSemanticVerifier(llm_client=client)

    with pytest.raises(SemanticVerificationUnavailable) as exc_info:
        await verifier.verify_pair(fp_a, fp_b, heuristic_score=0.45)

    assert exc_info.value.reason in (FailureReason.MODEL_UNAVAILABLE, FailureReason.UNKNOWN)


@pytest.mark.asyncio
async def test_opaque_labels_sent_to_gemini(opaque_fingerprints):
    """Multimodal call strictly passes neutral labels and zero card IDs."""
    fp_a, fp_b = opaque_fingerprints
    client = ScriptedLLMClient([
        SetVerificationResponse(
            continuations=["Candidate 1"],
            rejected=[],
            evidence_quotes=[],
            confidence=0.88,
        )
    ])
    verifier = GeminiSemanticVerifier(llm_client=client)

    proposal = await verifier.verify_pair(fp_a, fp_b, heuristic_score=0.80)

    assert len(client.requests) == 1
    req = client.requests[0]

    # Verify neutral labels
    text_parts = [p.text for p in req.parts if isinstance(p, TextPart)]
    combined = "\n".join(text_parts)
    assert "Anchor:" in combined
    assert "Candidate 1:" in combined
    assert fp_a.card_id not in combined
    assert fp_b.card_id not in combined

    assert proposal.semantic_verified is True
    assert proposal.semantic_score == 0.88
    assert proposal.recommended_action == "PAIR"


def test_no_manifest_reference_in_module():
    """Module source code contains zero references to manifest.json or fixtures."""
    import recipeboxer.correlation.semantic as mod

    source = inspect.getsource(mod)
    assert "manifest.json" not in source
    assert "sample_cards" not in source
    assert "tests/fixtures" not in source
    assert "_MANIFEST_RECIPE_CLUSTER_CACHE" not in source
    assert "_get_manifest_recipe_cluster_lookup" not in source
    assert not hasattr(mod, "_MANIFEST_RECIPE_CLUSTER_CACHE")
    assert not hasattr(mod, "_get_manifest_recipe_cluster_lookup")


def test_correlation_proposal_defaults_and_backward_compatibility():
    """CorrelationProposal supports new degradation fields while remaining backward compatible."""
    # Default construction
    prop = CorrelationProposal(
        proposal_id="prop_test_001",
        ordered_card_ids=["card_1", "card_2"],
    )
    assert prop.semantic_verified is True
    assert prop.degraded_reason is None
    assert prop.semantic_score == 0.0

    # Explicit degraded construction
    degraded = CorrelationProposal(
        proposal_id="prop_test_002",
        ordered_card_ids=["card_3", "card_4"],
        semantic_verified=False,
        degraded_reason="NO_MODEL",
        semantic_score=0.5,
    )
    assert degraded.semantic_verified is False
    assert degraded.degraded_reason == "NO_MODEL"
    assert degraded.semantic_score == 0.5
