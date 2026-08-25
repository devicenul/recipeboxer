"""Plan To Eat RFC 4180-compliant CSV serializer and export engine.

Serializes RecipeSchema objects into exact 9-column Plan To Eat CSV files
with proper multi-line quoting, quote escaping, and UTF-8 encoding.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger(__name__)

PLAN_TO_EAT_COLUMNS: List[str] = [
    "Title",
    "Description",
    "Ingredients",
    "Directions",
    "Prep Time",
    "Total Time",
    "Yield",
    "Tags",
    "Source",
]


class PlanToEatExporter:
    """Serializes RecipeSchema collections to Plan To Eat-compatible RFC 4180 CSV format."""

    def __init__(self, encoding: str = "utf-8") -> None:
        """Initialize PlanToEatExporter.

        Args:
            encoding: Text encoding (default: 'utf-8').
        """
        self.encoding = encoding
        self.columns = list(PLAN_TO_EAT_COLUMNS)

    def export_to_csv_string(self, recipes: List[RecipeSchema]) -> str:
        """Serializes a list of RecipeSchema objects into an RFC 4180 CSV string.

        Args:
            recipes: List of RecipeSchema models.

        Returns:
            RFC 4180-compliant CSV formatted string with CRLF line terminators.
        """
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=self.columns,
            delimiter=",",
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\r\n",
        )

        writer.writeheader()

        for recipe in recipes:
            # 1. Format ingredients: each item on separate line
            ingredients_str = "\n".join(recipe.ingredients) if recipe.ingredients else ""

            # 2. Format directions: each numbered step on separate line
            directions_str = "\n".join(recipe.directions) if recipe.directions else ""

            # 3. Format tags: comma-separated
            tags_str = ", ".join(recipe.tags) if recipe.tags else ""

            row: Dict[str, str] = {
                "Title": recipe.title or "",
                "Description": recipe.description or "",
                "Ingredients": ingredients_str,
                "Directions": directions_str,
                "Prep Time": recipe.prep_time or "",
                "Total Time": recipe.total_time or "",
                "Yield": recipe.yield_servings or "",
                "Tags": tags_str,
                "Source": recipe.source_notes or "",
            }
            writer.writerow(row)

        return output.getvalue()

    def export_to_file(
        self,
        recipes: List[RecipeSchema],
        output_path: Union[str, Path],
    ) -> Path:
        """Serializes recipes and writes them to a file on disk.

        Args:
            recipes: List of RecipeSchema models.
            output_path: Destination file path.

        Returns:
            Resolved Path of the written file.
        """
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        csv_content = self.export_to_csv_string(recipes)
        path.write_text(csv_content, encoding=self.encoding)
        logger.info(f"Exported {len(recipes)} recipes to Plan To Eat CSV: {path}")
        return path

    def export_to_bytes(self, recipes: List[RecipeSchema]) -> bytes:
        """Serializes recipes into UTF-8 encoded bytes.

        Args:
            recipes: List of RecipeSchema models.

        Returns:
            UTF-8 encoded bytes of the CSV string.
        """
        return self.export_to_csv_string(recipes).encode(self.encoding)

    def validate_csv_format(self, csv_content: str) -> bool:
        """Validates that a CSV string conforms to Plan To Eat columns and RFC 4180 parsing.

        Args:
            csv_content: CSV string content to validate.

        Returns:
            True if valid, raises ValueError otherwise.
        """
        if not csv_content or not csv_content.strip():
            raise ValueError("CSV content is empty")

        reader = csv.reader(io.StringIO(csv_content))
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("CSV has no rows")

        if header != self.columns:
            raise ValueError(f"Header mismatch. Expected {self.columns}, got {header}")

        for line_idx, row in enumerate(reader, start=2):
            if len(row) != len(self.columns):
                raise ValueError(
                    f"Row {line_idx} column count mismatch: expected {len(self.columns)} fields, found {len(row)}"
                )

        return True

    validate_csv = validate_csv_format


def export_recipes_to_plan_to_eat_csv(recipes: List[RecipeSchema]) -> str:
    """Convenience functional wrapper for PlanToEatExporter.export_to_csv_string."""
    return PlanToEatExporter().export_to_csv_string(recipes)
