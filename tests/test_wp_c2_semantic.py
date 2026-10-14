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

from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.models.correlation import CorrelationProposal
from recipeboxer.models.domain import BoundaryText, CardRole, VisualFingerprint


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
    """When no API key is provided and no client injected, proposal is unverified and UNCERTAIN."""
    fp_a, fp_b = opaque_fingerprints
    verifier = GeminiSemanticVerifier(api_key=None, client=None)

    proposal = await verifier.verify_pair(fp_a, fp_b, heuristic_score=0.62)

    assert isinstance(proposal, CorrelationProposal)
    assert proposal.semantic_verified is False
    assert proposal.degraded_reason == "NO_MODEL"
    assert proposal.recommended_action == "UNCERTAIN"
    assert proposal.semantic_score != 0.95
    assert proposal.semantic_score == 0.5
    # Confidence reflects heuristic score only when no model and no text cues
    assert proposal.confidence == 0.62
    assert "95%" not in proposal.continuity_explanation
    assert "Strong semantic continuity" not in proposal.continuity_explanation


@pytest.mark.asyncio
async def test_boundary_cue_fingerprints_higher_but_capped_scores(opaque_fingerprints, boundary_cue_fingerprints):
    """Explicit boundary cues yield higher scores than no-cue fingerprints, but confidence is capped."""
    fp_no_cue_a, fp_no_cue_b = opaque_fingerprints
    fp_cue_a, fp_cue_b = boundary_cue_fingerprints

    verifier = GeminiSemanticVerifier(api_key=None, client=None)

    h_score = 0.55
    prop_no_cue = await verifier.verify_pair(fp_no_cue_a, fp_no_cue_b, heuristic_score=h_score)
    prop_with_cue = await verifier.verify_pair(fp_cue_a, fp_cue_b, heuristic_score=h_score)

    assert prop_no_cue.semantic_verified is False
    assert prop_with_cue.semantic_verified is False
    assert prop_with_cue.degraded_reason == "NO_MODEL"

    # Higher semantic score and confidence from real boundary evidence
    assert prop_with_cue.semantic_score > prop_no_cue.semantic_score
    assert prop_with_cue.confidence > prop_no_cue.confidence

    # Confidence must be capped below 1.0 (specifically <= 0.80 in offline unverified mode)
    assert prop_with_cue.confidence <= 0.80
    assert prop_with_cue.semantic_score != 0.95


@pytest.mark.asyncio
async def test_live_api_exception_handled_as_degraded_with_reason(opaque_fingerprints):
    """Live API errors are logged and returned as degraded proposals rather than raising."""
    fp_a, fp_b = opaque_fingerprints
    verifier = GeminiSemanticVerifier(api_key="valid-dummy-key", client=None)

    with patch.object(verifier, "_call_gemini_semantic_api", side_effect=httpx.ConnectError("Connection refused")):
        proposal = await verifier.verify_pair(fp_a, fp_b, heuristic_score=0.45)

    assert isinstance(proposal, CorrelationProposal)
    assert proposal.semantic_verified is False
    assert proposal.degraded_reason == "API_ERROR: ConnectError"
    assert proposal.recommended_action == "UNCERTAIN"
    assert proposal.confidence == 0.45


@pytest.mark.asyncio
async def test_opaque_labels_sent_to_gemini(opaque_fingerprints):
    """Dual image call strictly passes 'Card A' / 'Card B' opaque labels."""
    fp_a, fp_b = opaque_fingerprints
    verifier = GeminiSemanticVerifier(api_key="valid-dummy-key", client=None)

    intercepted_payload = None

    async def fake_post(url, json=None, **kwargs):
        nonlocal intercepted_payload
        intercepted_payload = json
        from unittest.mock import MagicMock
        mock_resp = MagicMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"semantic_score": 0.88, "recommended_action": "PAIR", "explanation": "Matching handwriting."}'
                            }
                        ]
                    }
                }
            ]
        }
        return mock_resp

    with patch("httpx.AsyncClient.post", side_effect=fake_post):
        proposal = await verifier._call_gemini_semantic_api(fp_a, fp_b, heuristic_score=0.80)

    assert intercepted_payload is not None
    prompt_text = intercepted_payload["contents"][0]["parts"][0]["text"]

    assert "Front Card: Card A" in prompt_text
    assert "Back Card: Card B" in prompt_text
    assert fp_a.card_id not in prompt_text
    assert fp_b.card_id not in prompt_text

    assert proposal.semantic_verified is True
    assert proposal.degraded_reason is None
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
