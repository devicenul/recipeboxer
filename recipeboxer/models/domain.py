"""Domain substrate and visual fingerprint models for RecipeBoxer."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Union
from pydantic import BaseModel, Field, field_validator


class CardRole(str, Enum):
    """Functional role of a scanned recipe card."""
    FRONT_ONLY = "FRONT_ONLY"
    BACK_ONLY = "BACK_ONLY"
    STANDALONE = "STANDALONE"
    UNKNOWN = "UNKNOWN"


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
