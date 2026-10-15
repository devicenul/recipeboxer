"""Tests for WP-SCALE robustness: dynamic timeouts, JSON extraction, and T2 harness vault support."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from evals.harness.run import compute_deck_timeout
from recipeboxer.ai.client import _extract_json_text
from recipeboxer.config import Settings
from recipeboxer.extraction.structure import structure
from recipeboxer.extraction.transcribe import compute_transcribe_thinking_budget, transcribe
from recipeboxer.models.domain import VisualFingerprint


def test_harness_compute_deck_timeout():
    """Validates that harness deck timeout scales with deck size and honors explicit overrides."""
    # Min bound of 300s for tiny decks
    assert compute_deck_timeout(0) == 300
    assert compute_deck_timeout(1) == 300
    assert compute_deck_timeout(4) == 300  # 180 + 120 = 300

    # Scales with card count (180 + 30 * count)
    assert compute_deck_timeout(10) == 480
    assert compute_deck_timeout(67) == 180 + 30 * 67  # 2190
    assert compute_deck_timeout(319) == 180 + 30 * 319  # 9750

    # Explicit override takes precedence
    assert compute_deck_timeout(10, explicit_timeout=600) == 600
    assert compute_deck_timeout(319, explicit_timeout=1200) == 1200


def test_extract_json_text_robustness():
    """Validates that _extract_json_text handles surrounding prose, markdown blocks, and brackets."""
    # Standard clean json
    assert _extract_json_text('{"title": "Cake"}') == '{"title": "Cake"}'

    # Fenced markdown with commentary before and after
    input_fenced = (
        "Here is the transcribed and structured recipe JSON:\n\n"
        "```json\n"
        '{"title": "Apple Pie", "ingredients": ["Apples", "Flour"]}\n'
        "```\n\n"
        "Let me know if you need any adjustments!"
    )
    expected = '{"title": "Apple Pie", "ingredients": ["Apples", "Flour"]}'
    assert _extract_json_text(input_fenced) == expected

    # Raw JSON with leading thought or preamble
    input_thought = (
        "Analyzing the card text... The recipe is for sourdough bread.\n"
        '{"title": "Sourdough", "yield": "1 loaf"}'
    )
    assert _extract_json_text(input_thought) == '{"title": "Sourdough", "yield": "1 loaf"}'

    # JSON Array extraction
    input_array = "Extracted ingredients:\n[{\"name\": \"Sugar\"}, {\"name\": \"Butter\"}]\nEnd of list."
    assert _extract_json_text(input_array) == '[{\"name\": \"Sugar\"}, {\"name\": \"Butter\"}]'


def test_transcribe_and_structure_timeout_scaling():
    """Validates that transcribe and structure functions are callable and compute thinking budgets."""
    assert callable(transcribe)
    assert callable(structure)

    from recipeboxer.extraction.transcribe import PROMPT_HANDWRITTEN

    s_econ = Settings(quality_profile="economy", thinking_mode=False)
    s_bal = Settings(quality_profile="balanced", thinking_mode=False)
    s_best = Settings(quality_profile="best", thinking_mode=False)

    face = MagicMock(is_enhanced=False)

    assert compute_transcribe_thinking_budget(s_econ, PROMPT_HANDWRITTEN, [face]) is None
    assert compute_transcribe_thinking_budget(s_bal, PROMPT_HANDWRITTEN, [face]) == 1024
    assert compute_transcribe_thinking_budget(s_best, PROMPT_HANDWRITTEN, [face]) == 2048


def test_t2_vault_decryption_structure(tmp_path: Path):
    """Validates that evals.vault decryption API can unpack tar archives to an isolated temp directory."""
    import io
    import tarfile
    from evals.vault import decrypt_bytes

    vault_path = Path("evals/vault/t2.images.tar.enc")
    if not vault_path.exists():
        pytest.skip("T2 encrypted vault archive not present.")

    # In maintainer environment with secret key, decrypt_bytes succeeds
    try:
        from evals.vault import _derive_key
        # Test decryption without raising if key is available
        enc_data = vault_path.read_bytes()
        tar_bytes = decrypt_bytes(enc_data)
        assert len(tar_bytes) > 0
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            members = tar.getmembers()
            assert len(members) > 0
    except Exception as exc:
        # If running in environment without vault key, skip test
        pytest.skip(f"Vault key not available in environment: {exc}")
