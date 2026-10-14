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

    def __init__(self, encoding: str = "utf-8", include_transcription_notes: bool = False) -> None:
        """Initialize PlanToEatExporter.

        Args:
            encoding: Text encoding (default: 'utf-8').
            include_transcription_notes: Whether to include transcription notes and warnings in export.
        """
        self.encoding = encoding
        self.include_transcription_notes = include_transcription_notes
        self.columns = list(PLAN_TO_EAT_COLUMNS)

    def export_to_csv_string(
        self,
        recipes: List[RecipeSchema],
        include_transcription_notes: Optional[bool] = None,
    ) -> str:
        """Serializes a list of RecipeSchema objects into an RFC 4180 CSV string.

        Args:
            recipes: List of RecipeSchema models.
            include_transcription_notes: Optional flag override.

        Returns:
            RFC 4180-compliant CSV formatted string with CRLF line terminators.
        """
        inc_notes = self.include_transcription_notes if include_transcription_notes is None else include_transcription_notes
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
            # 1. Format ingredients: uses normalized_text when present else original_text
            all_ings = recipe.all_ingredients() if hasattr(recipe, "all_ingredients") else []
            if all_ings:
                ingredients_str = "\n".join(ing.display_text for ing in all_ings if ing.display_text)
            else:
                ingredients_str = "\n".join(recipe.ingredients) if recipe.ingredients else ""

            # 2. Format directions: uses normalized_text when present else text / original_text
            all_steps = recipe.all_steps() if hasattr(recipe, "all_steps") else []
            if all_steps:
                directions_str = "\n".join(step.display_text for step in all_steps if step.display_text)
            else:
                directions_str = "\n".join(recipe.directions) if recipe.directions else ""

            # 3. Format tags: comma-separated
            tags_str = ", ".join(recipe.tags) if recipe.tags else ""

            # 4. Description and notes
            desc_parts: List[str] = []
            if recipe.description:
                desc_parts.append(recipe.description)
            if hasattr(recipe, "headnote") and recipe.headnote and recipe.headnote.text:
                desc_parts.append(recipe.headnote.text)

            if inc_notes:
                notes = getattr(recipe, "notes", [])
                for n in notes:
                    if hasattr(n, "text") and n.text:
                        desc_parts.append(f"[Note]: {n.text}")
                warnings = getattr(recipe, "warnings", [])
                for w in warnings:
                    desc_parts.append(f"[Warning {w.code}]: {w.message}")

            desc_str = "\n\n".join(desc_parts)

            # 5. Times and Yield
            prep_str = recipe.prep_time.display_text if (hasattr(recipe, "prep_time") and recipe.prep_time) else (getattr(recipe, "prep_time_str", "") or "")
            total_str = recipe.total_time.display_text if (hasattr(recipe, "total_time") and recipe.total_time) else (getattr(recipe, "total_time_str", "") or "")
            yield_str = recipe.yield_value.display_text if (hasattr(recipe, "yield_value") and recipe.yield_value) else (recipe.yield_servings or "")

            row: Dict[str, str] = {
                "Title": recipe.title or "",
                "Description": desc_str,
                "Ingredients": ingredients_str,
                "Directions": directions_str,
                "Prep Time": prep_str,
                "Total Time": total_str,
                "Yield": yield_str,
                "Tags": tags_str,
                "Source": recipe.source_notes or "",
            }
            writer.writerow(row)

        return output.getvalue()

    def export_to_file(
        self,
        recipes: List[RecipeSchema],
        output_path: Union[str, Path],
        include_transcription_notes: Optional[bool] = None,
    ) -> Path:
        """Serializes recipes and writes them to a file on disk."""
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        csv_content = self.export_to_csv_string(recipes, include_transcription_notes=include_transcription_notes)
        with path.open("w", newline="", encoding=self.encoding) as f:
            f.write(csv_content)
        logger.info(f"Exported {len(recipes)} recipes to Plan To Eat CSV: {path}")
        return path

    def export_to_bytes(
        self,
        recipes: List[RecipeSchema],
        include_transcription_notes: Optional[bool] = None,
    ) -> bytes:
        """Serializes recipes into UTF-8 encoded bytes."""
        return self.export_to_csv_string(recipes, include_transcription_notes=include_transcription_notes).encode(self.encoding)

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


def export_recipes_to_plan_to_eat_csv(
    recipes: List[RecipeSchema],
    include_transcription_notes: bool = False,
) -> str:
    """Convenience functional wrapper for PlanToEatExporter.export_to_csv_string."""
    return PlanToEatExporter(include_transcription_notes=include_transcription_notes).export_to_csv_string(recipes)
