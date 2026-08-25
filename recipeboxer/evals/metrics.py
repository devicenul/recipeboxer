"""Quantitative evaluation metric suite for RecipeBoxer.

Provides pure-Python, mathematically rigorous metric calculations for:
1. Pairing Metrics (Precision, Recall, F1, multi-card groupings)
2. Transcription Error Rates (Levenshtein-based CER & WER across text, ingredients, directions, fractions, metadata)
3. Schema Conformity & Completeness (Plan To Eat 9-column CSV, Schema.org JSON-LD, ISO 8601 durations)
"""

from __future__ import annotations

import itertools
import math
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, TypeVar, Union
from pydantic import BaseModel, Field

from recipeboxer.exporter.json_ld import JsonLdExporter, human_duration_to_iso8601
from recipeboxer.exporter.plan_to_eat import PlanToEatExporter
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import CardRole
from recipeboxer.models.recipe import RecipeSchema

T = TypeVar("T")

# ============================================================================
# 1. UNICODE FRACTION NORMALIZATION & TEXT UTILITIES
# ============================================================================

UNICODE_FRACTIONS: Dict[str, str] = {
    "½": "1/2",
    "⅓": "1/3",
    "⅔": "2/3",
    "¼": "1/4",
    "¾": "3/4",
    "⅕": "1/5",
    "⅖": "2/5",
    "⅗": "3/5",
    "⅘": "4/5",
    "⅙": "1/6",
    "⅚": "5/6",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}

# Regex matching common vulgar and ASCII fractions
FRACTION_REGEX = re.compile(
    r"(?:\b\d+[\s\-]+)?(?:[½⅓⅔¼¾⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]|\b\d+\s*/\s*\d+\b)",
    flags=re.IGNORECASE,
)


def normalize_text(
    text: Optional[str],
    preserve_case: bool = False,
    preserve_punctuation: bool = False,
    normalize_fractions: bool = True,
) -> str:
    """Normalizes recipe text for accurate character and word error rate computation.

    Steps:
    1. Replaces unicode vulgar fractions with standard ASCII (e.g. '½' -> '1/2').
    2. Replaces smart quotes, dashes, and degree symbols with standard ASCII.
    3. Normalizes unicode characters (NFKD decomposition).
    4. Strips extraneous punctuation if preserve_punctuation is False (keeping fraction slashes and digits).
    5. Collapses multiple whitespace characters and strips edges.
    6. Lowers case if preserve_case is False.

    Args:
        text: Raw text string.
        preserve_case: If True, retains original upper/lower casing.
        preserve_punctuation: If True, keeps punctuation marks.
        normalize_fractions: If True, converts unicode fractions to ASCII.

    Returns:
        Normalized clean string.
    """
    if not text:
        return ""

    s = str(text)

    # 1. Normalize fraction slashes (U+2044 fraction slash and U+2215 division slash -> /)
    s = s.replace("⁄", "/").replace("∕", "/")

    # 2. Replace unicode vulgar fractions
    if normalize_fractions:
        for u_frac, a_frac in UNICODE_FRACTIONS.items():
            s = s.replace(u_frac, f" {a_frac} ")

    # 3. Normalize smart quotes, dashes, degree signs
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = s.replace("—", " - ").replace("–", " - ")
    s = s.replace("°F", " F").replace("°C", " C").replace("°", " deg ")

    # 4. Unicode normalization
    s = unicodedata.normalize("NFKD", s)

    # 5. Case handling
    if not preserve_case:
        s = s.lower()

    # 6. Punctuation handling
    if not preserve_punctuation:
        # Keep letters, numbers, spaces, and slashes for fractions (and hyphens in words)
        s = re.sub(r"[^\w\s\/\-]", " ", s)
        # Normalize slashes with spaces around them: e.g. "1 / 2" -> "1/2"
        s = re.sub(r"(\d+)\s*\/\s*(\d+)", r"\1/\2", s)

    # 7. Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_fractions(text: Optional[str]) -> List[str]:
    """Extracts and standardizes all fraction occurrences from text.

    Examples:
        - "1/2 cup sugar and 3/4 tsp salt" -> ["1/2", "3/4"]
        - "½ cup butter and 1 ⅛ tsp vanilla" -> ["1/2", "1 1/8"]
        - "1 / 2 cup and 3⁄4 cup" -> ["1/2", "3/4"]
        - "1-1/2 cups flour and 2 - 1/4 tsp salt" -> ["1 1/2", "2 1/4"]

    Args:
        text: Input string.

    Returns:
        List of standardized fraction strings.
    """
    if not text:
        return []

    # First normalize fraction slashes and convert unicode vulgar fractions to ASCII
    norm = str(text).replace("⁄", "/").replace("∕", "/")
    for u_frac, a_frac in UNICODE_FRACTIONS.items():
        norm = norm.replace(u_frac, f" {a_frac} ")

    matches = re.findall(r"\b(?:\d+[\s\-]+)?\d+\s*\/\s*\d+\b", norm)
    # Clean up whitespace and hyphens inside fractions e.g. "1  1/2" -> "1 1/2", "1-1/2" -> "1 1/2", "1 / 2" -> "1/2"
    cleaned_matches: List[str] = []
    for m in matches:
        c = re.sub(r"(\d+)\s*\/\s*(\d+)", r"\1/\2", m.strip())
        c = re.sub(r"(\d+)[\s\-]+(\d+\/\d+)", r"\1 \2", c)
        c = re.sub(r"\s+", " ", c)
        cleaned_matches.append(c)
    return cleaned_matches


# ============================================================================
# 2. LEVENSHTEIN DISTANCE, CER & WER
# ============================================================================

def levenshtein_distance(seq1: Sequence[Any], seq2: Sequence[Any]) -> int:
    """Computes the exact Levenshtein edit distance between two sequences.

    Uses dynamic programming with O(min(N, M)) memory footprint.

    Args:
        seq1: First sequence (string, list of characters, or list of words).
        seq2: Second sequence.

    Returns:
        Minimum number of insertions, deletions, or substitutions.
    """
    if seq1 == seq2:
        return 0

    len1, len2 = len(seq1), len(seq2)
    if len1 == 0:
        return len2
    if len2 == 0:
        return len1

    # Ensure seq2 is the shorter sequence for O(min(N, M)) space
    if len1 < len2:
        seq1, seq2 = seq2, seq1
        len1, len2 = len2, len1

    prev_row = list(range(len2 + 1))
    curr_row = [0] * (len2 + 1)

    for i, item1 in enumerate(seq1):
        curr_row[0] = i + 1
        for j, item2 in enumerate(seq2):
            cost = 0 if item1 == item2 else 1
            curr_row[j + 1] = min(
                curr_row[j] + 1,        # insertion
                prev_row[j + 1] + 1,    # deletion
                prev_row[j] + cost,     # substitution
            )
        prev_row, curr_row = curr_row, prev_row

    return prev_row[len2]


def character_error_rate(
    reference: Optional[str],
    hypothesis: Optional[str],
    normalize: bool = True,
) -> float:
    """Calculates Levenshtein Character Error Rate (CER).

    CER = LevenshteinDistance(ref, hyp) / max(len(ref), 1)

    Edge cases:
    - ref == "" and hyp == "": 0.0
    - ref == "" and hyp != "": 1.0
    - ref != "" and hyp == "": 1.0

    Args:
        reference: Ground-truth reference string.
        hypothesis: Predicted hypothesis string.
        normalize: Whether to apply text normalization before comparison.

    Returns:
        CER float value >= 0.0 (0.0 represents perfect character match).
    """
    ref_clean = normalize_text(reference) if normalize else (reference or "")
    hyp_clean = normalize_text(hypothesis) if normalize else (hypothesis or "")

    if ref_clean == hyp_clean:
        return 0.0

    len_ref = len(ref_clean)
    len_hyp = len(hyp_clean)

    if len_ref == 0:
        return 1.0 if len_hyp > 0 else 0.0

    dist = levenshtein_distance(ref_clean, hyp_clean)
    return float(dist / len_ref)


def word_error_rate(
    reference: Optional[str],
    hypothesis: Optional[str],
    normalize: bool = True,
) -> float:
    """Calculates Levenshtein Word Error Rate (WER).

    WER = LevenshteinDistance(words(ref), words(hyp)) / max(len(words(ref)), 1)

    Args:
        reference: Ground-truth reference string.
        hypothesis: Predicted hypothesis string.
        normalize: Whether to apply text normalization before tokenization.

    Returns:
        WER float value >= 0.0 (0.0 represents perfect word match).
    """
    ref_clean = normalize_text(reference) if normalize else (reference or "")
    hyp_clean = normalize_text(hypothesis) if normalize else (hypothesis or "")

    ref_words = ref_clean.split() if ref_clean else []
    hyp_words = hyp_clean.split() if hyp_clean else []

    if ref_words == hyp_words:
        return 0.0

    len_ref = len(ref_words)
    len_hyp = len(hyp_words)

    if len_ref == 0:
        return 1.0 if len_hyp > 0 else 0.0

    dist = levenshtein_distance(ref_words, hyp_words)
    return float(dist / len_ref)


# ============================================================================
# 3. RECIPE-LEVEL TRANSCRIPTION METRIC SUITE
# ============================================================================

class TranscriptionMetricResult(BaseModel):
    """Detailed transcription accuracy metrics comparing hypothesis against ground truth."""
    recipe_id: str = Field(default="", description="Identifier of the evaluated recipe")
    overall_cer: float = Field(default=0.0, ge=0.0, description="Full recipe Character Error Rate")
    overall_wer: float = Field(default=0.0, ge=0.0, description="Full recipe Word Error Rate")
    ingredient_cer: float = Field(default=0.0, ge=0.0, description="Ingredients Character Error Rate")
    ingredient_wer: float = Field(default=0.0, ge=0.0, description="Ingredients Word Error Rate")
    direction_cer: float = Field(default=0.0, ge=0.0, description="Directions Character Error Rate")
    direction_wer: float = Field(default=0.0, ge=0.0, description="Directions Word Error Rate")
    metadata_cer: float = Field(default=0.0, ge=0.0, description="Metadata (times, yield, title) CER")
    metadata_wer: float = Field(default=0.0, ge=0.0, description="Metadata (times, yield, title) WER")
    fraction_accuracy: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Precision/Recall accuracy of extracted fractions (1/8, 1/4, 1/2, 3/4)",
    )
    direction_sequence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Step sequence monotonicity and numbering fidelity (0.0 to 1.0)",
    )


def _serialize_recipe_text(recipe: RecipeSchema) -> str:
    """Concatenates all text fields of a recipe into a canonical string."""
    parts = [
        recipe.title or "",
        recipe.description or "",
        recipe.yield_servings or "",
        recipe.prep_time or "",
        recipe.cook_time or "",
        recipe.total_time or "",
        "\n".join(str(i) for i in (recipe.ingredients or []) if i and str(i).strip()),
        "\n".join(str(d) for d in (recipe.directions or []) if d and str(d).strip()),
        " ".join(str(t) for t in (recipe.tags or []) if t and str(t).strip()),
        recipe.source_notes or "",
    ]
    return "\n".join(p for p in parts if p)


def _serialize_metadata_text(recipe: RecipeSchema) -> str:
    """Concatenates title, times, yield, and source notes into a single string."""
    parts = [
        recipe.title or "",
        recipe.prep_time or "",
        recipe.cook_time or "",
        recipe.total_time or "",
        recipe.yield_servings or "",
        recipe.source_notes or "",
    ]
    return " | ".join(p for p in parts if p)


def calculate_fraction_accuracy(
    ref_fractions: List[str],
    hyp_fractions: List[str],
) -> float:
    """Calculates F1 accuracy of fraction extraction against reference fractions.

    Args:
        ref_fractions: Ground truth fraction list.
        hyp_fractions: Predicted fraction list.

    Returns:
        Score between 0.0 and 1.0 (1.0 if both empty or perfect match).
    """
    if not ref_fractions and not hyp_fractions:
        return 1.0
    if not ref_fractions or not hyp_fractions:
        return 0.0

    # Count matching fractions with multiset frequency handling
    ref_counts: Dict[str, int] = {}
    for f in ref_fractions:
        ref_counts[f] = ref_counts.get(f, 0) + 1

    tp = 0
    for f in hyp_fractions:
        if ref_counts.get(f, 0) > 0:
            tp += 1
            ref_counts[f] -= 1

    precision = tp / len(hyp_fractions) if hyp_fractions else 0.0
    recall = tp / len(ref_fractions) if ref_fractions else 0.0

    if precision + recall == 0.0:
        return 0.0
    return float(2 * precision * recall / (precision + recall))


def calculate_direction_sequence_score(
    ref_directions: List[str],
    hyp_directions: List[str],
) -> float:
    """Evaluates sequential numbering and ordering preservation of direction steps.

    Checks:
    1. Step count ratio: min(ref_len, hyp_len) / max(ref_len, hyp_len)
    2. Numbering monotonicity: are extracted steps strictly sequential (1, 2, 3...)?
       If steps are unnumbered strings in list, list ordering is assumed valid.

    Args:
        ref_directions: Ground truth direction steps.
        hyp_directions: Predicted direction steps.

    Returns:
        Score between 0.0 and 1.0.
    """
    ref_clean = [str(d).strip() for d in (ref_directions or []) if d and str(d).strip()]
    hyp_clean = [str(d).strip() for d in (hyp_directions or []) if d and str(d).strip()]

    if not ref_clean and not hyp_clean:
        return 1.0
    if not ref_clean or not hyp_clean:
        return 0.0

    # 1. Step count preservation
    count_score = min(len(ref_clean), len(hyp_clean)) / max(len(ref_clean), len(hyp_clean))

    # 2. Numbering monotonicity check
    numbered_steps: List[Tuple[int, int]] = []
    for idx, step in enumerate(hyp_clean, start=1):
        m = re.match(r"^(?:Step\s+)?[\(\[]?\s*(\d+)\s*[\.\:\)\-\]]\s*", step, flags=re.IGNORECASE)
        if m:
            numbered_steps.append((idx, int(m.group(1))))

    if not numbered_steps:
        monotonicity_score = 1.0
    else:
        sequential_count = sum(1 for idx, num in numbered_steps if idx == num)
        monotonicity_score = sequential_count / len(numbered_steps)

    return float(0.5 * count_score + 0.5 * monotonicity_score)


def calculate_transcription_metrics(
    reference: RecipeSchema,
    hypothesis: RecipeSchema,
    recipe_id: Optional[str] = None,
) -> TranscriptionMetricResult:
    """Calculates comprehensive transcription error rates comparing hypothesis to reference.

    Args:
        reference: Ground-truth RecipeSchema.
        hypothesis: Candidate RecipeSchema to benchmark.
        recipe_id: Optional recipe ID string.

    Returns:
        Validated TranscriptionMetricResult.
    """
    rec_id = recipe_id or reference.recipe_id or hypothesis.recipe_id or "recipe_eval"

    # 1. Overall text CER / WER
    ref_all = _serialize_recipe_text(reference)
    hyp_all = _serialize_recipe_text(hypothesis)
    overall_cer = character_error_rate(ref_all, hyp_all)
    overall_wer = word_error_rate(ref_all, hyp_all)

    # 2. Ingredients CER / WER
    ref_ing = "\n".join(str(i) for i in (reference.ingredients or []) if i and str(i).strip())
    hyp_ing = "\n".join(str(i) for i in (hypothesis.ingredients or []) if i and str(i).strip())
    ing_cer = character_error_rate(ref_ing, hyp_ing)
    ing_wer = word_error_rate(ref_ing, hyp_ing)

    # 3. Directions CER / WER
    ref_dir = "\n".join(str(d) for d in (reference.directions or []) if d and str(d).strip())
    hyp_dir = "\n".join(str(d) for d in (hypothesis.directions or []) if d and str(d).strip())
    dir_cer = character_error_rate(ref_dir, hyp_dir)
    dir_wer = word_error_rate(ref_dir, hyp_dir)

    # 4. Metadata CER / WER
    ref_meta = _serialize_metadata_text(reference)
    hyp_meta = _serialize_metadata_text(hypothesis)
    meta_cer = character_error_rate(ref_meta, hyp_meta)
    meta_wer = word_error_rate(ref_meta, hyp_meta)

    # 5. Fraction extraction accuracy
    ref_fracs = extract_fractions(ref_all)
    hyp_fracs = extract_fractions(hyp_all)
    frac_acc = calculate_fraction_accuracy(ref_fracs, hyp_fracs)

    # 6. Direction step sequence score
    seq_score = calculate_direction_sequence_score(reference.directions or [], hypothesis.directions or [])

    return TranscriptionMetricResult(
        recipe_id=rec_id,
        overall_cer=round(overall_cer, 4),
        overall_wer=round(overall_wer, 4),
        ingredient_cer=round(ing_cer, 4),
        ingredient_wer=round(ing_wer, 4),
        direction_cer=round(dir_cer, 4),
        direction_wer=round(dir_wer, 4),
        metadata_cer=round(meta_cer, 4),
        metadata_wer=round(meta_wer, 4),
        fraction_accuracy=round(frac_acc, 4),
        direction_sequence_score=round(seq_score, 4),
    )


# ============================================================================
# 4. PAIRING METRICS (PRECISION, RECALL, F1, MULTI-CARD)
# ============================================================================

class PairingMetricResult(BaseModel):
    """Pairing accuracy metrics for card correlation and multi-card grouping."""
    precision: float = Field(default=0.0, ge=0.0, le=1.0, description="Pairing Precision (TP / (TP + FP))")
    recall: float = Field(default=0.0, ge=0.0, le=1.0, description="Pairing Recall (TP / (TP + FN))")
    f1_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Harmonic Mean F1 Score")
    true_positives: int = Field(default=0, ge=0, description="Count of correctly correlated card links")
    false_positives: int = Field(default=0, ge=0, description="Count of incorrectly proposed card links")
    false_negatives: int = Field(default=0, ge=0, description="Count of missed ground-truth card links")
    total_predicted: int = Field(default=0, ge=0, description="Total predicted pairs/links")
    total_ground_truth: int = Field(default=0, ge=0, description="Total ground-truth pairs/links")
    multi_card_accuracy: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="F1 / Jaccard score specifically on 3+ card groupings",
    )
    standalone_accuracy: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Accuracy on single-sided standalone card classification",
    )


def _canonical_card_id(card_id: Optional[str]) -> Optional[str]:
    """Standardizes card ID by stripping whitespace and common file extensions."""
    if not card_id:
        return None
    s = str(card_id).strip()
    return s


def _extract_pairs_from_input(
    raw_input: Any,
) -> Tuple[Set[Tuple[str, str]], Set[str], List[Set[str]]]:
    """Extracts (pair_links, standalone_cards, full_clusters) from various input representations.

    Supports:
    - List of tuples e.g. [("c1", "c2"), ("c3", None)]
    - List of CorrelationProposal
    - CorrelationResult
    - List of sets/lists e.g. [{"c1", "c2"}, {"c3", "c4", "c5"}]
    - SampleCardCorpus or List of CardEntry
    """
    pair_links: Set[Tuple[str, str]] = set()
    standalone_cards: Set[str] = set()
    full_clusters: List[Set[str]] = []

    if raw_input is None:
        return pair_links, standalone_cards, full_clusters

    # 1. SampleCardCorpus, Manifest, or Dict of {recipe_id: [CardEntry, ...]}
    cards_dict = getattr(raw_input, "_cards_by_recipe", None)
    if cards_dict is None and isinstance(raw_input, dict):
        cards_dict = raw_input

    if cards_dict is not None:
        for rec_id, cards in cards_dict.items():
            if not cards:
                continue
            sorted_cards = sorted(
                cards,
                key=lambda c: getattr(c, "recipe_cluster", {}).get("sequence_position", 0)
                if hasattr(c, "recipe_cluster")
                else 0,
            )
            card_ids = [getattr(c, "card_id", str(c)) for c in sorted_cards]
            full_clusters.append(set(card_ids))

            i = 0
            while i < len(sorted_cards):
                c_curr = sorted_cards[i]
                c_id = getattr(c_curr, "card_id", str(c_curr))
                c_role_raw = getattr(c_curr, "role", None)
                c_role_str = str(c_role_raw.value if hasattr(c_role_raw, "value") else c_role_raw).upper()

                if c_role_str == "STANDALONE" or i == len(sorted_cards) - 1:
                    standalone_cards.add(c_id)
                    i += 1
                else:
                    c_next = sorted_cards[i + 1]
                    next_id = getattr(c_next, "card_id", str(c_next))
                    next_role_raw = getattr(c_next, "role", None)
                    next_role_str = str(next_role_raw.value if hasattr(next_role_raw, "value") else next_role_raw).upper()

                    if (
                        (c_role_str == "FRONT_ONLY" and next_role_str == "BACK_ONLY")
                        or (c_role_str != "STANDALONE" and next_role_str != "STANDALONE")
                    ):
                        pair = tuple(sorted([c_id, next_id]))
                        pair_links.add(pair)
                        i += 2
                    else:
                        standalone_cards.add(c_id)
                        i += 1
        return pair_links, standalone_cards, full_clusters

    # 2. CorrelationResult
    if isinstance(raw_input, CorrelationResult):
        raw_input = raw_input.proposals

    # 3. List of items
    if isinstance(raw_input, (list, tuple, set)):
        # If list of CardEntry items
        if raw_input and hasattr(next(iter(raw_input)), "card_id") and hasattr(next(iter(raw_input)), "recipe_cluster"):
            grouped: Dict[str, List[Any]] = {}
            for item in raw_input:
                r_id = getattr(item, "recipe_cluster", {}).get("recipe_id", "rec_default")
                if r_id not in grouped:
                    grouped[r_id] = []
                grouped[r_id].append(item)
            return _extract_pairs_from_input(grouped)

        for item in raw_input:
            if isinstance(item, CorrelationProposal):
                f = _canonical_card_id(item.front_card_id)
                b = _canonical_card_id(item.back_card_id)
                if item.is_standalone or not b:
                    if f:
                        standalone_cards.add(f)
                        full_clusters.append({f})
                else:
                    if f and b:
                        pair = tuple(sorted([f, b]))
                        pair_links.add(pair)
                        full_clusters.append({f, b})

            elif isinstance(item, (set, list, tuple)):
                items = [_canonical_card_id(x) for x in item if x]
                items = [x for x in items if x is not None]
                if len(items) == 1:
                    standalone_cards.add(items[0])
                    full_clusters.append({items[0]})
                elif len(items) == 2:
                    pair = tuple(sorted([items[0], items[1]]))
                    pair_links.add(pair)
                    full_clusters.append(set(items))
                elif len(items) >= 3:
                    full_clusters.append(set(items))
                    # Pair sequential sheets
                    i = 0
                    while i < len(items):
                        if i == len(items) - 1:
                            standalone_cards.add(items[i])
                            i += 1
                        else:
                            pair = tuple(sorted([items[i], items[i + 1]]))
                            pair_links.add(pair)
                            i += 2

    return pair_links, standalone_cards, full_clusters


def calculate_pairing_metrics(
    predicted: Any,
    ground_truth: Any,
) -> PairingMetricResult:
    """Calculates Precision, Recall, F1, and multi-card metrics for correlation proposals.

    Accepts predicted proposals and ground truth in multiple flexible formats:
    - List of 2-tuples [('front.jpg', 'back.jpg'), ('single.jpg', None)]
    - List of CorrelationProposal instances
    - CorrelationResult instance
    - Multi-card grouping clusters [{'c1', 'c2', 'c3'}]
    - SampleCardCorpus or manifest-derived structures

    Args:
        predicted: Model predictions / proposals.
        ground_truth: True reference pairings.

    Returns:
        Validated PairingMetricResult instance.
    """
    pred_links, pred_standalones, pred_clusters = _extract_pairs_from_input(predicted)
    gt_links, gt_standalones, gt_clusters = _extract_pairs_from_input(ground_truth)

    # 1. Evaluate Pairwise Links & Standalone Markers
    pred_all_elements = set(pred_links) | {(c, c) for c in pred_standalones}
    gt_all_elements = set(gt_links) | {(c, c) for c in gt_standalones}

    if not pred_all_elements and not gt_all_elements:
        return PairingMetricResult(
            precision=1.0,
            recall=1.0,
            f1_score=1.0,
            true_positives=0,
            false_positives=0,
            false_negatives=0,
            total_predicted=0,
            total_ground_truth=0,
            multi_card_accuracy=1.0,
            standalone_accuracy=1.0,
        )

    tp_elements = pred_all_elements & gt_all_elements
    fp_elements = pred_all_elements - gt_all_elements
    fn_elements = gt_all_elements - pred_all_elements

    tp = len(tp_elements)
    fp = len(fp_elements)
    fn = len(fn_elements)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    # 2. Standalone accuracy
    if not gt_standalones and not pred_standalones:
        standalone_acc = 1.0
    elif not gt_standalones or not pred_standalones:
        standalone_acc = 0.0
    else:
        st_tp = len(pred_standalones & gt_standalones)
        st_fp = len(pred_standalones - gt_standalones)
        st_fn = len(gt_standalones - pred_standalones)
        st_p = st_tp / (st_tp + st_fp) if (st_tp + st_fp) > 0 else 0.0
        st_r = st_tp / (st_tp + st_fn) if (st_tp + st_fn) > 0 else 0.0
        standalone_acc = (2 * st_p * st_r / (st_p + st_r)) if (st_p + st_r) > 0 else 0.0

    # 3. Multi-card 3+ groupings accuracy
    gt_multi = [c for c in gt_clusters if len(c) >= 3]
    if not gt_multi:
        multi_acc = 1.0
    else:
        scores: List[float] = []
        for gtc in gt_multi:
            matched_pred_cards: Set[str] = set()
            for p in pred_links:
                if p[0] in gtc or p[1] in gtc:
                    matched_pred_cards.add(p[0])
                    matched_pred_cards.add(p[1])
            for s in pred_standalones:
                if s in gtc:
                    matched_pred_cards.add(s)

            if matched_pred_cards:
                inter = len(matched_pred_cards & gtc)
                union = len(matched_pred_cards | gtc)
                scores.append(inter / union if union > 0 else 0.0)
            else:
                scores.append(0.0)
        multi_acc = sum(scores) / len(scores) if scores else 0.0

    return PairingMetricResult(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1_score=round(f1, 4),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        total_predicted=len(pred_all_elements),
        total_ground_truth=len(gt_all_elements),
        multi_card_accuracy=round(multi_acc, 4),
        standalone_accuracy=round(standalone_acc, 4),
    )


# ============================================================================
# 5. SCHEMA CONFORMITY & COMPLETENESS VALIDATOR
# ============================================================================

class SchemaConformityResult(BaseModel):
    """Validation report verifying adherence to Plan To Eat and Schema.org RFCs."""
    is_valid: bool = Field(default=False, description="True if recipe passes all mandatory conformity checks")
    conformity_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Overall compliance score (0.0 to 1.0)")
    passed_checks: List[str] = Field(default_factory=list, description="List of passed validation check names")
    failed_checks: List[str] = Field(default_factory=list, description="List of failed validation check names")
    plan_to_eat_compliant: bool = Field(default=False, description="Adheres to Plan To Eat 9-column CSV RFC 4180")
    schema_org_compliant: bool = Field(default=False, description="Adheres to Schema.org/Recipe JSON-LD specifications")
    non_empty_title: bool = Field(default=False, description="Recipe title is present and non-empty")
    non_empty_ingredients: bool = Field(default=False, description="Ingredients list has >= 1 non-empty item")
    non_empty_directions: bool = Field(default=False, description="Directions list has >= 1 non-empty step")
    sequential_directions: bool = Field(default=False, description="Directions strictly follow '1. ', '2. ' numbering")
    iso8601_duration_valid: bool = Field(default=False, description="Prep/cook/total times convert to valid ISO 8601")
    fractions_clean: bool = Field(default=True, description="No corrupt fraction glyphs or unparsed artifacts")
    error_details: List[str] = Field(default_factory=list, description="Descriptive failure details")


def validate_schema_conformity(recipe: RecipeSchema) -> SchemaConformityResult:
    """Strictly validates a RecipeSchema instance against Plan To Eat and Schema.org standards.

    Performs 8 comprehensive validation checks:
    1. `non_empty_title`: Non-empty string title.
    2. `non_empty_ingredients`: At least 1 ingredient string.
    3. `non_empty_directions`: At least 1 direction step string.
    4. `sequential_directions`: Steps sequentially numbered '1. ', '2. ', etc.
    5. `plan_to_eat_compliant`: Validates 9-column CSV export & round-trip parse.
    6. `schema_org_compliant`: Validates Schema.org Recipe JSON-LD export.
    7. `iso8601_duration_valid`: Validates ISO 8601 duration parsing on times.
    8. `fractions_clean`: Ingredients/directions contain valid parsed fraction forms.

    Args:
        recipe: RecipeSchema instance to evaluate.

    Returns:
        Validated SchemaConformityResult.
    """
    passed: List[str] = []
    failed: List[str] = []
    errors: List[str] = []

    # 1. Non-empty title
    title_ok = bool(recipe.title and recipe.title.strip())
    if title_ok:
        passed.append("non_empty_title")
    else:
        failed.append("non_empty_title")
        errors.append("Recipe title is missing or whitespace only.")

    # 2. Non-empty ingredients
    ing_ok = bool(recipe.ingredients and len([i for i in recipe.ingredients if i and i.strip()]) > 0)
    if ing_ok:
        passed.append("non_empty_ingredients")
    else:
        failed.append("non_empty_ingredients")
        errors.append("Recipe ingredients list is empty.")

    # 3. Non-empty directions
    dir_ok = bool(recipe.directions and len([d for d in recipe.directions if d and d.strip()]) > 0)
    if dir_ok:
        passed.append("non_empty_directions")
    else:
        failed.append("non_empty_directions")
        errors.append("Recipe directions list is empty.")

    # 4. Sequential directions numbering
    seq_ok = True
    if dir_ok:
        for idx, step in enumerate(recipe.directions, start=1):
            m = re.match(r"^(\d+)\.\s+", step.strip())
            if not m or int(m.group(1)) != idx:
                seq_ok = False
                break
    else:
        seq_ok = False

    if seq_ok:
        passed.append("sequential_directions")
    else:
        failed.append("sequential_directions")
        errors.append("Recipe directions are not sequentially numbered starting at '1. '")

    # 5. Plan To Eat 9-column CSV compliance
    pte_ok = False
    try:
        exporter = PlanToEatExporter()
        csv_str = exporter.export_to_csv_string([recipe])
        exporter.validate_csv_format(csv_str)
        pte_ok = True
        passed.append("plan_to_eat_compliant")
    except Exception as exc:
        failed.append("plan_to_eat_compliant")
        errors.append(f"Plan To Eat CSV validation failed: {exc}")

    # 6. Schema.org Recipe JSON-LD compliance
    schema_ok = False
    try:
        jsonld_exp = JsonLdExporter()
        ld_dict = jsonld_exp.export_to_json_ld_dict(recipe)
        if ld_dict.get("@context") == "https://schema.org" and ld_dict.get("@type") == "Recipe":
            if ld_dict.get("name") and isinstance(ld_dict.get("recipeIngredient"), list):
                schema_ok = True
        if schema_ok:
            passed.append("schema_org_compliant")
        else:
            failed.append("schema_org_compliant")
            errors.append("Schema.org JSON-LD missing required Recipe fields.")
    except Exception as exc:
        failed.append("schema_org_compliant")
        errors.append(f"Schema.org JSON-LD generation failed: {exc}")

    # 7. ISO 8601 Duration validity
    duration_ok = True
    for time_field in ("prep_time", "cook_time", "total_time"):
        val = getattr(recipe, time_field, None)
        if val and val.strip():
            s_val = val.lower().strip()
            # Recognize 0 duration (e.g. "0 mins", "0 min", "0 hrs", "0", "0 hours 0 minutes", "0m 0s")
            cleaned_zero = re.sub(
                r"[0\s\-\,\;]|(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s|zero)",
                "",
                s_val,
            )
            if cleaned_zero == "" and ("0" in s_val or "zero" in s_val):
                continue
            iso_val = human_duration_to_iso8601(val)
            if not iso_val:
                duration_ok = False
                errors.append(f"Field '{time_field}' ('{val}') could not be converted to valid ISO 8601 duration.")

    if duration_ok:
        passed.append("iso8601_duration_valid")
    else:
        failed.append("iso8601_duration_valid")

    # 8. Fraction formatting cleanliness
    fractions_ok = True
    for item in (recipe.ingredients or []) + (recipe.directions or []):
        if not item:
            continue
        s_item = str(item)
        if "\ufffd" in s_item or "???" in s_item:
            fractions_ok = False
            errors.append(f"Corrupt fraction or replacement character detected: {s_item}")
            break

    if fractions_ok:
        passed.append("fractions_clean")
    else:
        failed.append("fractions_clean")

    total_checks = 8
    score = len(passed) / total_checks
    is_valid = (len(failed) == 0)

    return SchemaConformityResult(
        is_valid=is_valid,
        conformity_score=round(score, 4),
        passed_checks=passed,
        failed_checks=failed,
        plan_to_eat_compliant=pte_ok,
        schema_org_compliant=schema_ok,
        non_empty_title=title_ok,
        non_empty_ingredients=ing_ok,
        non_empty_directions=dir_ok,
        sequential_directions=seq_ok,
        iso8601_duration_valid=duration_ok,
        fractions_clean=fractions_ok,
        error_details=errors,
    )
