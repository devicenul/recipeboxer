"""Unit and regression tests for global minimum-cost path cover optimization (WP-GD, G7).

Asserts behavior of path_cover on crafted link matrices:
- Chains (2-card, 3-card, N-card)
- Standalones (below dummy cost threshold)
- Branching conflicts (in-degree and out-degree)
- Cycle prevention (2-node and 3-node directed cycles)
- Title-bearing start constraint enforcement and repair by breaking weakest link
- Completeness prior modulation of dummy endpoint costs
- Permutation invariance and determinism
- Tiny ILP (scipy.optimize.milp) execution on ambiguous components.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple

import pytest

from recipeboxer.correlation.optimizer import (
    PairingOptimizer,
    _is_title_bearing_start,
    _solve_component_milp,
    path_cover,
)
from recipeboxer.models.domain import CardRole, VisualFingerprint


class TestPathCoverCraftedMatrices:
    """Evaluates path_cover on handcrafted directed link probability matrices."""

    def test_path_cover_empty_and_single_node(self) -> None:
        assert path_cover([], {}, {}) == []
        assert path_cover(["card_1"], {}, {}) == [["card_1"]]

    def test_path_cover_simple_linear_chain(self) -> None:
        """2 cards with high transition probability are linked into a single chain."""
        nodes = ["card_A", "card_B"]
        links = {("card_A", "card_B"): 0.95}
        node_costs = {
            "card_A": {"expects_continuation": 0.85, "is_complete": 0.15, "is_start": 0.85},
            "card_B": {"expects_continuation": 0.15, "is_complete": 0.85, "is_start": 0.15},
        }
        chains = path_cover(nodes, links, node_costs)
        assert chains == [["card_A", "card_B"]]

    def test_path_cover_three_card_chain(self) -> None:
        """3 cards linked in order A -> B -> C."""
        nodes = ["c1", "c2", "c3"]
        links = {
            ("c1", "c2"): 0.92,
            ("c2", "c3"): 0.88,
        }
        node_costs = {
            "c1": {"is_start": 0.90, "expects_continuation": 0.90, "has_title": True},
            "c2": {"is_start": 0.20, "expects_continuation": 0.80, "has_title": False},
            "c3": {"is_start": 0.10, "is_complete": 0.95, "has_title": False},
        }
        chains = path_cover(nodes, links, node_costs)
        assert chains == [["c1", "c2", "c3"]]

    def test_path_cover_standalones_below_threshold(self) -> None:
        """Weak link (p=0.20) does not overcome dummy endpoint costs."""
        nodes = ["card_X", "card_Y"]
        links = {("card_X", "card_Y"): 0.20}
        node_costs = {
            "card_X": {"expects_continuation": 0.30, "is_complete": 0.80},
            "card_Y": {"is_start": 0.70, "is_complete": 0.80},
        }
        chains = path_cover(nodes, links, node_costs)
        assert chains == [["card_X"], ["card_Y"]]

    def test_path_cover_branching_out_degree_conflict(self) -> None:
        """A links to both B (p=0.95) and C (p=0.60). Hungarian solver picks A -> B."""
        nodes = ["A", "B", "C"]
        links = {
            ("A", "B"): 0.95,
            ("A", "C"): 0.60,
        }
        node_costs = {
            "A": {"has_title": True, "expects_continuation": 0.90},
            "B": {"is_complete": 0.85},
            "C": {"is_complete": 0.85},
        }
        chains = path_cover(nodes, links, node_costs)
        assert ["A", "B"] in chains
        assert ["C"] in chains
        assert len(chains) == 2

    def test_path_cover_branching_in_degree_conflict(self) -> None:
        """Both A (p=0.90) and B (p=0.55) link to C. Hungarian solver picks A -> C."""
        nodes = ["A", "B", "C"]
        links = {
            ("A", "C"): 0.90,
            ("B", "C"): 0.55,
        }
        node_costs = {
            "A": {"has_title": True, "expects_continuation": 0.85},
            "B": {"has_title": True, "expects_continuation": 0.85},
            "C": {"is_complete": 0.90},
        }
        chains = path_cover(nodes, links, node_costs)
        assert ["A", "C"] in chains
        assert ["B"] in chains
        assert len(chains) == 2

    def test_path_cover_two_node_cycle_prevention(self) -> None:
        """Mutual links A -> B (0.90) and B -> A (0.75). Weakest link (B -> A) is broken."""
        nodes = ["A", "B"]
        links = {
            ("A", "B"): 0.90,
            ("B", "A"): 0.75,
        }
        node_costs = {
            "A": {"is_start": 0.80, "has_title": True},
            "B": {"is_start": 0.20, "has_title": False},
        }
        chains = path_cover(nodes, links, node_costs)
        assert chains == [["A", "B"]]

    def test_path_cover_three_node_cycle_prevention(self) -> None:
        """Directed cycle A -> B -> C -> A. Weakest link C -> A is broken."""
        nodes = ["A", "B", "C"]
        links = {
            ("A", "B"): 0.92,
            ("B", "C"): 0.85,
            ("C", "A"): 0.65,
        }
        node_costs = {
            "A": {"has_title": True, "is_start": 0.90},
            "B": {"has_title": False, "is_start": 0.30},
            "C": {"has_title": False, "is_start": 0.20},
        }
        chains = path_cover(nodes, links, node_costs)
        assert chains == [["A", "B", "C"]]

    def test_path_cover_title_bearing_start_constraint(self) -> None:
        """Two cards with has_title=True cannot remain in the same chain."""
        nodes = ["apple_crisp_front", "pecan_pie_front"]
        links = {
            ("apple_crisp_front", "pecan_pie_front"): 0.95,
        }
        node_costs = {
            "apple_crisp_front": {"has_title": True, "visible_title": "Apple Crisp"},
            "pecan_pie_front": {"has_title": True, "visible_title": "Pecan Pie"},
        }
        chains = path_cover(nodes, links, node_costs)
        # Chain must be repaired by breaking the title-violating link
        assert chains == [["apple_crisp_front"], ["pecan_pie_front"]]

    def test_path_cover_title_bearing_in_three_card_chain(self) -> None:
        """Chain A (title) -> B (cont) -> C (title). Weakest link B -> C is broken."""
        nodes = ["card_1", "card_2", "card_3"]
        links = {
            ("card_1", "card_2"): 0.92,
            ("card_2", "card_3"): 0.75,
        }
        node_costs = {
            "card_1": {"has_title": True, "visible_title": "Cinnamon Rolls"},
            "card_2": {"has_title": False, "is_start": 0.10},
            "card_3": {"has_title": True, "visible_title": "Dinner Rolls"},
        }
        chains = path_cover(nodes, links, node_costs)
        assert chains == [["card_1", "card_2"], ["card_3"]]

    def test_path_cover_completeness_prior_modulation(self) -> None:
        """Completeness prior penalizes leaving continuation unlinked, bridging borderline link."""
        nodes = ["front_incomplete", "back_continuation"]
        # Borderline probability 0.45 ordinarily would not beat symmetric dummy costs
        links = {("front_incomplete", "back_continuation"): 0.45}
        node_costs = {
            "front_incomplete": {"expects_continuation": 0.95, "is_complete": 0.05, "is_start": 0.85},
            "back_continuation": {"is_start": 0.05, "is_complete": 0.90},
        }
        chains = path_cover(nodes, links, node_costs)
        assert chains == [["front_incomplete", "back_continuation"]]

    def test_path_cover_deterministic_permutation_invariance(self) -> None:
        """Permuting input node list yields identical chain assignments and ordering."""
        nodes = ["c1", "c2", "c3", "c4", "c5", "c6"]
        links = {
            ("c1", "c2"): 0.92,
            ("c3", "c4"): 0.88,
            ("c4", "c5"): 0.81,
        }
        node_costs = {
            "c1": {"has_title": True, "is_start": 0.90},
            "c2": {"is_complete": 0.85},
            "c3": {"has_title": True, "is_start": 0.90},
            "c4": {"is_start": 0.20},
            "c5": {"is_complete": 0.90},
            "c6": {"is_complete": 0.95},
        }

        baseline = path_cover(nodes, links, node_costs)

        rng = random.Random(42)
        for _ in range(5):
            shuffled = list(nodes)
            rng.shuffle(shuffled)
            result = path_cover(shuffled, links, node_costs)
            assert result == baseline

    def test_tiny_milp_ambiguous_component(self) -> None:
        """MILP solver resolves small ambiguous component with close probabilities."""
        comp_nodes = ["n1", "n2", "n3"]
        comp_links = {
            ("n1", "n2"): 0.82,
            ("n1", "n3"): 0.80,
            ("n2", "n3"): 0.79,
        }
        comp_node_costs = {
            "n1": {"has_title": True, "is_start": 0.90},
            "n2": {"has_title": False, "is_start": 0.30},
            "n3": {"has_title": False, "is_complete": 0.85},
        }
        c_no_succ = {"n1": 0.60, "n2": 0.40, "n3": 0.30}
        c_no_pred = {"n1": 0.20, "n2": 0.45, "n3": 0.50}

        edges = _solve_component_milp(
            comp_nodes,
            comp_links,
            comp_node_costs,
            c_no_succ,
            c_no_pred,
        )
        assert edges is not None
        # Must be valid directed path without branching or cycles
        out_nodes = [u for u, _ in edges]
        in_nodes = [v for _, v in edges]
        assert len(out_nodes) == len(set(out_nodes))
        assert len(in_nodes) == len(set(in_nodes))
