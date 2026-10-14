"""Paprika Recipe Manager (.paprikarecipes) Exporter for RecipeBoxer.

Serializes RecipeSchema models into standard Paprika JSON format and compresses into
.paprikarecipes gzip archives with base64-encoded archival card photos.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import logging
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger(__name__)


class PaprikaExporter:
    """Serializes RecipeSchema collections to Paprika .paprikarecipes gzip format."""

    def __init__(self, encoding: str = "utf-8", include_transcription_notes: bool = False) -> None:
        self.encoding = encoding
        self.include_transcription_notes = include_transcription_notes

    def recipe_to_paprika_dict(
        self,
        recipe: RecipeSchema,
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
        include_transcription_notes: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Converts a single RecipeSchema into Paprika JSON dictionary format."""
        inc_notes = self.include_transcription_notes if include_transcription_notes is None else include_transcription_notes
        uid = f"{uuid.uuid4()}".upper()
        now_str = recipe.created_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Paprika expects ingredients and directions as newline-separated strings
        all_ings = recipe.all_ingredients() if hasattr(recipe, "all_ingredients") else []
        if all_ings:
            ingredients_str = "\n".join(ing.display_text for ing in all_ings if ing.display_text)
        else:
            ingredients_str = "\n".join(recipe.ingredients) if recipe.ingredients else ""

        all_steps = recipe.all_steps() if hasattr(recipe, "all_steps") else []
        if all_steps:
            directions_str = "\n".join(step.display_text for step in all_steps if step.display_text)
        else:
            directions_str = "\n".join(recipe.directions) if recipe.directions else ""

        prep_str = recipe.prep_time.display_text if (hasattr(recipe, "prep_time") and recipe.prep_time) else (getattr(recipe, "prep_time_str", "") or "")
        cook_str = recipe.cook_time.display_text if (hasattr(recipe, "cook_time") and recipe.cook_time) else (getattr(recipe, "cook_time_str", "") or "")
        total_str = recipe.total_time.display_text if (hasattr(recipe, "total_time") and recipe.total_time) else (getattr(recipe, "total_time_str", "") or "")
        servings_str = recipe.yield_value.display_text if (hasattr(recipe, "yield_value") and recipe.yield_value) else (recipe.yield_servings or "")

        notes_parts: List[str] = []
        if recipe.source_notes:
            notes_parts.append(recipe.source_notes)
        if hasattr(recipe, "headnote") and recipe.headnote and recipe.headnote.text:
            notes_parts.append(recipe.headnote.text)

        if inc_notes:
            notes = getattr(recipe, "notes", [])
            for n in notes:
                if hasattr(n, "text") and n.text:
                    notes_parts.append(f"[Note]: {n.text}")
            warnings = getattr(recipe, "warnings", [])
            for w in warnings:
                notes_parts.append(f"[Warning {w.code}]: {w.message}")

        notes_str = "\n".join(notes_parts)

        paprika_data: Dict[str, Any] = {
            "uid": uid,
            "name": recipe.title,
            "description": recipe.description or "",
            "ingredients": ingredients_str,
            "directions": directions_str,
            "prep_time": prep_str,
            "cook_time": cook_str,
            "total_time": total_str,
            "servings": servings_str,
            "categories": list(recipe.tags) if recipe.tags else [],
            "notes": notes_str,
            "source": "RecipeBoxer Scanned Card",
            "source_url": "",
            "difficulty": "",
            "rating": 0,
            "scale": None,
            "created": now_str,
            "photo": None,
            "photo_data": None,
            "photo_large": None,
            "photo_url": recipe.image_urls[0] if recipe.image_urls else None,
            "photo_hash": None,
            "hash": None,
            "in_trash": None,
            "is_pinned": None,
            "on_favorites": 0,
            "on_grocery_list": None,
        }

        # Resolve primary card scan photo for Paprika
        if image_resolver and recipe.card_ids:
            for cid in recipe.card_ids:
                resolved = image_resolver(cid)
                if resolved is None:
                    continue

                img_bytes: Optional[bytes] = None
                if isinstance(resolved, (bytes, bytearray)):
                    img_bytes = bytes(resolved)
                elif isinstance(resolved, (str, Path)):
                    p = Path(resolved)
                    if p.exists() and p.is_file():
                        img_bytes = p.read_bytes()

                if img_bytes:
                    b64_str = base64.b64encode(img_bytes).decode("ascii")
                    img_hash = hashlib.sha256(img_bytes).hexdigest()
                    paprika_data["photo"] = f"{cid}.jpg"
                    paprika_data["photo_data"] = b64_str
                    paprika_data["photo_large"] = b64_str
                    paprika_data["photo_hash"] = img_hash
                    break

        if len(recipe.card_ids) > 1:
            extra_notes = f"\nArchival Cards: {', '.join(recipe.card_ids)}"
            if paprika_data["notes"]:
                paprika_data["notes"] = f"{paprika_data['notes']}{extra_notes}"
            else:
                paprika_data["notes"] = extra_notes.strip()

        # Compute payload hash
        content_for_hash = json.dumps(paprika_data, sort_keys=True).encode("utf-8")
        paprika_data["hash"] = hashlib.sha256(content_for_hash).hexdigest()

        return paprika_data

    def export_to_paprika_bytes(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
        as_zip_archive: bool = False,
        include_transcription_notes: Optional[bool] = None,
    ) -> bytes:
        """Serializes recipes into .paprikarecipes gzip/zip archive bytes."""
        recipe_list = [recipes] if isinstance(recipes, RecipeSchema) else list(recipes)
        paprika_dicts = [
            self.recipe_to_paprika_dict(
                r,
                image_resolver=image_resolver,
                include_transcription_notes=include_transcription_notes,
            )
            for r in recipe_list
        ]

        if as_zip_archive and len(paprika_dicts) >= 1:
            # Package as ZIP archive of gzipped .paprikarecipe files
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for idx, r_dict in enumerate(paprika_dicts, start=1):
                    raw_json = json.dumps(r_dict, ensure_ascii=False).encode(self.encoding)
                    gz_recipe = gzip.compress(raw_json)
                    safe_name = "".join(c if c.isalnum() else "_" for c in r_dict["name"]).strip("_")
                    zf.writestr(f"{safe_name}_{idx}.paprikarecipe", gz_recipe)
            return zip_buf.getvalue()

        # Standard canonical .paprikarecipes: gzipped JSON payload
        payload = paprika_dicts[0] if len(paprika_dicts) == 1 else paprika_dicts
        raw_json = json.dumps(payload, indent=2, ensure_ascii=False).encode(self.encoding)
        return gzip.compress(raw_json)

    def export_to_file(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        output_path: Union[str, Path],
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
        as_zip_archive: bool = False,
        include_transcription_notes: Optional[bool] = None,
    ) -> Path:
        """Writes .paprikarecipes gzip archive to file on disk."""
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        archive_bytes = self.export_to_paprika_bytes(
            recipes,
            image_resolver=image_resolver,
            as_zip_archive=as_zip_archive,
            include_transcription_notes=include_transcription_notes,
        )
        path.write_bytes(archive_bytes)
        count = 1 if isinstance(recipes, RecipeSchema) else len(recipes)
        logger.info(f"Exported {count} recipe(s) to Paprika archive: {path}")
        return path

    @staticmethod
    def decompress_paprika_archive(archive_bytes: bytes) -> List[Dict[str, Any]]:
        """Decompresses and parses a .paprikarecipes archive (supports both gzip and zip formats)."""
        # Try gzip decompression first
        try:
            decompressed = gzip.decompress(archive_bytes)
            data = json.loads(decompressed.decode("utf-8"))
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return [data]
        except Exception:
            pass

        # Try zip archive decompression
        try:
            zip_buf = io.BytesIO(archive_bytes)
            results: List[Dict[str, Any]] = []
            with zipfile.ZipFile(zip_buf, mode="r") as zf:
                for name in zf.namelist():
                    file_data = zf.read(name)
                    # Each inner file may be gzipped
                    try:
                        inner_data = gzip.decompress(file_data)
                        parsed = json.loads(inner_data.decode("utf-8"))
                        results.append(parsed)
                    except Exception:
                        parsed = json.loads(file_data.decode("utf-8"))
                        results.append(parsed)
            return results
        except Exception as exc:
            raise ValueError(f"Failed to decompress .paprikarecipes archive: {exc}") from exc


def export_recipes_to_paprika(
    recipes: Union[RecipeSchema, List[RecipeSchema]],
    image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    as_zip_archive: bool = False,
    include_transcription_notes: bool = False,
) -> bytes:
    """Convenience helper to export recipes into .paprikarecipes gzip archive bytes."""
    return PaprikaExporter(include_transcription_notes=include_transcription_notes).export_to_paprika_bytes(
        recipes,
        image_resolver=image_resolver,
        as_zip_archive=as_zip_archive,
    )
