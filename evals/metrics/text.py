"""Text normalization, edit distance, and unit parsing utilities for evals.

Pure functions with no dependencies outside the standard library.
"""

from __future__ import annotations

import math
import re
import unicodedata
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence

# Mapping of unicode vulgar fractions to ASCII fraction representations
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

# Standard unit canonicalization dictionary
UNIT_SYNONYMS: Dict[str, str] = {
    # Cups
    "c": "cup",
    "c.": "cup",
    "cup": "cup",
    "cups": "cup",
    # Teaspoons
    "t": "tsp",
    "t.": "tsp",
    "tsp": "tsp",
    "tsp.": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    # Tablespoons
    "tbsp": "tbsp",
    "tbsp.": "tbsp",
    "tbs": "tbsp",
    "tbs.": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    # Pounds
    "lb": "lb",
    "lb.": "lb",
    "lbs": "lb",
    "lbs.": "lb",
    "pound": "lb",
    "pounds": "lb",
    # Ounces
    "oz": "oz",
    "oz.": "oz",
    "ounce": "oz",
    "ounces": "oz",
    # Fluid ounces
    "fl oz": "fl oz",
    "fl. oz.": "fl oz",
    "fluid ounce": "fl oz",
    "fluid ounces": "fl oz",
    # Pints, quarts, gallons
    "pt": "pint",
    "pt.": "pint",
    "pint": "pint",
    "pints": "pint",
    "qt": "quart",
    "qt.": "quart",
    "quart": "quart",
    "quarts": "quart",
    "gal": "gallon",
    "gal.": "gallon",
    "gallon": "gallon",
    "gallons": "gallon",
    # Metric
    "g": "g",
    "g.": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "kg.": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "ml": "ml",
    "ml.": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "l": "l",
    "l.": "l",
    "liter": "l",
    "liters": "l",
    # Count / culinary units
    "pinch": "pinch",
    "pinches": "pinch",
    "dash": "dash",
    "dashes": "dash",
    "pkg": "pkg",
    "pkg.": "pkg",
    "package": "pkg",
    "packages": "pkg",
    "clove": "clove",
    "cloves": "clove",
    "can": "can",
    "cans": "can",
    "slice": "slice",
    "slices": "slice",
}


def normalize_text(
    text: Optional[str],
    *,
    case_sensitive: bool = False,
    preserve_punctuation: bool = False,
    normalize_fractions: bool = True,
) -> str:
    """Normalizes recipe text using unicode NFKC, fraction conversion, and whitespace collapse.

    Steps:
    1. Fraction slash normalization (U+2044 fraction slash and U+2215 division slash -> /).
    2. Convert unicode vulgar fractions to ASCII (e.g. '½' -> '1/2').
    3. Normalize smart quotes, dashes, and degree symbols to standard ASCII.
    4. Unicode NFKC normalization.
    5. Case handling (lowercasing if case_sensitive is False).
    6. Extraneous punctuation stripping (keeping fraction slashes, digits, hyphens).
    7. Whitespace collapse and trimming.

    Args:
        text: Raw input string.
        case_sensitive: If True, keeps original casing. Default False.
        preserve_punctuation: If True, retains punctuation. Default False.
        normalize_fractions: If True, expands unicode fractions. Default True.

    Returns:
        Normalized clean string.
    """
    if text is None:
        return ""

    s = str(text)
    if not s:
        return ""

    # 1. Normalize fraction slashes
    s = s.replace("⁄", "/").replace("∕", "/")

    # 2. Unicode vulgar fractions conversion
    if normalize_fractions:
        for u_frac, a_frac in UNICODE_FRACTIONS.items():
            s = s.replace(u_frac, f" {a_frac} ")

    # 3. Smart quotes, dashes, degree symbols
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    s = s.replace("—", " - ").replace("–", " - ")
    s = s.replace("°F", " F").replace("°C", " C").replace("°", " deg ")

    # 4. Unicode NFKC normalization (as required by contract)
    s = unicodedata.normalize("NFKC", s)

    # 5. Case folding
    if not case_sensitive:
        s = s.lower()

    # 6. Punctuation handling
    if not preserve_punctuation:
        # Preserve decimal dots between digits (e.g. 1.5, 0.25)
        s = re.sub(r"(?<=\d)\.(?=\d)", "___DECIMAL___", s)
        # Keep word chars, whitespace, slashes, and hyphens
        s = re.sub(r"[^\w\s\/\-]", " ", s)
        s = s.replace("___DECIMAL___", ".")
        # Normalize slashes with spaces around them: e.g. "1 / 2" -> "1/2"
        s = re.sub(r"(\d+)\s*\/\s*(\d+)", r"\1/\2", s)

    # 7. Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def levenshtein_distance(seq1: Sequence[Any], seq2: Sequence[Any]) -> int:
    """Computes exact Levenshtein edit distance with O(min(N, M)) memory footprint."""
    if seq1 == seq2:
        return 0

    len1, len2 = len(seq1), len(seq2)
    if len1 == 0:
        return len2
    if len2 == 0:
        return len1

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


def cer(
    reference: Optional[str],
    hypothesis: Optional[str],
    *,
    normalize: bool = True,
    case_sensitive: bool = False,
) -> float:
    """Calculates normalized Character Error Rate (CER) capped at 1.0.

    CER = min(1.0, LevenshteinDistance(ref, hyp) / max(len(ref), 1))

    Edge cases:
    - ref == "" and hyp == "": 0.0
    - ref == "" and hyp != "": 1.0
    - ref != "" and hyp == "": 1.0
    - dist / len(ref) > 1.0: clamped to 1.0

    Args:
        reference: Ground-truth reference string.
        hypothesis: Predicted hypothesis string.
        normalize: Whether to apply text normalization.
        case_sensitive: Whether to retain case during normalization.

    Returns:
        Float error rate in [0.0, 1.0].
    """
    ref_clean = normalize_text(reference, case_sensitive=case_sensitive) if normalize else (reference or "")
    hyp_clean = normalize_text(hypothesis, case_sensitive=case_sensitive) if normalize else (hypothesis or "")

    if ref_clean == hyp_clean:
        return 0.0

    len_ref = len(ref_clean)
    len_hyp = len(hyp_clean)

    if len_ref == 0:
        return 1.0 if len_hyp > 0 else 0.0

    dist = levenshtein_distance(ref_clean, hyp_clean)
    return min(1.0, float(dist / len_ref))


def wer(
    reference: Optional[str],
    hypothesis: Optional[str],
    *,
    normalize: bool = True,
    case_sensitive: bool = False,
) -> float:
    """Calculates Word Error Rate (WER) across whitespace-delimited tokens capped at 1.0.

    WER = min(1.0, LevenshteinDistance(ref_words, hyp_words) / max(len(ref_words), 1))
    """
    ref_clean = normalize_text(reference, case_sensitive=case_sensitive) if normalize else (reference or "")
    hyp_clean = normalize_text(hypothesis, case_sensitive=case_sensitive) if normalize else (hypothesis or "")

    ref_words = ref_clean.split() if ref_clean else []
    hyp_words = hyp_clean.split() if hyp_clean else []

    if ref_words == hyp_words:
        return 0.0

    len_ref = len(ref_words)
    len_hyp = len(hyp_words)

    if len_ref == 0:
        return 1.0 if len_hyp > 0 else 0.0

    dist = levenshtein_distance(ref_words, hyp_words)
    return min(1.0, float(dist / len_ref))


def extract_fractions(text: Optional[str]) -> List[str]:
    """Extracts and standardizes all fraction occurrences from text."""
    if not text:
        return []

    norm = str(text).replace("⁄", "/").replace("∕", "/")
    for u_frac, a_frac in UNICODE_FRACTIONS.items():
        norm = norm.replace(u_frac, f" {a_frac} ")

    matches = re.findall(r"\b(?:\d+[\s\-]+)?\d+\s*\/\s*\d+\b", norm)
    cleaned: List[str] = []
    for m in matches:
        c = re.sub(r"(\d+)\s*\/\s*(\d+)", r"\1/\2", m.strip())
        c = re.sub(r"(\d+)[\s\-]+(\d+\/\d+)", r"\1 \2", c)
        c = re.sub(r"\s+", " ", c)
        cleaned.append(c)
    return cleaned


def normalize_unit(unit: Optional[str]) -> str:
    """Normalizes recipe units to canonical forms (c/cup/cups, tsp/t, tbsp/T, lb, oz, etc.).

    Args:
        unit: Raw unit string.

    Returns:
        Canonical unit string, or stripped string if not in synonym map.
    """
    if not unit:
        return ""

    raw = str(unit).strip()
    if not raw:
        return ""

    # Check uppercase 'T' / 'T.' for Tablespoon before lowercasing
    if raw in ("T", "T.", "TB", "TBS"):
        return "tbsp"
    if raw in ("t", "t."):
        return "tsp"

    cleaned = raw.lower().rstrip(".,")
    if cleaned in UNIT_SYNONYMS:
        return UNIT_SYNONYMS[cleaned]
    # Try with dot
    if f"{cleaned}." in UNIT_SYNONYMS:
        return UNIT_SYNONYMS[f"{cleaned}."]
    return cleaned


def parse_numeric_quantity(qty: Optional[str]) -> Optional[float]:
    """Parses a quantity string (int, float, fraction, or mixed fraction) into a float."""
    if not qty:
        return None

    s = normalize_text(str(qty), normalize_fractions=True)
    if not s:
        return None

    # Try simple float
    try:
        return float(s)
    except ValueError:
        pass

    # Try mixed number: e.g. "1 1/2" or "1-1/2"
    mixed_match = re.match(r"^(\d+)[\s\-]+(\d+)\s*\/\s*(\d+)$", s)
    if mixed_match:
        whole = int(mixed_match.group(1))
        num = int(mixed_match.group(2))
        denom = int(mixed_match.group(3))
        if denom != 0:
            return float(whole + Fraction(num, denom))

    # Try simple fraction: e.g. "1/2"
    frac_match = re.match(r"^(\d+)\s*\/\s*(\d+)$", s)
    if frac_match:
        num = int(frac_match.group(1))
        denom = int(frac_match.group(2))
        if denom != 0:
            return float(Fraction(num, denom))

    return None


def quantities_match(qty1: Optional[str], qty2: Optional[str], tolerance: float = 1e-4) -> bool:
    """Checks whether two quantity representations are semantically equivalent."""
    if qty1 is None and qty2 is None:
        return True
    if qty1 is None or qty2 is None:
        return False

    s1 = normalize_text(str(qty1), normalize_fractions=True)
    s2 = normalize_text(str(qty2), normalize_fractions=True)
    if s1 == s2:
        return True

    # Check numeric equivalence
    n1 = parse_numeric_quantity(qty1)
    n2 = parse_numeric_quantity(qty2)
    if n1 is not None and n2 is not None:
        return math.isclose(n1, n2, abs_tol=tolerance)

    return False
