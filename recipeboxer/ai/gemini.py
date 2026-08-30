"""Multimodal Gemini Model Interfaces and Anti-Cheating Prompt Sanitization for RecipeBoxer."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Optional

def __getattr__(name: str) -> Any:
    if name == "GeminiRecipeExtractor":
        from recipeboxer.extraction.extractor import GeminiRecipeExtractor
        return GeminiRecipeExtractor
    if name == "GeminiSemanticVerifier":
        from recipeboxer.correlation.semantic import GeminiSemanticVerifier
        return GeminiSemanticVerifier
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")



# Regex matching opaque card identifier patterns (e.g. card_a7b8c9d0.jpg, card_001.jpg, card1.jpg, deck_card_1.jpg)
OPAQUE_CARD_ID_PATTERN = re.compile(r"^card_[a-f0-9]{4,16}(?:\.[a-z]+)?$", re.IGNORECASE)
OPAQUE_SEQUENTIAL_PATTERN = re.compile(
    r"^(?:card|deck_card|page|image|img|part)_?\d+(?:\.[a-z]+)?$", re.IGNORECASE
)




def sanitize_prompt_card_id(card_id: Optional[str], default_role: str = "card") -> str:
    """Sanitizes a card identifier before embedding into multimodal LLM prompt templates.

    Ensures models cannot cheat using ground-truth recipe titles, historical dates, or modality tags
    embedded in filenames.

    Args:
        card_id: Raw card ID or filename (e.g. 'rec_vhc_peach_cobbler_1931_front.jpg' or 'card_a7b8c9d0.jpg').
        default_role: Fallback identifier if card_id is empty or None (e.g. 'card_front', 'card_back').

    Returns:
        Opaque sanitized identifier (e.g. 'card_a7b8c9d0.jpg' or 'card_front').
    """
    if not card_id:
        return default_role

    clean = Path(str(card_id).strip()).name

    # If it is already an opaque identifier, return it safely
    if OPAQUE_CARD_ID_PATTERN.match(clean) or OPAQUE_SEQUENTIAL_PATTERN.match(clean):
        return clean

    # Generic card role names are safe
    if clean.lower() in ("card_front", "card_back", "card_1", "card_2", "card_3", "card_4", "front", "back"):
        return clean.lower()

    # Hash raw filename to deterministic opaque ID to eliminate title/date leaks
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]
    ext = Path(clean).suffix if Path(clean).suffix else ".jpg"
    return f"card_{digest}{ext}"


def sanitize_prompt_text(text: str) -> str:
    """Sanitizes arbitrary prompt text by replacing un-obfuscated card filenames with opaque tokens."""
    if not text:
        return ""

    # Replace rec_<modality>_<title>_<year>_<role>.jpg patterns with opaque card hashes
    def _replace_rec_filename(match: re.Match[str]) -> str:
        raw_name = match.group(0)
        digest = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:8]
        return f"card_{digest}.jpg"

    sanitized = re.sub(
        r"rec_[a-z0-9_]+(?:\.jpe?g|\.png)?",
        _replace_rec_filename,
        text,
        flags=re.IGNORECASE,
    )
    return sanitized


__all__ = [
    "GeminiRecipeExtractor",
    "GeminiSemanticVerifier",
    "sanitize_prompt_card_id",
    "sanitize_prompt_text",
]
