"""Parsing and noise exclusion logic for printed clippings and package panels."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple, Union

from recipeboxer.models.recipe import RecipeSchema


# Common advertisement, coupon, and magazine marginalia patterns
PROMOTIONAL_LINE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bclip\s+and\s+save\b", re.IGNORECASE),
    re.compile(r"\bsave\s+(?:\$\s*\d+(?:\.\d{2})?|\d+\s*(?:¢|c\b|cents?\b))", re.IGNORECASE),
    re.compile(r"\bcoupon\s+expires\b|\boffer\s+expires\b", re.IGNORECASE),
    re.compile(r"\bsend\s+(?:\$\s*\d+|\d+\s*(?:¢|c\b|cents?\b))\s+(?:for|and|to)\b", re.IGNORECASE),
    re.compile(r"\bbox\s+tops?\b|\bproof\s+of\s+purchase\b", re.IGNORECASE),
    re.compile(r"\bfree\s+recipe\s+book(?:let)?\b", re.IGNORECASE),
    re.compile(r"\bwrite\s+to\s+box\s+\d+\b", re.IGNORECASE),
    re.compile(r"\bvoid\s+where\s+prohibited\b", re.IGNORECASE),
    re.compile(r"\bgood\s+housekeeping\s+institute\b", re.IGNORECASE),
    re.compile(r"\bpage\s+\d+\s+of\s+\d+\b|\bp\.\s*\d+\b", re.IGNORECASE),
    re.compile(r"\bcopyright\s+\d{4}\b|\b©\s*\d{4}\b", re.IGNORECASE),
    re.compile(r"\bfor\s+additional\s+recipes\s+visit\b", re.IGNORECASE),
    re.compile(r"\bat\s+your\s+grocer['’]s?\s+now\b", re.IGNORECASE),
    re.compile(r"\bredeem\s+at\s+(?:any\s+)?store\b", re.IGNORECASE),
    re.compile(r"\bcut\s+(?:along|on)\s+dotted\s+line\b|\btear\s+here\b|\bclip\s+here\b", re.IGNORECASE),
    re.compile(r"\blimit\s+one\s+(?:coupon\s+)?per\s+(?:customer|family|purchase)\b", re.IGNORECASE),
    re.compile(r"\bcash\s+value\s+1/\d+\b", re.IGNORECASE),
    re.compile(r"\b(?:store|manufacturer['’]?s|valuable)\s+coupon\b", re.IGNORECASE),
    re.compile(r"\bsatisfaction\s+guaranteed\s+or\s+(?:your\s+)?money\s+back\b", re.IGNORECASE),
]


def is_promotional_line(line: str) -> bool:
    """Checks whether a line of text matches promotional copy, coupons, or magazine noise."""
    if not line or not line.strip():
        return False
    clean = line.strip()
    return any(p.search(clean) for p in PROMOTIONAL_LINE_PATTERNS)


def filter_advertisement_noise(text_or_lines: Union[str, List[str]]) -> List[str]:
    """Filters out advertising copy, coupon text, and magazine headers/footers.

    Args:
        text_or_lines: Raw transcribed text string or list of text lines.

    Returns:
        List of cleaned, non-promotional text lines.
    """
    if isinstance(text_or_lines, str):
        lines = text_or_lines.splitlines()
    else:
        lines = text_or_lines

    cleaned: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not is_promotional_line(stripped):
            cleaned.append(stripped)

    return cleaned


def get_print_clipping_prompt() -> str:
    """Returns specialized prompt instructions for printed clippings and package labels."""
    return """You are an expert document transcription specialist focusing on printed newspaper clippings, magazine cutouts, and product box panels.
Transcribe this recipe clipping with strict focus on multi-column layout order and advertising noise removal:

1. LAYOUT & READING ORDER:
- Follow standard multi-column reading flow: transcribe the full ingredients column first, then the directions column.
- Capture primary title and headnotes from spanning banner headers.
- If sub-recipes exist (e.g. 'For the Sauce:'), retain clear subheadings.

2. TITLE INFERENCE & GUESSING:
- If the clipping has a clearly stated title, extract it cleanly without modification.
- If the clipping has NO stated title, or if the header is cut off/missing: infer the most descriptive title from ingredients/context and ALWAYS append the suffix ' [GUESS]' (e.g. 'Quick Drop Biscuits [GUESS]') to flag for verification.

3. ANECDOTE, STORY & METADATA PRESERVATION:
- Extract introductory culinary headnotes, promotional background anecdotes, and occasion notes into 'description'.
- Extract publication names, dates, clipped bylines, and brand attribution into 'source_notes'.
- STRICT ISOLATION: NEVER place narrative notes, brand history, or clipping marginalia into 'ingredients' or 'directions' — keep ingredients and directions strictly confined to culinary measurements and numbered preparation steps.

4. ADVERTISEMENT & COUPON EXCLUSION:
- STRICTLY EXCLUDE: 'Clip and save', coupon offers, expiration dates, box top redemption addresses, magazine page numbers, publication dates, and unrelated ad sidebars.
- Retain culinary product brand names (e.g. 'Crisco', 'Borden\\'s Eagle Brand') in title and source attribution notes.

5. STRUCTURED OUTPUT:
- Separate distinct ingredients into clean strings with quantities and units.
- Number directions sequentially as '1. ', '2. '.
- Output ONLY valid JSON adhering strictly to RecipeSchema.
"""
