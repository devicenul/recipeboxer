"""Obsidian Markdown Exporter with YAML frontmatter for RecipeBoxer.

Generates Markdown documents with YAML frontmatter structured for Obsidian Recipe View plugins,
GitHub Flavored Markdown (GFM), and culinary note-taking vaults.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
import yaml

from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger(__name__)


class MarkdownExporter:
    """Serializes RecipeSchema models into Obsidian-compatible Markdown with YAML frontmatter."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self.encoding = encoding

    def recipe_to_frontmatter_dict(self, recipe: RecipeSchema) -> Dict[str, Any]:
        """Builds YAML frontmatter dictionary conforming to Obsidian recipe standards."""
        fm: Dict[str, Any] = {
            "title": recipe.title,
        }

        if recipe.description:
            fm["description"] = recipe.description

        if recipe.tags:
            fm["tags"] = list(recipe.tags)

        if recipe.prep_time:
            fm["prep_time"] = recipe.prep_time

        if recipe.cook_time:
            fm["cook_time"] = recipe.cook_time

        if recipe.total_time:
            fm["total_time"] = recipe.total_time

        if recipe.yield_servings:
            fm["yield"] = recipe.yield_servings

        if recipe.source_notes:
            fm["source"] = recipe.source_notes

        if recipe.card_ids:
            fm["card_ids"] = list(recipe.card_ids)

        if recipe.created_at:
            fm["date_created"] = recipe.created_at

        return fm

    def export_single_recipe(
        self,
        recipe: RecipeSchema,
        use_wikilinks: bool = True,
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    ) -> str:
        """Serializes a single RecipeSchema into an Obsidian Markdown document."""
        fm_dict = self.recipe_to_frontmatter_dict(recipe)
        fm_yaml = yaml.dump(
            fm_dict,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ).strip()

        lines: List[str] = [
            "---",
            fm_yaml,
            "---",
            "",
            f"# {recipe.title}",
            "",
        ]

        if recipe.description:
            lines.append(f"> {recipe.description}")
            lines.append("")

        # Metadata summary
        meta_parts = []
        if recipe.yield_servings:
            meta_parts.append(f"**Yield:** {recipe.yield_servings}")
        if recipe.prep_time:
            meta_parts.append(f"**Prep Time:** {recipe.prep_time}")
        if recipe.cook_time:
            meta_parts.append(f"**Cook Time:** {recipe.cook_time}")
        if recipe.total_time:
            meta_parts.append(f"**Total Time:** {recipe.total_time}")

        if meta_parts:
            lines.append(" | ".join(meta_parts))
            lines.append("")

        # Ingredients
        if recipe.ingredients:
            lines.append("## Ingredients")
            lines.append("")
            for ing in recipe.ingredients:
                lines.append(f"- {ing}")
            lines.append("")

        # Directions
        if recipe.directions:
            lines.append("## Directions")
            lines.append("")
            for step in recipe.directions:
                lines.append(step)
            lines.append("")

        # Source / Notes
        if recipe.source_notes:
            lines.append("## Notes & Source")
            lines.append("")
            lines.append(recipe.source_notes)
            lines.append("")

        # Card Scans
        card_scans: List[str] = []
        if image_resolver:
            for cid in recipe.card_ids:
                res = image_resolver(cid)
                if res:
                    card_scans.append(str(res))
        if not card_scans and recipe.card_ids:
            card_scans.extend(recipe.card_ids)

        if card_scans:
            lines.append("## Archival Card Scans")
            lines.append("")
            for scan_ref in card_scans:
                fname = Path(scan_ref).name if "/" in scan_ref or "\\" in scan_ref else scan_ref
                if not fname.lower().endswith((".jpg", ".png", ".jpeg", ".webp")):
                    fname = f"{fname}.jpg"

                if use_wikilinks:
                    lines.append(f"![[{fname}]]")
                else:
                    lines.append(f"![Archival Card Scan]({scan_ref})")
            lines.append("")

        return "\n".join(lines)

    def export_to_markdown_string(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        use_wikilinks: bool = True,
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    ) -> str:
        """Serializes single recipe or list of recipes into formatted Markdown string."""
        recipe_list = [recipes] if isinstance(recipes, RecipeSchema) else list(recipes)
        if not recipe_list:
            return ""

        if len(recipe_list) == 1:
            return self.export_single_recipe(
                recipe_list[0],
                use_wikilinks=use_wikilinks,
                image_resolver=image_resolver,
            )

        docs = [
            self.export_single_recipe(
                r,
                use_wikilinks=use_wikilinks,
                image_resolver=image_resolver,
            )
            for r in recipe_list
        ]
        return "\n\n---\n\n".join(docs) + "\n"

    def export_to_file(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        output_path: Union[str, Path],
        use_wikilinks: bool = True,
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    ) -> Path:
        """Writes Obsidian Markdown to file on disk."""
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self.export_to_markdown_string(
            recipes,
            use_wikilinks=use_wikilinks,
            image_resolver=image_resolver,
        )
        path.write_text(content, encoding=self.encoding)
        count = 1 if isinstance(recipes, RecipeSchema) else len(recipes)
        logger.info(f"Exported {count} recipe(s) to Markdown: {path}")
        return path

    def export_to_bytes(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        use_wikilinks: bool = True,
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    ) -> bytes:
        """Serializes recipes into UTF-8 encoded bytes."""
        return self.export_to_markdown_string(
            recipes,
            use_wikilinks=use_wikilinks,
            image_resolver=image_resolver,
        ).encode(self.encoding)


def export_recipes_to_markdown(
    recipes: Union[RecipeSchema, List[RecipeSchema]],
    use_wikilinks: bool = True,
    image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
) -> str:
    """Convenience helper to export recipes as Obsidian Markdown string."""
    return MarkdownExporter().export_to_markdown_string(
        recipes,
        use_wikilinks=use_wikilinks,
        image_resolver=image_resolver,
    )
