"""Specialized Antigravity analysis skills and tools for RecipeBoxer."""

from recipeboxer.agent.skills.clipping import (
    filter_advertisement_noise,
    get_print_clipping_prompt,
    is_promotional_line,
)
from recipeboxer.agent.skills.handwriting import (
    get_vintage_handwriting_prompt,
    normalize_archaic_units,
    normalize_fractions,
)
from recipeboxer.agent.skills.tools import (
    ALL_AGENT_TOOLS,
    check_stain_symmetry_tool,
    classify_card_modality_tool,
    crop_card_borders_tool,
    deskew_card_image_tool,
    enhance_card_readability_tool,
    extract_paper_palette_tool,
)

__all__ = [
    "normalize_fractions",
    "normalize_archaic_units",
    "get_vintage_handwriting_prompt",
    "is_promotional_line",
    "filter_advertisement_noise",
    "get_print_clipping_prompt",
    "ALL_AGENT_TOOLS",
    "deskew_card_image_tool",
    "crop_card_borders_tool",
    "enhance_card_readability_tool",
    "extract_paper_palette_tool",
    "check_stain_symmetry_tool",
    "classify_card_modality_tool",
]
