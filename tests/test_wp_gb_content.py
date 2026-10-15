"""Unit and integration tests for WP-GB (G11 Content-Continuity Features).

Contracts:
- WAVE5_CONTRACTS.md §1.2, §3
- WAVE3_CONTRACTS.md §0 (Offline, deterministic, no LLM, fictitious recipe data)
- REMEDIATION_PLAN.md §9.1 item 2

Verifies:
1. Each feature fires strongly on its intended cue and not on controls.
2. Strict directionality (swapping a and b penalizes or zeros directed scores).
3. Robustness to real-world OCR noise and punctuation variants.
4. Empty / uncomputable fields return raw = None.
5. Completeness prior distributions and content tokens extraction.
"""

from __future__ import annotations

import pytest

from recipeboxer.correlation.content import (
    completeness_prior,
    content_features,
    content_tokens,
    light_lemmatize,
)
from recipeboxer.models.domain import (
    BoundaryText,
    CardRole,
    VisualFingerprint,
)


def _make_fp(
    card_id: str,
    *,
    title: str | None = None,
    first_lines: list[str] | None = None,
    last_lines: list[str] | None = None,
    boundary_first: str = "",
    boundary_last: str = "",
    has_continuation_sym: bool = False,
    is_cut_off: bool = False,
    printed_markers: list[str] | None = None,
    text_cut_off_end: bool | None = None,
    text_cut_off_start: bool | None = None,
    sections: list[str] | None = None,
    has_ingredients: bool | None = None,
    has_directions: bool | None = None,
    looks_like_back: bool | None = None,
) -> VisualFingerprint:
    """Helper to construct fictitious recipe card fingerprints."""
    return VisualFingerprint(
        card_id=card_id,
        image_path=f"/dummy/{card_id}.png",
        sha256_hash="f" * 64,
        role=CardRole.SEQUENCE_START if looks_like_back is not True else CardRole.SEQUENCE_CONTINUATION,
        visible_title=title,
        first_lines=first_lines or [],
        last_lines=last_lines or [],
        boundary_text=BoundaryText(
            first_line=boundary_first,
            last_line=boundary_last,
            has_continuation_symbol=has_continuation_sym,
            is_cut_off_mid_sentence=is_cut_off,
        ),
        printed_markers=printed_markers or [],
        text_cut_off_end=text_cut_off_end,
        text_cut_off_start=text_cut_off_start,
        sections_detected=sections or [],
        has_ingredients=has_ingredients,
        has_directions=has_directions,
        looks_like_back=looks_like_back,
    )


# ============================================================================
# 1. EMPTY / UNCOMPUTABLE INPUTS (EMPTY FIELDS -> RAW NONE)
# ============================================================================

def test_empty_fingerprints_return_all_raw_none():
    """Verifies that completely blank/empty fingerprints return raw = None across all features."""
    fp_a = _make_fp("card_a")
    fp_b = _make_fp("card_b")

    features = content_features(fp_a, fp_b)
    assert len(features) >= 6

    feature_map = {f.name: f for f in features}
    expected_names = {
        "content_xref",
        "sentence_continuity",
        "step_continuity",
        "title_position",
        "marker_evidence",
        "section_flow",
    }
    assert expected_names.issubset(set(feature_map.keys()))

    for name in expected_names:
        ev = feature_map[name]
        assert ev.raw is None, f"Feature '{name}' must have raw=None on empty input, got {ev.raw}"
        assert ev.log_lr is None, f"Feature '{name}' must have log_lr=None per §1.2 contract"

    # Also check mirrored ocr_cue if present
    if "ocr_cue" in feature_map:
        assert feature_map["ocr_cue"].raw is None


# ============================================================================
# 2. CONTENT XREF (INGREDIENTS IN DIRECTIONS & IDF)
# ============================================================================

def test_content_xref_cue_vs_control():
    """Verifies content_xref fires when card B directions cite card A ingredients, but not on control."""
    # Fictitious pairing: Nebula Starberry Dumplings
    fp_front = _make_fp(
        "front_dumplings",
        title="Nebula Starberry Dumplings",
        first_lines=["2 cups starberry mash", "1 cup moonflour", "1/2 cup crystalline honey"],
        last_lines=["1 tsp astral cardamom", "pinch celestial salt"],
        has_ingredients=True,
        has_directions=False,
    )
    fp_back = _make_fp(
        "back_dumplings",
        first_lines=["Combine starberry mash with crystalline honey in a copper basin."],
        last_lines=["Knead in moonflour until dough is elastic. Dust with astral cardamom."],
        has_ingredients=False,
        has_directions=True,
    )
    # Control: Unrelated fictitious recipe (Galactic Kelp Chowder)
    fp_control = _make_fp(
        "back_kelp",
        first_lines=["Simmer kelp fronds with powdered plankton in thermal broth."],
        last_lines=["Stir in volcanic brine and serve piping hot in basalt bowls."],
        has_ingredients=False,
        has_directions=True,
    )

    ev_match = {f.name: f for f in content_features(fp_front, fp_back)}["content_xref"]
    ev_ctrl = {f.name: f for f in content_features(fp_front, fp_control)}["content_xref"]

    assert ev_match.raw is not None and ev_match.raw >= 0.50
    assert ev_ctrl.raw is not None and ev_ctrl.raw == 0.0


def test_content_xref_deck_idf_weighting():
    """Verifies that distinctive vocabulary weighted by deck_idf increases correlation evidence."""
    fp_a = _make_fp(
        "front_x",
        first_lines=["1 cup water", "2 tbsp butter", "1/4 tsp rare_luminescent_spice"],
        last_lines=["pinch salt"],
    )
    fp_b_common = _make_fp(
        "back_common",
        first_lines=["Boil water and melt butter."],
        last_lines=["Simmer until reduced."],
    )
    fp_b_rare = _make_fp(
        "back_rare",
        first_lines=["Sprinkle rare_luminescent_spice into the mixture."],
        last_lines=["Allow infusion to cool."],
    )

    deck_idf = {
        "water": 0.2,
        "butter": 0.5,
        "salt": 0.1,
        "rare_luminescent_spice": 4.5,
    }

    ev_common = {f.name: f for f in content_features(fp_a, fp_b_common, deck_idf=deck_idf)}["content_xref"]
    ev_rare = {f.name: f for f in content_features(fp_a, fp_b_rare, deck_idf=deck_idf)}["content_xref"]

    assert ev_rare.raw is not None and ev_common.raw is not None
    assert ev_rare.raw > ev_common.raw


# ============================================================================
# 3. SENTENCE CONTINUITY & HYPHENATION
# ============================================================================

def test_sentence_continuity_hyphenation_across_faces():
    """Verifies hyphenated word completion across card boundaries achieves maximum score 1.0."""
    fp_a = _make_fp(
        "card_a",
        last_lines=["Before preparing the galactic pastry, pre-"],
        boundary_last="Before preparing the galactic pastry, pre-",
        text_cut_off_end=True,
    )
    fp_b = _make_fp(
        "card_b",
        first_lines=["heat the oven to 375 degrees."],
        boundary_first="heat the oven to 375 degrees.",
        text_cut_off_start=True,
    )

    ev = {f.name: f for f in content_features(fp_a, fp_b)}["sentence_continuity"]
    assert ev.raw == 1.0
    assert "preheat" in (ev.note or "").lower()


def test_sentence_continuity_dangling_conjunction_and_cutoff():
    """Verifies cut-off end and lowercase/dangling continuation trigger high continuity."""
    fp_a = _make_fp(
        "card_a",
        last_lines=["Beat egg whites until stiff peaks form, then gently fold in"],
        text_cut_off_end=True,
    )
    fp_b = _make_fp(
        "card_b",
        first_lines=["whipped cloudberry cream until fully incorporated."],
        text_cut_off_start=True,
    )

    ev = {f.name: f for f in content_features(fp_a, fp_b)}["sentence_continuity"]
    assert ev.raw is not None and ev.raw >= 0.90


def test_sentence_continuity_clean_period_vs_continuation():
    """Verifies that clean terminating punctuation on card A penalizes sentence continuity."""
    fp_a = _make_fp(
        "card_a",
        last_lines=["Bake for 45 minutes until golden brown."],
        text_cut_off_end=False,
    )
    fp_b = _make_fp(
        "card_b",
        first_lines=["Store in an airtight ceramic jar."],
        text_cut_off_start=False,
    )

    ev = {f.name: f for f in content_features(fp_a, fp_b)}["sentence_continuity"]
    assert ev.raw is not None and ev.raw <= 0.15


def test_sentence_continuity_directionality():
    """Verifies sentence continuity is strictly directed: forward scores high, reverse scores 0.0."""
    fp_a = _make_fp(
        "card_a",
        last_lines=["Stir vigorously and"],
        text_cut_off_end=True,
    )
    fp_b = _make_fp(
        "card_b",
        first_lines=["pour into buttered dish."],
        text_cut_off_start=True,
    )

    forward_ev = {f.name: f for f in content_features(fp_a, fp_b)}["sentence_continuity"]
    reverse_ev = {f.name: f for f in content_features(fp_b, fp_a)}["sentence_continuity"]

    assert forward_ev.raw is not None and forward_ev.raw >= 0.90
    assert reverse_ev.raw is not None and reverse_ev.raw == 0.0


# ============================================================================
# 4. STEP CONTINUITY (n -> n+1)
# ============================================================================

def test_step_continuity_sequential_progression():
    """Verifies numbered step progression n -> n+1 fires at 1.0."""
    fp_a = _make_fp(
        "card_a",
        first_lines=["1. Sift dry nebula powders together."],
        last_lines=["2. Add chilled star-butter in small cubes."],
    )
    fp_b = _make_fp(
        "card_b",
        first_lines=["3. Work dough with fingertips until crumbly."],
        last_lines=["4. Chill for one hour before rolling."],
    )

    ev = {f.name: f for f in content_features(fp_a, fp_b)}["step_continuity"]
    assert ev.raw == 1.0


def test_step_continuity_reverse_and_restarts_penalized():
    """Verifies non-sequential or reversed step numbering returns 0.0."""
    fp_a = _make_fp(
        "card_a",
        first_lines=["Step 3. Blend in celestial syrup."],
        last_lines=["Step 4. Bake at 350F."],
    )
    fp_b = _make_fp(
        "card_b",
        first_lines=["Step 1. Cream sugar and butter."],
        last_lines=["Step 2. Whisk eggs."],
    )

    ev_reverse = {f.name: f for f in content_features(fp_a, fp_b)}["step_continuity"]
    assert ev_reverse.raw == 0.0


# ============================================================================
# 5. TITLE POSITION (TITLE ON A ONLY, B PENALIZED)
# ============================================================================

def test_title_position_canonical_vs_clash_and_reverse():
    """Verifies title only on A yields 1.0; title on both or title on B yields 0.0."""
    fp_front = _make_fp("front", title="Martian Amber Torte")
    fp_back_no_title = _make_fp("back")
    fp_back_cont_title = _make_fp("back_cont", title="Martian Amber Torte (Cont'd)")
    fp_back_different_title = _make_fp("back_other", title="Venusian Cloud Souffle")

    # Canonical front -> back
    ev_canonical = {f.name: f for f in content_features(fp_front, fp_back_no_title)}["title_position"]
    assert ev_canonical.raw == 1.0

    # Back card carries continuation title
    ev_cont = {f.name: f for f in content_features(fp_front, fp_back_cont_title)}["title_position"]
    assert ev_cont.raw == 1.0

    # Title collision: separate titles on both cards
    ev_clash = {f.name: f for f in content_features(fp_front, fp_back_different_title)}["title_position"]
    assert ev_clash.raw == 0.0

    # Reverse: title on back card only
    ev_rev = {f.name: f for f in content_features(fp_back_no_title, fp_front)}["title_position"]
    assert ev_rev.raw == 0.0


# ============================================================================
# 6. MARKER EVIDENCE & OCR NOISE ROBUSTNESS
# ============================================================================

def test_marker_evidence_matching_cues_and_progression():
    """Verifies printed continuation markers match across cards."""
    # (over) -> cont'd
    fp_a = _make_fp("card_a", printed_markers=["over"])
    fp_b = _make_fp("card_b", printed_markers=["cont'd"])

    ev = {f.name: f for f in content_features(fp_a, fp_b)}["marker_evidence"]
    assert ev.raw == 1.0

    # Page 1 -> Page 2
    fp_p1 = _make_fp("p1", printed_markers=["Card 1"])
    fp_p2 = _make_fp("p2", printed_markers=["Card 2"])
    ev_page = {f.name: f for f in content_features(fp_p1, fp_p2)}["marker_evidence"]
    assert ev_page.raw == 1.0


def test_marker_evidence_directionality():
    """Verifies reverse marker ordering (e.g. Card 2 -> Card 1) scores 0.0."""
    fp_p1 = _make_fp("p1", printed_markers=["Page 1"])
    fp_p2 = _make_fp("p2", printed_markers=["Page 2"])

    forward_ev = {f.name: f for f in content_features(fp_p1, fp_p2)}["marker_evidence"]
    reverse_ev = {f.name: f for f in content_features(fp_p2, fp_p1)}["marker_evidence"]

    assert forward_ev.raw == 1.0
    assert reverse_ev.raw == 0.0


def test_marker_evidence_ocr_noise_robustness():
    """Verifies that noisy OCR variants of printed markers are correctly matched."""
    # Noisy markers: "(over ->)", "cont.d", "- 2 -", "card #2"
    noisy_variants_a = ["(over ->)", "turn over...", "p.t.o.", "see back >>"]
    noisy_variants_b = ["cont'd.", "(cont.d)", "- 2 -", "page #2", "card 2:"]

    for marker_a in noisy_variants_a:
        fp_a = _make_fp("a", printed_markers=[marker_a])
        ev = {f.name: f for f in content_features(fp_a, _make_fp("b"))}["marker_evidence"]
        assert ev.raw is not None and ev.raw >= 0.80, f"Failed to match noisy marker '{marker_a}'"

    for marker_b in noisy_variants_b:
        fp_b = _make_fp("b", printed_markers=[marker_b])
        ev = {f.name: f for f in content_features(_make_fp("a"), fp_b)}["marker_evidence"]
        assert ev.raw is not None and ev.raw >= 0.75, f"Failed to match noisy marker '{marker_b}'"


# ============================================================================
# 7. SECTION FLOW (INGREDIENTS -> DIRECTIONS)
# ============================================================================

def test_section_flow_canonical_vs_reverse():
    """Verifies section flow fires at 1.0 for ingredients->directions, and 0.0 for reverse."""
    fp_front = _make_fp("front", has_ingredients=True, has_directions=False, sections=["ingredients"])
    fp_back = _make_fp("back", has_ingredients=False, has_directions=True, sections=["directions"])

    forward_ev = {f.name: f for f in content_features(fp_front, fp_back)}["section_flow"]
    reverse_ev = {f.name: f for f in content_features(fp_back, fp_front)}["section_flow"]

    assert forward_ev.raw == 1.0
    assert reverse_ev.raw == 0.0


def test_section_flow_standalone_penalized():
    """Verifies that two cards each containing both ingredients and directions receive low score."""
    fp_1 = _make_fp("rec1", has_ingredients=True, has_directions=True)
    fp_2 = _make_fp("rec2", has_ingredients=True, has_directions=True)

    ev = {f.name: f for f in content_features(fp_1, fp_2)}["section_flow"]
    assert ev.raw is not None and ev.raw <= 0.15


# ============================================================================
# 8. COMPLETENESS PRIOR
# ============================================================================

def test_completeness_prior_complete_card():
    """Verifies a standalone card with title, ingredients, directions has high is_complete."""
    fp = _make_fp(
        "complete_card",
        title="Galactic Spiced Figs",
        has_ingredients=True,
        has_directions=True,
        text_cut_off_end=False,
    )
    priors = completeness_prior(fp)
    assert priors["is_complete"] >= 0.90
    assert priors["expects_continuation"] <= 0.20
    assert priors["is_start"] >= 0.85


def test_completeness_prior_expects_continuation():
    """Verifies card with ingredients only and 'over' marker expects continuation."""
    fp = _make_fp(
        "front_incomplete",
        title="Supernova Layer Cake",
        has_ingredients=True,
        has_directions=False,
        printed_markers=["(over)"],
        text_cut_off_end=True,
    )
    priors = completeness_prior(fp)
    assert priors["expects_continuation"] >= 0.85
    assert priors["is_complete"] <= 0.15
    assert priors["is_start"] >= 0.85


def test_completeness_prior_back_card():
    """Verifies back card with directions only is not classified as start."""
    fp = _make_fp(
        "back_card",
        has_ingredients=False,
        has_directions=True,
        looks_like_back=True,
        printed_markers=["Card 2"],
        text_cut_off_start=True,
    )
    priors = completeness_prior(fp)
    assert priors["is_start"] <= 0.15
    assert priors["is_complete"] <= 0.15


# ============================================================================
# 9. CONTENT TOKENS & LIGHT LEMMATIZE
# ============================================================================

def test_content_tokens_extraction_and_filtering():
    """Verifies content_tokens removes generic units and verbs while lemmatizing food nouns."""
    fp = _make_fp(
        "sample",
        title="Starlight Apple Tart",
        first_lines=["2 cups sliced apples", "1 tbsp melted butter"],
        last_lines=["3 teaspoons ground cinnamon", "bake in warm oven"],
    )
    toks = content_tokens(fp)
    # Food nouns lemmatized
    assert "apple" in toks
    assert "cinnamon" in toks
    assert "starlight" in toks
    assert "tart" in toks

    # Generic units and verbs filtered out
    for filtered in ("cups", "tbsp", "teaspoons", "bake", "warm", "melted"):
        assert filtered not in toks


def test_light_lemmatize_rules():
    """Verifies light_lemmatize morphological stemmer rules."""
    assert light_lemmatize("apples") == "apple"
    assert light_lemmatize("berries") == "berry"
    assert light_lemmatize("peaches") == "peach"
    assert light_lemmatize("tomatoes") == "tomato"
    assert light_lemmatize("chopping") == "chop"
    assert light_lemmatize("chopped") == "chop"
    assert light_lemmatize("melted") == "melt"
    assert light_lemmatize("softened") == "soften"
    assert light_lemmatize("egg") == "egg"
