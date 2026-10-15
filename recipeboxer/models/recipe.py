"""Structured recipe schema v2 conforming to Plan §8.2 and Contract §1.9."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


class Legibility(str, Enum):
    """Legibility classification for text segments and spans."""
    CLEAR = "CLEAR"
    UNCERTAIN = "UNCERTAIN"
    ILLEGIBLE = "ILLEGIBLE"


class IllegibleSpan(BaseModel):
    """Granular illegible span descriptor within a text line."""
    text_guess: Optional[str] = Field(
        default=None,
        description="Best-effort guess of unreadable text or None if completely obscured",
    )
    reason: str = Field(
        default="ambiguous",
        description="Reason for illegibility: faded, torn, stain, crossed_out, ambiguous",
    )


class TextSpan(BaseModel):
    """Segment of narrative text, headnote, margin note, or anecdote."""
    text: str = Field(..., description="Text content")
    kind: Optional[str] = Field(
        default=None,
        description="Classification: headnote, note, family_story, margin_note, ad",
    )
    source_face: int = Field(default=0, ge=0, description="0-indexed card face index")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0")
    legibility: Legibility = Field(default=Legibility.CLEAR, description="Legibility status")
    illegible_spans: List[IllegibleSpan] = Field(default_factory=list, description="Illegible spans")

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {v}")
        return float(v)

    @field_validator("source_face")
    @classmethod
    def validate_source_face(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"source_face must be >= 0, got {v}")
        return int(v)


class IngredientLine(BaseModel):
    """Granular culinary ingredient measurement and item definition."""
    original_text: str = Field(..., description="Verbatim text as transcribed from card face")
    quantity: Optional[str] = Field(default=None, description="Primary quantity (e.g. '1 1/2', '2')")
    quantity_max: Optional[str] = Field(default=None, description="Upper bound for quantity range (e.g. '3')")
    unit: Optional[str] = Field(default=None, description="Measurement unit (e.g. 'cup', 'tsp')")
    item: str = Field(default="", description="Ingredient name / item")
    preparation: Optional[str] = Field(default=None, description="Preparation note (e.g. 'chopped', 'sifted')")
    normalized_text: Optional[str] = Field(
        default=None,
        description="Normalized representation with standardized fractions and archaic units",
    )
    normalized_quantity: Optional[float] = Field(
        default=None,
        description="Normalized numeric quantity (where unambiguous)",
    )
    normalized_unit: Optional[str] = Field(
        default=None,
        description="Normalized standard measurement unit",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Extraction confidence 0.0 to 1.0")
    source_face: int = Field(default=0, ge=0, description="0-indexed card face index")
    legibility: Legibility = Field(default=Legibility.CLEAR, description="Legibility status")
    illegible_spans: List[IllegibleSpan] = Field(default_factory=list, description="Illegible spans")

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {v}")
        return float(v)

    @field_validator("source_face")
    @classmethod
    def validate_source_face(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"source_face must be >= 0, got {v}")
        return int(v)

    @property
    def display_text(self) -> str:
        """Returns normalized_text if present, else original_text, else assembled item."""
        if self.normalized_text and self.normalized_text.strip():
            return self.normalized_text.strip()
        if self.original_text and self.original_text.strip():
            return self.original_text.strip()
        parts = [p for p in [self.quantity, self.unit, self.item] if p]
        return " ".join(parts).strip()


class Step(BaseModel):
    """Individual preparation or cooking instruction step."""
    original_text: str = Field(..., description="Verbatim instruction text from card")
    text: Optional[str] = Field(default=None, description="Instruction step text")
    normalized_text: Optional[str] = Field(
        default=None,
        description="Normalized instruction text with standardized fractions/temperatures",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Extraction confidence 0.0 to 1.0")
    source_face: int = Field(default=0, ge=0, description="0-indexed card face index")
    legibility: Legibility = Field(default=Legibility.CLEAR, description="Legibility status")
    illegible_spans: List[IllegibleSpan] = Field(default_factory=list, description="Illegible spans")

    @model_validator(mode="before")
    @classmethod
    def _coerce_step_text(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if not data.get("text") and data.get("original_text"):
                data["text"] = data["original_text"]
        return data

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {v}")
        return float(v)

    @field_validator("source_face")
    @classmethod
    def validate_source_face(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"source_face must be >= 0, got {v}")
        return int(v)

    @property
    def display_text(self) -> str:
        """Returns normalized_text if present, else text, else original_text."""
        if self.normalized_text and self.normalized_text.strip():
            return self.normalized_text.strip()
        if self.text and self.text.strip():
            return self.text.strip()
        return self.original_text.strip()


class RecipeSection(BaseModel):
    """Logical grouping of ingredients and steps (e.g. 'Crust', 'Filling', or default)."""
    name: Optional[str] = Field(default=None, description="Section heading, or None for main")
    ingredients: List[IngredientLine] = Field(default_factory=list, description="Section ingredients")
    steps: List[Step] = Field(default_factory=list, description="Section instructions")

    @model_validator(mode="before")
    @classmethod
    def _coerce_title(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "title" in data and "name" not in data:
                data["name"] = data.pop("title")
        return data

    @property
    def title(self) -> Optional[str]:
        return self.name

    @title.setter
    def title(self, value: Optional[str]) -> None:
        self.name = value



class TimeValue(BaseModel):
    """Duration representation with original verbatim text and normalized values."""
    original_text: str = Field(default="", description="Verbatim time string (e.g. '20 mins')")
    normalized_iso: Optional[str] = Field(default=None, description="ISO 8601 duration (e.g. 'PT20M')")
    minutes: Optional[int] = Field(default=None, description="Total minutes duration")
    normalized_minutes: Optional[int] = Field(default=None, description="Total minutes duration (alias)")

    @model_validator(mode="before")
    @classmethod
    def _sync_minutes(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "minutes" in data and "normalized_minutes" not in data:
                data["normalized_minutes"] = data["minutes"]
            elif "normalized_minutes" in data and "minutes" not in data:
                data["minutes"] = data["normalized_minutes"]
        return data

    @property
    def display_text(self) -> str:
        if self.original_text and self.original_text.strip():
            return self.original_text.strip()
        if self.minutes is not None:
            return f"{self.minutes} mins"
        if self.normalized_iso:
            return self.normalized_iso
        return ""

    def __str__(self) -> str:
        return self.display_text

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.display_text == other or self.original_text == other
        if isinstance(other, TimeValue):
            return self.display_text == other.display_text
        return super().__eq__(other)

    def __bool__(self) -> bool:
        return bool(self.display_text and self.display_text.strip())

    def __getattr__(self, item: str) -> Any:
        if hasattr(str, item):
            return getattr(self.display_text, item)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{item}'")


class YieldValue(BaseModel):
    """Yield or serving size with original text and normalized servings."""
    original_text: str = Field(default="", description="Verbatim yield text (e.g. '24 cookies')")
    normalized_servings: Optional[float] = Field(default=None, description="Normalized numeric servings count")
    unit: Optional[str] = Field(default=None, description="Unit name (e.g. 'servings', 'cookies', 'bars')")

    @property
    def display_text(self) -> str:
        if self.original_text and self.original_text.strip():
            return self.original_text.strip()
        if self.normalized_servings is not None:
            u = f" {self.unit}" if self.unit else ""
            val = int(self.normalized_servings) if self.normalized_servings.is_integer() else self.normalized_servings
            return f"{val}{u}".strip()
        return ""

    def __str__(self) -> str:
        return self.display_text

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.display_text == other or self.original_text == other
        if isinstance(other, YieldValue):
            return self.display_text == other.display_text
        return super().__eq__(other)

    def __bool__(self) -> bool:
        return bool(self.display_text and self.display_text.strip())

    def __getattr__(self, item: str) -> Any:
        if hasattr(str, item):
            return getattr(self.display_text, item)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{item}'")


class Provenance(BaseModel):
    """Pipeline lineage, models, prompt versions, profile, and cost."""
    faces: List[str] = Field(default_factory=list, description="Card face identifiers in reading order")
    models: List[str] = Field(default_factory=list, description="LLM models used during transcription/extraction")
    prompt_versions: Dict[str, str] = Field(default_factory=dict, description="Prompt IDs and hashes")
    profile: Optional[str] = Field(default=None, description="Execution profile (economy, balanced, best)")
    cost: Optional[float] = Field(default=None, description="Total USD cost incurred")
    skipped_passes: List[str] = Field(default_factory=list, description="Passes that encountered errors and were skipped")


class RecipeWarning(BaseModel):
    """Pipeline warning or verification flag on extracted recipe data."""
    code: str = Field(..., description="Warning code (e.g. GROUNDING_UNSUPPORTED, LOW_CONFIDENCE, MODEL_DISAGREEMENT)")
    message: str = Field(..., description="Human-readable warning detail")
    field_path: Optional[str] = Field(default=None, description="JSON path of affected field")


class Recipe(BaseModel):
    """Authoritative Recipe Schema v2."""
    recipe_id: str = Field(
        default_factory=lambda: f"rec_{uuid.uuid4().hex[:8]}",
        description="Unique recipe identifier",
    )
    title: str = Field(..., description="Primary recipe title")
    title_is_inferred: bool = Field(
        default=False,
        description="True if title was not explicitly written and had to be inferred",
    )
    description: Optional[str] = Field(default="", description="Recipe description, intro, or headnote")
    headnote: Optional[TextSpan] = Field(default=None, description="Headnote, family story, or marginalia")
    notes: List[TextSpan] = Field(default_factory=list, description="Narrative notes, margin notes, clipping text")
    sections: List[RecipeSection] = Field(default_factory=list, description="Ordered sections of ingredients and steps")
    prep_time: Optional[TimeValue] = Field(default_factory=lambda: TimeValue(original_text=""), description="Preparation time")
    cook_time: Optional[TimeValue] = Field(default_factory=lambda: TimeValue(original_text=""), description="Cooking time")
    total_time: Optional[TimeValue] = Field(default_factory=lambda: TimeValue(original_text=""), description="Total time")
    yield_value: Optional[YieldValue] = Field(default_factory=lambda: YieldValue(original_text=""), description="Recipe yield or servings")
    tags: List[str] = Field(default_factory=list, description="Categorical tags")
    source_notes: Optional[str] = Field(default="", description="Attribution, handwritten card author, provenance")
    provenance: Optional[Provenance] = Field(default=None, description="Extraction provenance")
    warnings: List[RecipeWarning] = Field(default_factory=list, description="Grounding and confidence warnings")
    confidence: Optional[float] = Field(default=None, description="Overall confidence 0.0 to 1.0")
    created_at: Optional[str] = Field(default=None, description="ISO 8601 creation timestamp")
    updated_at: Optional[str] = Field(default=None, description="ISO 8601 update timestamp")
    image_urls: List[str] = Field(default_factory=list, description="Preview URLs for associated cards")
    card_ids: List[str] = Field(default_factory=list, description="Card face IDs associated with this recipe")

    @field_validator("title")
    @classmethod
    def validate_title_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Recipe title cannot be empty.")
        return v

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be between 0.0 and 1.0, got {v}")
        return float(v) if v is not None else None

    @field_validator("tags", mode="after")
    @classmethod
    def clean_and_deduplicate_tags(cls, tags: List[str]) -> List[str]:
        seen = set()
        deduped = []
        for tag in tags:
            cleaned = tag.strip()
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                deduped.append(cleaned)
        return deduped

    @model_validator(mode="before")
    @classmethod
    def _coerce_v1_inputs(cls, data: Any) -> Any:
        """Coerces flat v1 ingredients/directions and scalar fields into v2 sections and Value objects."""
        if not isinstance(data, dict):
            return data

        d = dict(data)

        # Validate types for legacy fields if present
        if "ingredients" in d and d["ingredients"] is not None and not isinstance(d["ingredients"], list):
            raise ValueError("ingredients must be a list")
        if "directions" in d and d["directions"] is not None and not isinstance(d["directions"], list):
            raise ValueError("directions must be a list")
        if "tags" in d and d["tags"] is not None and not isinstance(d["tags"], list):
            raise ValueError("tags must be a list")

        # Coerce description & source_notes if None
        if "description" in d and d["description"] is None:
            d["description"] = ""
        if "source_notes" in d and d["source_notes"] is None:
            d["source_notes"] = ""

        # Check if legacy flat ingredients or directions provided
        raw_ings = d.pop("ingredients", None)
        raw_dirs = d.pop("directions", None)

        has_sections = bool(d.get("sections"))
        # If both sections and raw_ings were provided, check if raw_ings was modified
        if has_sections and raw_ings is not None:
            existing_ings = []
            for s in d.get("sections", []):
                for ing in s.get("ingredients", []):
                    if isinstance(ing, str):
                        existing_ings.append(ing.strip())
                    elif isinstance(ing, dict):
                        existing_ings.append(ing.get("normalized_text") or ing.get("original_text") or "")
                    elif hasattr(ing, "display_text"):
                        existing_ings.append(ing.display_text)
            if [i.strip() for i in raw_ings if isinstance(i, str) and i.strip()] != [i for i in existing_ings if i]:
                has_sections = False

        if not has_sections and (raw_ings is not None or raw_dirs is not None):
            conv_ings: List[Dict[str, Any]] = []
            if raw_ings:
                for ing in raw_ings:
                    if isinstance(ing, str):
                        cleaned = ing.strip()
                        if not cleaned:
                            continue
                        conv_ings.append({
                            "original_text": cleaned,
                            "item": cleaned,
                            "confidence": 1.0,
                            "source_face": 0,
                            "legibility": "CLEAR",
                        })
                    elif isinstance(ing, dict):
                        conv_ings.append(ing)
                    elif hasattr(ing, "model_dump"):
                        conv_ings.append(ing.model_dump())

            conv_steps: List[Dict[str, Any]] = []
            if raw_dirs:
                idx = 1
                for step in raw_dirs:
                    if isinstance(step, str):
                        stripped = step.strip()
                        if not stripped:
                            continue
                        cleaned = re.sub(
                            r"^(?:(?:Step\s+)?\d+\s*[\.\)\:\-]\s*|\(\d+\)\s*)",
                            "",
                            stripped,
                            flags=re.IGNORECASE,
                        ).strip()
                        conv_steps.append({
                            "original_text": stripped,
                            "text": f"{idx}. {cleaned}" if cleaned else stripped,
                            "confidence": 1.0,
                            "source_face": 0,
                            "legibility": "CLEAR",
                        })
                        idx += 1
                    elif isinstance(step, dict):
                        conv_steps.append(step)
                        idx += 1
                    elif hasattr(step, "model_dump"):
                        conv_steps.append(step.model_dump())
                        idx += 1

            d["sections"] = [{
                "name": None,
                "ingredients": conv_ings,
                "steps": conv_steps,
            }]

        # Coerce prep_time / cook_time / total_time
        for t_field in ("prep_time", "cook_time", "total_time"):
            if t_field in d:
                val = d[t_field]
                if isinstance(val, str):
                    d[t_field] = {"original_text": val.strip()}
                elif val is None:
                    d[t_field] = {"original_text": ""}

        # Coerce yield_servings or yield into yield_value
        raw_yield = d.pop("yield_servings", None)
        if raw_yield is None:
            raw_yield = d.pop("yield", None)
        if raw_yield is not None:
            if isinstance(raw_yield, str):
                d["yield_value"] = {"original_text": raw_yield.strip()}
            elif isinstance(raw_yield, (int, float)):
                d["yield_value"] = {"original_text": str(raw_yield), "normalized_servings": float(raw_yield)}
            elif isinstance(raw_yield, YieldValue):
                d["yield_value"] = raw_yield
        elif "yield_value" in d:
            val = d["yield_value"]
            if isinstance(val, str):
                d["yield_value"] = {"original_text": val.strip()}
            elif val is None:
                d["yield_value"] = {"original_text": ""}

        # Coerce notes if list of strings
        raw_notes = d.get("notes")
        if isinstance(raw_notes, list) and raw_notes and isinstance(raw_notes[0], str):
            d["notes"] = [{"text": s.strip(), "confidence": 1.0, "source_face": 0, "legibility": "CLEAR"} for s in raw_notes if s and s.strip()]

        # Coerce headnote if string
        raw_hn = d.get("headnote")
        if isinstance(raw_hn, str) and raw_hn.strip():
            d["headnote"] = {"text": raw_hn.strip(), "confidence": 1.0, "source_face": 0, "legibility": "CLEAR"}

        return d

    # Helper accessors
    def all_ingredients(self) -> List[IngredientLine]:
        """Returns a flat list of all IngredientLine objects across all sections."""
        res: List[IngredientLine] = []
        for sec in self.sections:
            res.extend(sec.ingredients)
        return res

    def all_steps(self) -> List[Step]:
        """Returns a flat list of all Step objects across all sections."""
        res: List[Step] = []
        for sec in self.sections:
            res.extend(sec.steps)
        return res

    @computed_field
    @property
    def ingredients(self) -> List[str]:
        """Convenience property returning list of ingredient display texts."""
        return [ing.display_text for ing in self.all_ingredients()]

    @ingredients.setter
    def ingredients(self, val: Any) -> None:
        if isinstance(val, list):
            conv_ings = []
            for ing in val:
                if isinstance(ing, str):
                    c = ing.strip()
                    if c:
                        conv_ings.append(IngredientLine(original_text=c, item=c))
                elif isinstance(ing, IngredientLine):
                    conv_ings.append(ing)
                elif isinstance(ing, dict):
                    conv_ings.append(IngredientLine(**ing))
            if not self.sections:
                self.sections = [RecipeSection(ingredients=conv_ings)]
            else:
                self.sections[0].ingredients = conv_ings

    @computed_field
    @property
    def directions(self) -> List[str]:
        """Convenience property returning list of step display texts."""
        return [step.display_text for step in self.all_steps()]

    @directions.setter
    def directions(self, val: Any) -> None:
        if isinstance(val, list):
            conv_steps = []
            idx = 1
            for step in val:
                if isinstance(step, str):
                    s = step.strip()
                    if not s:
                        continue
                    cleaned = re.sub(
                        r"^(?:(?:Step\s+)?\d+\s*[\.\)\:\-]\s*|\(\d+\)\s*)",
                        "",
                        s,
                        flags=re.IGNORECASE,
                    ).strip()
                    conv_steps.append(Step(original_text=s, text=f"{idx}. {cleaned}" if cleaned else s))
                    idx += 1
                elif isinstance(step, Step):
                    conv_steps.append(step)
                    idx += 1
                elif isinstance(step, dict):
                    conv_steps.append(Step(**step))
                    idx += 1
            if not self.sections:
                self.sections = [RecipeSection(steps=conv_steps)]
            else:
                self.sections[0].steps = conv_steps

    @computed_field
    @property
    def yield_servings(self) -> str:
        """Convenience property returning yield display string."""
        return self.yield_value.display_text if self.yield_value else ""

    @yield_servings.setter
    def yield_servings(self, val: Optional[str]) -> None:
        if val is None:
            self.yield_value = YieldValue(original_text="")
        elif isinstance(val, YieldValue):
            self.yield_value = val
        else:
            self.yield_value = YieldValue(original_text=str(val).strip())

    def flagged_count(self) -> int:
        """Counts total warnings + uncertain/illegible lines and illegible spans."""
        count = len(self.warnings)
        for ing in self.all_ingredients():
            if ing.legibility in (Legibility.UNCERTAIN, Legibility.ILLEGIBLE):
                count += 1
            count += len(ing.illegible_spans)
            if ing.confidence < 0.8:
                count += 1
        for step in self.all_steps():
            if step.legibility in (Legibility.UNCERTAIN, Legibility.ILLEGIBLE):
                count += 1
            count += len(step.illegible_spans)
            if step.confidence < 0.8:
                count += 1
        if self.headnote:
            if self.headnote.legibility in (Legibility.UNCERTAIN, Legibility.ILLEGIBLE):
                count += 1
            count += len(self.headnote.illegible_spans)
            if self.headnote.confidence < 0.8:
                count += 1
        for note in self.notes:
            if note.legibility in (Legibility.UNCERTAIN, Legibility.ILLEGIBLE):
                count += 1
            count += len(note.illegible_spans)
            if note.confidence < 0.8:
                count += 1
        return count

    def needs_review(self) -> bool:
        """True if recipe contains warnings, flagged spans, or low confidence."""
        return self.flagged_count() > 0 or (self.confidence is not None and self.confidence < 0.8)

    def to_scoring_dict(self) -> Dict[str, Any]:
        """Produces the structured dictionary shape expected by evals/scoring.

        Shape: {
            "title": str,
            "yield": str | None,
            "ingredients": [{"raw": str, "quantity": str | None, "unit": str | None, "item": str, "section": str | None}],
            "directions": [str],
            "notes": [str],
        }
        """
        ingredients_list: List[Dict[str, Any]] = []
        directions_list: List[str] = []

        for section in self.sections:
            sec_name = section.name
            for ing in section.ingredients:
                raw_text = ing.normalized_text if ing.normalized_text else ing.original_text
                if not raw_text:
                    parts = [p for p in [ing.quantity, ing.unit, ing.item] if p]
                    raw_text = " ".join(parts).strip()
                ingredients_list.append({
                    "raw": raw_text,
                    "quantity": ing.quantity,
                    "unit": ing.unit,
                    "item": ing.item,
                    "section": sec_name,
                })
            for step in section.steps:
                step_text = step.normalized_text if step.normalized_text else (step.text or step.original_text)
                if step_text and step_text.strip():
                    directions_list.append(step_text.strip())

        notes_list: List[str] = []
        if self.headnote and self.headnote.text.strip():
            notes_list.append(self.headnote.text.strip())
        for n in self.notes:
            if n.text.strip():
                notes_list.append(n.text.strip())
        if self.description and self.description.strip() and self.description.strip() not in notes_list:
            notes_list.insert(0, self.description.strip())

        yield_str: Optional[str] = None
        if self.yield_value:
            yield_str = self.yield_value.display_text or None

        return {
            "title": self.title,
            "yield": yield_str,
            "ingredients": ingredients_list,
            "directions": directions_list,
            "notes": notes_list,
        }


# Alias for import stability per contract §1.9
RecipeSchema = Recipe


# -----------------------------------------------------------------------------
# Gemini Developer API Compatible Structured Response Models
# (Flat, no Dict[str, Any], no extra='allow', strict Enums/Primitives/Lists)
# -----------------------------------------------------------------------------

class RecipeIllegibleSpanResponse(BaseModel):
    """Developer API compatible descriptor for an unreadable span."""
    text_guess: Optional[str] = Field(default=None, description="Best-effort guess of unreadable text or null")
    reason: str = Field(default="ambiguous", description="Reason for illegibility: faded, torn, stain, crossed_out, ambiguous")

    def to_domain(self) -> IllegibleSpan:
        return IllegibleSpan(text_guess=self.text_guess, reason=self.reason)


class RecipeTextSpanResponse(BaseModel):
    """Developer API compatible narrative text segment or note."""
    text: str = Field(..., description="Text content")
    kind: Optional[str] = Field(default=None, description="Classification: headnote, note, family_story, margin_note, ad")
    source_face: int = Field(default=0, ge=0, description="0-indexed card face index")
    legibility: Legibility = Field(default=Legibility.CLEAR, description="Legibility status")
    illegible_spans: List[RecipeIllegibleSpanResponse] = Field(default_factory=list, description="Illegible spans")

    def to_domain(self) -> TextSpan:
        return TextSpan(
            text=self.text,
            kind=self.kind,
            source_face=self.source_face,
            legibility=self.legibility,
            illegible_spans=[s.to_domain() for s in self.illegible_spans],
        )


class RecipeIngredientLineResponse(BaseModel):
    """Developer API compatible ingredient line specification."""
    original_text: str = Field(..., description="Verbatim text as transcribed from card face")
    quantity: Optional[str] = Field(default=None, description="Primary quantity or null if missing/to taste")
    quantity_max: Optional[str] = Field(default=None, description="Upper bound for quantity range or null")
    unit: Optional[str] = Field(default=None, description="Measurement unit or null")
    item: str = Field(default="", description="Ingredient name / item")
    preparation: Optional[str] = Field(default=None, description="Preparation note or null")
    source_face: int = Field(default=0, ge=0, description="0-indexed card face index")
    legibility: Legibility = Field(default=Legibility.CLEAR, description="Legibility status")
    illegible_spans: List[RecipeIllegibleSpanResponse] = Field(default_factory=list, description="Illegible spans")

    def to_domain(self) -> IngredientLine:
        return IngredientLine(
            original_text=self.original_text,
            quantity=self.quantity,
            quantity_max=self.quantity_max,
            unit=self.unit,
            item=self.item,
            preparation=self.preparation,
            source_face=self.source_face,
            legibility=self.legibility,
            illegible_spans=[s.to_domain() for s in self.illegible_spans],
        )


class RecipeStepResponse(BaseModel):
    """Developer API compatible instruction step."""
    original_text: str = Field(..., description="Verbatim instruction text from card")
    text: Optional[str] = Field(default=None, description="Instruction step text")
    source_face: int = Field(default=0, ge=0, description="0-indexed card face index")
    legibility: Legibility = Field(default=Legibility.CLEAR, description="Legibility status")
    illegible_spans: List[RecipeIllegibleSpanResponse] = Field(default_factory=list, description="Illegible spans")

    def to_domain(self) -> Step:
        return Step(
            original_text=self.original_text,
            text=self.text or self.original_text,
            source_face=self.source_face,
            legibility=self.legibility,
            illegible_spans=[s.to_domain() for s in self.illegible_spans],
        )


class RecipeSectionResponse(BaseModel):
    """Developer API compatible logical grouping of ingredients and steps."""
    name: Optional[str] = Field(default=None, description="Section heading, or null for main")
    ingredients: List[RecipeIngredientLineResponse] = Field(default_factory=list, description="Section ingredients")
    steps: List[RecipeStepResponse] = Field(default_factory=list, description="Section instructions")

    def to_domain(self) -> RecipeSection:
        return RecipeSection(
            name=self.name,
            ingredients=[i.to_domain() for i in self.ingredients],
            steps=[s.to_domain() for s in self.steps],
        )


class RecipeTimeValueResponse(BaseModel):
    """Developer API compatible duration representation."""
    original_text: str = Field(default="", description="Verbatim time string (e.g. '20 mins')")
    minutes: Optional[int] = Field(default=None, description="Total minutes duration")
    normalized_iso: Optional[str] = Field(default=None, description="ISO 8601 duration (e.g. 'PT20M')")

    def to_domain(self) -> TimeValue:
        return TimeValue(
            original_text=self.original_text,
            minutes=self.minutes,
            normalized_iso=self.normalized_iso,
        )


class RecipeYieldValueResponse(BaseModel):
    """Developer API compatible yield or serving size representation."""
    original_text: str = Field(default="", description="Verbatim yield text (e.g. '24 cookies')")
    normalized_servings: Optional[float] = Field(default=None, description="Normalized numeric servings count")
    unit: Optional[str] = Field(default=None, description="Unit name (e.g. 'servings', 'cookies', 'bars')")

    def to_domain(self) -> YieldValue:
        return YieldValue(
            original_text=self.original_text,
            normalized_servings=self.normalized_servings,
            unit=self.unit,
        )


class RecipeStructureResponse(BaseModel):
    """Gemini Developer API compatible structured response schema for recipe structuring."""

    title: str = Field(..., description="Primary recipe title")
    title_is_inferred: bool = Field(default=False, description="True if title was not explicitly written on card")
    description: Optional[str] = Field(default="", description="Recipe description, intro, or headnote")
    headnote: Optional[RecipeTextSpanResponse] = Field(default=None, description="Headnote, family story, or marginalia")
    notes: List[RecipeTextSpanResponse] = Field(default_factory=list, description="Narrative notes, margin notes, clipping ads")
    sections: List[RecipeSectionResponse] = Field(default_factory=list, description="Ordered sections of ingredients and steps")
    prep_time: Optional[RecipeTimeValueResponse] = Field(default=None, description="Preparation time")
    cook_time: Optional[RecipeTimeValueResponse] = Field(default=None, description="Cooking time")
    total_time: Optional[RecipeTimeValueResponse] = Field(default=None, description="Total time")
    yield_value: Optional[RecipeYieldValueResponse] = Field(default=None, description="Recipe yield or servings")
    tags: List[str] = Field(default_factory=list, description="Categorical tags")
    source_notes: Optional[str] = Field(default="", description="Attribution, handwritten card author, provenance")

    def to_recipe(self) -> Recipe:
        """Maps flat Developer-API-compatible response to authoritative Recipe Schema v2."""
        return Recipe(
            title=self.title,
            title_is_inferred=self.title_is_inferred,
            description=self.description or "",
            headnote=self.headnote.to_domain() if self.headnote else None,
            notes=[n.to_domain() for n in self.notes],
            sections=[s.to_domain() for s in self.sections],
            prep_time=self.prep_time.to_domain() if self.prep_time else TimeValue(original_text=""),
            cook_time=self.cook_time.to_domain() if self.cook_time else TimeValue(original_text=""),
            total_time=self.total_time.to_domain() if self.total_time else TimeValue(original_text=""),
            yield_value=self.yield_value.to_domain() if self.yield_value else YieldValue(original_text=""),
            tags=list(self.tags),
            source_notes=self.source_notes or "",
        )


__all__ = [
    "Legibility",
    "IllegibleSpan",
    "TextSpan",
    "IngredientLine",
    "Step",
    "RecipeSection",
    "TimeValue",
    "YieldValue",
    "Provenance",
    "RecipeWarning",
    "Recipe",
    "RecipeSchema",
    "RecipeIllegibleSpanResponse",
    "RecipeTextSpanResponse",
    "RecipeIngredientLineResponse",
    "RecipeStepResponse",
    "RecipeSectionResponse",
    "RecipeTimeValueResponse",
    "RecipeYieldValueResponse",
    "RecipeStructureResponse",
]

