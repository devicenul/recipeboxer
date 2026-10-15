"""Global optimization and path cover for card sequence correlation in RecipeBoxer.

Implements minimum-cost path cover via bipartite matching (Hungarian algorithm)
on -log p with dummy successor/predecessor costs modulated by completeness priors,
strict cycle prevention, title-bearing start constraints repaired by weakest link,
and optional tiny ILP for small ambiguous components.
"""

from __future__ import annotations

import logging
import math
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import numpy as np
from scipy.optimize import LinearConstraint, linear_sum_assignment, milp

from recipeboxer.config import get_settings
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.vision.heuristics import _get_enum_str, screen_candidate_pairs

logger = logging.getLogger(__name__)


def _is_title_bearing_start(node_id: str, node_costs: Dict[str, Dict[str, Any]]) -> bool:
    """Checks whether a card is flagged as a title-bearing recipe start."""
    priors = node_costs.get(node_id, {})
    has_title = priors.get("has_title") is True or bool(priors.get("visible_title"))
    return bool(has_title)


def _titles_conflict(u: str, v: str, node_costs: Dict[str, Dict[str, Any]]) -> bool:
    """Checks whether two nodes bear genuinely conflicting (distinct) recipe titles."""
    priors_u = node_costs.get(u, {})
    priors_v = node_costs.get(v, {})
    t_u = (priors_u.get("visible_title") or "").strip()
    t_v = (priors_v.get("visible_title") or "").strip()
    if not t_u or not t_v:
        return False
    norm_u = re.sub(r"[^a-z0-9]", "", t_u.lower())
    norm_v = re.sub(r"[^a-z0-9]", "", t_v.lower())
    if not norm_u or not norm_v:
        return False
    # If they match or one is substring of the other (e.g. continuation title), they do not conflict
    if norm_u == norm_v or norm_u in norm_v or norm_v in norm_u:
        return False
    # Check continuation markers
    tb_lower = t_v.lower()
    if any(h in tb_lower for h in ("cont", "continued", "page 2", "card 2", "pt 2", "part 2")):
        return False
    return True


def _solve_component_milp(
    comp_nodes: List[str],
    comp_links: Dict[Tuple[str, str], float],
    comp_node_costs: Dict[str, Dict[str, Any]],
    c_no_succ: Dict[str, float],
    c_no_pred: Dict[str, float],
) -> Optional[List[Tuple[str, str]]]:
    """Solves exact path cover for a small ambiguous component using scipy.optimize.milp."""
    K = len(comp_nodes)
    if K <= 1:
        return []

    node_to_idx = {name: i for i, name in enumerate(comp_nodes)}
    cand_edges = [
        (u, v) for (u, v) in comp_links
        if u in node_to_idx and v in node_to_idx and u != v
    ]
    if not cand_edges:
        return []

    m = len(cand_edges)
    # Variables: x_e for each edge (m vars), t_u for MTZ ordering (K vars)
    # Total vars = m + K
    n_vars = m + K

    # Objective:
    # Taking edge (u, v) costs -ln(p) and saves c_no_succ(u) + c_no_pred(v)
    c = np.zeros(n_vars, dtype=float)
    for idx, (u, v) in enumerate(cand_edges):
        p = max(1e-6, min(1.0 - 1e-6, comp_links[(u, v)]))
        edge_cost = -math.log(p)
        c[idx] = edge_cost - c_no_succ.get(u, 0.35) - c_no_pred.get(v, 0.35)

    # Integrality: 1 for binary x_e, 0 for continuous t_u
    integrality = np.zeros(n_vars, dtype=int)
    integrality[:m] = 1

    # Bounds:
    lb = np.zeros(n_vars, dtype=float)
    ub = np.zeros(n_vars, dtype=float)
    ub[:m] = 1.0  # x_e in [0, 1]
    ub[m:] = float(K - 1)  # t_u in [0, K-1]

    row_list: List[np.ndarray] = []
    lhs_list: List[float] = []
    rhs_list: List[float] = []

    # 1. Out-degree <= 1 for each u
    for u in comp_nodes:
        out_indices = [idx for idx, (src, _) in enumerate(cand_edges) if src == u]
        if out_indices:
            row = np.zeros(n_vars, dtype=float)
            for idx in out_indices:
                row[idx] = 1.0
            row_list.append(row)
            lhs_list.append(-np.inf)
            rhs_list.append(1.0)

    # 2. In-degree <= 1 for each v
    for v in comp_nodes:
        in_indices = [idx for idx, (_, dst) in enumerate(cand_edges) if dst == v]
        if in_indices:
            row = np.zeros(n_vars, dtype=float)
            for idx in in_indices:
                row[idx] = 1.0
            row_list.append(row)
            lhs_list.append(-np.inf)
            rhs_list.append(1.0)

    # 3. MTZ Subtour Elimination: t_u - t_v + K * x_{uv} <= K - 1
    for idx, (u, v) in enumerate(cand_edges):
        u_idx = node_to_idx[u]
        v_idx = node_to_idx[v]
        row = np.zeros(n_vars, dtype=float)
        row[idx] = float(K)
        row[m + u_idx] = 1.0
        row[m + v_idx] = -1.0
        row_list.append(row)
        lhs_list.append(-np.inf)
        rhs_list.append(float(K - 1))

    # 4. Title constraint: direct link between cards with conflicting titles is forbidden
    for idx, (u, v) in enumerate(cand_edges):
        if _titles_conflict(u, v, comp_node_costs):
            row = np.zeros(n_vars, dtype=float)
            row[idx] = 1.0
            row_list.append(row)
            lhs_list.append(0.0)
            rhs_list.append(0.0)

    if not row_list:
        return []

    A = np.vstack(row_list)
    constraints = LinearConstraint(A, lhs_list, rhs_list)

    try:
        from scipy.optimize import Bounds
        res = milp(c=c, integrality=integrality, bounds=Bounds(lb, ub), constraints=constraints)
        if res.success and res.x is not None:
            chosen = []
            for idx, edge in enumerate(cand_edges):
                if res.x[idx] > 0.5:
                    chosen.append(edge)
            return chosen
    except Exception as exc:
        logger.debug(f"MILP solver skipped for component: {exc}")

    return None


def path_cover(
    nodes: List[str],
    links: Dict[Tuple[str, str], float],
    node_costs: Dict[str, Dict[str, Any]],
) -> List[List[str]]:
    """Finds a minimum-cost directed path cover over nodes based on link probabilities and node priors.

    Formulates path cover as a minimum-weight bipartite matching problem via
    scipy.optimize.linear_sum_assignment on -log p with dummy "no successor"
    and "no predecessor" endpoints. Incorporates completeness priors into node costs,
    detects and breaks directed cycles, resolves title-bearing start conflicts by breaking
    the weakest link, and uses a tiny ILP (scipy.optimize.milp) for small ambiguous components.

    Args:
        nodes: List of card IDs in the deck / batch.
        links: Directed transition probabilities {(src, dst): p} with p in [0.0, 1.0].
        node_costs: Priors per card ID with keys like 'expects_continuation', 'is_complete',
                    'is_start', 'has_title', 'visible_title'.

    Returns:
        Deterministic list of ordered node chains [ [c1, c2, ...], [c3], ... ]
        covering all input nodes exactly once.
    """
    if not nodes:
        return []

    # Deterministic node ordering
    node_list = sorted(list(dict.fromkeys(nodes)))
    N = len(node_list)
    if N == 1:
        return [[node_list[0]]]

    node_to_idx = {name: i for i, name in enumerate(node_list)}

    # Base link threshold p0 = 0.50 -> -ln(0.5) ~ 0.69315
    p0 = 0.50
    base_stop_cost = -0.5 * math.log(p0)  # ~0.34657

    c_no_succ: Dict[str, float] = {}
    c_no_pred: Dict[str, float] = {}

    for u in node_list:
        u_priors = node_costs.get(u, {})
        expects_cont = float(u_priors.get("expects_continuation", 0.5))
        is_comp = float(u_priors.get("is_complete", 0.5))
        # Having no successor is penalized if card expects continuation; discounted if complete
        c_succ = base_stop_cost + 0.6 * (expects_cont - 0.5) - 0.3 * (is_comp - 0.5)
        c_no_succ[u] = max(0.18, c_succ)

        is_st = float(u_priors.get("is_start", 0.5))
        if _is_title_bearing_start(u, node_costs):
            is_st = max(is_st, 0.90)
        # Having no predecessor is discounted if card is a start; penalized if continuation
        c_pred = base_stop_cost - 0.6 * (is_st - 0.5)
        c_no_pred[u] = max(0.18, c_pred)

    # Construct 2N x 2N assignment matrix
    # Block (1,1) [0..N, 0..N]: edge (u_i -> u_j)
    # Block (1,2) [0..N, N..2N]: dummy successor for u_i
    # Block (2,1) [N..2N, 0..N]: dummy predecessor for u_j
    # Block (2,2) [N..2N, N..2N]: dummy-dummy matching at 0 cost
    BIG_PENALTY = 1e6
    C = np.full((2 * N, 2 * N), BIG_PENALTY, dtype=float)

    for (u, v), p in links.items():
        if u in node_to_idx and v in node_to_idx and u != v:
            i, j = node_to_idx[u], node_to_idx[v]
            p_clamped = max(1e-6, min(1.0 - 1e-6, float(p)))
            C[i, j] = -math.log(p_clamped)

    for i, u in enumerate(node_list):
        C[i, N + i] = c_no_succ[u]

    for j, v in enumerate(node_list):
        C[N + j, j] = c_no_pred[v]

    C[N:, N:] = 0.0

    # Deterministic tie-breaker
    tie_breaker = np.fromfunction(
        lambda r, c: 1e-8 * (r * (2 * N) + c),
        (2 * N, 2 * N),
        dtype=float,
    )
    C += tie_breaker

    row_ind, col_ind = linear_sum_assignment(C)

    # Initial directed edges
    matched_edges: Dict[str, str] = {}
    for r in range(N):
        c = col_ind[r]
        if c < N and C[r, c] < BIG_PENALTY / 2:
            u = node_list[r]
            v = node_list[c]
            matched_edges[u] = v

    # 1. Cycle detection and repair
    def detect_and_break_cycles(edges: Dict[str, str]) -> None:
        visited: Set[str] = set()
        for start_node in node_list:
            if start_node in visited:
                continue
            curr = start_node
            path: List[str] = []
            seen_in_path: Dict[str, int] = {}
            while curr in edges and curr not in visited:
                seen_in_path[curr] = len(path)
                path.append(curr)
                visited.add(curr)
                nxt = edges[curr]
                if nxt in seen_in_path:
                    # Found cycle from seen_in_path[nxt] to len(path)-1
                    cycle_nodes = path[seen_in_path[nxt]:]
                    # Find weakest link in cycle
                    cycle_edges = [(cycle_nodes[k], cycle_nodes[(k + 1) % len(cycle_nodes)]) for k in range(len(cycle_nodes))]
                    weakest_edge = min(
                        cycle_edges,
                        key=lambda e: links.get(e, 0.0),
                    )
                    del edges[weakest_edge[0]]
                    break
                curr = nxt

    detect_and_break_cycles(matched_edges)

    # 2. Check for small ambiguous components eligible for tiny MILP
    # Partition nodes into weakly connected components based on candidate links
    parent: Dict[str, str] = {n: n for n in node_list}

    def find_p(x: str) -> str:
        if parent[x] != x:
            parent[x] = find_p(parent[x])
        return parent[x]

    def union_p(x: str, y: str) -> None:
        rx, ry = find_p(x), find_p(y)
        if rx != ry:
            parent[rx] = ry

    for (u, v) in links:
        if u in node_to_idx and v in node_to_idx and u != v:
            union_p(u, v)

    components: Dict[str, List[str]] = {}
    for n in node_list:
        root = find_p(n)
        components.setdefault(root, []).append(n)

    for root, comp_nodes in components.items():
        K = len(comp_nodes)
        if 2 <= K <= 8:
            # Check for ambiguity: multiple conflicting candidates or close probabilities
            comp_links = {(u, v): p for (u, v), p in links.items() if u in comp_nodes and v in comp_nodes}
            has_ambiguity = False
            for u in comp_nodes:
                out_p = [p for (src, _), p in comp_links.items() if src == u]
                in_p = [p for (_, dst), p in comp_links.items() if dst == u]
                if len(out_p) >= 2 and abs(sorted(out_p, reverse=True)[0] - sorted(out_p, reverse=True)[1]) < 0.20:
                    has_ambiguity = True
                    break
                if len(in_p) >= 2 and abs(sorted(in_p, reverse=True)[0] - sorted(in_p, reverse=True)[1]) < 0.20:
                    has_ambiguity = True
                    break

            if has_ambiguity:
                milp_edges = _solve_component_milp(
                    comp_nodes,
                    comp_links,
                    node_costs,
                    c_no_succ,
                    c_no_pred,
                )
                if milp_edges is not None:
                    # Remove existing edges within component and replace with MILP solution
                    for u in comp_nodes:
                        if u in matched_edges and matched_edges[u] in comp_nodes:
                            del matched_edges[u]
                    for (u, v) in milp_edges:
                        matched_edges[u] = v

    # Re-verify cycle freedom
    detect_and_break_cycles(matched_edges)

    # 3. Assemble linear chains from matched_edges
    in_degree: Dict[str, int] = {n: 0 for n in node_list}
    for src, dst in matched_edges.items():
        in_degree[dst] = in_degree.get(dst, 0) + 1

    start_nodes = [n for n in node_list if in_degree[n] == 0]
    chains: List[List[str]] = []
    visited_nodes: Set[str] = set()

    for st in start_nodes:
        chain = [st]
        visited_nodes.add(st)
        curr = st
        while curr in matched_edges:
            nxt = matched_edges[curr]
            if nxt in visited_nodes:
                break
            chain.append(nxt)
            visited_nodes.add(nxt)
            curr = nxt
        chains.append(chain)

    # Catch any isolated or leftover nodes
    for n in node_list:
        if n not in visited_nodes:
            chains.append([n])
            visited_nodes.add(n)

    # 4. Repair conflicting title constraint: split chains containing genuinely conflicting titles
    # Split chain at the weakest link between cards with conflicting titles
    repaired_chains: List[List[str]] = []
    for chain in chains:
        pending_chains = [chain]
        while pending_chains:
            ch = pending_chains.pop(0)
            split_occurred = False
            for i in range(len(ch)):
                for j in range(i + 1, len(ch)):
                    if _titles_conflict(ch[i], ch[j], node_costs):
                        # Find the weakest link between ch[i] and ch[j]
                        weakest_k = i
                        weakest_p = float("inf")
                        for k in range(i, j):
                            edge = (ch[k], ch[k + 1])
                            p_val = links.get(edge, 0.0)
                            if p_val < weakest_p:
                                weakest_p = p_val
                                weakest_k = k
                        left_ch = ch[:weakest_k + 1]
                        right_ch = ch[weakest_k + 1:]
                        pending_chains.append(left_ch)
                        pending_chains.append(right_ch)
                        split_occurred = True
                        break
                if split_occurred:
                    break
            if not split_occurred:
                repaired_chains.append(ch)

    # Sort each chain's order is preserved; sort the list of chains deterministically
    return sorted(repaired_chains, key=lambda c: c[0])


# Named structural priors for global path cover optimization.
# These priors represent empirical initial hypotheses regarding card boundaries and roles
# used purely to parameterize dummy start / end transition costs in bipartite matching.
# INVARIANT (INV-AC4 / FIND-04): They must NEVER be reported as CorrelationProposal.confidence
# or recipe confidence.
STRUCTURAL_PRIOR_START_TITLE: float = 0.85
STRUCTURAL_PRIOR_START_DEFAULT: float = 0.50
STRUCTURAL_PRIOR_COMPLETE_STANDALONE: float = 0.90
STRUCTURAL_PRIOR_COMPLETE_DEFAULT: float = 0.50
STRUCTURAL_PRIOR_EXPECTS_CONT_START: float = 0.85
STRUCTURAL_PRIOR_EXPECTS_CONT_LOW: float = 0.15
STRUCTURAL_PRIOR_EXPECTS_CONT_DEFAULT: float = 0.50


class PairingOptimizer:

    """Solves open-ended directed sequence correlation and clustering for recipe digitization."""

    def __init__(
        self,
        min_pair_confidence: float = 0.50,
        verifier: Optional[GeminiSemanticVerifier] = None,
    ) -> None:
        """Initialize PairingOptimizer.

        Args:
            min_pair_confidence: Minimum transition confidence required to link adjacent cards.
            verifier: Optional GeminiSemanticVerifier instance for two-stage correlation.
        """
        self.settings = get_settings()
        self.min_pair_confidence = min_pair_confidence
        self.verifier = verifier or GeminiSemanticVerifier()

    async def optimize(
        self,
        fingerprints: List[VisualFingerprint],
        candidate_proposals: Optional[List[CorrelationProposal]] = None,
    ) -> CorrelationResult:
        """Runs the two-stage correlation pipeline and optimizes global card matching.

        Args:
            fingerprints: List of all VisualFingerprint cards in the session.
            candidate_proposals: Optional pre-computed CorrelationProposal items. If None,
                                 runs Stage 1 screening and Stage 2 verification.

        Returns:
            CorrelationResult with finalized proposals and unmatched cards.
        """
        if candidate_proposals is None:
            # Stage 1: Directed heuristic screening
            screened_pairs = screen_candidate_pairs(
                fingerprints,
                threshold=self.settings.heuristic_screen_threshold,
            )

            # Stage 2: Gemini semantic verification
            candidate_proposals = await self.verifier.verify_candidate_proposals(screened_pairs)

        return self.optimize_proposals(fingerprints, candidate_proposals)

    def optimize_proposals(
        self,
        fingerprints: List[VisualFingerprint],
        proposals: List[CorrelationProposal],
    ) -> CorrelationResult:
        """Chains candidate transitions into maximal ordered N-card sequence proposals via path cover.

        Uses path_cover (Hungarian assignment on -log p with completeness priors)
        instead of greedy-only chaining, ensuring global optimality, cycle freedom,
        and title constraint compliance.

        Args:
            fingerprints: List of all VisualFingerprint items in the dataset.
            proposals: Candidate CorrelationProposal items (pairwise or multi-card).

        Returns:
            CorrelationResult containing assigned sequences, standalones, and unmatched cards.
        """
        if not fingerprints:
            return CorrelationResult(proposals=[], unmatched_card_ids=[])

        fp_by_id: Dict[str, VisualFingerprint] = {fp.card_id: fp for fp in fingerprints}
        nodes = list(fp_by_id.keys())

        # Construct links and candidate metadata lookup
        links: Dict[Tuple[str, str], float] = {}
        edge_prop_map: Dict[Tuple[str, str], CorrelationProposal] = {}
        multi_card_prebuilt: Dict[Tuple[str, ...], CorrelationProposal] = {}

        for prop in proposals:
            conf = prop.confidence if prop.confidence is not None else (prop.semantic_score if prop.semantic_score is not None else prop.heuristic_score)
            if conf is None:
                conf = 0.0

            if prop.recommended_action == "REJECT" or conf < self.min_pair_confidence:
                continue

            card_ids = prop.ordered_card_ids
            if len(card_ids) == 2:
                u, v = card_ids[0], card_ids[1]
                # Keep highest confidence if duplicate proposal exists
                if (u, v) not in links or conf > links[(u, v)]:
                    links[(u, v)] = conf
                    edge_prop_map[(u, v)] = prop
            elif len(card_ids) >= 3:
                multi_card_prebuilt[tuple(card_ids)] = prop
                for k in range(len(card_ids) - 1):
                    u, v = card_ids[k], card_ids[k + 1]
                    if (u, v) not in links or conf > links[(u, v)]:
                        links[(u, v)] = conf
                        edge_prop_map[(u, v)] = prop

        # Construct node costs from completeness priors
        node_costs: Dict[str, Dict[str, Any]] = {}
        for fp in fingerprints:
            role_str = _get_enum_str(fp.role)
            has_title = bool(getattr(fp, "visible_title", None))
            is_start = STRUCTURAL_PRIOR_START_TITLE if (has_title or role_str == "SEQUENCE_START" or role_str == "FRONT") else STRUCTURAL_PRIOR_START_DEFAULT
            is_comp = STRUCTURAL_PRIOR_COMPLETE_STANDALONE if (role_str == "STANDALONE" or getattr(fp, "is_standalone", False)) else STRUCTURAL_PRIOR_COMPLETE_DEFAULT
            expects_cont = STRUCTURAL_PRIOR_EXPECTS_CONT_START if (
                role_str == "SEQUENCE_START"
                or getattr(fp, "text_cut_off_end", False)
                or (getattr(fp, "has_ingredients", False) and not getattr(fp, "has_directions", False))
            ) else (STRUCTURAL_PRIOR_EXPECTS_CONT_LOW if is_comp > 0.7 else STRUCTURAL_PRIOR_EXPECTS_CONT_DEFAULT)

            node_costs[fp.card_id] = {
                "expects_continuation": expects_cont,
                "is_complete": is_comp,
                "is_start": is_start,
                "has_title": has_title,
                "visible_title": getattr(fp, "visible_title", None),
            }

        # Run global path cover
        chains = path_cover(nodes, links, node_costs)

        final_proposals: List[CorrelationProposal] = []
        unmatched_card_ids: List[str] = []
        assigned_card_ids: Set[str] = set()

        for chain in chains:
            if len(chain) == 1:
                cid = chain[0]
                fp = fp_by_id.get(cid)
                role_str = _get_enum_str(fp.role) if fp else ""
                # Check if classified as standalone
                if role_str == "STANDALONE" or (fp and getattr(fp, "is_standalone", False)):
                    # RT3-03: the fingerprint role confidence is a classification prior, not evidence
                    # that this card is a complete standalone recipe -> no reported confidence.
                    conf = None
                    standalone_prop = CorrelationProposal(
                        proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                        ordered_card_ids=[cid],
                        part_count=1,
                        transition_scores=[],
                        heuristic_score=0.0,
                        semantic_score=None,
                        confidence=conf,
                        is_standalone=True,
                        continuity_explanation="Single-sided card classified as self-contained standalone recipe.",
                        recommended_action="STANDALONE",
                    )
                    final_proposals.append(standalone_prop)
                    assigned_card_ids.add(cid)
                else:
                    unmatched_card_ids.append(cid)
            else:
                # Multi-card sequence (N >= 2)
                chain_tuple = tuple(chain)
                if chain_tuple in multi_card_prebuilt:
                    prop = multi_card_prebuilt[chain_tuple]
                    final_proposals.append(prop)
                    for cid in chain:
                        assigned_card_ids.add(cid)
                    continue

                t_scores: List[float] = []
                h_scores: List[float] = []
                s_scores: List[float] = []
                explanations: List[str] = []

                for k in range(len(chain) - 1):
                    edge = (chain[k], chain[k + 1])
                    t_conf = links.get(edge, self.min_pair_confidence)
                    t_scores.append(t_conf)
                    matched_p = edge_prop_map.get(edge)
                    if matched_p:
                        h_s = matched_p.heuristic_score if getattr(matched_p, "heuristic_score", None) is not None else t_conf
                        h_scores.append(h_s)
                        s_s = getattr(matched_p, "semantic_score", None)
                        if s_s is not None and s_s > 0.0:
                            s_scores.append(s_s)
                        elif getattr(matched_p, "confidence", None) is not None and getattr(matched_p, "semantic_verified", True):
                            s_scores.append(matched_p.confidence)
                        elif s_s is not None:
                            s_scores.append(s_s)
                        if getattr(matched_p, "continuity_explanation", ""):
                            explanations.append(matched_p.continuity_explanation)

                avg_conf = sum(t_scores) / len(t_scores) if t_scores else 0.50
                avg_h = sum(h_scores) / len(h_scores) if h_scores else avg_conf

                # Missing semantic evidence handling (FIND-04)
                missing_semantic = len(s_scores) < len(t_scores) or not s_scores
                if missing_semantic:
                    avg_s = sum(s_scores) / len(s_scores) if s_scores else None
                    final_conf = min(t_scores) if t_scores else None
                    action = "UNCERTAIN"
                    sem_verified = False
                    deg_reason = "MISSING_SEMANTIC_EVIDENCE"
                else:
                    avg_s = sum(s_scores) / len(s_scores)
                    final_conf = avg_conf
                    action = "SEQUENCE" if len(chain) > 2 else "PAIR"
                    sem_verified = True
                    deg_reason = None

                full_expl = " | ".join(explanations) if explanations else f"Connected {len(chain)}-card sequence."

                seq_proposal = CorrelationProposal(
                    proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                    ordered_card_ids=chain,
                    part_count=len(chain),
                    transition_scores=t_scores,
                    heuristic_score=round(avg_h, 4),
                    semantic_score=round(avg_s, 4) if avg_s is not None else None,
                    confidence=round(final_conf, 4),
                    is_standalone=False,
                    continuity_explanation=full_expl,
                    recommended_action=action,
                    semantic_verified=sem_verified,
                    degraded_reason=deg_reason,
                )
                final_proposals.append(seq_proposal)
                for cid in chain:
                    assigned_card_ids.add(cid)

        return CorrelationResult(
            proposals=final_proposals,
            unmatched_card_ids=unmatched_card_ids,
        )
