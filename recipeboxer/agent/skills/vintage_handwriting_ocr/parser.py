"""Parsing and normalization logic for vintage handwritten recipe cards."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple, Union

from recipeboxer.models.recipe import RecipeSchema


# Unicode vulgar fraction mappings
UNICODE_FRACTION_MAP: Dict[str, str] = {
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

# Archaic unit replacements
ARCHAIC_UNIT_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:(?:a|an)\s+)?(?:piece|lump|pat|knob|chunk)?\s*(?:of\s+)?butter\s+(?:the\s+)?size\s+of\s+an?\s+egg\b", re.IGNORECASE), "1/4 cup butter (size of an egg)"),
    (re.compile(r"\b(?:(?:a|an)\s+)?(?:piece|lump|pat|knob|chunk)?\s*(?:of\s+)?butter\s+(?:the\s+)?size\s+of\s+a\s+walnut\b", re.IGNORECASE), "2 tbsp butter (size of a walnut)"),
    (re.compile(r"\b(?:(?:a|an)\s+)?(?:piece|lump|pat|knob|chunk)?\s*(?:of\s+)?butter\s+(?:the\s+)?size\s+of\s+a\s+hazelnut\b", re.IGNORECASE), "1 tsp butter (size of a hazelnut)"),
    (re.compile(r"\b(?:(?:a|an)\s+)?(?:piece|lump|pat|knob|chunk)?\s*(?:of\s+)?butter\s+(?:the\s+)?size\s+of\s+a\s+pea\b", re.IGNORECASE), "1/2 tsp butter (size of a pea)"),
    (re.compile(r"\b(?:(?:a|an)\s+)?(?:piece|lump|pat|knob|chunk)?\s*(?:of\s+)?butter\s+(?:the\s+)?size\s+of\s+a\s+hickory\s+nut\b", re.IGNORECASE), "1 1/2 tbsp butter (size of a hickory nut)"),
    (re.compile(r"\boleo(?:margarine)?\b", re.IGNORECASE), "oleomargarine (butter)"),
    (re.compile(r"\bsaleratus\b", re.IGNORECASE), "baking soda (saleratus)"),
    (re.compile(r"\bsweet\s+milk\b", re.IGNORECASE), "whole milk (sweet milk)"),
    (re.compile(r"\bsour\s+milk\b", re.IGNORECASE), "buttermilk (sour milk)"),
    (re.compile(r"\b(a|an|one|\d+(?:/\d+)?|\d+\s+\d+/\d+)\s+(gills?)\b", re.IGNORECASE), r"\1 \2 (1/2 cup)"),
    (re.compile(r"\b(a|an|one|\d+(?:/\d+)?|\d+\s+\d+/\d+)\s+(pecks?)\b", re.IGNORECASE), r"\1 \2 (2 gal)"),
    (re.compile(r"\b(a|an|one|\d+(?:/\d+)?|\d+\s+\d+/\d+)\s+(teacups?)\b", re.IGNORECASE), r"\1 \2 (3/4 cup)"),
    (re.compile(r"\b(a|an|one|\d+(?:/\d+)?|\d+\s+\d+/\d+)\s+(wineglass(?:es)?)\b", re.IGNORECASE), r"\1 \2 (1/4 cup)"),
    (re.compile(r"\b(a|an|one|\d+(?:/\d+)?|\d+\s+\d+/\d+)\s+(tumblers?)\b", re.IGNORECASE), r"\1 \2 (1 cup)"),
    (re.compile(r"\b(a|an|one|\d+(?:/\d+)?|\d+\s+\d+/\d+)\s+(drams?|drachms?)\b", re.IGNORECASE), r"\1 \2 (1/8 oz)"),
    (re.compile(r"\b(a|an|one|\d+(?:/\d+)?|\d+\s+\d+/\d+)\s+(scruples?)\b", re.IGNORECASE), r"\1 \2 (1/24 oz)"),
]

# Shorthand abbreviation replacements
ABBREVIATION_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(\b\d+(?:/\d+)?|\b\d+\s+\d+/\d+)\s*T\.?(?=\s|$|,)"), r"\1 tbsp"),
    (re.compile(r"(\b\d+(?:/\d+)?|\b\d+\s+\d+/\d+)\s*t\.?(?=\s|$|,)"), r"\1 tsp"),
    (re.compile(r"(\b\d+(?:/\d+)?|\b\d+\s+\d+/\d+)\s*C\.?(?=\s|$|,)"), r"\1 cup"),
    (re.compile(r"\b(?:Tbsp|Tbs|Tblsp|Tbl)\.?(?=\s|$|,)", re.IGNORECASE), "tbsp"),
    (re.compile(r"\btsps?\.?(?=\s|$|,)", re.IGNORECASE), "tsp"),
    (re.compile(r"\bpkgs?\.?(?=\s|$|,)", re.IGNORECASE), "pkg"),
    (re.compile(r"\bpts?\.?(?=\s|$|,)", re.IGNORECASE), "pint"),
    (re.compile(r"\bqts?\.?(?=\s|$|,)", re.IGNORECASE), "quart"),
    (re.compile(r"\bozs?\.?(?=\s|$|,)", re.IGNORECASE), "oz"),
    (re.compile(r"\blbs?\.?(?=\s|$|,)", re.IGNORECASE), "lb"),
    (re.compile(r"\bc\.(?=\s|$|,)", re.IGNORECASE), "cup"),
]


def normalize_fractions(text: str) -> str:
    """Normalizes Unicode vulgar fractions and composite mixed fractions into standard ASCII.

    Examples:
        '½ cup' -> '1/2 cup'
        '1½ cups' -> '1 1/2 cups'
        '1-1/2 cups' -> '1 1/2 cups'
        '¾ tsp' -> '3/4 tsp'
    """
    if not text:
        return ""

    # Standardize unicode fraction slashes and division slashes
    text = text.replace("\u2044", "/").replace("\u2215", "/")

    # Replace mixed number patterns like '1½' -> '1 1/2'
    for u_char, ascii_frac in UNICODE_FRACTION_MAP.items():
        # Digit followed immediately by fraction
        text = re.sub(rf"(\d+){u_char}", rf"\1 {ascii_frac}", text)
        # Standalone fraction
        text = text.replace(u_char, ascii_frac)

    # Normalize multiple spaces around fractions first
    text = re.sub(r"(\d+)\s+/\s+(\d+)", r"\1/\2", text)

    # Convert hyphenated mixed numbers like '1-1/2' or '1 - 1/2' -> '1 1/2'
    # Use negative lookbehind (?<!/) to preserve fraction ranges like '1/4 - 1/2 tsp'
    text = re.sub(r"(?<!/)\b(\d+)\s*-\s*(\d+/\d+)", r"\1 \2", text)

    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_archaic_units(text: str) -> str:
    """Normalizes historical culinary terms, shorthand units, and archaic volume words.

    Examples:
        '1/4 cup oleo' -> '1/4 cup oleomargarine (butter)'
        '1 tsp saleratus' -> '1 tsp baking soda (saleratus)'
        'butter size of an egg' -> '1/4 cup butter (size of an egg)'
        '2 T sugar' -> '2 tbsp sugar'
        '1 t vanilla' -> '1 tsp vanilla'
    """
    if not text:
        return ""

    normalized = normalize_fractions(text)

    # Apply archaic unit replacements
    for pattern, replacement in ARCHAIC_UNIT_PATTERNS:
        normalized = pattern.sub(replacement, normalized)

    # Apply abbreviation patterns
    for pattern, replacement in ABBREVIATION_PATTERNS:
        normalized = pattern.sub(replacement, normalized)

    return normalized


def get_vintage_handwriting_prompt() -> str:
    """Returns specialized prompt instructions for vintage cursive handwriting."""
    return """You are a master culinary paleographer specializing in 19th and 20th-century American cursive handwriting.
Transcribe this recipe card with deep attention to historical handwriting nuances:

1. PALEO-TRANSCRIPTION RULES:
- Distinguish 'T' (Tablespoon / tbsp) vs 't' (teaspoon / tsp).
- Standardize all fractions to ASCII format (1/8, 1/4, 1/3, 1/2, 2/3, 3/4).
- Expand historical terms: 'oleo' (oleomargarine/butter), 'saleratus' (baking soda), 'sweet milk' (whole milk).
- Convert archaic butter measurements ('butter size of an egg' -> 1/4 cup butter, 'size of a walnut' -> 2 tbsp).
- Disambiguate cursive letterforms ('r' vs 'v', 'n' vs 'u', 'S' vs 'L').

2. FORMATTING REQUIREMENTS:
- Separate multi-ingredient lines into distinct individual ingredient strings.
- Format directions sequentially with clear '1. ', '2. ', '3. ' step numbering.
- Preserve card author, dates, and historical attribution in 'source_notes'.

Respond strictly with valid JSON conforming to the RecipeSchema.
"""
