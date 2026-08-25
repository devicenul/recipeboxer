"""Cooklang (.cook) Serializer and Exporter for RecipeBoxer.

Serializes RecipeSchema models into strict Cooklang markup specification with
metadata (>>), ingredient annotations (@ingredient{quantity%unit}), cookware (#cookware{}),
and timer annotations (~timer{duration%unit}).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger(__name__)

# Common cookware terms to auto-detect and annotate
COOKWARE_TERMS = [
    "baking dish",
    "baking pan",
    "baking sheet",
    "cookie sheet",
    "casserole dish",
    "roasting pan",
    "dutch oven",
    "cast iron skillet",
    "saucepan",
    "frying pan",
    "stock pot",
    "skillet",
    "mixing bowl",
    "large bowl",
    "small bowl",
    "medium bowl",
    "bowl",
    "pot",
    "pan",
    "oven",
    "blender",
    "food processor",
    "stand mixer",
    "hand mixer",
    "whisk",
    "rolling pin",
    "wire rack",
    "colander",
    "sifter",
    "spatula",
    "mason jar",
]

# Standard culinary units
CULINARY_UNITS = [
    "tablespoons", "tablespoon", "tbsp", "tbs", "t",
    "teaspoons", "teaspoon", "tsp",
    "cups", "cup", "c",
    "ounces", "ounce", "oz",
    "fluid ounces", "fl oz",
    "pounds", "pound", "lbs", "lb",
    "grams", "gram", "g",
    "kilograms", "kilogram", "kg",
    "milliliters", "milliliter", "ml",
    "liters", "liter", "l",
    "pints", "pint", "pt",
    "quarts", "quart", "qt",
    "gallons", "gallon", "gal",
    "pinches", "pinch",
    "dashes", "dash",
    "cloves", "clove",
    "slices", "slice",
    "packages", "package", "pkgs", "pkg", "packet", "packets",
    "cans", "can",
    "bunches", "bunch",
    "heads", "head",
    "stalks", "stalk",
    "sprigs", "sprig",
    "sticks", "stick",
    "pieces", "piece", "pcs",
    "drops", "drop",
    "bottles", "bottle",
]


def parse_ingredient_components(ingredient_str: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Deconstructs a raw ingredient line into (name, quantity, unit).

    Examples:
        - "4 cups all-purpose flour" -> ("all-purpose flour", "4", "cups")
        - "1/3 cup granulated sugar" -> ("granulated sugar", "1/3", "cup")
        - "2 large eggs" -> ("eggs", "2", "large") or ("large eggs", "2", None)
        - "1 packet active dry yeast" -> ("active dry yeast", "1", "packet")
        - "salt and pepper to taste" -> ("salt and pepper", None, None)
    """
    raw = ingredient_str.strip()
    if not raw:
        return ("", None, None)

    # 1. Match leading quantity (e.g. 1 1/2, 1/3, 2.5, 2-3, 4)
    qty_pattern = r"^((?:\d+\s+)?\d+/\d+|\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)\s*"
    qty_match = re.match(qty_pattern, raw)

    if not qty_match:
        # No leading numeric quantity
        return (raw, None, None)

    qty_str = qty_match.group(1).strip()
    remainder = raw[qty_match.end():].strip()

    if not remainder:
        return (qty_str, None, None)

    # 2. Check for unit in remainder
    # Sort units by descending length to match "fluid ounces" before "ounces"
    for unit in sorted(CULINARY_UNITS, key=len, reverse=True):
        unit_pattern = rf"^({re.escape(unit)}(?:s|\b))\s*,?\s*(?:of\s+)?"
        unit_match = re.match(unit_pattern, remainder, flags=re.IGNORECASE)
        if unit_match:
            unit_found = unit_match.group(1).strip().rstrip(",")
            raw_name = remainder[unit_match.end():].strip()
            name = re.sub(r"^of\s+", "", raw_name, flags=re.IGNORECASE).strip()
            return (name if name else unit_found, qty_str, unit_found if name else None)

    # If no recognized unit, check if first word is a size descriptor like "large", "medium", "small"
    size_match = re.match(r"^(large|medium|small|extra-large)\s+(.*)", remainder, flags=re.IGNORECASE)
    if size_match:
        size = size_match.group(1).strip()
        name = size_match.group(2).strip()
        return (name, qty_str, size)

    return (remainder, qty_str, None)


def format_cooklang_ingredient(name: str, quantity: Optional[str] = None, unit: Optional[str] = None) -> str:
    """Formats an ingredient into standard Cooklang @name{qty%unit} syntax."""
    clean_name = name.strip()
    if not clean_name:
        return ""

    if quantity and unit:
        return f"@{clean_name}{{{quantity}%{unit}}}"
    elif quantity:
        return f"@{clean_name}{{{quantity}}}"
    else:
        # Single word can be @name, multi-word is @name{}
        if " " in clean_name:
            return f"@{clean_name}{{}}"
        return f"@{clean_name}{{}}"


def annotate_cookware_in_step(step_text: str) -> str:
    """Detects and annotates cookware terms in instruction step with #cookware{} without corrupting existing annotations."""
    placeholders: List[str] = []

    def save_item(item_str: str) -> str:
        placeholders.append(item_str)
        return f"__CK_PH_{len(placeholders)-1}__"

    # Protect existing annotations (@name{...}, #name{...}, ~{...})
    protected = re.sub(r"[@#~][^{}]*\{[^{}]*\}", lambda m: save_item(m.group(0)), step_text)

    for term in sorted(COOKWARE_TERMS, key=len, reverse=True):
        pattern = rf"\b{re.escape(term)}\b"
        def repl(m: re.Match) -> str:
            return save_item(f"#{term}{{}}")
        protected = re.sub(pattern, repl, protected, count=1, flags=re.IGNORECASE)

    for idx, orig in enumerate(placeholders):
        protected = protected.replace(f"__CK_PH_{idx}__", orig)

    return protected


def annotate_timers_in_step(step_text: str) -> str:
    """Detects and annotates cooking durations in instruction step with ~timer{quantity%unit} without corrupting existing annotations."""
    placeholders: List[str] = []

    def save_item(item_str: str) -> str:
        placeholders.append(item_str)
        return f"__TM_PH_{len(placeholders)-1}__"

    # Protect existing annotations
    protected = re.sub(r"[@#~][^{}]*\{[^{}]*\}", lambda m: save_item(m.group(0)), step_text)

    timer_qty_pattern = r"((?:(?:\d+\s+)?\d+/\d+|\d+(?:\.\d+)?)(?:\s*(?:-|to)\s*(?:(?:\d+\s+)?\d+/\d+|\d+(?:\.\d+)?))?)"
    timer_pattern = rf"\b{timer_qty_pattern}\s*(minutes|minute|mins|min|hours|hour|hrs|hr|seconds|second|secs|sec)\b"

    def replace_timer(match: re.Match) -> str:
        qty = match.group(1).strip()
        unit = match.group(2).strip()
        return save_item(f"~{{{qty}%{unit}}}")

    protected = re.sub(timer_pattern, replace_timer, protected, flags=re.IGNORECASE)

    for idx, orig in enumerate(placeholders):
        protected = protected.replace(f"__TM_PH_{idx}__", orig)

    return protected


class CooklangExporter:
    """Serializes RecipeSchema models into Cooklang (.cook) specification."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self.encoding = encoding

    def export_single_recipe(self, recipe: RecipeSchema) -> str:
        """Serializes a single RecipeSchema into a Cooklang (.cook) formatted document."""
        lines: List[str] = []

        # 1. Metadata Headers (>> key: value)
        lines.append(f">> title: {recipe.title}")

        if recipe.description:
            lines.append(f">> description: {recipe.description}")

        if recipe.prep_time:
            lines.append(f">> prep time: {recipe.prep_time}")

        if recipe.cook_time:
            lines.append(f">> cook time: {recipe.cook_time}")

        if recipe.total_time:
            lines.append(f">> total time: {recipe.total_time}")

        if recipe.yield_servings:
            lines.append(f">> servings: {recipe.yield_servings}")

        if recipe.tags:
            lines.append(f">> tags: {', '.join(recipe.tags)}")

        if recipe.source_notes:
            lines.append(f">> source: {recipe.source_notes}")

        if recipe.created_at:
            lines.append(f">> created_at: {recipe.created_at}")

        lines.append("")

        # 2. Parsed ingredients mapping
        parsed_ingredients = []
        for ing in recipe.ingredients:
            name, qty, unit = parse_ingredient_components(ing)
            cook_repr = format_cooklang_ingredient(name, qty, unit)
            parsed_ingredients.append((name, cook_repr))

        # Sort ingredients descending by length so longer phrases (e.g. 'melted butter') match before 'butter'
        sorted_for_matching = sorted(parsed_ingredients, key=lambda x: len(x[0]), reverse=True)

        # 3. Directions Steps with embedded annotations
        annotated_steps: List[str] = []
        used_ingredients = set()

        for step in recipe.directions:
            # Clean leading step numbers
            cleaned_step = re.sub(r"^(?:Step\s+)?\d+[\.\)\-:]\s*", "", step, flags=re.IGNORECASE).strip()

            # Annotate ingredients mentioned in step using placeholder protection and robust word boundaries
            step_annotated = cleaned_step
            ing_placeholders: List[str] = []

            def save_ing(item_str: str) -> str:
                ing_placeholders.append(item_str)
                return f"__ING_PH_{len(ing_placeholders)-1}__"

            for name, cook_repr in sorted_for_matching:
                if not name:
                    continue
                prefix = r"\b" if re.match(r"^\w", name) else r"(?<!\S)"
                suffix = r"\b" if re.search(r"\w$", name) else r"(?!\S)"
                pattern = rf"{prefix}{re.escape(name)}{suffix}"
                if re.search(pattern, step_annotated, flags=re.IGNORECASE):
                    def repl_ing(m: re.Match) -> str:
                        return save_ing(cook_repr)
                    step_annotated = re.sub(pattern, repl_ing, step_annotated, count=1, flags=re.IGNORECASE)
                    used_ingredients.add(name)

            for idx, orig in enumerate(ing_placeholders):
                step_annotated = step_annotated.replace(f"__ING_PH_{idx}__", orig)

            # Annotate cookware
            step_annotated = annotate_cookware_in_step(step_annotated)

            # Annotate timers
            step_annotated = annotate_timers_in_step(step_annotated)

            annotated_steps.append(step_annotated)

        # 4. Check for unused ingredients and define them
        unused_ingredients = [
            cook_repr for name, cook_repr in parsed_ingredients if name not in used_ingredients
        ]

        if unused_ingredients:
            lines.append("-- Ingredients:")
            for item in unused_ingredients:
                lines.append(item)
            lines.append("")

        # 5. Add steps
        if annotated_steps:
            lines.append("-- Directions:")
            for step in annotated_steps:
                lines.append(step)
                lines.append("")

        return "\n".join(lines).strip() + "\n"

    def export_to_cooklang_string(self, recipes: Union[RecipeSchema, List[RecipeSchema]]) -> str:
        """Serializes recipe(s) into Cooklang string."""
        recipe_list = [recipes] if isinstance(recipes, RecipeSchema) else list(recipes)

        if not recipe_list:
            return ""

        if len(recipe_list) == 1:
            return self.export_single_recipe(recipe_list[0])

        docs = [self.export_single_recipe(r) for r in recipe_list]
        return "\n\n---\n\n".join(docs) + "\n"

    def export_to_file(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        output_path: Union[str, Path],
    ) -> Path:
        """Writes Cooklang markup to file on disk."""
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self.export_to_cooklang_string(recipes)
        path.write_text(content, encoding=self.encoding)
        count = 1 if isinstance(recipes, RecipeSchema) else len(recipes)
        logger.info(f"Exported {count} recipe(s) to Cooklang: {path}")
        return path

    def export_to_bytes(self, recipes: Union[RecipeSchema, List[RecipeSchema]]) -> bytes:
        """Serializes Cooklang recipes into UTF-8 encoded bytes."""
        return self.export_to_cooklang_string(recipes).encode(self.encoding)


def export_recipes_to_cooklang(recipes: Union[RecipeSchema, List[RecipeSchema]]) -> str:
    """Convenience helper to export recipes to Cooklang (.cook) format string."""
    return CooklangExporter().export_to_cooklang_string(recipes)
