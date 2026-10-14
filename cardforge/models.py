"""Data models and enums for procedural synthetic card generation."""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
from PIL import Image
from pydantic import BaseModel, Field


class PaperSubstrate(str, Enum):
    """Categorical substrate paper types."""
    AGED_YELLOW = "AGED_YELLOW"
    WHITE_LINEN = "WHITE_LINEN"
    RULED_INDEX = "RULED_INDEX"
    GRID_GRAPH = "GRID_GRAPH"
    KRAFT_BROWN = "KRAFT_BROWN"
    BLANK = "BLANK"

    def __str__(self) -> str:
        return str(self.value)


class CardModality(str, Enum):
    """Visual and historical rendering modality."""
    HANDWRITTEN_CURSIVE = "HANDWRITTEN_CURSIVE"
    TYPEWRITTEN = "TYPEWRITTEN"
    PRINTED_CLIPPING = "PRINTED_CLIPPING"
    PRINTED_CLIPPING_OR_LABEL = "PRINTED_CLIPPING_OR_LABEL"
    MIXED_MEDIA = "MIXED_MEDIA"

    def __str__(self) -> str:
        return str(self.value)

    @classmethod
    def normalize(cls, val: Any) -> CardModality:
        """Normalizes string or enum value to canonical CardModality."""
        if isinstance(val, CardModality):
            return val
        s = str(getattr(val, "value", val)).strip().upper()
        if s in ("PRINTED_CLIPPING_OR_LABEL", "PRINTED_CLIPPING", "CLIPPING", "NEWSPRINT"):
            return cls.PRINTED_CLIPPING_OR_LABEL
        if s in ("TYPEWRITTEN", "TYPEWRITER", "TYPED"):
            return cls.TYPEWRITTEN
        if s in ("MIXED_MEDIA", "MIXED", "DAMAGED", "STAINED"):
            return cls.MIXED_MEDIA
        return cls.HANDWRITTEN_CURSIVE


class CardRole(str, Enum):
    """Functional role or sequence position marker of a scanned recipe card."""
    SEQUENCE_START = "SEQUENCE_START"
    SEQUENCE_CONTINUATION = "SEQUENCE_CONTINUATION"
    STANDALONE = "STANDALONE"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return str(self.value)

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


class InkType(str, Enum):
    """Types of ink and writing media."""
    BLUE_BALLPOINT = "BLUE_BALLPOINT"
    BLACK_BALLPOINT = "BLACK_BALLPOINT"
    FOUNTAIN_PEN = "FOUNTAIN_PEN"
    PENCIL_GRAPHITE = "PENCIL_GRAPHITE"
    TYPEWRITER_RIBBON = "TYPEWRITER_RIBBON"
    FELT_TIP = "FELT_TIP"
    FELT_TIP_MARKER = "FELT_TIP_MARKER"
    PRINTED_TYPE = "PRINTED_TYPE"
    PRINTED_TYPESET = "PRINTED_TYPESET"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return str(self.value)


class DegradationLevel(str, Enum):
    """Physical damage and age degradation intensity."""
    NONE = "NONE"
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"

    def __str__(self) -> str:
        return str(self.value)


@dataclass
class DegradationConfig:
    """Detailed physical artifact and degradation parameters."""
    level: DegradationLevel = DegradationLevel.NONE
    oil_smudges: int = 0
    coffee_rings: int = 0
    ink_fade: float = 0.0
    edge_tears: bool = False
    dog_ears: bool = False
    rotation_skew_deg: float = 0.0
    stamps: bool = False
    tape_residue: bool = False
    bleed_through: bool = False

    @classmethod
    def from_level(
        cls,
        level: DegradationLevel,
        rng: Optional[random.Random] = None,
    ) -> DegradationConfig:
        """Constructs a realistic DegradationConfig corresponding to an intensity level."""
        r = rng or random.Random()
        if level == DegradationLevel.NONE:
            return cls(level=DegradationLevel.NONE)
        elif level == DegradationLevel.MILD:
            return cls(
                level=DegradationLevel.MILD,
                oil_smudges=1 if r.random() < 0.5 else 0,
                coffee_rings=1 if r.random() < 0.3 else 0,
                ink_fade=round(r.uniform(0.05, 0.15), 2),
                edge_tears=False,
                dog_ears=r.random() < 0.4,
                rotation_skew_deg=round(r.uniform(-1.0, 1.0), 2),
                stamps=r.random() < 0.2,
                tape_residue=False,
                bleed_through=r.random() < 0.4,
            )
        elif level == DegradationLevel.MODERATE:
            return cls(
                level=DegradationLevel.MODERATE,
                oil_smudges=r.randint(1, 2),
                coffee_rings=1 if r.random() < 0.6 else 0,
                ink_fade=round(r.uniform(0.15, 0.30), 2),
                edge_tears=r.random() < 0.5,
                dog_ears=r.random() < 0.6,
                rotation_skew_deg=round(r.uniform(-2.5, 2.5), 2),
                stamps=r.random() < 0.4,
                tape_residue=r.random() < 0.4,
                bleed_through=True,
            )
        else:  # SEVERE
            return cls(
                level=DegradationLevel.SEVERE,
                oil_smudges=r.randint(2, 3),
                coffee_rings=r.randint(1, 2),
                ink_fade=round(r.uniform(0.30, 0.50), 2),
                edge_tears=True,
                dog_ears=True,
                rotation_skew_deg=round(r.uniform(-4.0, 4.0), 2),
                stamps=r.random() < 0.5,
                tape_residue=True,
                bleed_through=True,
            )

    for_level = from_level


class RecipeSchema(BaseModel):
    """Minimal standalone recipe schema carrying ground-truth fields for card generation."""
    recipe_id: str = Field(
        default_factory=lambda: f"rec_{uuid.uuid4().hex[:8]}",
        description="Unique recipe identifier",
    )
    title: str = Field(..., description="Recipe title / name")
    description: Optional[str] = Field(default="", description="Recipe description, history, or intro notes")
    yield_servings: Optional[str] = Field(default="", description="Recipe yield or serving count")
    prep_time: Optional[str] = Field(default="", description="Preparation time")
    cook_time: Optional[str] = Field(default="", description="Cook/bake time")
    total_time: Optional[str] = Field(default="", description="Total time from start to finish")
    ingredients: List[str] = Field(default_factory=list, description="List of ingredient lines")
    directions: List[str] = Field(default_factory=list, description="Ordered list of preparation steps")
    tags: List[str] = Field(default_factory=list, description="Recipe tags")
    source_notes: Optional[str] = Field(default="", description="Source attribution notes")
    card_ids: List[str] = Field(default_factory=list, description="Source card filenames/IDs")
    image_urls: List[str] = Field(default_factory=list, description="List of attached image URLs or paths")
    created_at: Optional[Any] = Field(default=None, description="Creation timestamp")
    updated_at: Optional[Any] = Field(default=None, description="Update timestamp")


@dataclass
class GeneratedCard:
    """Container for a procedurally generated card scan image and ground-truth metadata."""
    image: Image.Image
    card_id: str
    filename: str
    modality: CardModality
    role: CardRole
    recipe_cluster: Dict[str, Any]
    physical_characteristics: Dict[str, Any]
    recipe: RecipeSchema
