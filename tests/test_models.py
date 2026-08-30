"""Unit tests for RecipeBoxer Pydantic domain models (recipeboxer/models/)."""

from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import (
    ArtifactType,
    BoundaryText,
    CardQuadrant,
    CardRole,
    InkColor,
    InkInfo,
    InkType,
    PaperColorInfo,
    PaperColorTone,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.models.hitl import ApprovalPayload, CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import (
    IngestionMode,
    PipelineStartRequest,
    PipelineStartResponse,
    PipelineState,
    PipelineStatus,
    ProgressMetrics,
    TokenUsage,
)
from recipeboxer.models.recipe import RecipeSchema


# ============================================================================
# 1. DOMAIN & FINGERPRINT MODELS
# ============================================================================

def test_card_role_enum():
    """Verify CardRole enum values and validation."""
    assert CardRole.SEQUENCE_START == "SEQUENCE_START"
    assert CardRole.SEQUENCE_CONTINUATION == "SEQUENCE_CONTINUATION"
    assert CardRole.STANDALONE == "STANDALONE"
    assert CardRole.UNKNOWN == "UNKNOWN"

    assert CardRole("SEQUENCE_START") == CardRole.SEQUENCE_START
    with pytest.raises(ValueError):
        CardRole("INVALID_ROLE")


def test_paper_color_info_hex_and_cielab():
    """Verify PaperColorInfo hex color normalization and CIELAB bounds."""
    # Standard valid uppercase hex
    color1 = PaperColorInfo(hex_approx="#FAF6EE", lab_l=92.5, lab_a=1.5, lab_b=12.0)
    assert color1.hex_approx == "#FAF6EE"
    assert color1.lab_l == 92.5

    # Lowercase hex normalization
    color2 = PaperColorInfo(hex_approx="faf6ee")
    assert color2.hex_approx == "#FAF6EE"

    # Missing '#' prefix normalization
    color3 = PaperColorInfo(hex_approx="FFFFFF")
    assert color3.hex_approx == "#FFFFFF"

    # Invalid hex format
    with pytest.raises(ValidationError):
        PaperColorInfo(hex_approx="ZZZZZZ")

    with pytest.raises(ValidationError):
        PaperColorInfo(hex_approx="#12345")  # 5 chars instead of 6

    # CIELAB bounds validation
    with pytest.raises(ValidationError):
        PaperColorInfo(lab_l=-1.0)  # ge=0.0

    with pytest.raises(ValidationError):
        PaperColorInfo(lab_l=101.0)  # le=100.0

    with pytest.raises(ValidationError):
        PaperColorInfo(lab_a=-130.0)  # ge=-128.0

    with pytest.raises(ValidationError):
        PaperColorInfo(lab_b=130.0)  # le=128.0


def test_paper_ruling_info():
    """Verify PaperRulingInfo creation and line spacing validation."""
    ruling = PaperRulingInfo(
        has_ruling=True,
        ruling_type=PaperRulingType.STANDARD_RULED_BLUE,
        line_spacing_px=24.5,
    )
    assert ruling.has_ruling is True
    assert ruling.ruling_type == PaperRulingType.STANDARD_RULED_BLUE
    assert ruling.line_spacing_px == 24.5

    with pytest.raises(ValidationError):
        PaperRulingInfo(line_spacing_px=-5.0)


def test_ink_info():
    """Verify InkInfo creation."""
    ink = InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT)
    assert ink.ink_color == InkColor.BLUE
    assert ink.ink_type == InkType.BLUE_BALLPOINT


def test_physical_artifact():
    """Verify PhysicalArtifact creation and quadrant assignments."""
    artifact = PhysicalArtifact(
        artifact_type=ArtifactType.OIL_GREASE_STAIN,
        quadrant=CardQuadrant.TOP_RIGHT,
        severity="SEVERE",
        description="Grease stain on upper right corner",
    )
    assert artifact.artifact_type == ArtifactType.OIL_GREASE_STAIN
    assert artifact.quadrant == CardQuadrant.TOP_RIGHT
    assert artifact.severity == "SEVERE"


def test_boundary_text():
    """Verify BoundaryText model."""
    boundary = BoundaryText(
        first_line="Grandma's Sugar Cookies",
        last_line="Continue on back side ->",
        has_continuation_symbol=True,
        is_cut_off_mid_sentence=False,
    )
    assert boundary.has_continuation_symbol is True
    assert boundary.is_cut_off_mid_sentence is False


def test_visual_fingerprint_full_roundtrip():
    """Verify complete VisualFingerprint JSON serialization and deserialization."""
    fp = VisualFingerprint(
        card_id="card_001.jpg",
        image_path="/scans/card_001.jpg",
        sha256_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        role=CardRole.SEQUENCE_START,
        role_confidence=0.97,
        paper_color=PaperColorInfo(
            tone_name="Aged Cream",
            hex_approx="#F4E8C1",
            lab_l=90.0,
            lab_a=2.0,
            lab_b=18.0,
            tone_enum=PaperColorTone.OFF_WHITE_CREAM,
        ),
        ruling=PaperRulingInfo(
            has_ruling=True,
            ruling_type=PaperRulingType.HORIZONTAL_LINES,
            line_spacing_px=24.0,
        ),
        ink=InkInfo(ink_color=InkColor.BLUE, ink_type=InkType.BLUE_BALLPOINT),
        artifacts=[
            PhysicalArtifact(
                artifact_type=ArtifactType.OIL_STAIN,
                quadrant=CardQuadrant.TOP_RIGHT,
                description="Small oil spot",
            )
        ],
        boundary_text=BoundaryText(
            first_line="Grandma's Cinnamon Rolls",
            last_line="Mix until smooth (over ->)",
            has_continuation_symbol=True,
            is_cut_off_mid_sentence=True,
        ),
        extracted_at=datetime(2026, 8, 24, 20, 0, 0, tzinfo=timezone.utc),
    )

    json_str = fp.model_dump_json()
    assert "card_001.jpg" in json_str
    assert "SEQUENCE_START" in json_str
    assert "#F4E8C1" in json_str

    deserialized = VisualFingerprint.model_validate_json(json_str)
    assert deserialized.card_id == fp.card_id
    assert deserialized.sha256_hash == fp.sha256_hash
    assert deserialized.role == CardRole.SEQUENCE_START
    assert deserialized.role_confidence == 0.97
    assert deserialized.paper_color.hex_approx == "#F4E8C1"
    assert len(deserialized.artifacts) == 1
    assert deserialized.artifacts[0].artifact_type == ArtifactType.OIL_STAIN
    assert deserialized.boundary_text.has_continuation_symbol is True


# ============================================================================
# 2. CORRELATION & HITL MODELS
# ============================================================================

def test_correlation_proposal_and_result():
    """Verify CorrelationProposal and CorrelationResult structures and bounds."""
    proposal = CorrelationProposal(
        proposal_id="prop_001",
        ordered_card_ids=["card_01.jpg", "card_02.jpg"],
        part_count=2,
        heuristic_score=0.88,
        semantic_score=0.95,
        confidence=0.929,
        is_standalone=False,
        continuity_explanation="Ingredients and directions continue cleanly.",
        recommended_action="SEQUENCE",
    )
    assert proposal.confidence == 0.929
    assert proposal.part_count == 2
    assert proposal.recommended_action == "SEQUENCE"

    # Test confidence bounds
    with pytest.raises(ValidationError):
        CorrelationProposal(
            proposal_id="p",
            ordered_card_ids=["f"],
            confidence=1.5,  # le=1.0
        )

    result = CorrelationResult(
        proposals=[proposal],
        unmatched_card_ids=["card_noise.jpg"],
    )
    assert len(result.proposals) == 1
    assert "card_noise.jpg" in result.unmatched_card_ids


def test_hitl_approval_models():
    """Verify HITL models and ApprovalPayload serialization."""
    approval = CardPairApproval(
        proposal_id="prop_001",
        ordered_card_ids=["card_01.jpg", "card_02.jpg"],
        decision=HITLDecision.APPROVE,
        notes="Looks perfect",
    )
    assert approval.decision == HITLDecision.APPROVE

    payload = ApprovalPayload(
        session_id="sess_123",
        approvals=[approval],
        rejected_card_ids=["card_bad.jpg"],
        continue_pipeline=True,
    )
    assert payload.session_id == "sess_123"
    assert len(payload.approvals) == 1
    assert payload.continue_pipeline is True


# ============================================================================
# 3. RECIPE SCHEMA & NORMALIZATION
# ============================================================================

def test_recipe_schema_empty_title_validation():
    """Verify RecipeSchema rejects empty or whitespace-only titles."""
    with pytest.raises(ValidationError):
        RecipeSchema(title="")

    with pytest.raises(ValidationError):
        RecipeSchema(title="   ")


def test_recipe_schema_directions_normalization():
    """Verify RecipeSchema automatically normalizes messy/unnumbered directions to '1. ', '2. '."""
    raw_directions = [
        "Preheat oven to 350°F.",
        "Step 2: Mix flour, sugar, and salt.",
        "3. Beat eggs and milk.",
        "(4) Pour into baking dish.",
        "5) Bake for 30 minutes.",
        "6 - Cool on wire rack before serving.",
        "   ",  # Should be filtered out
    ]

    recipe = RecipeSchema(
        title="Vanilla Cake",
        ingredients=["2 cups flour", "1 cup sugar", "2 eggs"],
        directions=raw_directions,
    )

    expected = [
        "1. Preheat oven to 350°F.",
        "2. Mix flour, sugar, and salt.",
        "3. Beat eggs and milk.",
        "4. Pour into baking dish.",
        "5. Bake for 30 minutes.",
        "6. Cool on wire rack before serving.",
    ]
    assert recipe.directions == expected


def test_recipe_schema_directions_interleaved_empty_lines():
    """Verify RecipeSchema handles leading, trailing, and interleaved empty/blank directions without numbering gaps."""
    raw_directions = [
        "",
        "   \t\n  ",
        "Step 1: First step",
        "",
        "   ",
        "Step 2: Second step",
        "\r\n\t",
        "Third step without prefix",
        " ",
        "Step 4: Fourth step",
        "",
    ]
    recipe = RecipeSchema(
        title="Test Interleaved",
        directions=raw_directions,
    )
    expected = [
        "1. First step",
        "2. Second step",
        "3. Third step without prefix",
        "4. Fourth step",
    ]
    assert recipe.directions == expected


def test_recipe_schema_ingredients_cleaning():
    """Verify RecipeSchema strips whitespace and discards empty ingredient entries."""
    raw_ingredients = [
        "  1 cup sugar  ",
        "",
        "2 cups flour",
        "   ",
        "1/2 tsp salt",
    ]
    recipe = RecipeSchema(
        title="Simple Bread",
        ingredients=raw_ingredients,
        directions=["1. Mix.", "2. Bake."],
    )
    assert recipe.ingredients == ["1 cup sugar", "2 cups flour", "1/2 tsp salt"]


def test_recipe_schema_tag_deduplication():
    """Verify RecipeSchema deduplicates tags preserving original casing and order."""
    raw_tags = ["Dessert", "Baking", "dessert", "Quick", "BAKING", "Sweet"]
    recipe = RecipeSchema(
        title="Brownies",
        ingredients=["1 cup cocoa"],
        directions=["1. Bake."],
        tags=raw_tags,
    )
    assert recipe.tags == ["Dessert", "Baking", "Quick", "Sweet"]


# ============================================================================
# 4. PIPELINE STATUS & API SCHEMAS
# ============================================================================

def test_pipeline_status_model():
    """Verify PipelineStatus telemetry structure."""
    status = PipelineStatus(
        session_id="sess_001",
        status=PipelineState.HITL_PENDING,
        mode=IngestionMode.PARALLEL,
        progress=ProgressMetrics(
            total_scans=10,
            fingerprinted=10,
            proposals=5,
            approved=0,
            extracted=0,
            percent_complete=50.0,
        ),
        token_usage=TokenUsage(
            prompt_tokens=5000,
            candidates_tokens=1500,
            total_tokens=6500,
        ),
        elapsed_seconds=12.4,
    )
    assert status.status == PipelineState.HITL_PENDING
    assert status.progress.fingerprinted == 10
    assert status.token_usage.total_tokens == 6500


def test_pipeline_start_request_and_response():
    """Verify PipelineStartRequest and PipelineStartResponse schemas."""
    req = PipelineStartRequest(
        scans_dir="/path/to/scans",
        mode=IngestionMode.SLOW_BATCH,
        max_concurrency=4,
        slow_batch_size=2,
        slow_batch_interval_seconds=1.5,
    )
    assert req.mode == IngestionMode.SLOW_BATCH
    assert req.slow_batch_size == 2

    resp = PipelineStartResponse(
        session_id="sess_abc",
        status=PipelineState.SCANNING,
        total_scans_found=8,
        mode=IngestionMode.SLOW_BATCH,
        message="Started",
    )
    assert resp.total_scans_found == 8


def test_pipeline_start_request_default_tags():
    """Verify PipelineStartRequest defaults and explicit default_tags list."""
    req_default = PipelineStartRequest(scans_dir="/path/to/scans")
    assert req_default.default_tags == []

    req_with_tags = PipelineStartRequest(
        scans_dir="/path/to/scans",
        default_tags=["Vintage", "Dessert", "Grandma's Box"],
    )
    assert req_with_tags.default_tags == ["Vintage", "Dessert", "Grandma's Box"]
