"""Configurable Multi-Format Recipe Ordering and Categorical Grouping for RecipeBoxer.

Provides GroupingPreset enums, CuratedSection and CuratedCookbook Pydantic models,
and the curate_recipes engine supporting custom drag-and-drop reordering,
course taxonomy matching, contributor normalization, tag clustering, and alphabetical grouping.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union
from pydantic import BaseModel, Field

from recipeboxer.models.recipe import RecipeSchema


class GroupingPreset(str, Enum):
    NONE = "none"
    COURSE = "course"
    CONTRIBUTOR = "contributor"
    TAGS = "tags"
    ALPHABETICAL = "alphabetical"
    CUSTOM = "custom"


class CuratedSection(BaseModel):
    """A logical grouped section of recipes in a curated cookbook."""
    section_id: str = Field(..., description="Unique section key e.g. 'course_desserts_sweets'")
    title: str = Field(..., description="Display title for section divider and TOC e.g. 'Desserts & Sweets'")
    subtitle: Optional[str] = Field(None, description="Optional subtitle or recipe count e.g. '3 Recipes'")
    description: Optional[str] = Field(None, description="Optional section commentary or historical intro")
    recipes: List[RecipeSchema] = Field(default_factory=list, description="Ordered recipes belonging to this section")


class CuratedCookbook(BaseModel):
    """Authoritative structured cookbook containing ordered sections and rendering configuration."""
    title: str = Field("RecipeBoxer Archival Cookbook", description="Overall cookbook title")
    grouping_preset: GroupingPreset = Field(GroupingPreset.NONE, description="Active grouping preset")
    sections: List[CuratedSection] = Field(default_factory=list, description="Ordered sections with recipes")
    include_section_dividers: bool = Field(True, description="Whether to emit divider pages")
    include_toc: bool = Field(True, description="Whether to emit Table of Contents")
    toc_title: str = Field("Table of Contents", description="Title heading for TOC")

    @property
    def all_recipes(self) -> List[RecipeSchema]:
        """Flattened list of all recipes in curated sequence."""
        res: List[RecipeSchema] = []
        for s in self.sections:
            res.extend(s.recipes)
        return res

    @property
    def total_recipe_count(self) -> int:
        """Total number of recipes across all sections."""
        return sum(len(s.recipes) for s in self.sections)


# Standard culinary course taxonomy in canonical dinner order
COURSE_TAXONOMY: List[Tuple[str, str, Set[str]]] = [
    (
        "course_appetizers",
        "Appetizers & Starters",
        {
            "appetizer", "appetizers", "starter", "starters", "dip", "dips", "snack", "snacks",
            "canapé", "canape", "canapes", "spread", "spreads", "finger food", "finger foods",
            "hors d'oeuvre", "hors doeuvre", "hors d'oeuvres", "amuse-bouche", "bruschetta",
            "crostini", "salsa", "guacamole", "nachos", "tapas", "pate", "paté"
        },
    ),
    (
        "course_soups_salads",
        "Soups & Salads",
        {
            "soup", "soups", "stew", "stews", "chili", "chowder", "chowders", "bisque",
            "broth", "broths", "salad", "salads", "slaw", "slaws", "dressing", "dressings",
            "gazpacho", "gumbo", "vinaigrette"
        },
    ),
    (
        "course_main_dishes",
        "Main Dishes",
        {
            "main", "mains", "main dish", "main dishes", "entree", "entrees", "dinner", "dinners",
            "lunch", "beef", "chicken", "pork", "poultry", "fish", "seafood", "pasta",
            "casserole", "casseroles", "roast", "steak", "steaks", "curry", "curries",
            "pizza", "burger", "burgers", "taco", "tacos", "salmon", "turkey",
            "lamb", "meatball", "meatballs", "lasagna", "stir fry", "stir-fry",
            "ribs", "shrimp", "pot roast", "meatloaf"
        },
    ),
    (
        "course_side_dishes",
        "Side Dishes",
        {
            "side", "sides", "side dish", "side dishes", "vegetable", "vegetables",
            "potato", "potatoes", "rice", "grain", "grains", "stuffing",
            "beans", "corn", "squash", "cabbage", "asparagus", "green beans",
            "mashed potatoes", "gratin"
        },
    ),
    (
        "course_breads_baking",
        "Breads & Baking",
        {
            "bread", "breads", "roll", "rolls", "biscuit", "biscuits", "muffin",
            "muffins", "scone", "scones", "yeast", "sourdough", "loaf", "loaves",
            "focaccia", "bagel", "bagels", "cornbread", "dough", "crust",
            "pancake", "pancakes", "waffle", "waffles"
        },
    ),
    (
        "course_desserts_sweets",
        "Desserts & Sweets",
        {
            "dessert", "desserts", "cake", "cakes", "pie", "pies", "cookie",
            "cookies", "bar", "bars", "brownie", "brownies", "candy", "candies",
            "pastry", "pastries", "tart", "tarts", "pudding", "puddings",
            "ice cream", "sweet", "sweets", "fudge", "cobbler", "cobblers",
            "crisp", "crisps", "frosting", "icing", "cheesecake", "truffle",
            "truffles", "cupcake", "cupcakes", "parfait", "sorbet", "custard"
        },
    ),
    (
        "course_beverages_drinks",
        "Beverages & Drinks",
        {
            "drink", "drinks", "beverage", "beverages", "cocktail", "cocktails",
            "punch", "punches", "tea", "teas", "coffee", "smoothie", "smoothies",
            "cider", "lemonade", "mocktail", "mocktails", "juice", "juices",
            "spritz", "shake", "shakes", "latte", "cocoa"
        },
    ),
]


def _normalize_title_for_sorting(title: str) -> str:
    """Strips leading English articles ('The ', 'A ', 'An ') and whitespace for clean alphabetical sorting."""
    t = (title or "").strip()
    return re.sub(r"^(the|a|an)\s+", "", t, flags=re.IGNORECASE).strip()


def _extract_contributor_name(source_notes: Optional[str]) -> str:
    """Extracts a clean, normalized contributor or family attribution from source notes."""
    if not source_notes or not source_notes.strip():
        return "Family Collection / Unattributed"

    raw = source_notes.strip()

    # Check for possessive box/kitchen notes like "Grandma Clara's Sunday Suppers", "Mom's Kitchen", "Aunt Marie's recipe"
    possessive_match = re.search(r"^([A-Za-z0-9\s]+?)'s(?:\s+|$)", raw, re.IGNORECASE)
    if possessive_match:
        name = possessive_match.group(1).strip()
        if name and len(name) >= 2:
            return name

    # Strip prefixes like "From the kitchen of", "From:", "Submitted by:", "Source:", "Courtesy of:"
    cleaned = re.sub(
        r"^(from\s+the\s+kitchen\s+of|from\s*:?|source\s*:?|submitted\s+by\s*:?|handed\s+down\s+by\s*:?|courtesy\s+of\s*:?|recipe\s+from\s*:?)\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()

    # Remove trailing parenthetical dates or notes like "(1952)" or "(handed down)"
    cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()
    # Remove trailing quotation marks or periods
    cleaned = re.sub(r'[\.\"\']+$', '', cleaned).strip()

    if not cleaned:
        return "Family Collection / Unattributed"

    # Truncate overly long free-form notes to first clause/sentence
    if len(cleaned) > 50:
        first_clause = re.split(r"[,;—\-\.]", cleaned)[0].strip()
        if first_clause and len(first_clause) >= 3:
            return first_clause

    return cleaned


def _reorder_recipes(
    recipes: Sequence[RecipeSchema],
    recipe_order: Optional[Union[List[str], str]],
) -> List[RecipeSchema]:
    """Reorders recipes according to an explicit sequence of recipe IDs."""
    recipe_list = list(recipes)
    if not recipe_order:
        return recipe_list

    if isinstance(recipe_order, str):
        order_ids = [s.strip() for s in recipe_order.split(",") if s.strip()]
    else:
        order_ids = [str(s).strip() for s in recipe_order if str(s).strip()]

    if not order_ids:
        return recipe_list

    id_to_recipe = {r.recipe_id: r for r in recipe_list}
    ordered: List[RecipeSchema] = []
    seen: Set[str] = set()

    for r_id in order_ids:
        if r_id in id_to_recipe and r_id not in seen:
            ordered.append(id_to_recipe[r_id])
            seen.add(r_id)

    # Append any remaining recipes not listed in recipe_order in their original sequence
    for r in recipe_list:
        if r.recipe_id not in seen:
            ordered.append(r)
            seen.add(r.recipe_id)

    return ordered


def _match_course_category(recipe: RecipeSchema) -> Tuple[str, str]:
    """Determines the course category for a recipe based on tags and title keywords."""
    # 1. Check tags
    recipe_tags_lower = [t.lower().strip() for t in recipe.tags]
    for sec_id, sec_title, keywords in COURSE_TAXONOMY:
        for t in recipe_tags_lower:
            if t in keywords:
                return sec_id, sec_title

    # 2. Check title keywords
    title_words = set(re.findall(r"[a-zA-Z\u00C0-\u017F]+", recipe.title.lower()))
    for sec_id, sec_title, keywords in COURSE_TAXONOMY:
        if title_words.intersection(keywords):
            return sec_id, sec_title

    # 3. Fallback check for multi-word keywords in title (e.g. "ice cream", "finger food", "pot roast")
    title_lower = recipe.title.lower()
    for sec_id, sec_title, keywords in COURSE_TAXONOMY:
        for kw in keywords:
            if " " in kw or "-" in kw:
                if kw in title_lower or kw.replace("-", " ") in title_lower:
                    return sec_id, sec_title

    # Fallback to Other Recipes
    return "course_other", "Other Recipes"


def curate_recipes(
    recipes: Sequence[RecipeSchema],
    recipe_order: Optional[Union[List[str], str]] = None,
    grouping_preset: Union[GroupingPreset, str] = GroupingPreset.NONE,
    include_section_dividers: bool = True,
    include_toc: bool = True,
    toc_title: str = "Table of Contents",
    cookbook_title: str = "RecipeBoxer Archival Cookbook",
) -> CuratedCookbook:
    """Curates, reorders, and groups recipes into structured sections for multi-format export.

    Args:
        recipes: Collection of RecipeSchema models.
        recipe_order: Optional list or comma-separated string of recipe_ids.
        grouping_preset: Preset algorithm ('none', 'course', 'contributor', 'tags', 'alphabetical', 'custom').
        include_section_dividers: Whether section divider pages should be generated.
        include_toc: Whether Table of Contents should be generated.
        toc_title: Title string for Table of Contents.
        cookbook_title: Overall title for cookbook output.

    Returns:
        CuratedCookbook model populated with ordered sections and configuration.
    """
    if isinstance(grouping_preset, str):
        preset_enum = GroupingPreset(grouping_preset.strip().lower())
    elif isinstance(grouping_preset, GroupingPreset):
        preset_enum = grouping_preset
    else:
        preset_enum = GroupingPreset.NONE

    # Step 1: Reorder recipes if custom order provided
    ordered_recipes = _reorder_recipes(recipes, recipe_order)

    sections: List[CuratedSection] = []

    if not ordered_recipes:
        return CuratedCookbook(
            title=cookbook_title,
            grouping_preset=preset_enum,
            sections=[],
            include_section_dividers=include_section_dividers,
            include_toc=include_toc,
            toc_title=toc_title,
        )

    if preset_enum in (GroupingPreset.NONE, GroupingPreset.CUSTOM):
        count = len(ordered_recipes)
        sub = f"{count} Recipe" if count == 1 else f"{count} Recipes"
        sections.append(
            CuratedSection(
                section_id="section_all",
                title="All Recipes",
                subtitle=sub,
                recipes=ordered_recipes,
            )
        )

    elif preset_enum == GroupingPreset.COURSE:
        # Group according to canonical COURSE_TAXONOMY
        buckets: Dict[str, List[RecipeSchema]] = {sec_id: [] for sec_id, _, _ in COURSE_TAXONOMY}
        buckets["course_other"] = []
        id_to_title = {sec_id: title for sec_id, title, _ in COURSE_TAXONOMY}
        id_to_title["course_other"] = "Other Recipes"

        for r in ordered_recipes:
            sec_id, _ = _match_course_category(r)
            buckets[sec_id].append(r)

        # Build sections in canonical order (omitting empty sections)
        canonical_order = [sec_id for sec_id, _, _ in COURSE_TAXONOMY] + ["course_other"]
        for sec_id in canonical_order:
            sec_recs = buckets.get(sec_id, [])
            if sec_recs:
                count = len(sec_recs)
                sub = f"{count} Recipe" if count == 1 else f"{count} Recipes"
                sections.append(
                    CuratedSection(
                        section_id=sec_id,
                        title=id_to_title[sec_id],
                        subtitle=sub,
                        recipes=sec_recs,
                    )
                )

    elif preset_enum == GroupingPreset.CONTRIBUTOR:
        contributor_buckets: Dict[str, List[RecipeSchema]] = {}
        for r in ordered_recipes:
            contributor = _extract_contributor_name(r.source_notes)
            contributor_buckets.setdefault(contributor, []).append(r)

        # Sort contributors alphabetically, placing "Family Collection / Unattributed" at end
        unattributed_key = "Family Collection / Unattributed"
        sorted_contributors = sorted(
            [c for c in contributor_buckets.keys() if c != unattributed_key],
            key=lambda c: c.lower(),
        )
        if unattributed_key in contributor_buckets:
            sorted_contributors.append(unattributed_key)

        for c_name in sorted_contributors:
            sec_recs = contributor_buckets[c_name]
            sec_id = f"contrib_{re.sub(r'[^a-zA-Z0-9]+', '_', c_name).strip('_').lower()}" or "contrib_general"
            count = len(sec_recs)
            sub = f"{count} Recipe" if count == 1 else f"{count} Recipes"
            sections.append(
                CuratedSection(
                    section_id=sec_id,
                    title=c_name,
                    subtitle=sub,
                    recipes=sec_recs,
                )
            )

    elif preset_enum == GroupingPreset.TAGS:
        tag_buckets: Dict[str, List[RecipeSchema]] = {}
        untagged_key = "General / Untagged"

        for r in ordered_recipes:
            if r.tags and r.tags[0].strip():
                # Primary tag normalized
                raw_tag = r.tags[0].strip()
                norm_tag = raw_tag.title() if raw_tag.islower() or raw_tag.isupper() else raw_tag
                tag_buckets.setdefault(norm_tag, []).append(r)
            else:
                tag_buckets.setdefault(untagged_key, []).append(r)

        sorted_tags = sorted(
            [t for t in tag_buckets.keys() if t != untagged_key],
            key=lambda t: t.lower(),
        )
        if untagged_key in tag_buckets:
            sorted_tags.append(untagged_key)

        for t_name in sorted_tags:
            sec_recs = tag_buckets[t_name]
            sec_id = f"tag_{re.sub(r'[^a-zA-Z0-9]+', '_', t_name).strip('_').lower()}" or "tag_general"
            count = len(sec_recs)
            sub = f"{count} Recipe" if count == 1 else f"{count} Recipes"
            sections.append(
                CuratedSection(
                    section_id=sec_id,
                    title=t_name,
                    subtitle=sub,
                    recipes=sec_recs,
                )
            )

    elif preset_enum == GroupingPreset.ALPHABETICAL:
        # Sort recipes A-Z by normalized title
        alpha_sorted = sorted(
            ordered_recipes,
            key=lambda r: _normalize_title_for_sorting(r.title).lower(),
        )

        letter_buckets: Dict[str, List[RecipeSchema]] = {}
        for r in alpha_sorted:
            norm_t = _normalize_title_for_sorting(r.title)
            first_char = norm_t[0].upper() if norm_t else "#"
            if not first_char.isalpha():
                first_char = "#"
            letter_buckets.setdefault(first_char, []).append(r)

        sorted_letters = sorted(
            [letter for letter in letter_buckets.keys() if letter != "#"]
        )
        if "#" in letter_buckets:
            sorted_letters.append("#")

        for letter in sorted_letters:
            sec_recs = letter_buckets[letter]
            sec_id = f"alpha_{letter.lower() if letter.isalpha() else 'num'}"
            count = len(sec_recs)
            sub = f"{count} Recipe" if count == 1 else f"{count} Recipes"
            sections.append(
                CuratedSection(
                    section_id=sec_id,
                    title=letter,
                    subtitle=sub,
                    recipes=sec_recs,
                )
            )

    return CuratedCookbook(
        title=cookbook_title,
        grouping_preset=preset_enum,
        sections=sections,
        include_section_dividers=include_section_dividers,
        include_toc=include_toc,
        toc_title=toc_title,
    )
