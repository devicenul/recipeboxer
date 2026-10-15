"""Domain normalizers for recipe fractions, archaic units, and advertisement noise (WP-XC, X7).

Authoritative contracts: WAVE5_CONTRACTS.md §2 (Extraction interfaces), §3 (Ownership).
Adheres to REMEDIATION_PLAN.md §8.3 (WP-X7):
- Calls normalize_fractions, normalize_archaic_units from vintage_handwriting_ocr.
- Calls is_promotional_line / filter_advertisement_noise from print_clipping_extractor.
- Writes EXCLUSIVELY to normalized_* fields:
    - IngredientLine.normalized_text
    - IngredientLine.normalized_quantity (unambiguous numeric float only)
    - IngredientLine.normalized_unit
    - Step.normalized_text
    - TimeValue.normalized_iso / normalized_minutes
    - YieldValue.normalized_servings
- NEVER alters or overwrites original_text (must preserve verbatim transcription).
- Promotional / advertisement noise is moved to notes with a warning, NOT deleted.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from recipeboxer.agent.skills.print_clipping_extractor.parser import (
    filter_advertisement_noise,
    is_promotional_line,
)
from recipeboxer.agent.skills.vintage_handwriting_ocr.parser import (
    normalize_archaic_units,
    normalize_fractions,
)
from recipeboxer.models.recipe import (
    IngredientLine,
    Recipe,
    RecipeSection,
    RecipeWarning,
    Step,
    TextSpan,
    TimeValue,
    YieldValue,
)


# Known standard culinary unit canonical forms
CANONICAL_UNITS: Dict[str, str] = {
    "c": "cup",
    "c.": "cup",
    "cup": "cup",
    "cups": "cup",
    "t": "tsp",
    "t.": "tsp",
    "tsp": "tsp",
    "tsp.": "tsp",
    "tsps": "tsp",
    "tsps.": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "tbs": "tbsp",
    "tbs.": "tbsp",
    "tbsp": "tbsp",
    "tbsp.": "tbsp",
    "tbsps": "tbsp",
    "tbsps.": "tbsp",
    "tbl": "tbsp",
    "tbl.": "tbsp",
    "tblsp": "tbsp",
    "tblsp.": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "oz": "oz",
    "oz.": "oz",
    "ounce": "oz",
    "ounces": "oz",
    "lb": "lb",
    "lb.": "lb",
    "lbs": "lb",
    "lbs.": "lb",
    "pound": "lb",
    "pounds": "lb",
    "pt": "pint",
    "pt.": "pint",
    "pint": "pint",
    "pints": "pint",
    "qt": "quart",
    "qt.": "quart",
    "quart": "quart",
    "quarts": "quart",
    "gal": "gal",
    "gal.": "gal",
    "gallon": "gal",
    "gallons": "gal",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "kg": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
    "ml": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "pkg": "pkg",
    "pkg.": "pkg",
    "package": "pkg",
    "packages": "pkg",
    "pinch": "pinch",
    "dash": "dash",
    "drop": "drop",
    "drops": "drop",
    "clove": "clove",
    "cloves": "clove",
    "slice": "slice",
    "slices": "slice",
    "can": "can",
    "cans": "can",
    "stick": "stick",
    "sticks": "stick",
    "gill": "gill",
    "gills": "gill",
    "teacup": "teacup",
    "teacups": "teacup",
    "peck": "peck",
    "pecks": "peck",
}

# Words indicating ambiguous or non-numeric quantities
AMBIGUOUS_QUANTITY_WORDS = {
    "pinch",
    "pinches",
    "dash",
    "dashes",
    "taste",
    "to taste",
    "handful",
    "handfuls",
    "some",
    "few",
    "several",
    "smidgen",
    "splash",
    "sprinkle",
    "as needed",
    "optional",
    "garnish",
}


def parse_unambiguous_quantity(raw_val: Union[str, float, int, None]) -> Optional[float]:
    """Parses a quantity string or numeric value into a float only where unambiguous.

    Returns:
        float: Exact decimal quantity (e.g. '1 1/2' -> 1.5, '1½' -> 1.5, '3/4' -> 0.75, '2' -> 2.0).
        None: When quantity is ambiguous, a range ('1-2', '1 to 2'), descriptive ('to taste'), or empty.
    """
    if raw_val is None:
        return None

    if isinstance(raw_val, (int, float)):
        return float(raw_val)

    s = str(raw_val).strip()
    if not s:
        return None

    # Standardize unicode vulgar fractions into ASCII (e.g. '1½' -> '1 1/2')
    s = normalize_fractions(s)
    s_lower = s.lower()

    # Check for ambiguous descriptive words
    for word in AMBIGUOUS_QUANTITY_WORDS:
        if word in s_lower:
            return None

    # Check for ranges ('1 to 2', '1 or 2', '1/4 to 1/2', etc.)
    if " to " in s_lower or " or " in s_lower:
        return None

    # Check for whole-number or fraction ranges with hyphen (e.g. '1-2', '1/4 - 1/2', '2 - 3')
    # Note: '1-1/2' was already converted to '1 1/2' by normalize_fractions.
    if re.search(r"(?<!/)\b\d+(?:\.\d+)?\s*-\s*\d+(?!/)\b", s):
        return None
    if re.search(r"\d+/\d+\s*-\s*\d+/\d+", s):
        return None

    # 1. Mixed number pattern: e.g. '1 1/2', '2 3/4'
    mixed_match = re.match(r"^(\d+)\s+(\d+)/(\d+)$", s)
    if mixed_match:
        whole = int(mixed_match.group(1))
        num = int(mixed_match.group(2))
        denom = int(mixed_match.group(3))
        if denom == 0:
            return None
        return round(whole + (num / denom), 4)

    # 2. Simple fraction pattern: e.g. '1/2', '3/4'
    frac_match = re.match(r"^(\d+)/(\d+)$", s)
    if frac_match:
        num = int(frac_match.group(1))
        denom = int(frac_match.group(2))
        if denom == 0:
            return None
        return round(num / denom, 4)

    # 3. Simple integer or float pattern: e.g. '2', '1.5', '0.25'
    num_match = re.match(r"^(\d+(?:\.\d+)?)$", s)
    if num_match:
        return float(num_match.group(1))

    return None


def parse_unambiguous_unit(raw_unit: Optional[str]) -> Optional[str]:
    """Standardizes a measurement unit string into its canonical representation."""
    if not raw_unit or not str(raw_unit).strip():
        return None

    raw_str = str(raw_unit).strip()
    # Check case-sensitive culinary shorthand: T/Tbsp (tablespoon) vs t/tsp (teaspoon)
    if raw_str in ("T", "T.", "Tbsp", "Tbsp.", "Tbs", "Tbs.", "Tbl", "Tbl.", "Tblsp", "Tblsp."):
        return "tbsp"
    if raw_str in ("t", "t.", "tsp", "tsp.", "tsps", "tsps."):
        return "tsp"
    if raw_str in ("C", "C."):
        return "cup"

    clean = raw_str.lower()
    # Strip trailing punctuation if present
    clean_stripped = clean.rstrip(".,;")
    return CANONICAL_UNITS.get(clean) or CANONICAL_UNITS.get(clean_stripped) or clean_stripped


def _extract_leading_quantity_and_unit(text: str) -> Tuple[Optional[float], Optional[str]]:
    """Extracts unambiguous numeric quantity and unit from the beginning of normalized ingredient text."""
    if not text:
        return None, None

    # Match leading quantity: mixed fraction, simple fraction, or decimal/int
    m = re.match(r"^(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\b(?:\s+([a-zA-Z.]+))?", text.strip())
    if not m:
        return None, None

    qty_str = m.group(1)
    unit_str = m.group(2)

    qty = parse_unambiguous_quantity(qty_str)
    unit = parse_unambiguous_unit(unit_str) if unit_str else None
    return qty, unit


def normalize_ingredient_line(line: IngredientLine) -> IngredientLine:
    """Normalizes fractions and archaic units on an IngredientLine, writing ONLY to normalized_* fields.

    Leaves line.original_text strictly bit-for-bit verbatim.
    """
    source_text = line.original_text or line.item or ""
    norm_text = normalize_archaic_units(source_text)

    # Parse numeric quantity
    norm_qty: Optional[float] = None
    if line.quantity:
        norm_qty = parse_unambiguous_quantity(line.quantity)

    # Parse unit
    norm_unit: Optional[str] = None
    if line.unit:
        norm_unit = parse_unambiguous_unit(line.unit)

    # If quantity or unit was missing from structured fields, attempt extraction from normalized text
    extracted_qty, extracted_unit = _extract_leading_quantity_and_unit(norm_text)
    if norm_qty is None and extracted_qty is not None:
        norm_qty = extracted_qty
    if norm_unit is None and extracted_unit is not None:
        norm_unit = extracted_unit

    updates: Dict[str, Any] = {
        "normalized_text": norm_text,
        "normalized_quantity": norm_qty,
        "normalized_unit": norm_unit,
    }
    return line.model_copy(update=updates)


def normalize_step(step: Step) -> Step:
    """Normalizes fractions and archaic measurements on a Step, writing ONLY to normalized_text.

    Leaves step.original_text strictly bit-for-bit verbatim.
    """
    source_text = step.original_text or step.text or ""
    norm_text = normalize_archaic_units(source_text)
    return step.model_copy(update={"normalized_text": norm_text})


def normalize_time_value(time_val: Optional[TimeValue]) -> Optional[TimeValue]:
    """Normalizes duration and ISO 8601 formatting for TimeValue."""
    if time_val is None:
        return None

    raw_text = time_val.original_text or ""
    minutes = time_val.minutes or time_val.normalized_minutes
    iso_val = time_val.normalized_iso

    if minutes is None and raw_text:
        # Simple extraction e.g. '20 mins', '1 hour', '1 hr 15 mins'
        hr_m = re.search(r"(\d+)\s*(?:hours?|hrs?|h\b)", raw_text, re.IGNORECASE)
        min_m = re.search(r"(\d+)\s*(?:minutes?|mins?|m\b)", raw_text, re.IGNORECASE)
        total_m = 0
        found = False
        if hr_m:
            total_m += int(hr_m.group(1)) * 60
            found = True
        if min_m:
            total_m += int(min_m.group(1))
            found = True
        if found:
            minutes = total_m

    if minutes is not None and not iso_val:
        hrs = minutes // 60
        rem_mins = minutes % 60
        if hrs > 0 and rem_mins > 0:
            iso_val = f"PT{hrs}H{rem_mins}M"
        elif hrs > 0:
            iso_val = f"PT{hrs}H"
        else:
            iso_val = f"PT{rem_mins}M"

    return time_val.model_copy(
        update={
            "minutes": minutes,
            "normalized_minutes": minutes,
            "normalized_iso": iso_val,
        }
    )


def normalize_yield_value(yield_val: Optional[YieldValue]) -> Optional[YieldValue]:
    """Normalizes servings count for YieldValue."""
    if yield_val is None:
        return None

    raw_text = yield_val.original_text or ""
    servings = yield_val.normalized_servings

    if servings is None and raw_text:
        # Extract leading or standalone integer/float servings count
        num_m = re.search(r"\b(\d+(?:\.\d+)?)\b", raw_text)
        if num_m:
            servings = float(num_m.group(1))

    return yield_val.model_copy(
        update={
            "normalized_servings": servings,
        }
    )


def normalize_recipe(recipe: Recipe) -> Recipe:
    """Applies domain normalizers across all recipe fields in Recipe Schema v2.

    Adheres strictly to the contract:
    1. Calls normalize_fractions, normalize_archaic_units, filter_advertisement_noise.
    2. Writes EXCLUSIVELY to normalized_* fields.
    3. NEVER alters original_text (preserving verbatim card transcription).
    4. Promotional / advertisement noise is moved to notes with a warning, NOT deleted.

    Args:
        recipe: The structured Recipe instance to normalize.

    Returns:
        Recipe: A normalized Recipe with populated normalized_* fields and preserved original_text.
    """
    new_warnings: List[RecipeWarning] = list(recipe.warnings)
    new_notes: List[TextSpan] = list(recipe.notes)
    new_sections: List[RecipeSection] = []

    # Check existing notes for promotional content and add warnings if detected
    for note in recipe.notes:
        note_str = note.text if hasattr(note, "text") else str(note)
        if is_promotional_line(note_str):
            new_warnings.append(
                RecipeWarning(
                    code="ADVERTISEMENT_NOISE",
                    message=f"Promotional content retained in notes: '{note_str}'",
                    field_path="notes",
                )
            )

    # Process sections: ingredients and steps
    for sec in recipe.sections:
        clean_ingredients: List[IngredientLine] = []
        for ing in sec.ingredients:
            raw_text = ing.original_text or ing.item or ""
            if is_promotional_line(raw_text):
                # Move to notes with a warning, do NOT delete
                new_notes.append(
                    TextSpan(
                        text=raw_text,
                        confidence=ing.confidence,
                        source_face=ing.source_face,
                        legibility=ing.legibility,
                    )
                )
                new_warnings.append(
                    RecipeWarning(
                        code="ADVERTISEMENT_NOISE",
                        message=f"Moved advertisement noise to notes: '{raw_text}'",
                        field_path="notes",
                    )
                )
            else:
                clean_ingredients.append(normalize_ingredient_line(ing))

        clean_steps: List[Step] = []
        for step in sec.steps:
            raw_text = step.original_text or step.text or ""
            if is_promotional_line(raw_text):
                # Move to notes with a warning, do NOT delete
                new_notes.append(
                    TextSpan(
                        text=raw_text,
                        confidence=step.confidence,
                        source_face=step.source_face,
                        legibility=step.legibility,
                    )
                )
                new_warnings.append(
                    RecipeWarning(
                        code="ADVERTISEMENT_NOISE",
                        message=f"Moved advertisement noise to notes: '{raw_text}'",
                        field_path="notes",
                    )
                )
            else:
                clean_steps.append(normalize_step(step))

        new_sections.append(
            sec.model_copy(
                update={
                    "ingredients": clean_ingredients,
                    "steps": clean_steps,
                }
            )
        )

    # Normalize time and yield values
    prep = normalize_time_value(recipe.prep_time)
    cook = normalize_time_value(recipe.cook_time)
    total = normalize_time_value(recipe.total_time)
    y_val = normalize_yield_value(recipe.yield_value)

    return recipe.model_copy(
        update={
            "sections": new_sections,
            "notes": new_notes,
            "warnings": new_warnings,
            "prep_time": prep,
            "cook_time": cook,
            "total_time": total,
            "yield_value": y_val,
        }
    )
