"""Standard Raw Recipe JSON exporter for RecipeBoxer."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Union

from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger(__name__)


class JsonExporter:
    """Serializes collections of RecipeSchema models into standard JSON."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self.encoding = encoding

    def export_to_json_string(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        indent: int = 2,
    ) -> str:
        """Serializes recipe(s) to formatted JSON string.

        Args:
            recipes: Single RecipeSchema or list of RecipeSchema models.
            indent: JSON indentation spaces.

        Returns:
            Formatted JSON string.
        """
        if isinstance(recipes, RecipeSchema):
            data: Any = recipes.model_dump()
        else:
            data = [r.model_dump() for r in recipes]

        return json.dumps(data, indent=indent, ensure_ascii=False)

    def export_to_file(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        output_path: Union[str, Path],
        indent: int = 2,
    ) -> Path:
        """Writes serialized recipes to JSON file on disk."""
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self.export_to_json_string(recipes, indent=indent)
        path.write_text(content, encoding=self.encoding)
        count = 1 if isinstance(recipes, RecipeSchema) else len(recipes)
        logger.info(f"Exported {count} recipe(s) to JSON: {path}")
        return path

    def export_to_bytes(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        indent: int = 2,
    ) -> bytes:
        """Serializes recipes into UTF-8 encoded bytes."""
        return self.export_to_json_string(recipes, indent=indent).encode(self.encoding)

    def validate_json_export(self, json_content: str) -> bool:
        """Validates that a JSON string parses into valid RecipeSchema instances."""
        if not json_content or not json_content.strip():
            raise ValueError("JSON content is empty")

        data = json.loads(json_content)
        if isinstance(data, list):
            for item in data:
                RecipeSchema.model_validate(item)
        elif isinstance(data, dict):
            RecipeSchema.model_validate(data)
        else:
            raise ValueError("Root JSON element must be an object or list of objects")

        return True


def export_recipes_to_json(recipes: Union[RecipeSchema, List[RecipeSchema]]) -> str:
    """Convenience functional wrapper for JsonExporter."""
    return JsonExporter().export_to_json_string(recipes)
