"""Tests for WP-G1 structured fingerprinting and heuristics extensions.

Verifies:
- Prompt registry loading and short SHA-256 versioning.
- Request shape: stage FINGERPRINT, response_model FingerprintV2Response, system instruction
  from registry, and no filenames in any TextPart or ImagePart.
- Structured response mapping to VisualFingerprint with all §1.3 fields.
- Deterministic cache key incorporating prompt version and model in FingerprintCache.
- Typed failure mapping to FingerprintUnavailable subclasses and FailureReason.
- Pixel-only degraded fallback setting degraded=True with zero fabricated text fields.
- Header score progression using printed_markers, text_cut_off, looks_like_back, and content structure.
- Features model interfaces (PixelFeatures, CaptureMeta).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import List

import pytest
from PIL import Image

from recipeboxer.ai.client import (
    ImagePart,
    LLMRequest,
    LLMStage,
    ScriptedLLMClient,
    TextPart,
)
from recipeboxer.config import get_settings
from recipeboxer.models.domain import (
    BoundaryText,
    CardRole,
    InkColor,
    InkType,
    PaperColorTone,
    PaperRulingType,
    VisualFingerprint,
)
from recipeboxer.models.failures import FailureReason, FingerprintUnavailable, StageUnavailableError
from recipeboxer.models.features import CaptureMeta, PixelFeatures
from recipeboxer.prompts import (
    get_prompt,
    get_prompt_text,
    get_prompt_version,
    list_prompts,
)
from recipeboxer.storage.cache import FingerprintCache
from recipeboxer.vision.fingerprint import (
    FingerprintV2Response,
    GeminiVisualExtractor,
    compute_fingerprint_cache_key,
)
from recipeboxer.vision.heuristics import calculate_sequential_header_score


@pytest.fixture
def sample_test_card(tmp_path: Path) -> Path:
    """Creates a minimal synthetic test card image."""
    img_path = tmp_path / "card_alpha_001.jpg"
    img = Image.new("RGB", (300, 200), color=(250, 248, 235))
    img.save(img_path)
    return img_path


# ---------------------------------------------------------------------------
# 1. Prompt Registry Tests
# ---------------------------------------------------------------------------

def test_prompt_registry_load_and_version() -> None:
    """Verifies that prompt registry loads markdown files and exposes version hash."""
    record = get_prompt("fingerprint_v2")
    assert record.id == "fingerprint_v2"
    assert len(record.version) == 8
    assert "verbatim" in record.text.lower() or "exactly as printed" in record.text.lower()
    assert record.text == get_prompt_text("fingerprint_v2")
    assert record.version == get_prompt_version("fingerprint_v2")

    # Asking with .md extension works equivalently
    record_md = get_prompt("fingerprint_v2.md")
    assert record_md.id == "fingerprint_v2"
    assert record_md.version == record.version

    # Listing prompts includes fingerprint_v2
    available = list_prompts()
    assert "fingerprint_v2" in available

    # Missing prompt raises KeyError
    with pytest.raises(KeyError, match="not found in prompt registry"):
        get_prompt("nonexistent_prompt_12345")


# ---------------------------------------------------------------------------
# 2. Request Shape and Data Privacy Tests (INV-AC1, INV-AC2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fingerprint_request_shape_and_privacy(sample_test_card: Path) -> None:
    """Verifies LLMRequest adheres to stage FINGERPRINT, registry instruction, and no filename leakage."""
    settings = get_settings()
    expected_model = settings.get_stage_model(LLMStage.FINGERPRINT)
    prompt_record = get_prompt("fingerprint_v2")

    mock_resp = FingerprintV2Response(
        visible_title="Lemon Bars",
        first_lines=["Lemon Bars", "1 cup butter"],
        last_lines=["Bake at 350F for 20 mins"],
        role=CardRole.STANDALONE,
        role_confidence=0.9,
    )
    client = ScriptedLLMClient([mock_resp])
    extractor = GeminiVisualExtractor(llm_client=client)

    fp = await extractor.extract(sample_test_card)

    assert len(client.requests) == 1
    req = client.requests[0]

    # Verify stage, model, system instruction, and response model
    assert req.stage == LLMStage.FINGERPRINT
    assert req.model == expected_model
    assert req.system_instruction == prompt_record.text
    assert req.response_model is FingerprintV2Response

    # Verify parts: one text instruction, one image with neutral label
    assert len(req.parts) == 2
    text_part = req.parts[0]
    image_part = req.parts[1]
    assert isinstance(text_part, TextPart)
    assert isinstance(image_part, ImagePart)
    assert image_part.label == "recipe_card_image"

    # Anti-cheat check: strictly no filename or path in prompt text parts or labels
    forbidden_tokens = [sample_test_card.name, sample_test_card.stem, str(sample_test_card)]
    full_prompt_text = f"{req.system_instruction} {text_part.text} {image_part.label}".lower()
    for token in forbidden_tokens:
        assert token.lower() not in full_prompt_text, f"Filename token '{token}' leaked into request!"

    assert fp.visible_title == "Lemon Bars"
    assert fp.degraded is False


# ---------------------------------------------------------------------------
# 3. Structured Output Mapping Tests (§1.3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fingerprint_v2_response_mapping(sample_test_card: Path) -> None:
    """Verifies that all §1.3 fields on FingerprintV2Response map accurately to VisualFingerprint."""
    resp = FingerprintV2Response(
        visible_title="Spiced Pecan Pie",
        first_lines=["Spiced Pecan Pie", "1 unbaked 9-inch pie crust"],
        last_lines=["Cool completely before slicing.", "(cont'd on back)"],
        printed_markers=["1", "Card 1"],
        text_cut_off_start=False,
        text_cut_off_end=True,
        sections_detected=["title", "ingredients", "directions"],
        script_descriptor="vintage cursive script in black ink",
        orientation_deg=0,
        looks_like_back=False,
        has_ingredients=True,
        has_directions=True,
        role=CardRole.SEQUENCE_START,
        role_confidence=0.92,
        paper_tone=PaperColorTone.OFF_WHITE_CREAM,
        paper_color_hex="#F8F4E3",
        paper_ruling=PaperRulingType.STANDARD_RULED_BLUE,
        ink_color=InkColor.BLACK,
        ink_type=InkType.BALLPOINT_PEN,
        stains_or_damage=["OIL_STAIN", "COFFEE_TEA_RING"],
    )
    client = ScriptedLLMClient([resp])
    extractor = GeminiVisualExtractor(llm_client=client)

    fp = await extractor.extract(sample_test_card)

    assert fp.card_id == sample_test_card.name
    assert fp.visible_title == "Spiced Pecan Pie"
    assert fp.first_lines == ["Spiced Pecan Pie", "1 unbaked 9-inch pie crust"]
    assert fp.last_lines == ["Cool completely before slicing.", "(cont'd on back)"]
    assert fp.printed_markers == ["1", "Card 1"]
    assert fp.text_cut_off_start is False
    assert fp.text_cut_off_end is True
    assert fp.sections_detected == ["title", "ingredients", "directions"]
    assert fp.script_descriptor == "vintage cursive script in black ink"
    assert fp.orientation_deg == 0
    assert fp.looks_like_back is False
    assert fp.has_ingredients is True
    assert fp.has_directions is True
    assert fp.role == CardRole.SEQUENCE_START
    assert fp.role_confidence == 0.92
    assert fp.degraded is False
    assert fp.degraded_reason is None

    # Substrate mapping
    assert fp.paper_color.tone_name == "OFF_WHITE_CREAM"
    assert fp.paper_color.hex_approx == "#F8F4E3"
    assert fp.ruling.has_ruling is True
    assert fp.ruling.ruling_type == PaperRulingType.STANDARD_RULED_BLUE
    assert fp.ink.ink_color == InkColor.BLACK
    assert fp.ink.ink_type == InkType.BALLPOINT_PEN
    assert len(fp.artifacts) == 2

    # Backward compatibility with boundary_text
    assert fp.boundary_text.first_line == "Spiced Pecan Pie"
    assert fp.boundary_text.last_line == "(cont'd on back)"
    assert fp.boundary_text.has_continuation_symbol is True


# ---------------------------------------------------------------------------
# 4. Cache Key and FingerprintCache Integration
# ---------------------------------------------------------------------------

def test_cache_key_includes_prompt_version_and_model() -> None:
    """Verifies that compute_fingerprint_cache_key includes image hash, model, and prompt version."""
    img_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    key_base = compute_fingerprint_cache_key(img_hash, "gemini-2.5-flash", "v1_abcd")
    key_diff_version = compute_fingerprint_cache_key(img_hash, "gemini-2.5-flash", "v2_efgh")
    key_diff_model = compute_fingerprint_cache_key(img_hash, "gemini-2.5-pro", "v1_abcd")
    key_diff_img = compute_fingerprint_cache_key("0" * 64, "gemini-2.5-flash", "v1_abcd")

    assert len(key_base) == 64
    assert key_base != key_diff_version
    assert key_base != key_diff_model
    assert key_base != key_diff_img


@pytest.mark.asyncio
async def test_fingerprint_cache_hit_and_miss(sample_test_card: Path) -> None:
    """Verifies caching by compound key in FingerprintCache avoids redundant LLMClient calls."""
    cache = FingerprintCache(":memory:")
    mock_resp = FingerprintV2Response(
        visible_title="Cached Butter Cookies",
        first_lines=["Butter Cookies"],
        last_lines=["Bake 12 mins"],
        role=CardRole.STANDALONE,
    )
    client = ScriptedLLMClient([mock_resp])
    extractor = GeminiVisualExtractor(llm_client=client, cache=cache)

    # 1. First call: Cache miss -> invokes LLM client
    fp1 = await extractor.extract(sample_test_card)
    assert fp1.visible_title == "Cached Butter Cookies"
    assert len(client.requests) == 1

    # 2. Second call: Cache hit -> returns cached fingerprint without calling LLM
    fp2 = await extractor.extract(sample_test_card)
    assert fp2.visible_title == "Cached Butter Cookies"
    assert len(client.requests) == 1  # Unchanged request count
    assert fp2.sha256_hash == fp1.sha256_hash


# ---------------------------------------------------------------------------
# 5. Failure Typing and Degraded Fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failure_typing_rates_and_timeouts(sample_test_card: Path) -> None:
    """Verifies that LLMClient exceptions map to typed FingerprintUnavailable."""
    # Rate limit failure
    client_rate = ScriptedLLMClient([RuntimeError("429 Resource has been exhausted / quota exceeded")])
    extractor_rate = GeminiVisualExtractor(llm_client=client_rate)
    with pytest.raises(FingerprintUnavailable) as exc_info:
        await extractor_rate.extract(sample_test_card)
    assert exc_info.value.reason == FailureReason.RATE_LIMITED
    assert exc_info.value.card_ids == [sample_test_card.name]

    # Timeout failure
    client_timeout = ScriptedLLMClient([TimeoutError("Operation timed out after 60s")])
    extractor_timeout = GeminiVisualExtractor(llm_client=client_timeout)
    with pytest.raises(FingerprintUnavailable) as exc_info:
        await extractor_timeout.extract(sample_test_card)
    assert exc_info.value.reason == FailureReason.TIMEOUT

    # Invalid response failure
    client_invalid = ScriptedLLMClient([StageUnavailableError("Malformed JSON payload", reason=FailureReason.INVALID_RESPONSE)])
    extractor_invalid = GeminiVisualExtractor(llm_client=client_invalid)
    with pytest.raises(FingerprintUnavailable) as exc_info:
        await extractor_invalid.extract(sample_test_card)
    assert exc_info.value.reason == FailureReason.INVALID_RESPONSE

    # Schema validation failure (invalid JSON text when response_model is required)
    client_schema = ScriptedLLMClient(["not valid json for schema"])
    extractor_schema = GeminiVisualExtractor(llm_client=client_schema)
    with pytest.raises(FingerprintUnavailable) as exc_info:
        await extractor_schema.extract(sample_test_card)
    assert exc_info.value.reason == FailureReason.SCHEMA_VALIDATION



@pytest.mark.asyncio
async def test_offline_degraded_path_never_invents_text(sample_test_card: Path) -> None:
    """Verifies that offline pixel-only extraction sets degraded=True and never fabricates text."""
    extractor = GeminiVisualExtractor(api_key=None)
    fp = await extractor.extract(sample_test_card)

    assert fp.degraded is True
    assert "offline" in (fp.degraded_reason or "").lower()
    assert fp.role == CardRole.UNKNOWN
    assert fp.role_confidence == 0.0

    # Never invent text fields
    assert fp.visible_title is None
    assert fp.first_lines == []
    assert fp.last_lines == []
    assert fp.printed_markers == []
    assert fp.text_cut_off_end is None
    assert fp.text_cut_off_start is None
    assert fp.boundary_text.first_line == ""
    assert fp.boundary_text.last_line == ""


# ---------------------------------------------------------------------------
# 6. Sequential Header Score Heuristics (§1.3 extensions)
# ---------------------------------------------------------------------------

def test_sequential_header_score_neutral_when_absent() -> None:
    """Verifies that when v2 fields are absent/neutral, header score baseline is 0.50."""
    fp1 = VisualFingerprint(
        card_id="c1.jpg",
        image_path="/c1.jpg",
        sha256_hash="h1",
        role=CardRole.UNKNOWN,
    )
    fp2 = VisualFingerprint(
        card_id="c2.jpg",
        image_path="/c2.jpg",
        sha256_hash="h2",
        role=CardRole.UNKNOWN,
    )
    assert calculate_sequential_header_score(fp1, fp2) == 0.50


def test_sequential_header_score_printed_markers() -> None:
    """Verifies that printed_markers (e.g. ['1'] -> ['2']) govern sequential scoring."""
    fp1 = VisualFingerprint(
        card_id="c1.jpg",
        image_path="/c1.jpg",
        sha256_hash="h1",
        role=CardRole.UNKNOWN,
        printed_markers=["Card 1"],
    )
    fp2 = VisualFingerprint(
        card_id="c2.jpg",
        image_path="/c2.jpg",
        sha256_hash="h2",
        role=CardRole.UNKNOWN,
        printed_markers=["Card 2"],
    )

    # Forward progression Card 1 -> Card 2 scores 1.00
    assert calculate_sequential_header_score(fp1, fp2) == 1.00
    # Reverse progression Card 2 -> Card 1 scores 0.10
    assert calculate_sequential_header_score(fp2, fp1) == 0.10


def test_sequential_header_score_text_cut_offs() -> None:
    """Verifies text_cut_off_end on predecessor and text_cut_off_start on successor boost score."""
    fp1_cut = VisualFingerprint(
        card_id="c1.jpg",
        image_path="/c1.jpg",
        sha256_hash="h1",
        role=CardRole.UNKNOWN,
        text_cut_off_end=True,
    )
    fp2_cut = VisualFingerprint(
        card_id="c2.jpg",
        image_path="/c2.jpg",
        sha256_hash="h2",
        role=CardRole.UNKNOWN,
        text_cut_off_start=True,
    )
    fp_neutral = VisualFingerprint(
        card_id="c0.jpg",
        image_path="/c0.jpg",
        sha256_hash="h0",
        role=CardRole.UNKNOWN,
    )

    assert calculate_sequential_header_score(fp1_cut, fp_neutral) == 0.75
    assert calculate_sequential_header_score(fp_neutral, fp2_cut) == 0.75
    assert calculate_sequential_header_score(fp1_cut, fp2_cut) == 1.00


def test_sequential_header_score_looks_like_back_and_content_progression() -> None:
    """Verifies looks_like_back and ingredients->directions content progression heuristics."""
    fp_front = VisualFingerprint(
        card_id="c1.jpg",
        image_path="/c1.jpg",
        sha256_hash="h1",
        role=CardRole.UNKNOWN,
        looks_like_back=False,
        has_ingredients=True,
        has_directions=False,
    )
    fp_back = VisualFingerprint(
        card_id="c2.jpg",
        image_path="/c2.jpg",
        sha256_hash="h2",
        role=CardRole.UNKNOWN,
        looks_like_back=True,
        has_ingredients=False,
        has_directions=True,
    )

    # Directed Front -> Back pair: looks_like_back bonus (+0.15) + content progression bonus (+0.15)
    fwd_score = calculate_sequential_header_score(fp_front, fp_back)
    assert fwd_score == 0.80

    # Reversed Back -> Front pair: penalties applied
    rev_score = calculate_sequential_header_score(fp_back, fp_front)
    assert rev_score == 0.00


# ---------------------------------------------------------------------------
# 7. Features Model Interface Contract Tests (§1.4, §1.5)
# ---------------------------------------------------------------------------

def test_features_models_contracts() -> None:
    """Verifies PixelFeatures and CaptureMeta conform to contracts §1.4/§1.5."""
    pix = PixelFeatures(
        paper_lab=(92.5, 1.2, 8.4),
        paper_lab_std=(0.5, 0.2, 0.4),
        ruling_spacing_px=24.0,
        ruling_angle_deg=0.0,
        ruling_kind="lined",
        ink_hues=[210.0],
        aspect_ratio=1.5,
        long_edge_px=1600,
        deskew_angle_deg=0.5,
        crop_box=(10, 10, 590, 390),
        stain_mask_ref="mask_session_001",
    )
    assert pix.paper_lab == (92.5, 1.2, 8.4)
    assert pix.ruling_kind == "lined"

    cap = CaptureMeta(
        taken_at=datetime(2026, 10, 14, 12, 0, 0),
        subsec=500,
        device="Scanner_A",
        source="exif",
        natural_sort_index=1,
    )
    assert cap.source == "exif"
    assert cap.natural_sort_index == 1

    # Attach to VisualFingerprint
    fp = VisualFingerprint(
        card_id="c1.jpg",
        image_path="/c1.jpg",
        sha256_hash="h1",
        role=CardRole.UNKNOWN,
        pixel=pix,
        capture=cap,
    )
    assert fp.pixel is not None
    assert fp.pixel.long_edge_px == 1600
    assert fp.capture is not None
    assert fp.capture.device == "Scanner_A"
