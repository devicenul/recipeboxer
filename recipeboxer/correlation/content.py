"""Content-continuity and semantic structure correlation engine (WP-GB, G11).

Pure, deterministic, offline feature extraction for pairing recipe cards
based on text flow, cross-references, section progression, and completeness priors.

Zero LLM calls, zero eval-corpus vocabulary.
Implements WAVE5_CONTRACTS.md §1.2 and REMEDIATION_PLAN.md §9.1 item 2.

Raw Feature Ranges:
-------------------
- content_xref: [0.0, 1.0] or None.
    1.0 = strong cross-reference token overlap between ingredients and directions
          (weighted by deck IDF if provided); 0.0 = zero token overlap;
          None = text/tokens uncomputable.
- sentence_continuity: [0.0, 1.0] or None.
    1.0 = definite forward sentence continuity across card faces (e.g. hyphenated
          word completion, trailing dangling conjunction/comma to lowercase continuation);
    0.0 = cleanly terminated sentence / reverse order;
    None = boundary text and cutoff metadata uncomputable.
- step_continuity: [0.0, 1.0] or None.
    1.0 = exact sequential step progression (n -> n+1);
    0.6 = forward step jump (n < m);
    0.0 = non-sequential or reverse step numbers (m <= n);
    None = no numbered steps detected on cards.
- title_position: [0.0, 1.0] or None.
    1.0 = title on preceding card A only (canonical front -> back);
    0.0 = title clash (both cards have distinct titles) or reversed;
    None = neither card has visible title data.
- marker_evidence: [0.0, 1.0] or None (also mirrored as ocr_cue).
    1.0 = matching printed markers (e.g. A has 'over' and B has 'cont\'d', or page 1 -> page 2);
    0.85 = single forward marker on A or continuation marker on B;
    0.0 = reverse marker order;
    None = no printed markers or cues detected in text fields.
- section_flow: [0.0, 1.0] or None.
    1.0 = canonical recipe section flow (A has ingredients without directions, B has directions);
    0.6 = A has ingredients and directions, B has directions;
    0.0 = reverse section flow (directions -> ingredients);
    None = section information uncomputable.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from recipeboxer.models.correlation import FeatureEvidence
from recipeboxer.models.domain import VisualFingerprint


# Generic English cooking measurement units (no corpus-specific terms)
GENERIC_UNITS: Set[str] = {
    "c", "cup", "cups",
    "tbsp", "tablespoon", "tablespoons", "tbs", "tb",
    "tsp", "teaspoon", "teaspoons", "ts",
    "oz", "ounce", "ounces",
    "lb", "lbs", "pound", "pounds",
    "pinch", "pinches", "dash", "dashes",
    "can", "cans", "pkg", "package", "packages",
    "qt", "quart", "quarts",
    "pt", "pint", "pints",
    "gal", "gallon", "gallons",
    "ml", "l", "g", "kg", "gram", "grams", "liter", "liters",
    "clove", "cloves", "slice", "slices", "stick", "sticks",
    "drop", "drops", "bunch", "bunches", "head", "heads",
    "jar", "jars", "bag", "bags", "box", "boxes", "packet", "packets",
}

# Generic English stopwords and prepositions
GENERIC_STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "with",
    "by", "into", "from", "until", "then", "all", "each", "is", "are", "be",
    "it", "as", "if", "so", "up", "out", "about", "also", "well", "both", "very",
    "too", "more", "most", "some", "any", "no", "not", "only", "such", "than",
    "that", "this", "these", "those", "when", "while", "where", "after", "before",
    "over", "under", "per", "approx", "about", "degree", "degrees", "f", "c",
    "min", "minute", "minutes", "hr", "hour", "hours", "sec", "second", "seconds",
}

# Generic preparation verbs
GENERIC_COOKING_VERBS: Set[str] = {
    "mix", "stir", "bake", "cook", "heat", "preheat", "beat", "blend", "whisk",
    "fold", "pour", "add", "combine", "chill", "serve", "place", "cut", "chop",
    "dice", "melt", "boil", "simmer", "drain", "cool", "shape", "roll", "spread",
    "sprinkle", "layer", "grease", "cover", "refrigerate", "freeze", "knead",
    "sift", "season", "garnish", "roast", "fry", "saute", "grill", "steam",
    "toast", "warm", "press", "drop", "spoon", "transfer", "set", "let",
}

# Continuation cues and markers
FORWARD_MARKERS: Set[str] = {
    "over", "turn over", "see back", "p.t.o", "pto", "cont", "cont.",
    "cont'd", "continued", "->", "card 1", "part 1", "page 1",
    "pt 1", "turn", "next card", "on back",
}

CONTINUATION_HEADERS: Set[str] = {
    "cont", "cont.", "cont'd", "continued", "page 2", "card 2", "part 2",
    "pt 2", "instructions continued", "directions continued", "step",
    "(cont'd)", "(continued)", "cont'd from",
}

DANGLING_CONJUNCTIONS: Set[str] = {
    "and", "or", "with", "in", "into", "to", "until", "then", "of", "for",
    "from", "while", "when", "after", "before", "by", "as", "add", "mix",
}


def light_lemmatize(word: str) -> str:
    """Applies rule-based English morphological reduction for recipe vocabulary.

    Handles plurals (s/es/ies), participle suffixes (ing/ed), and strips punctuation.
    Never alters word roots below minimum length thresholds.
    """
    w = word.strip().lower()
    w = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", w)
    if not w or len(w) <= 3:
        return w

    # Plural reductions
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("es") and len(w) > 4:
        if w.endswith(("shes", "ches", "sses", "xes", "zes")):
            return w[:-2]
        if w.endswith(("toes", "poes")):
            return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]

    # Participle / past tense reductions
    if w.endswith("ing") and len(w) > 5:
        # e.g. chopping -> chop, baking -> bake
        stem = w[:-3]
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            return stem[:-1]
        return stem
    if w.endswith("ed") and len(w) > 4:
        # e.g. chopped -> chop, melted -> melt
        stem = w[:-2]
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            return stem[:-1]
        if stem.endswith("i"):
            return stem[:-1] + "y"
        return stem

    return w


def _extract_raw_text_lines(fp: VisualFingerprint) -> List[str]:
    """Collects all readable boundary and header text lines from a fingerprint."""
    lines: List[str] = []
    if fp.visible_title:
        lines.append(fp.visible_title)
    if fp.first_lines:
        lines.extend(fp.first_lines)
    if fp.boundary_text and fp.boundary_text.first_line:
        lines.append(fp.boundary_text.first_line)
    if fp.last_lines:
        lines.extend(fp.last_lines)
    if fp.boundary_text and fp.boundary_text.last_line:
        lines.append(fp.boundary_text.last_line)
    return [ln.strip() for ln in lines if ln and ln.strip()]


def _extract_content_tokens_from_lines(lines: List[str]) -> Set[str]:
    """Tokenizes lines into lemmatized content nouns, filtering units/verbs/stopwords."""
    tokens: Set[str] = set()
    for line in lines:
        raw_words = re.findall(r"[a-zA-Z0-9]+(?:'[a-zA-Z]+)?", line)
        for rw in raw_words:
            w_lower = rw.lower().replace("'", "")
            if len(w_lower) <= 2 or w_lower.isdigit():
                continue
            if w_lower in GENERIC_UNITS or w_lower in GENERIC_STOPWORDS or w_lower in GENERIC_COOKING_VERBS:
                continue
            lemma = light_lemmatize(w_lower)
            if lemma and len(lemma) > 2 and lemma not in GENERIC_STOPWORDS:
                tokens.add(lemma)
    return tokens


# ============================================================================
# 1. PUBLIC API: content_tokens
# ============================================================================

def content_tokens(fp: VisualFingerprint) -> List[str]:
    """Extracts distinctive normalized content tokens from a fingerprint for top-k retrieval.

    Filters out generic units, stopwords, and preparation verbs. Returns unique lemmatized
    tokens in deterministic sorted order.

    Args:
        fp: Card visual fingerprint.

    Returns:
        List of unique content tokens, or empty list if no text is present.
    """
    lines = _extract_raw_text_lines(fp)
    if not lines:
        return []
    toks = _extract_content_tokens_from_lines(lines)
    return sorted(toks)


# ============================================================================
# 2. INDIVIDUAL FEATURE EVALUATORS
# ============================================================================

def _compute_content_xref(
    fp_a: VisualFingerprint,
    fp_b: VisualFingerprint,
    deck_idf: Optional[Dict[str, float]] = None,
) -> FeatureEvidence:
    """Evaluates cross-reference overlap between card A ingredients/front and card B directions/back."""
    lines_a = _extract_raw_text_lines(fp_a)
    lines_b = _extract_raw_text_lines(fp_b)

    if not lines_a or not lines_b:
        return FeatureEvidence(
            name="content_xref",
            raw=None,
            log_lr=None,
            note="Uncomputable: missing text lines on one or both cards",
        )

    toks_a = _extract_content_tokens_from_lines(lines_a)
    toks_b = _extract_content_tokens_from_lines(lines_b)

    if not toks_a or not toks_b:
        return FeatureEvidence(
            name="content_xref",
            raw=None,
            log_lr=None,
            note="Uncomputable: no content tokens extracted after stopword filtering",
        )

    common = toks_a.intersection(toks_b)
    if not common:
        return FeatureEvidence(
            name="content_xref",
            raw=0.0,
            log_lr=None,
            note="Zero shared content tokens between cards",
        )

    # Weighted or unweighted overlap score
    if deck_idf is not None and len(deck_idf) > 0:
        w_common = sum(deck_idf.get(t, 1.0) for t in common)
        w_a = sum(deck_idf.get(t, 1.0) for t in toks_a)
        w_b = sum(deck_idf.get(t, 1.0) for t in toks_b)
        w_union = sum(deck_idf.get(t, 1.0) for t in toks_a.union(toks_b))
        # Blend of overlap coefficient (shared / min(|A|, |B|)) and Jaccard
        overlap_coeff = w_common / max(1e-6, min(w_a, w_b))
        jaccard = w_common / max(1e-6, w_union)
        score = min(1.0, max(0.0, 0.80 * overlap_coeff + 0.20 * jaccard))
    else:
        overlap_coeff = len(common) / min(len(toks_a), len(toks_b))
        jaccard = len(common) / len(toks_a.union(toks_b))
        score = min(1.0, max(0.0, 0.80 * overlap_coeff + 0.20 * jaccard))

    return FeatureEvidence(
        name="content_xref",
        raw=round(score, 4),
        log_lr=None,
        note=f"Matched {len(common)} tokens: {sorted(list(common))[:4]}",
    )


def _compute_sentence_continuity(
    fp_a: VisualFingerprint,
    fp_b: VisualFingerprint,
) -> FeatureEvidence:
    """Evaluates directed sentence continuation from card A's tail to card B's head."""
    # Preceding card tail
    tail_a = ""
    if fp_a.last_lines:
        tail_a = fp_a.last_lines[-1].strip()
    elif fp_a.boundary_text and fp_a.boundary_text.last_line:
        tail_a = fp_a.boundary_text.last_line.strip()

    # Following card head
    head_b = ""
    if fp_b.first_lines:
        head_b = fp_b.first_lines[0].strip()
    elif fp_b.boundary_text and fp_b.boundary_text.first_line:
        head_b = fp_b.boundary_text.first_line.strip()

    has_text = bool(tail_a or head_b)
    has_cut_meta = (
        fp_a.text_cut_off_end is not None
        or fp_a.text_cut_off_start is not None
        or fp_b.text_cut_off_end is not None
        or fp_b.text_cut_off_start is not None
        or (fp_a.boundary_text and fp_a.boundary_text.is_cut_off_mid_sentence)
        or (fp_b.boundary_text and fp_b.boundary_text.is_cut_off_mid_sentence)
    )

    if not has_text and not has_cut_meta:
        return FeatureEvidence(
            name="sentence_continuity",
            raw=None,
            log_lr=None,
            note="Uncomputable: missing boundary text and cut-off metadata",
        )

    # 1. Hyphenation check across card faces: "pre-" + "heat" -> "preheat"
    if tail_a and head_b:
        # Check trailing hyphen (standard hyphen, en-dash, em-dash)
        hyphen_match = re.search(r"\b([a-zA-Z]{2,})[-–—]\s*$", tail_a)
        head_word_match = re.search(r"^\s*([a-zA-Z]{2,})\b", head_b)
        if hyphen_match and head_word_match:
            prefix = hyphen_match.group(1).lower()
            suffix = head_word_match.group(1).lower()
            # If head starts with lowercase suffix or forms valid combined token
            is_lower_start = head_word_match.group(1)[0].islower()
            if is_lower_start or len(prefix + suffix) >= 5:
                return FeatureEvidence(
                    name="sentence_continuity",
                    raw=1.0,
                    log_lr=None,
                    note=f"Cross-card hyphenation: '{prefix}-' + '{suffix}' -> '{prefix + suffix}'",
                )

    # 2. Directed cutoff cues
    score = 0.50

    # Preceding card ends cut off
    a_is_cut = (
        (fp_a.text_cut_off_end is True)
        or (fp_a.boundary_text and fp_a.boundary_text.is_cut_off_mid_sentence is True)
    )
    if tail_a:
        tail_clean = re.sub(r"\s+", " ", tail_a).strip()
        last_char = tail_clean[-1] if tail_clean else ""
        tail_words = tail_clean.lower().split()
        last_word = re.sub(r"[^a-z]", "", tail_words[-1]) if tail_words else ""

        if last_char in (",", ";", ":", "-") or last_word in DANGLING_CONJUNCTIONS:
            a_is_cut = True
        elif last_char in (".", "!", "?") and not any(tail_clean.lower().endswith(u) for u in ("tsp.", "tbsp.", "oz.", "lb.", "c.", "min.")):
            # Definite sentence termination
            a_clean_end = True
        else:
            a_clean_end = False
    else:
        a_clean_end = False

    # Following card starts with continuation
    b_is_cont_start = (fp_b.text_cut_off_start is True)
    if head_b:
        first_char = head_b[0]
        head_words = head_b.lower().split()
        first_word = re.sub(r"[^a-z]", "", head_words[0]) if head_words else ""

        if first_char.islower():
            b_is_cont_start = True
        elif first_word in DANGLING_CONJUNCTIONS or first_word in ("minutes", "degrees", "hours", "smooth", "golden", "tender"):
            b_is_cont_start = True

    # Check for reverse continuity (B ends cut off and A starts continuation, or A starts cut off / B ends cut off)
    b_tail = fp_b.last_lines[-1].strip() if fp_b.last_lines else ""
    a_head = fp_a.first_lines[0].strip() if fp_a.first_lines else ""
    is_reverse = (
        (fp_b.text_cut_off_end is True or (b_tail and b_tail[-1] in (",", "-")))
        or (fp_a.text_cut_off_start is True or (a_head and a_head[0].islower()))
    ) and not (a_is_cut or b_is_cont_start)

    if is_reverse:
        return FeatureEvidence(
            name="sentence_continuity",
            raw=0.0,
            log_lr=None,
            note="Reverse sentence continuity detected (B precedes A)",
        )

    # Directional score resolution
    if a_is_cut and b_is_cont_start:
        return FeatureEvidence(
            name="sentence_continuity",
            raw=0.95,
            log_lr=None,
            note="Card A cut off mid-sentence and Card B starts as continuation",
        )
    elif a_is_cut:
        return FeatureEvidence(
            name="sentence_continuity",
            raw=0.75,
            log_lr=None,
            note="Card A text cut off at tail",
        )
    elif b_is_cont_start:
        return FeatureEvidence(
            name="sentence_continuity",
            raw=0.70,
            log_lr=None,
            note="Card B text starts mid-sentence",
        )
    elif a_clean_end and head_b and head_b[0].isupper() and not b_is_cont_start:
        # Preceding card cleanly ends and following card starts a new sentence/title
        return FeatureEvidence(
            name="sentence_continuity",
            raw=0.10,
            log_lr=None,
            note="Card A terminates cleanly with period and Card B starts capitalized",
        )

    return FeatureEvidence(
        name="sentence_continuity",
        raw=0.50,
        log_lr=None,
        note="Neutral continuity cues",
    )


def _compute_step_continuity(
    fp_a: VisualFingerprint,
    fp_b: VisualFingerprint,
) -> FeatureEvidence:
    """Evaluates sequential instruction step progression (e.g. step n -> n+1)."""
    step_pattern = re.compile(r"(?:^|\n|\.\s*)\s*(?:step\s*)?([0-9]{1,2})[\.\)\:\-]\s+", re.IGNORECASE)

    def _extract_steps(fp: VisualFingerprint) -> List[int]:
        steps: List[int] = []
        lines = _extract_raw_text_lines(fp)
        for line in lines:
            matches = step_pattern.findall(line)
            for m in matches:
                try:
                    num = int(m)
                    if 1 <= num <= 50:
                        steps.append(num)
                except ValueError:
                    pass
        return sorted(list(set(steps)))

    steps_a = _extract_steps(fp_a)
    steps_b = _extract_steps(fp_b)

    if not steps_a and not steps_b:
        return FeatureEvidence(
            name="step_continuity",
            raw=None,
            log_lr=None,
            note="Uncomputable: no numbered steps found on either card",
        )

    if steps_a and steps_b:
        last_a = steps_a[-1]
        first_b = steps_b[0]
        if first_b == last_a + 1:
            return FeatureEvidence(
                name="step_continuity",
                raw=1.0,
                log_lr=None,
                note=f"Consecutive step sequence: step {last_a} -> step {first_b}",
            )
        elif first_b > last_a + 1:
            return FeatureEvidence(
                name="step_continuity",
                raw=0.60,
                log_lr=None,
                note=f"Forward step gap: step {last_a} -> step {first_b}",
            )
        elif first_b <= last_a:
            return FeatureEvidence(
                name="step_continuity",
                raw=0.0,
                log_lr=None,
                note=f"Non-sequential or reverse steps: step {last_a} vs step {first_b}",
            )

    # If Card A is ingredients and Card B starts with Step 1
    if not steps_a and steps_b:
        if steps_b[0] == 1 and fp_a.has_ingredients is True:
            return FeatureEvidence(
                name="step_continuity",
                raw=0.75,
                log_lr=None,
                note="Card A contains ingredients and Card B begins at Step 1",
            )
        elif steps_b[0] > 1:
            return FeatureEvidence(
                name="step_continuity",
                raw=0.30,
                log_lr=None,
                note=f"Card B starts at step {steps_b[0]} without preceding steps on A",
            )

    # Card A has steps but Card B has no numbered steps
    return FeatureEvidence(
        name="step_continuity",
        raw=0.20,
        log_lr=None,
        note="Card A contains numbered steps but Card B has none",
    )


def _compute_title_position(
    fp_a: VisualFingerprint,
    fp_b: VisualFingerprint,
) -> FeatureEvidence:
    """Evaluates canonical title placement (front only) and penalizes title collisions."""
    title_a = (fp_a.visible_title or "").strip()
    title_b = (fp_b.visible_title or "").strip()

    if not title_a and not title_b:
        return FeatureEvidence(
            name="title_position",
            raw=None,
            log_lr=None,
            note="Uncomputable: neither card has title data",
        )

    # Check if title_b is merely a continuation header (e.g. "Chocolate Cake (cont'd)" or "Part 2")
    is_b_continuation_title = False
    if title_b:
        tb_lower = title_b.lower()
        if any(h in tb_lower for h in ("cont", "continued", "page 2", "card 2", "pt 2", "part 2")):
            is_b_continuation_title = True

    if title_a and (not title_b or is_b_continuation_title):
        return FeatureEvidence(
            name="title_position",
            raw=1.0,
            log_lr=None,
            note="Preceding card A has title and following card B has no independent title",
        )
    elif title_a and title_b and not is_b_continuation_title:
        norm_a = re.sub(r"[^a-z0-9]", "", title_a.lower())
        norm_b = re.sub(r"[^a-z0-9]", "", title_b.lower())
        if norm_a and norm_b and (norm_a == norm_b or norm_a in norm_b or norm_b in norm_a):
            return FeatureEvidence(
                name="title_position",
                raw=0.95,
                log_lr=None,
                note=f"Matching titles across cards: '{title_a}' and '{title_b}'",
            )
        # Title clash: two distinct recipe titles indicate separate recipes
        return FeatureEvidence(
            name="title_position",
            raw=0.0,
            log_lr=None,
            note=f"Title clash: separate titles '{title_a}' vs '{title_b}'",
        )
    elif not title_a and title_b:
        # Reverse order: title appears on back card B instead of front card A
        return FeatureEvidence(
            name="title_position",
            raw=0.0,
            log_lr=None,
            note=f"Reverse title order: title on B ('{title_b}') but not on A",
        )

    return FeatureEvidence(
        name="title_position",
        raw=0.50,
        log_lr=None,
        note="Neutral title positioning",
    )


def _compute_marker_evidence(
    fp_a: VisualFingerprint,
    fp_b: VisualFingerprint,
) -> FeatureEvidence:
    """Evaluates printed physical and boundary markers ('over', 'cont\\'d', 'page 2')."""
    markers_a = [m.strip().lower() for m in (fp_a.printed_markers or []) if m and m.strip()]
    markers_b = [m.strip().lower() for m in (fp_b.printed_markers or []) if m and m.strip()]

    # Also inspect boundary text lines for explicit continuation tokens
    lines_a = [ln.lower() for ln in _extract_raw_text_lines(fp_a)]
    lines_b = [ln.lower() for ln in _extract_raw_text_lines(fp_b)]

    has_any_markers = bool(markers_a or markers_b)
    has_text = bool(lines_a or lines_b)

    if not has_any_markers and not has_text:
        return FeatureEvidence(
            name="marker_evidence",
            raw=None,
            log_lr=None,
            note="Uncomputable: no markers or text available",
        )

    # 1. Page/part numbering progression: e.g. "page 1" / "1" on A and "page 2" / "2" on B
    def _extract_page_num(markers: List[str], lines: List[str]) -> Optional[int]:
        for m in markers:
            # Handle OCR noise like "- 2 -", "(2)", "p. 2", "card #2"
            m_clean = re.sub(r"[^a-z0-9]", " ", m).strip()
            if m_clean.isdigit():
                return int(m_clean)
            p_match = re.search(r"\b(?:page|card|part|pt|p)\s*#?\s*([0-9]{1,2})\b", m_clean)
            if p_match:
                return int(p_match.group(1))
        # Inspect lines
        for ln in lines:
            p_match = re.search(r"\b(?:page|card|part|pt)\s*#?\s*([0-9]{1,2})\b", ln)
            if p_match:
                return int(p_match.group(1))
        return None

    page_a = _extract_page_num(markers_a, lines_a)
    page_b = _extract_page_num(markers_b, lines_b)

    if page_a is not None and page_b is not None:
        if page_b == page_a + 1:
            return FeatureEvidence(
                name="marker_evidence",
                raw=1.0,
                log_lr=None,
                note=f"Sequential page/card marker progression: {page_a} -> {page_b}",
            )
        elif page_b > page_a:
            return FeatureEvidence(
                name="marker_evidence",
                raw=0.75,
                log_lr=None,
                note=f"Forward page/card gap: {page_a} -> {page_b}",
            )
        elif page_b <= page_a:
            return FeatureEvidence(
                name="marker_evidence",
                raw=0.0,
                log_lr=None,
                note=f"Reverse or duplicate page markers: {page_a} vs {page_b}",
            )

    # 2. Text marker matching ('over' on A, 'cont\'d' / 'page 2' on B)
    def _has_forward_marker(markers: List[str], lines: List[str]) -> bool:
        for m in markers:
            m_norm = re.sub(r"[^a-z0-9]", " ", m).strip()
            m_nospace = re.sub(r"[^a-z0-9]", "", m).strip()
            if any(fm in m_norm or fm in m_nospace for fm in ("over", "turn over", "see back", "pto", "cont", "continued")):
                return True
        for ln in lines[-2:]:
            ln_norm = re.sub(r"[^a-z0-9]", " ", ln).strip()
            ln_nospace = re.sub(r"[^a-z0-9]", "", ln).strip()
            words = ln_norm.split()
            if "over" in words and len(words) <= 4:
                return True
            if any(fm in ln_norm or fm in ln_nospace for fm in ("turn over", "see back", "p t o", "pto", "cont d", "continued")):
                return True
        return False

    def _has_continuation_marker(markers: List[str], lines: List[str]) -> bool:
        for m in markers:
            m_norm = re.sub(r"[^a-z0-9]", " ", m).strip()
            m_nospace = re.sub(r"[^a-z0-9]", "", m).strip()
            if any(cm in m_norm or cm in m_nospace for cm in ("cont", "continued", "page 2", "card 2", "part 2", "pt 2", "instructions continued", "page2", "card2", "part2", "pt2")):
                return True
        for ln in lines[:2]:
            ln_norm = re.sub(r"[^a-z0-9]", " ", ln).strip()
            ln_nospace = re.sub(r"[^a-z0-9]", "", ln).strip()
            if any(cm in ln_norm or cm in ln_nospace for cm in ("cont", "continued", "page 2", "card 2", "part 2", "pt 2", "page2", "card2", "part2", "pt2")):
                return True
        return False

    a_forward = _has_forward_marker(markers_a, lines_a) or (page_a is not None and page_a == 1)
    b_cont = _has_continuation_marker(markers_b, lines_b) or (page_b is not None and page_b >= 2)

    if a_forward and b_cont:
        return FeatureEvidence(
            name="marker_evidence",
            raw=1.0,
            log_lr=None,
            note="Forward marker on Card A matching continuation header on Card B",
        )
    elif a_forward:
        return FeatureEvidence(
            name="marker_evidence",
            raw=0.85,
            log_lr=None,
            note="Forward continuation marker detected on Card A",
        )
    elif b_cont:
        return FeatureEvidence(
            name="marker_evidence",
            raw=0.85,
            log_lr=None,
            note="Continuation header detected on Card B",
        )

    # Check for reverse cues (B has forward marker pointing forward, or A has page 2)
    b_forward = _has_forward_marker(markers_b, lines_b)
    a_cont = _has_continuation_marker(markers_a, lines_a)
    if b_forward or a_cont:
        return FeatureEvidence(
            name="marker_evidence",
            raw=0.0,
            log_lr=None,
            note="Reverse marker order detected",
        )

    # If neither card has any marker cues found
    return FeatureEvidence(
        name="marker_evidence",
        raw=None,
        log_lr=None,
        note="Uncomputable: no recognized markers in text fields",
    )


def _compute_section_flow(
    fp_a: VisualFingerprint,
    fp_b: VisualFingerprint,
) -> FeatureEvidence:
    """Evaluates section flow from ingredients on front card A to directions on back card B."""
    sec_a = [s.strip().lower() for s in (fp_a.sections_detected or [])]
    sec_b = [s.strip().lower() for s in (fp_b.sections_detected or [])]

    has_meta_a = (fp_a.has_ingredients is not None or fp_a.has_directions is not None or bool(sec_a))
    has_meta_b = (fp_b.has_ingredients is not None or fp_b.has_directions is not None or bool(sec_b))

    if not has_meta_a and not has_meta_b:
        return FeatureEvidence(
            name="section_flow",
            raw=None,
            log_lr=None,
            note="Uncomputable: section detection metadata absent",
        )

    a_ing = (fp_a.has_ingredients is True) or ("ingredients" in sec_a)
    a_dir = (fp_a.has_directions is True) or ("directions" in sec_a)
    b_ing = (fp_b.has_ingredients is True) or ("ingredients" in sec_b)
    b_dir = (fp_b.has_directions is True) or ("directions" in sec_b)

    # Canonical flow: A has ingredients without directions, B has directions
    if a_ing and not a_dir and b_dir:
        return FeatureEvidence(
            name="section_flow",
            raw=1.0,
            log_lr=None,
            note="Canonical section flow: Card A ingredients -> Card B directions",
        )
    elif a_ing and a_dir and b_dir and not b_ing:
        # A starts recipe with ingredients+steps, B continues directions
        return FeatureEvidence(
            name="section_flow",
            raw=0.60,
            log_lr=None,
            note="Card A contains ingredients and directions, Card B continues directions",
        )
    elif a_dir and not a_ing and b_ing:
        # Reverse flow: directions on front and ingredients on back
        return FeatureEvidence(
            name="section_flow",
            raw=0.0,
            log_lr=None,
            note="Reverse section flow: directions on A, ingredients on B",
        )
    elif a_ing and a_dir and b_ing and b_dir:
        # Both cards appear to have complete standalone recipes
        return FeatureEvidence(
            name="section_flow",
            raw=0.10,
            log_lr=None,
            note="Both cards contain ingredients and directions (standalone recipes)",
        )
    elif a_ing and not a_dir and b_ing and not b_dir:
        # Both cards are ingredients only
        return FeatureEvidence(
            name="section_flow",
            raw=0.20,
            log_lr=None,
            note="Both cards contain ingredient lists only",
        )

    return FeatureEvidence(
        name="section_flow",
        raw=0.40,
        log_lr=None,
        note="Indeterminate section flow",
    )


# ============================================================================
# 3. PUBLIC API: content_features
# ============================================================================

def content_features(
    fp_a: VisualFingerprint,
    fp_b: VisualFingerprint,
    *,
    deck_idf: Optional[Dict[str, float]] = None,
    include_ocr_cue: bool = True,
) -> List[FeatureEvidence]:
    """Computes directed pairwise content-continuity features from card A to card B.

    Pure, deterministic, and fully offline. Returns raw measurements only (log_lr = None).

    Args:
        fp_a: Preceding candidate card visual fingerprint.
        fp_b: Following candidate card visual fingerprint.
        deck_idf: Optional dictionary of token -> IDF weight to weight distinctive
            shared vocabulary.
        include_ocr_cue: If True, also includes 'ocr_cue' mirroring 'marker_evidence'
            for backward compatibility with models expecting the ocr_cue feature name.

    Returns:
        List of FeatureEvidence models representing directed content continuity.
    """
    ev_xref = _compute_content_xref(fp_a, fp_b, deck_idf=deck_idf)
    ev_sent = _compute_sentence_continuity(fp_a, fp_b)
    ev_step = _compute_step_continuity(fp_a, fp_b)
    ev_title = _compute_title_position(fp_a, fp_b)
    ev_marker = _compute_marker_evidence(fp_a, fp_b)
    ev_flow = _compute_section_flow(fp_a, fp_b)

    features = [
        ev_xref,
        ev_sent,
        ev_step,
        ev_title,
        ev_marker,
    ]

    if include_ocr_cue:
        features.append(
            FeatureEvidence(
                name="ocr_cue",
                raw=ev_marker.raw,
                log_lr=None,
                weight=ev_marker.weight,
                note=ev_marker.note,
            )
        )

    features.append(ev_flow)
    return features


# ============================================================================
# 4. PUBLIC API: completeness_prior
# ============================================================================

def completeness_prior(fp: VisualFingerprint) -> Dict[str, float]:
    """Computes recipe completeness priors for a single card face.

    Estimates whether the card face represents a standalone recipe, expects a
    continuation, or represents the start of a recipe. Serves as node costs in
    global graph path-cover optimization (WP-GD, G7).

    Args:
        fp: Card visual fingerprint.

    Returns:
        Dictionary mapping:
            - 'expects_continuation': Probability in [0.0, 1.0] that this card needs a successor.
            - 'is_complete': Probability in [0.0, 1.0] that this single card is a standalone recipe.
            - 'is_start': Probability in [0.0, 1.0] that this card begins a recipe chain.
    """
    has_title = bool(fp.visible_title and fp.visible_title.strip())
    sec = [s.strip().lower() for s in (fp.sections_detected or [])]

    has_ing = (fp.has_ingredients is True) or ("ingredients" in sec)
    has_dir = (fp.has_directions is True) or ("directions" in sec)
    is_back = (fp.looks_like_back is True)

    is_cut_end = (fp.text_cut_off_end is True) or (
        fp.boundary_text is not None and fp.boundary_text.is_cut_off_mid_sentence is True
    )
    is_cut_start = (fp.text_cut_off_start is True)

    # Check forward markers on card
    markers = [m.strip().lower() for m in (fp.printed_markers or []) if m and m.strip()]
    lines = [ln.lower() for ln in _extract_raw_text_lines(fp)]
    has_forward_cue = any(
        any(fm in m for fm in ("over", "turn over", "see back", "pto", "cont", "continued"))
        for m in markers
    ) or any(
        any(fm in ln for fm in ("(over)", "turn over", "cont'd on back", "continued on next"))
        for ln in lines[-2:]
    )

    # Check reverse / continuation header
    has_cont_header = any(
        any(cm in m for cm in ("cont", "continued", "page 2", "card 2", "part 2"))
        for m in markers
    ) or (
        bool(lines) and any(cm in lines[0] for cm in ("cont", "continued", "page 2", "card 2", "part 2"))
    )

    # 1. is_start probability
    p_start = 0.50
    if has_title:
        p_start += 0.35
    if has_ing:
        p_start += 0.15
    if is_back:
        p_start -= 0.40
    if is_cut_start:
        p_start -= 0.30
    if has_cont_header:
        p_start -= 0.45
    if has_dir and not has_ing and not has_title:
        p_start -= 0.35

    # 2. expects_continuation probability
    p_cont = 0.20
    if has_ing and not has_dir:
        p_cont += 0.55
    if is_cut_end:
        p_cont += 0.40
    if has_forward_cue:
        p_cont += 0.45
    if has_dir and not is_cut_end and not has_forward_cue:
        p_cont -= 0.25
    if is_back and not has_forward_cue and not is_cut_end:
        p_cont -= 0.20

    # 3. is_complete probability
    p_complete = 0.50
    if has_title and has_ing and has_dir and not is_cut_end and not has_forward_cue:
        p_complete = 0.92
    else:
        if not has_dir:
            p_complete -= 0.45
        if not has_ing:
            p_complete -= 0.35
        if is_cut_end or has_forward_cue:
            p_complete -= 0.45
        if is_back or has_cont_header or is_cut_start:
            p_complete -= 0.40

    p_start = min(1.0, max(0.0, round(p_start, 3)))
    p_cont = min(1.0, max(0.0, round(p_cont, 3)))
    p_complete = min(1.0, max(0.0, round(p_complete, 3)))

    return {
        "expects_continuation": p_cont,
        "is_complete": p_complete,
        "is_start": p_start,
    }
