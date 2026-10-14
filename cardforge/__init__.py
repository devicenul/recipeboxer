"""CardForge: Procedural synthetic recipe card generation engine.

Synthetic recipe card generator used by RecipeBoxer evals; intended to become its
own repository; must never be imported by the recipeboxer app.
"""

from __future__ import annotations

from cardforge.generator import (
    SyntheticCardGenerator,
    apply_coffee_ring,
    apply_date_stamp,
    apply_dog_ear,
    apply_edge_tear,
    apply_ink_fade,
    apply_oil_smudge,
    apply_rotation_skew,
    apply_tape_residue,
    create_paper_substrate,
    create_parser,
    generate_synthetic_corpus,
    get_font,
    main,
    _FONT_CACHE,
)
from cardforge.models import (
    CardModality,
    CardRole,
    DegradationConfig,
    DegradationLevel,
    GeneratedCard,
    InkType,
    PaperSubstrate,
    RecipeSchema,
)
from cardforge.templates_t0 import ARCHIVAL_RECIPE_TEMPLATES

__version__ = "0.1.0"

__all__ = [
    "CardModality",
    "PaperSubstrate",
    "DegradationLevel",
    "DegradationConfig",
    "GeneratedCard",
    "SyntheticCardGenerator",
    "generate_synthetic_corpus",
    "apply_coffee_ring",
    "apply_oil_smudge",
    "apply_ink_fade",
    "apply_dog_ear",
    "apply_edge_tear",
    "apply_date_stamp",
    "apply_tape_residue",
    "apply_rotation_skew",
    "create_paper_substrate",
    "get_font",
    "main",
    "create_parser",
    "CardRole",
    "InkType",
    "RecipeSchema",
    "ARCHIVAL_RECIPE_TEMPLATES",
    "__version__",
]
