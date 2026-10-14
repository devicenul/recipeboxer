"""Prompt registry for RecipeBoxer (WP-G1, WP-X10).

Loads prompt markdown templates by prompt ID and computes a deterministic short
SHA-256 version hash for provenance and caching.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

PROMPTS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class PromptRecord:
    """Immutable prompt template record containing text and provenance version."""

    id: str
    text: str
    version: str  # Short (8-character) SHA-256 digest of stripped prompt text


_PROMPT_CACHE: Dict[str, PromptRecord] = {}


def _compute_prompt_version(text: str) -> str:
    """Computes a deterministic 8-character SHA-256 version hash."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:8]


def get_prompt(prompt_id: str) -> PromptRecord:
    """Loads a prompt template by ID from the prompts directory.

    Args:
        prompt_id: Identifier of the prompt (e.g. 'fingerprint_v2' or 'fingerprint_v2.md').

    Returns:
        PromptRecord containing id, prompt text, and short sha256 version.

    Raises:
        KeyError: If prompt markdown file does not exist.
    """
    clean_id = prompt_id.removesuffix(".md").strip()
    if clean_id in _PROMPT_CACHE:
        return _PROMPT_CACHE[clean_id]

    prompt_path = PROMPTS_DIR / f"{clean_id}.md"
    if not prompt_path.is_file():
        raise KeyError(f"Prompt '{clean_id}' not found in prompt registry ({prompt_path})")

    text = prompt_path.read_text(encoding="utf-8")
    version = _compute_prompt_version(text)
    record = PromptRecord(id=clean_id, text=text, version=version)
    _PROMPT_CACHE[clean_id] = record
    return record


def get_prompt_text(prompt_id: str) -> str:
    """Convenience helper returning the raw text of a prompt template."""
    return get_prompt(prompt_id).text


def get_prompt_version(prompt_id: str) -> str:
    """Convenience helper returning the short version hash of a prompt template."""
    return get_prompt(prompt_id).version


def list_prompts() -> List[str]:
    """Returns sorted list of available prompt IDs in the registry."""
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))


def clear_prompt_cache() -> None:
    """Clears in-memory cached prompt records (used in tests)."""
    _PROMPT_CACHE.clear()


__all__ = [
    "PromptRecord",
    "get_prompt",
    "get_prompt_text",
    "get_prompt_version",
    "list_prompts",
    "clear_prompt_cache",
]
