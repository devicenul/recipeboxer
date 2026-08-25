"""Structured recipe schema conforming to Plan To Eat specifications."""

from __future__ import annotations

import re
import uuid
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class RecipeSchema(BaseModel):
    """Structured recipe model adhering to Plan To Eat CSV formatting requirements."""
    recipe_id: str = Field(
        default_factory=lambda: f"rec_{uuid.uuid4().hex[:8]}",
        description="Unique recipe identifier",
    )
    title: str = Field(
        ...,
        description="Recipe title / name",
    )
    description: Optional[str] = Field(
        default="",
        description="Recipe description, history, or intro notes",
    )
    yield_servings: Optional[str] = Field(
        default="",
        description="Recipe yield or serving count (e.g. '24 cookies', '4 servings')",
    )
    prep_time: Optional[str] = Field(
        default="",
        description="Preparation time (e.g. '20 mins')",
    )
    cook_time: Optional[str] = Field(
        default="",
        description="Cook/bake time (e.g. '45 mins')",
    )
    total_time: Optional[str] = Field(
        default="",
        description="Total time from start to finish (e.g. '1 hr 5 mins')",
    )
    ingredients: List[str] = Field(
        default_factory=list,
        description="List of individual ingredient items with quantities and units",
    )
    directions: List[str] = Field(
        default_factory=list,
        description="Ordered list of preparation steps, formatted with leading step numbers ('1. ...', '2. ...')",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="List of recipe categorization tags (e.g. ['Dessert', 'Baking'])",
    )
    source_notes: Optional[str] = Field(
        default="",
        description="Attribution, handwritten notes, cookbook page or card source",
    )
    card_ids: List[str] = Field(
        default_factory=list,
        description="List of source scanned card IDs associated with this recipe",
    )
    image_urls: List[str] = Field(
        default_factory=list,
        description="Optional list of image preview URLs for the associated cards",
    )
    created_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 timestamp of recipe creation",
    )
    updated_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 timestamp of last recipe update",
    )

    @field_validator("title")
    @classmethod
    def validate_title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Recipe title cannot be empty.")
        return v

    @field_validator("directions", mode="after")
    @classmethod
    def normalize_numbered_directions(cls, directions: List[str]) -> List[str]:
        """Ensures every direction sequentially starts with '1. ', '2. ', etc."""
        normalized: List[str] = []
        idx = 1
        for step in directions:
            step = step.strip()
            if not step:
                continue
            # Remove any existing leading numbers e.g. "1.", "1 -", "Step 1:", "(1)", "1)"
            cleaned = re.sub(
                r"^(?:(?:Step\s+)?\d+\s*[\.\)\:\-]\s*|\(\d+\)\s*)",
                "",
                step,
                flags=re.IGNORECASE,
            ).strip()
            normalized.append(f"{idx}. {cleaned}")
            idx += 1
        return normalized

    @field_validator("ingredients", mode="after")
    @classmethod
    def clean_ingredients_list(cls, ingredients: List[str]) -> List[str]:
        """Strips whitespace and eliminates empty lines from ingredients."""
        return [item.strip() for item in ingredients if item and item.strip()]

    @field_validator("tags", mode="after")
    @classmethod
    def clean_and_deduplicate_tags(cls, tags: List[str]) -> List[str]:
        """Strips whitespace and deduplicates tags preserving insertion order."""
        seen = set()
        deduped = []
        for tag in tags:
            cleaned = tag.strip()
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                deduped.append(cleaned)
        return deduped
