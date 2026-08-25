"""Schema.org Recipe JSON-LD export engine.

Serializes RecipeSchema objects into structured Schema.org/Recipe JSON-LD
format compatible with Paprika, Mealie, Cooklang, Google Recipe Search, and web recipe managers.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger(__name__)


def human_duration_to_iso8601(duration_str: Optional[str]) -> Optional[str]:
    """Converts human-readable time strings into standard ISO 8601 duration format.

    Examples:
        - "20 mins" -> "PT20M"
        - "1 hr 15 mins" -> "PT1H15M"
        - "2 hours" -> "PT2H"
        - "45 seconds" -> "PT45S"
        - "1 day 2 hours" -> "P1DT2H"
        - "PT30M" -> "PT30M"
        - "" or None -> None

    Args:
        duration_str: Freeform human duration string.

    Returns:
        ISO 8601 duration string (e.g. 'PT1H15M') or None if invalid/empty.
    """
    if not duration_str or not duration_str.strip():
        return None

    cleaned = duration_str.strip().upper()

    # Already valid ISO 8601 duration (e.g. PT30M, P1DT2H)
    if re.match(r"^P(?:\d+D)?(?:T(?:\d+H)?(?:\d+M)?(?:\d+S)?)?$", cleaned) and cleaned not in ("P", "PT"):
        return cleaned

    s = duration_str.lower().strip()

    days = 0
    hours = 0
    minutes = 0
    seconds = 0

    # Extract days
    day_match = re.search(r"(\d+)\s*(?:d|day|days)", s)
    if day_match:
        days = int(day_match.group(1))

    # Extract hours
    hr_match = re.search(r"(\d+)\s*(?:h|hr|hrs|hour|hours)", s)
    if hr_match:
        hours = int(hr_match.group(1))

    # Extract minutes
    min_match = re.search(r"(\d+)\s*(?:m|min|mins|minute|minutes)", s)
    if min_match:
        minutes = int(min_match.group(1))

    # Extract seconds
    sec_match = re.search(r"(\d+)\s*(?:s|sec|secs|second|seconds)", s)
    if sec_match:
        seconds = int(sec_match.group(1))

    # Fallback: if single bare number e.g. "30" or "45", treat as minutes
    if days == 0 and hours == 0 and minutes == 0 and seconds == 0:
        bare_match = re.match(r"^(\d+)$", s)
        if bare_match:
            minutes = int(bare_match.group(1))
        else:
            return None

    date_part = f"{days}D" if days > 0 else ""
    time_part = ""
    if hours > 0:
        time_part += f"{hours}H"
    if minutes > 0:
        time_part += f"{minutes}M"
    if seconds > 0:
        time_part += f"{seconds}S"

    if not date_part and not time_part:
        return None

    if time_part:
        return f"P{date_part}T{time_part}"
    return f"P{date_part}"


class JsonLdExporter:
    """Serializes RecipeSchema models to Schema.org JSON-LD structured data."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self.encoding = encoding

    def recipe_to_json_ld_dict(self, recipe: RecipeSchema) -> Dict[str, Any]:
        """Converts a single RecipeSchema into a Schema.org Recipe JSON-LD dictionary.

        Args:
            recipe: RecipeSchema model.

        Returns:
            JSON-LD dictionary conforming to Schema.org Recipe specifications.
        """
        instructions = []
        for idx, step in enumerate(recipe.directions, start=1):
            # Clean leading step numbers
            cleaned_step = re.sub(r"^\d+\.\s*", "", step).strip()
            instructions.append({
                "@type": "HowToStep",
                "position": idx,
                "text": cleaned_step,
            })

        data: Dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Recipe",
            "name": recipe.title,
        }

        if recipe.description:
            data["description"] = recipe.description

        if recipe.yield_servings:
            data["recipeYield"] = recipe.yield_servings

        prep_iso = human_duration_to_iso8601(recipe.prep_time)
        if prep_iso:
            data["prepTime"] = prep_iso

        cook_iso = human_duration_to_iso8601(recipe.cook_time)
        if cook_iso:
            data["cookTime"] = cook_iso

        total_iso = human_duration_to_iso8601(recipe.total_time)
        if total_iso:
            data["totalTime"] = total_iso

        if recipe.ingredients:
            data["recipeIngredient"] = list(recipe.ingredients)

        if instructions:
            data["recipeInstructions"] = instructions

        if recipe.tags:
            data["keywords"] = ", ".join(recipe.tags)
            data["recipeCategory"] = recipe.tags[0]

        if recipe.source_notes:
            data["comment"] = recipe.source_notes

        if recipe.created_at:
            data["datePublished"] = recipe.created_at

        return data

    def export_to_json_ld_dict(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
    ) -> Dict[str, Any]:
        """Serializes single recipe or recipe collection into Schema.org format.

        For multiple recipes, packages them inside a `@graph` array.
        """
        if isinstance(recipes, RecipeSchema):
            return self.recipe_to_json_ld_dict(recipes)

        if len(recipes) == 1:
            return self.recipe_to_json_ld_dict(recipes[0])

        graph = [self.recipe_to_json_ld_dict(r) for r in recipes]
        return {
            "@context": "https://schema.org",
            "@graph": graph,
        }

    def export_to_json_ld_string(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        indent: int = 2,
    ) -> str:
        """Serializes recipes into formatted JSON-LD string."""
        data = self.export_to_json_ld_dict(recipes)
        return json.dumps(data, indent=indent, ensure_ascii=False)

    def export_to_file(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        output_path: Union[str, Path],
        indent: int = 2,
    ) -> Path:
        """Serializes recipes and writes JSON-LD to file."""
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self.export_to_json_ld_string(recipes, indent=indent)
        path.write_text(content, encoding=self.encoding)
        count = 1 if isinstance(recipes, RecipeSchema) else len(recipes)
        logger.info(f"Exported {count} recipe(s) to JSON-LD: {path}")
        return path

    def export_to_bytes(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        indent: int = 2,
    ) -> bytes:
        """Serializes recipes into UTF-8 encoded bytes."""
        return self.export_to_json_ld_string(recipes, indent=indent).encode(self.encoding)

    def validate_json_ld(self, json_content: Union[str, Dict[str, Any]]) -> bool:
        """Validates that content is valid Schema.org Recipe JSON-LD.

        Raises:
            ValueError: If structure fails validation.
        """
        if isinstance(json_content, str):
            if not json_content.strip():
                raise ValueError("JSON-LD content is empty")
            try:
                data = json.loads(json_content)
            except Exception as e:
                raise ValueError(f"Invalid JSON in JSON-LD payload: {e}") from e
        else:
            data = json_content

        if "@context" not in data or "schema.org" not in str(data["@context"]).lower():
            raise ValueError("Missing or invalid @context for Schema.org")

        if "@graph" in data:
            if not isinstance(data["@graph"], list) or len(data["@graph"]) == 0:
                raise ValueError("@graph must be a non-empty list")
            for item in data["@graph"]:
                if item.get("@type") != "Recipe":
                    raise ValueError(f"Graph item must have @type 'Recipe', found: {item.get('@type')}")
                if not item.get("name"):
                    raise ValueError("Recipe in graph missing 'name'")
        else:
            if data.get("@type") != "Recipe":
                raise ValueError(f"Root object must have @type 'Recipe', found: {data.get('@type')}")
            if not data.get("name"):
                raise ValueError("Recipe missing 'name'")

        return True


def export_recipes_to_json_ld(recipes: Union[RecipeSchema, List[RecipeSchema]]) -> str:
    """Convenience function for JSON-LD export."""
    return JsonLdExporter().export_to_json_ld_string(recipes)
