"""RecipeBoxer utility tools and procedural generators."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from recipeboxer.tools.card_generator import (
        CardModality,
        DegradationConfig,
        DegradationLevel,
        GeneratedCard,
        PaperSubstrate,
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
        generate_synthetic_corpus,
        get_font,
    )

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
]


def __getattr__(name: str):
    if name in __all__:
        import recipeboxer.tools.card_generator as cg

        return getattr(cg, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
