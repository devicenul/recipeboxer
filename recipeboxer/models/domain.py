"""Domain substrate and visual fingerprint models for RecipeBoxer."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional, Union
from pydantic import BaseModel, Field, field_validator

from recipeboxer.models.features import CaptureMeta, PixelFeatures


class CardRole(str, Enum):
    """Functional role or sequence position marker of a scanned recipe card."""
    SEQUENCE_START = "SEQUENCE_START"
    SEQUENCE_CONTINUATION = "SEQUENCE_CONTINUATION"
    STANDALONE = "STANDALONE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def _missing_(cls, value: object) -> Optional[CardRole]:
        if isinstance(value, str):
            val = value.upper().strip().replace("-", "_").replace(" ", "_")
            if val in (
                "FRONT_ONLY", "FRONT", "START", "SEQUENCE_START",
                "CARD1_FRONT", "PART1_FRONT", "PT1_FRONT", "01_FRONT", "PART1", "PT1", "CARD1",
            ):
                return cls.SEQUENCE_START
            if val in (
                "BACK_ONLY", "BACK", "CONTINUATION", "SEQUENCE_CONTINUATION",
                "CARD1_BACK", "CARD2_FRONT", "CARD2_BACK", "CARD3_FRONT", "CARD3_BACK",
                "CARD4_FRONT", "CARD4_BACK",
                "PART2", "PART3", "PART4", "PART5",
                "PT2", "PT3", "PT4", "PT5",
                "PART2_FRONT", "PART2_BACK", "PART3_FRONT", "PART3_BACK",
                "PT2_FRONT", "PT2_BACK", "PT3_FRONT", "PT3_BACK",
                "02_FRONT", "02_BACK", "03_FRONT", "03_BACK", "04_FRONT", "04_BACK",
                "02", "03", "04",
            ) or any(val.startswith(p) for p in ("CARD2", "CARD3", "CARD4", "PART2", "PART3", "PART4", "PT2", "PT3", "PT4")):
                return cls.SEQUENCE_CONTINUATION
            if val in ("STANDALONE", "SINGLE", "SINGLE_SIDED"):
                return cls.STANDALONE
            if val in ("UNKNOWN", "NOISE", "TAB", "DIVIDER", "BLANK"):
                return cls.UNKNOWN
        return None


# Backward compatibility aliases
CardRole.FRONT_ONLY = CardRole.SEQUENCE_START  # type: ignore[attr-defined]
CardRole.BACK_ONLY = CardRole.SEQUENCE_CONTINUATION  # type: ignore[attr-defined]


class PaperColorTone(str, Enum):
    """Categorical substrate color tones."""
    WHITE = "WHITE"
    OFF_WHITE_CREAM = "OFF_WHITE_CREAM"
    YELLOWED_AGED = "YELLOWED_AGED"
    KRAFT_BROWN = "KRAFT_BROWN"
    PASTEL_YELLOW = "PASTEL_YELLOW"
    PASTEL_PINK = "PASTEL_PINK"
    PASTEL_BLUE = "PASTEL_BLUE"
    PASTEL_GREEN = "PASTEL_GREEN"
    OTHER_DARK = "OTHER_DARK"


class PaperRulingType(str, Enum):
    """Ruling line and grid categories."""
    UNRULED_BLANK = "UNRULED_BLANK"
    NONE = "NONE"
    STANDARD_RULED_BLUE = "STANDARD_RULED_BLUE"
    HORIZONTAL_LINES = "HORIZONTAL_LINES"
    NARROW_RULED = "NARROW_RULED"
    WIDE_RULED = "WIDE_RULED"
    INDEX_CARD_HEADER_RULED = "INDEX_CARD_HEADER_RULED"
    GRID_GRAPH = "GRID_GRAPH"
    GRID = "GRID"
    DOTTED = "DOTTED"
    OTHER = "OTHER"


class InkType(str, Enum):
    """Writing mediums and printing types."""
    BALLPOINT_PEN = "BALLPOINT_PEN"
    BLUE_BALLPOINT = "BLUE_BALLPOINT"
    BLACK_BALLPOINT = "BLACK_BALLPOINT"
    GEL_PEN = "GEL_PEN"
    FOUNTAIN_PEN = "FOUNTAIN_PEN"
    PENCIL = "PENCIL"
    PENCIL_GRAPHITE = "PENCIL_GRAPHITE"
    TYPEWRITER_RIBBON = "TYPEWRITER_RIBBON"
    FELT_TIP = "FELT_TIP"
    FELT_TIP_MARKER = "FELT_TIP_MARKER"
    PRINTED_TYPE = "PRINTED_TYPE"
    PRINTED_TYPESET = "PRINTED_TYPESET"
    UNKNOWN = "UNKNOWN"


class InkColor(str, Enum):
    """Dominant ink colors."""
    BLUE = "BLUE"
    BLACK = "BLACK"
    BLUE_BLACK = "BLUE_BLACK"
    RED = "RED"
    GREEN = "GREEN"
    PURPLE = "PURPLE"
    GRAPHITE_GRAY = "GRAPHITE_GRAY"
    UNKNOWN = "UNKNOWN"


class ArtifactType(str, Enum):
    """Types of physical stains and card flaws."""
    OIL_STAIN = "OIL_STAIN"
    OIL_GREASE_STAIN = "OIL_GREASE_STAIN"
    LIQUID_STAIN = "LIQUID_STAIN"
    COFFEE_TEA_RING = "COFFEE_TEA_RING"
    FOOD_SPLATTER_SAUCE = "FOOD_SPLATTER_SAUCE"
    CORNER_DOG_EAR_FOLD = "CORNER_DOG_EAR_FOLD"
    HORIZONTAL_CREASE = "HORIZONTAL_CREASE"
    VERTICAL_CREASE = "VERTICAL_CREASE"
    TEAR = "TEAR"
    TEAR_EDGE = "TEAR_EDGE"
    TAPE_RESIDUE_YELLOWED = "TAPE_RESIDUE_YELLOWED"
    HOLE_PUNCH = "HOLE_PUNCH"
    PUNCH_HOLE_TOP = "PUNCH_HOLE_TOP"
    PUNCH_HOLE_SIDE = "PUNCH_HOLE_SIDE"
    PAPERCLIP_INDENT = "PAPERCLIP_INDENT"
    NOTCH = "NOTCH"
    STAPLE = "STAPLE"
    SMUDGE = "SMUDGE"


class CardQuadrant(str, Enum):
    """Quadrant regions of a physical card."""
    TOP_LEFT = "TOP_LEFT"
    TOP_RIGHT = "TOP_RIGHT"
    BOTTOM_LEFT = "BOTTOM_LEFT"
    BOTTOM_RIGHT = "BOTTOM_RIGHT"
    CENTER = "CENTER"
    EDGE_TOP = "EDGE_TOP"
    EDGE_BOTTOM = "EDGE_BOTTOM"
    EDGE_LEFT = "EDGE_LEFT"
    EDGE_RIGHT = "EDGE_RIGHT"


class PaperColorInfo(BaseModel):
    """Paper colorimetry and tone information."""
    tone_name: str = Field(
        default="WHITE",
        description="Categorical substrate color tone (e.g. WHITE, Aged Cream)",
    )
    hex_approx: str = Field(
        default="#FFFFFF",
        description="Hexadecimal color approximation (#RRGGBB)",
    )
    lab_l: float = Field(
        default=100.0,
        ge=0.0,
        le=100.0,
        description="CIELAB Lightness (0.0 to 100.0)",
    )
    lab_a: float = Field(
        default=0.0,
        ge=-128.0,
        le=128.0,
        description="CIELAB a* axis (-128.0 to 128.0)",
    )
    lab_b: float = Field(
        default=0.0,
        ge=-128.0,
        le=128.0,
        description="CIELAB b* axis (-128.0 to 128.0)",
    )
    tone_enum: Optional[PaperColorTone] = Field(
        default=None,
        description="Optional standardized PaperColorTone enum",
    )

    @field_validator("hex_approx")
    @classmethod
    def validate_hex_color(cls, v: str) -> str:
        v = v.strip().upper()
        if not v.startswith("#"):
            v = f"#{v}"
        if not re.match(r"^#[0-9A-F]{6}$", v):
            raise ValueError(f"Invalid hex color format: '{v}'. Must be 6 hex digits (#RRGGBB).")
        return v


class PaperRulingInfo(BaseModel):
    """Substrate ruling lines and grid characteristics."""
    has_ruling: bool = Field(
        default=False,
        description="Whether the card substrate has ruling lines or grid",
    )
    ruling_type: Union[PaperRulingType, str] = Field(
        default="NONE",
        description="Ruling category (e.g. NONE, HORIZONTAL_LINES, GRID)",
    )
    line_spacing_px: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Pixel spacing between ruling lines if detected",
    )


class InkInfo(BaseModel):
    """Writing medium and ink characteristics."""
    ink_color: Union[InkColor, str] = Field(
        default="BLACK",
        description="Dominant ink color (BLUE, BLACK, RED, etc.)",
    )
    ink_type: Union[InkType, str] = Field(
        default="BALLPOINT_PEN",
        description="Writing instrument or printing medium",
    )


class PhysicalArtifact(BaseModel):
    """Identifies physical flaws, stains, or markings on a card."""
    artifact_type: Union[ArtifactType, str] = Field(
        ...,
        description="Type of physical artifact (OIL_STAIN, TEAR, HOLE_PUNCH, etc.)",
    )
    quadrant: Union[CardQuadrant, str] = Field(
        default="CENTER",
        description="Quadrant location on card",
    )
    severity: Optional[str] = Field(
        default="MODERATE",
        description="Visual severity (MILD, MODERATE, SEVERE)",
    )
    description: Optional[str] = Field(
        default="",
        description="Brief physical description of the artifact",
    )


class BoundaryText(BaseModel):
    """First/last text lines and continuation markers for cross-card pairing."""
    first_line: str = Field(
        default="",
        description="First transcribed line of readable text on card",
    )
    last_line: str = Field(
        default="",
        description="Last transcribed line of readable text on card",
    )
    has_continuation_symbol: bool = Field(
        default=False,
        description="Whether card has continuation markers like '(over)', 'PTO', '->'",
    )
    is_cut_off_mid_sentence: bool = Field(
        default=False,
        description="Whether text ends abruptly without terminating punctuation",
    )


class CardPhysicalHints(BaseModel):
    """Optional container for physical and visual hints."""
    paper_color: PaperColorInfo = Field(default_factory=PaperColorInfo)
    ruling: PaperRulingInfo = Field(default_factory=PaperRulingInfo)
    ink: InkInfo = Field(default_factory=InkInfo)
    artifacts: List[PhysicalArtifact] = Field(default_factory=list)
    boundary_text: BoundaryText = Field(default_factory=BoundaryText)


class VisualFingerprint(BaseModel):
    """Composite visual feature vector representing a digitized recipe card."""
    card_id: str = Field(
        ...,
        description="Unique card scan identifier (e.g. filename 'card_001.jpg')",
    )
    image_path: str = Field(
        ...,
        description="Filesystem path to scanned card image",
    )
    sha256_hash: str = Field(
        ...,
        description="SHA-256 hex digest of the raw image bytes",
    )
    role: CardRole = Field(
        ...,
        description="Classified functional card role",
    )
    role_confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score of role classification",
    )

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, v: Any) -> Any:
        if v is None:
            return CardRole.UNKNOWN
        if isinstance(v, CardRole):
            return v
        v_str = str(getattr(v, "value", v)).strip()
        role = CardRole._missing_(v_str)
        if role is not None:
            return role
        try:
            return CardRole(v_str.upper())
        except ValueError:
            raise ValueError(f"Invalid CardRole: {v}")
    paper_color: PaperColorInfo = Field(default_factory=PaperColorInfo)
    ruling: PaperRulingInfo = Field(default_factory=PaperRulingInfo)
    ink: InkInfo = Field(default_factory=InkInfo)
    artifacts: List[PhysicalArtifact] = Field(
        default_factory=list,
        description="Detected physical flaws and stains",
    )
    boundary_text: BoundaryText = Field(default_factory=BoundaryText)
    extracted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when visual fingerprint was generated",
    )
    degraded: bool = Field(
        default=False,
        description="Whether visual fingerprint was generated via degraded offline pixel analysis or fallback",
    )
    degraded_reason: Optional[str] = Field(
        default=None,
        description="Reason why visual fingerprint is degraded",
    )
    # Wave 4 v2 fingerprint extensions (§1.3)
    visible_title: Optional[str] = Field(
        default=None,
        description="Visible recipe title exactly as printed on the card",
    )
    first_lines: List[str] = Field(
        default_factory=list,
        description="First 1-2 transcribed lines exactly as printed",
    )
    last_lines: List[str] = Field(
        default_factory=list,
        description="Last 1-2 transcribed lines exactly as printed",
    )
    printed_markers: List[str] = Field(
        default_factory=list,
        description="Explicit page/part markers exactly as printed (e.g. '2', 'cont\\'d', 'over')",
    )
    text_cut_off_end: Optional[bool] = Field(
        default=None,
        description="Whether card text ends abruptly without terminating sentence/step",
    )
    text_cut_off_start: Optional[bool] = Field(
        default=None,
        description="Whether card text starts abruptly mid-sentence/direction",
    )
    sections_detected: List[str] = Field(
        default_factory=list,
        description="Recipe sections detected on card face (e.g. 'ingredients', 'directions', 'notes')",
    )
    script_descriptor: Optional[str] = Field(
        default=None,
        description="Free-text handwriting or typography description",
    )
    orientation_deg: Optional[int] = Field(
        default=None,
        description="Card orientation angle in degrees (0, 90, 180, 270)",
    )
    looks_like_back: Optional[bool] = Field(
        default=None,
        description="Whether visual properties suggest the back side of a card",
    )
    has_ingredients: Optional[bool] = Field(
        default=None,
        description="Whether card contains an ingredient list or ingredient lines",
    )
    has_directions: Optional[bool] = Field(
        default=None,
        description="Whether card contains preparation steps or directions",
    )
    pixel: Optional[PixelFeatures] = Field(
        default=None,
        description="Low-level pixel features (WP-G2)",
    )
    capture: Optional[CaptureMeta] = Field(
        default=None,
        description="Capture metadata (WP-G3)",
    )
    embedding_ref: Optional[str] = Field(
        default=None,
        description="Reference key to substrate/multimodal embedding (WP-G4)",
    )
