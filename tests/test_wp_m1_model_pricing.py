"""Tests for WP-M1: Model ID remediation and pricing table validation.

Asserts that every model referenced by QUALITY_PROFILES, PROFILE_PARAMS, and
default settings is strictly priced in config/pricing.toml so that budget guards
and spend caps operate deterministically across all pipeline stages.
"""

from pathlib import Path
import pytest

from recipeboxer.ai.cost import PriceTable
from recipeboxer.config import (
    DEFAULT_CONSISTENCY_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_FLASH_LITE_MODEL,
    DEFAULT_FLASH_MODEL,
    DEFAULT_PRO_MODEL,
    PROFILE_PARAMS,
    QUALITY_PROFILES,
    Settings,
)
from recipeboxer.models.pipeline import PipelineStartRequest
from recipeboxer.models.session import IngestionConfig


def test_pricing_table_covers_all_quality_profiles():
    """Verify every model in QUALITY_PROFILES for every profile and stage is priced."""
    table = PriceTable.load()

    for profile_name, stage_map in QUALITY_PROFILES.items():
        for stage, model_id in stage_map.items():
            assert table.is_priced(model_id), (
                f"Model '{model_id}' in QUALITY_PROFILES['{profile_name}']['{stage}'] "
                f"is not priced in config/pricing.toml"
            )
            price = table.prices[model_id]
            assert price is not None
            assert price.input_per_mtok >= 0.0
            assert price.output_per_mtok >= 0.0


def test_pricing_table_covers_all_defaults():
    """Verify all default model constants and field defaults are priced."""
    table = PriceTable.load()

    defaults = [
        DEFAULT_FLASH_MODEL,
        DEFAULT_FLASH_LITE_MODEL,
        DEFAULT_PRO_MODEL,
        DEFAULT_CONSISTENCY_MODEL,
        DEFAULT_EMBEDDING_MODEL,
        Settings().default_model,
        Settings().embedding_model,
        PipelineStartRequest(scans_dir="tests/fixtures").model,
        IngestionConfig().model,
    ]

    for model_id in defaults:
        assert model_id is not None
        assert table.is_priced(model_id), (
            f"Default model '{model_id}' is not priced in config/pricing.toml"
        )


def test_pricing_table_covers_profile_params_models():
    """Verify any model referenced in PROFILE_PARAMS is priced."""
    table = PriceTable.load()

    for profile_name, params in PROFILE_PARAMS.items():
        if "other_model" in params:
            model_id = params["other_model"]
            assert table.is_priced(model_id), (
                f"Model '{model_id}' in PROFILE_PARAMS['{profile_name}']['other_model'] "
                f"is not priced in config/pricing.toml"
            )


def test_quality_profile_stage_mappings():
    """Verify explicit stage-to-model assignments per WP-M1 specification."""
    # Economy: all stages gemini-3.5-flash-lite except TRANSCRIBE/STRUCTURE gemini-3.8-flash
    econ = QUALITY_PROFILES["economy"]
    assert econ["TRANSCRIBE"] == "gemini-3.8-flash"
    assert econ["STRUCTURE"] == "gemini-3.8-flash"
    assert econ["FINGERPRINT"] == "gemini-3.5-flash-lite"
    assert econ["SET_VERIFY"] == "gemini-3.5-flash-lite"
    assert econ["GROUNDING"] == "gemini-3.5-flash-lite"
    assert econ["REPAIR"] == "gemini-3.5-flash-lite"
    assert econ["JUDGE"] == "gemini-3.5-flash-lite"
    assert econ["MODALITY"] == "gemini-3.5-flash-lite"

    # Balanced: gemini-3.8-flash for all SUT stages, JUDGE gemini-3.1-pro-preview
    bal = QUALITY_PROFILES["balanced"]
    assert bal["FINGERPRINT"] == "gemini-3.8-flash"
    assert bal["SET_VERIFY"] == "gemini-3.8-flash"
    assert bal["TRANSCRIBE"] == "gemini-3.8-flash"
    assert bal["STRUCTURE"] == "gemini-3.8-flash"
    assert bal["GROUNDING"] == "gemini-3.8-flash"
    assert bal["REPAIR"] == "gemini-3.8-flash"
    assert bal["MODALITY"] == "gemini-3.8-flash"
    assert bal["JUDGE"] == "gemini-3.1-pro-preview"

    # Best: FINGERPRINT gemini-3.8-flash, SET_VERIFY/TRANSCRIBE/STRUCTURE/GROUNDING/REPAIR/JUDGE gemini-3.1-pro-preview
    best = QUALITY_PROFILES["best"]
    assert best["FINGERPRINT"] == "gemini-3.8-flash"
    assert best["SET_VERIFY"] == "gemini-3.1-pro-preview"
    assert best["TRANSCRIBE"] == "gemini-3.1-pro-preview"
    assert best["STRUCTURE"] == "gemini-3.1-pro-preview"
    assert best["GROUNDING"] == "gemini-3.1-pro-preview"
    assert best["REPAIR"] == "gemini-3.1-pro-preview"
    assert best["JUDGE"] == "gemini-3.1-pro-preview"


def test_self_consistency_other_model_balanced():
    """Verify self-consistency other model for balanced is gemini-3.6-flash."""
    assert DEFAULT_CONSISTENCY_MODEL == "gemini-3.6-flash"
    assert PROFILE_PARAMS["balanced"].get("other_model") == "gemini-3.6-flash"


def test_embedding_default_model():
    """Verify embedding default model is gemini-embedding-2."""
    assert DEFAULT_EMBEDDING_MODEL == "gemini-embedding-2"
    assert Settings().embedding_model == "gemini-embedding-2"


def test_no_unpriced_or_nonexistent_models_in_profiles():
    """Verify that unpriced or nonexistent models are not present in QUALITY_PROFILES or defaults."""
    forbidden = ["gemini-3.7-flash", "gemini-3.7-pro", "gemini-2.5-pro", "gemini-embedding-001"]

    for profile_name, stage_map in QUALITY_PROFILES.items():
        for stage, model_id in stage_map.items():
            assert model_id not in forbidden, (
                f"Forbidden model '{model_id}' found in QUALITY_PROFILES['{profile_name}']['{stage}']"
            )

    settings = Settings()
    assert settings.default_model not in forbidden
    assert settings.embedding_model not in forbidden
