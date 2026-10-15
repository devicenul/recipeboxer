"""CardForge: Procedural synthetic recipe card generation engine.

Synthetic recipe card generator used by RecipeBoxer evals; intended to become its
own repository; must never be imported by the recipeboxer app.
"""

from __future__ import annotations

from cardforge.generator import (
    MissingGlyphError,
    SyntheticCardGenerator,
    adapt_text_for_font,
    apply_coffee_ring,
    apply_crease,
    apply_date_stamp,
    apply_dog_ear,
    apply_edge_tear,
    apply_glare,
    apply_ink_fade,
    apply_jpeg_noise,
    apply_oil_smudge,
    apply_perspective_warp,
    apply_rotation_skew,
    apply_show_through,
    apply_tape_residue,
    create_paper_substrate,
    create_parser,
    draw_text_with_fallback,
    generate_synthetic_corpus,
    get_font,
    get_font_cmap,
    main,
    validate_font_glyphs,
    _FONT_CACHE,
)
from cardforge.models import (
    CardModality,
    CardRole,
    CardStock,
    CardWriter,
    DegradationConfig,
    DegradationLevel,
    GeneratedCard,
    InkType,
    PaperSubstrate,
    RecipeSchema,
    RulingStyle,
)
from cardforge.templates_t0 import ARCHIVAL_RECIPE_TEMPLATES

__version__ = "0.1.0"

__all__ = [
    "CardModality",
    "PaperSubstrate",
    "RulingStyle",
    "CardStock",
    "CardWriter",
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
    "apply_crease",
    "apply_glare",
    "apply_perspective_warp",
    "apply_jpeg_noise",
    "apply_show_through",
    "create_paper_substrate",
    "get_font",
    "get_font_cmap",
    "MissingGlyphError",
    "validate_font_glyphs",
    "adapt_text_for_font",
    "draw_text_with_fallback",
    "main",
    "create_parser",
    "CardRole",
    "InkType",
    "RecipeSchema",
    "ARCHIVAL_RECIPE_TEMPLATES",
    "__version__",
]
