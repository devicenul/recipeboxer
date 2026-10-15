"""Tests for WP-GC: Multimodal Set-Level Verification (G6).

Verifies:
1. Request shape: neutral labels ('Anchor', 'Candidate 1..k'), prompt from registry, zero card ID/filename leakage.
2. Deterministic randomized order: seeded deterministically from content hashes to avoid position bias.
3. Label mapping: candidate labels mapped back to actual card IDs for continuations and rejections.
4. Quote validation: verbatim quotes checked against fingerprint text/OCR; missing/hallucinated quotes flagged and lower confidence.
5. Cache hit: identical (anchor hash, sorted candidate hashes, model, prompt version) returns cached result without LLM call.
6. Typed failures: no client -> NO_API_KEY; model errors -> SemanticVerificationUnavailable with typed reason.
7. Budget propagation: BudgetExceeded propagates unmodified.
8. Compatibility shim: GeminiSemanticVerifier delegates to SetVerifier with k=1.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional
import pytest

from recipeboxer.ai.client import ImagePart, LLMRequest, LLMResponse, LLMStage, TextPart, UsageTokens
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.config import get_settings
from recipeboxer.correlation.semantic import (
    DefaultImageProvider,
    GeminiSemanticVerifier,
    ImageProvider,
    SetVerificationResponse,
    SetVerifier,
    compute_set_verify_cache_key,
)
from recipeboxer.models.correlation import CorrelationProposal, SetVerification
from recipeboxer.models.domain import BoundaryText, CardRole, VisualFingerprint
from recipeboxer.models.failures import (
    BudgetExceeded,
    FailureReason,
    SemanticVerificationUnavailable,
)
from recipeboxer.prompts import get_prompt


def _create_mock_fingerprint(
    card_id: str,
    sha256: str,
    role: CardRole = CardRole.SEQUENCE_START,
    title: Optional[str] = None,
    first_lines: Optional[List[str]] = None,
    last_lines: Optional[List[str]] = None,
    printed_markers: Optional[List[str]] = None,
    boundary_text: Optional[BoundaryText] = None,
    ocr_lines: Optional[List[str]] = None,
) -> VisualFingerprint:
    """Helper to construct a valid VisualFingerprint with custom textual metadata."""
    return VisualFingerprint(
        card_id=card_id,
        image_path=f"/fake/{card_id}",
        sha256_hash=sha256,
        role=role,
        visible_title=title,
        first_lines=first_lines or [],
        last_lines=last_lines or [],
        printed_markers=printed_markers or [],
        boundary_text=boundary_text or BoundaryText(),
        ocr_lines=ocr_lines or [],
    )


class DictImageProvider:
    """In-memory test double for ImageProvider."""

    def __init__(self, images: dict[str, bytes]) -> None:
        self.images = images

    def get(self, card_id: str) -> bytes:
        return self.images.get(card_id, b"")


# ---------------------------------------------------------------------------
# 1. Request Shape & Privacy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_shape_neutral_labels_and_registry_prompt():
    """Verify system instruction is loaded from prompt registry and labels are strictly neutral."""
    anchor = _create_mock_fingerprint(
        card_id="secret_filename_front_123.jpg",
        sha256="a" * 64,
        title="Dutch Apple Pie",
        first_lines=["Pastry for 9-inch pie crust"],
        last_lines=["Mix apples with sugar and cinnamon (over ->)"],
        boundary_text=BoundaryText(first_line="Dutch Apple Pie", last_line="(over ->)"),
    )
    cand1 = _create_mock_fingerprint(
        card_id="secret_filename_back_456.jpg",
        sha256="b" * 64,
        role=CardRole.SEQUENCE_CONTINUATION,
        first_lines=["Bake at 375F for 45 minutes until bubbly"],
        printed_markers=["2", "cont'd"],
        boundary_text=BoundaryText(first_line="Bake at 375F"),
    )
    cand2 = _create_mock_fingerprint(
        card_id="distractor_card_789.jpg",
        sha256="c" * 64,
        title="Beef Stew",
        first_lines=["2 lbs chuck roast, cubed"],
    )

    client = ScriptedLLMClient([
        SetVerificationResponse(
            continuations=["Candidate 1"],
            rejected=["Candidate 2"],
            evidence_quotes=["Dutch Apple Pie", "Bake at 375F"],
            confidence=0.95,
        )
    ])

    images = DictImageProvider({
        anchor.card_id: b"fake_anchor_jpeg_bytes",
        cand1.card_id: b"fake_cand1_jpeg_bytes",
        cand2.card_id: b"fake_cand2_jpeg_bytes",
    })

    verifier = SetVerifier(llm_client=client)
    res = await verifier.verify(anchor, [cand1, cand2], images=images)

    assert len(client.requests) == 1
    req = client.requests[0]

    # Stage & Prompt registry checks
    assert req.stage == LLMStage.SET_VERIFY
    registry_prompt = get_prompt("set_verify_v1")
    assert req.system_instruction == registry_prompt.text
    assert req.response_model == SetVerificationResponse

    # Privacy check (INV-AC2): No card IDs or filenames in any part
    for part in req.parts:
        if isinstance(part, TextPart):
            assert "secret_filename" not in part.text
            assert "distractor_card" not in part.text
            assert "front_123" not in part.text
            assert "back_456" not in part.text
            assert "789" not in part.text
        elif isinstance(part, ImagePart):
            assert part.label in ("Anchor", "Candidate 1", "Candidate 2")

    # Text parts contain neutral headers
    text_parts = [p.text for p in req.parts if isinstance(p, TextPart)]
    combined_text = "\n".join(text_parts)
    assert "Anchor:" in combined_text
    assert "Candidate 1:" in combined_text
    assert "Candidate 2:" in combined_text


# ---------------------------------------------------------------------------
# 2. Deterministic Randomized Order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deterministic_randomized_order_avoids_position_bias():
    """Verify candidates are shuffled deterministically based on content hashes."""
    anchor = _create_mock_fingerprint("anchor", sha256="1" * 64, title="Anchor Soup")
    cand_a = _create_mock_fingerprint("c_alpha", sha256="2" * 64, title="Alpha Soup")
    cand_b = _create_mock_fingerprint("c_beta", sha256="3" * 64, title="Beta Salad")
    cand_c = _create_mock_fingerprint("c_gamma", sha256="4" * 64, title="Gamma Bread")

    client1 = ScriptedLLMClient([SetVerificationResponse(continuations=["none"], confidence=0.0)])
    client2 = ScriptedLLMClient([SetVerificationResponse(continuations=["none"], confidence=0.0)])

    verifier1 = SetVerifier(llm_client=client1)
    verifier2 = SetVerifier(llm_client=client2)

    # Call 1 with candidates in order [A, B, C]
    await verifier1.verify(anchor, [cand_a, cand_b, cand_c])
    req1 = client1.requests[0]

    # Call 2 with candidates in reverse order [C, B, A]
    await verifier2.verify(anchor, [cand_c, cand_b, cand_a])
    req2 = client2.requests[0]

    # Both requests must have identical parts order and text despite different input order
    text_parts1 = [p.text for p in req1.parts if isinstance(p, TextPart)]
    text_parts2 = [p.text for p in req2.parts if isinstance(p, TextPart)]
    assert text_parts1 == text_parts2

    # Different candidate set yields a different permutation or content
    cand_d = _create_mock_fingerprint("c_delta", sha256="5" * 64, title="Delta Cake")
    client3 = ScriptedLLMClient([SetVerificationResponse(continuations=["none"], confidence=0.0)])
    verifier3 = SetVerifier(llm_client=client3)
    await verifier3.verify(anchor, [cand_a, cand_b, cand_d])
    req3 = client3.requests[0]
    text_parts3 = [p.text for p in req3.parts if isinstance(p, TextPart)]
    assert text_parts3 != text_parts1


# ---------------------------------------------------------------------------
# 3. Label Mapping Back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_label_mapping_back_to_card_ids():
    """Verify candidate labels are mapped accurately to original card_ids."""
    anchor = _create_mock_fingerprint("anchor_01", sha256="11" * 32)
    cand1 = _create_mock_fingerprint("card_banana_bread_back", sha256="22" * 32, title="Banana Bread")
    cand2 = _create_mock_fingerprint("card_chili_front", sha256="33" * 32, title="Texas Chili")

    # Deterministically determine which candidate is Candidate 1 vs Candidate 2
    from recipeboxer.correlation.semantic import compute_set_verify_cache_key
    import hashlib, random

    base_cands = sorted([cand1, cand2], key=lambda c: (c.sha256_hash, c.card_id))
    seed_str = f"{anchor.sha256_hash}:" + ",".join(sorted(c.sha256_hash for c in [cand1, cand2]))
    seed_int = int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed_int)
    shuffled = list(base_cands)
    rng.shuffle(shuffled)
    label_map = {f"Candidate {i + 1}": c.card_id for i, c in enumerate(shuffled)}

    target_candidate_label = [lbl for lbl, cid in label_map.items() if cid == cand1.card_id][0]
    rejected_candidate_label = [lbl for lbl, cid in label_map.items() if cid == cand2.card_id][0]

    client = ScriptedLLMClient([
        SetVerificationResponse(
            continuations=[target_candidate_label],
            rejected=[rejected_candidate_label],
            evidence_quotes=["Banana Bread"],
            confidence=0.91,
        )
    ])

    verifier = SetVerifier(llm_client=client)
    result = await verifier.verify(anchor, [cand1, cand2])

    assert result.anchor == "anchor_01"
    assert result.continuations == [cand1.card_id]
    assert cand2.card_id in result.rejected
    assert result.confidence == 0.91


@pytest.mark.asyncio
async def test_label_mapping_none_continuations():
    """Verify that 'none' or empty continuations reject all candidates."""
    anchor = _create_mock_fingerprint("anchor_01", sha256="11" * 32)
    cand1 = _create_mock_fingerprint("cand_01", sha256="22" * 32)
    cand2 = _create_mock_fingerprint("cand_02", sha256="33" * 32)

    client = ScriptedLLMClient([
        SetVerificationResponse(
            continuations=["none"],
            rejected=["Candidate 1", "Candidate 2"],
            evidence_quotes=[],
            confidence=0.85,
        )
    ])

    verifier = SetVerifier(llm_client=client)
    result = await verifier.verify(anchor, [cand1, cand2])

    assert result.continuations == []
    assert set(result.rejected) == {cand1.card_id, cand2.card_id}


# ---------------------------------------------------------------------------
# 4. Quote Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quote_validation_verified_quotes_preserve_confidence():
    """Quotes matching visible text in the fingerprints are verified and keep confidence."""
    anchor = _create_mock_fingerprint(
        "anchor_card",
        sha256="aa" * 32,
        title="Old Fashioned Sugar Cookies",
        first_lines=["1 cup softened butter", "1 1/2 cups sugar"],
    )
    cand = _create_mock_fingerprint(
        "cand_card",
        sha256="bb" * 32,
        first_lines=["Roll dough to 1/4 inch thickness"],
        last_lines=["Bake 8 to 10 minutes at 350 degrees F"],
    )

    client = ScriptedLLMClient([
        SetVerificationResponse(
            continuations=["Candidate 1"],
            rejected=[],
            evidence_quotes=["Old Fashioned Sugar Cookies", "Bake 8 to 10 minutes"],
            confidence=0.90,
        )
    ])

    verifier = SetVerifier(llm_client=client)
    res = await verifier.verify(anchor, [cand])

    assert res.confidence == 0.90
    assert len(res.flags) == 0


@pytest.mark.asyncio
async def test_quote_validation_hallucinated_quotes_lower_confidence():
    """Quotes not found in fingerprint text/OCR are flagged and reduce confidence."""
    anchor = _create_mock_fingerprint(
        "anchor_card",
        sha256="aa" * 32,
        title="Sugar Cookies",
        first_lines=["1 cup butter"],
        ocr_lines=["Sugar Cookies", "1 cup butter"],
    )
    cand = _create_mock_fingerprint(
        "cand_card",
        sha256="bb" * 32,
        first_lines=["Bake at 350F"],
        ocr_lines=["Bake at 350F"],
    )

    client = ScriptedLLMClient([
        SetVerificationResponse(
            continuations=["Candidate 1"],
            rejected=[],
            evidence_quotes=["Completely fabricated quote about chocolate chips", "Not in text"],
            confidence=0.90,
        )
    ])

    verifier = SetVerifier(llm_client=client)
    res = await verifier.verify(anchor, [cand])

    # Confidence must be lowered due to hallucinated quotes
    assert res.confidence < 0.90
    assert any("UNVERIFIED_QUOTE" in f for f in res.flags)


@pytest.mark.asyncio
async def test_quote_validation_mixed_quotes_flags_unverified_and_lowers_confidence():
    """When some quotes match but others are unverified, flags unverified quotes and lowers confidence."""
    anchor = _create_mock_fingerprint(
        "anchor_card",
        sha256="aa" * 32,
        title="Chocolate Brownies",
        first_lines=["1/2 cup butter, melted"],
        ocr_lines=["Chocolate Brownies", "1/2 cup butter, melted"],
    )
    cand = _create_mock_fingerprint(
        "cand_card",
        sha256="bb" * 32,
        first_lines=["Bake at 325F for 30 minutes"],
        ocr_lines=["Bake at 325F for 30 minutes"],
    )

    client = ScriptedLLMClient([
        SetVerificationResponse(
            continuations=["Candidate 1"],
            rejected=[],
            evidence_quotes=["Chocolate Brownies", "3 cups chopped walnuts never mentioned anywhere"],
            confidence=0.90,
        )
    ])

    verifier = SetVerifier(llm_client=client)
    res = await verifier.verify(anchor, [cand])

    assert res.confidence < 0.90
    assert any("UNVERIFIED_QUOTE" in f for f in res.flags)
    assert any("walnuts" in f for f in res.flags)


# ---------------------------------------------------------------------------
# 5. Cache Hit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_avoids_duplicate_llm_call():
    """Identical verification request hits cache and makes exactly one LLM call."""
    anchor = _create_mock_fingerprint("anchor_1", sha256="11" * 32, title="Bread")
    cand1 = _create_mock_fingerprint("cand_1", sha256="22" * 32, title="Bake")
    cand2 = _create_mock_fingerprint("cand_2", sha256="33" * 32, title="Stew")

    client = ScriptedLLMClient([
        SetVerificationResponse(
            continuations=["Candidate 1"],
            rejected=["Candidate 2"],
            evidence_quotes=["Bread"],
            confidence=0.88,
        )
    ])

    cache: dict = {}
    verifier = SetVerifier(llm_client=client, cache=cache)

    res1 = await verifier.verify(anchor, [cand1, cand2])
    assert len(client.requests) == 1

    # Second call with same anchor and candidates (even in different list order)
    res2 = await verifier.verify(anchor, [cand2, cand1])
    assert len(client.requests) == 1  # No second call!

    assert res1.continuations == res2.continuations
    assert res1.confidence == res2.confidence


# ---------------------------------------------------------------------------
# 6. Typed Failures & Errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_client_raises_semantic_verification_unavailable():
    """Invoking SetVerifier without a client raises SemanticVerificationUnavailable(NO_API_KEY)."""
    anchor = _create_mock_fingerprint("anchor", sha256="1" * 64)
    cand = _create_mock_fingerprint("cand", sha256="2" * 64)

    verifier = SetVerifier(llm_client=None)

    with pytest.raises(SemanticVerificationUnavailable) as exc_info:
        await verifier.verify(anchor, [cand])

    assert exc_info.value.reason == FailureReason.NO_API_KEY
    assert anchor.card_id in exc_info.value.card_ids


@pytest.mark.asyncio
async def test_llm_timeout_raises_typed_failure():
    """Timeout from LLMClient raises SemanticVerificationUnavailable(TIMEOUT)."""
    anchor = _create_mock_fingerprint("anchor", sha256="1" * 64)
    cand = _create_mock_fingerprint("cand", sha256="2" * 64)

    client = ScriptedLLMClient([asyncio.TimeoutError("Request timed out")])
    verifier = SetVerifier(llm_client=client)

    with pytest.raises(SemanticVerificationUnavailable) as exc_info:
        await verifier.verify(anchor, [cand])

    assert exc_info.value.reason in (FailureReason.TIMEOUT, FailureReason.MODEL_UNAVAILABLE)


@pytest.mark.asyncio
async def test_empty_candidates_returns_empty_verification():
    """When candidates list is empty, returns empty verification with None confidence without calling LLM."""
    anchor = _create_mock_fingerprint("anchor", sha256="1" * 64)
    client = ScriptedLLMClient([])
    verifier = SetVerifier(llm_client=client)

    res = await verifier.verify(anchor, [])
    assert res.anchor == "anchor"
    assert res.continuations == []
    assert res.rejected == []
    assert res.confidence is None
    assert len(client.requests) == 0


# ---------------------------------------------------------------------------
# 7. Budget Propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_exceeded_propagates_unmodified():
    """BudgetExceeded from LLMClient propagates without being caught or swallowed."""
    anchor = _create_mock_fingerprint("anchor", sha256="1" * 64)
    cand = _create_mock_fingerprint("cand", sha256="2" * 64)

    client = ScriptedLLMClient([BudgetExceeded("Spend cap reached: $5.00")])
    verifier = SetVerifier(llm_client=client)

    with pytest.raises(BudgetExceeded) as exc_info:
        await verifier.verify(anchor, [cand])

    assert "Spend cap reached" in str(exc_info.value)
    assert exc_info.value.reason == FailureReason.BUDGET_EXCEEDED


# ---------------------------------------------------------------------------
# 8. Deprecated Compatibility Shim (GeminiSemanticVerifier)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_semantic_verifier_delegates_to_set_verifier():
    """GeminiSemanticVerifier delegates pair verification to SetVerifier with k=1."""
    fp1 = _create_mock_fingerprint("front_01", sha256="11" * 32, title="Pecan Pie")
    fp2 = _create_mock_fingerprint("back_01", sha256="22" * 32, title="Directions")

    client = ScriptedLLMClient([
        SetVerificationResponse(
            continuations=["Candidate 1"],
            rejected=[],
            evidence_quotes=["Pecan Pie"],
            confidence=0.92,
        )
    ])

    verifier = GeminiSemanticVerifier(llm_client=client)
    proposal = await verifier.verify_pair(fp1, fp2, heuristic_score=0.80)

    assert isinstance(proposal, CorrelationProposal)
    assert proposal.ordered_card_ids == ["front_01", "back_01"]
    assert proposal.recommended_action == "PAIR"
    assert proposal.semantic_verified is True
    assert proposal.confidence > 0.80


@pytest.mark.asyncio
async def test_gemini_semantic_verifier_no_client_raises_no_api_key():
    """GeminiSemanticVerifier without a client or API key raises NO_API_KEY (INV-AC4/AC6)."""
    fp1 = _create_mock_fingerprint("front_01", sha256="11" * 32)
    fp2 = _create_mock_fingerprint("back_01", sha256="22" * 32)

    verifier = GeminiSemanticVerifier(api_key=None, client=None, llm_client=None)

    with pytest.raises(SemanticVerificationUnavailable) as exc_info:
        await verifier.verify_pair(fp1, fp2)

    assert exc_info.value.reason == FailureReason.NO_API_KEY
