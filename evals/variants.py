"""Dataset Naming and Encoding Variants for RecipeBoxer Evaluation Harness.

Part of WP-E2: Generates naming and encoding variants for evaluation corpora:
- 'opaque': Random hex names in random order.
- 'misleading': Names that actively lie (e.g. 'recipe_3_back.jpg' on a front face,
  swapped card numbers, wrong dish words from a fixed neutral word list unrelated to GT).
- 'shuffle': Camera-roll style 'IMG_0001..' in random order.
- 'reencode': Formats converted between JPEG, PNG, and WebP with quality jitter,
  EXIF stripped, pixels otherwise same.

Pure Pillow implementation, deterministic per seed, no recipeboxer imports.
"""

from __future__ import annotations

import os
import random
import shutil
from pathlib import Path
from typing import Dict, List, Set

from PIL import Image

SUPPORTED_VARIANTS = ("opaque", "misleading", "shuffle", "reencode")

# Lie tokens used for 'misleading' variant:
LIE_ROLE_TOKENS = (
    "back",
    "front",
    "side_b",
    "side_a",
    "part_2",
    "part_1",
    "card_2",
    "card_1",
    "page_2",
    "page_1",
    "continuation",
)

LIE_SWAPPED_NUMBERS = (
    "card_02_of_01",
    "part_03_of_01",
    "page_04_of_02",
    "card_99_of_01",
    "card_05_of_02",
)

LIE_DISH_WORDS = (
    "beef_stew",
    "chocolate_cake",
    "roast_chicken",
    "clam_chowder",
    "apple_pie",
    "lasagna",
    "banana_bread",
    "pork_chops",
    "chili_con_carne",
    "french_onion_soup",
    "waffles",
    "lemon_meringue",
    "guacamole",
    "meatballs",
    "mushroom_risotto",
    "tuna_casserole",
    "blueberry_pancakes",
    "split_pea_soup",
    "shepherds_pie",
    "pot_roast",
)

# Combined set of lie tokens for test verification and inspection
LIE_TOKENS: Set[str] = set(LIE_ROLE_TOKENS) | set(LIE_SWAPPED_NUMBERS) | set(LIE_DISH_WORDS)


def _detect_format(image_path: Path) -> str:
    """Detect image format from file extension or content."""
    ext = image_path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "JPEG"
    if ext == ".png":
        return "PNG"
    if ext == ".webp":
        return "WEBP"
    try:
        with Image.open(image_path) as im:
            fmt = (im.format or "").upper()
            if fmt in ("JPEG", "JPG"):
                return "JPEG"
            if fmt in ("PNG", "WEBP"):
                return fmt
    except Exception:
        pass
    return "JPEG"


def apply_variant(
    images: List[Path],
    variant: str,
    seed: int,
    out_dir: Path,
) -> Dict[str, str]:
    """Apply an evaluation naming/encoding variant to a list of corpus images.

    Parameters
    ----------
    images:
        List of Path objects pointing to original corpus images.
    variant:
        One of 'opaque', 'misleading', 'shuffle', 'reencode'.
    seed:
        Seed for pseudo-random number generator for deterministic outputs.
    out_dir:
        Output directory where transformed images will be written.

    Returns
    -------
    Dict[str, str]
        Mapping from new_filename -> original_corpus_image_id (i.e. img.name).
        Guaranteed to be a bijection.
    """
    if variant not in SUPPORTED_VARIANTS:
        raise ValueError(
            f"Unknown variant '{variant}'. Supported variants are: {list(SUPPORTED_VARIANTS)}"
        )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not images:
        return {}

    # Sort images deterministically to ensure reproducibility across platforms
    sorted_images = sorted(images, key=lambda p: (p.name, str(p)))
    rng = random.Random(seed)

    mapping: Dict[str, str] = {}
    used_names: Set[str] = set()

    if variant == "opaque":
        # Random hex names, random order
        shuffled = list(sorted_images)
        rng.shuffle(shuffled)
        for img_path in shuffled:
            ext = img_path.suffix.lower() or ".jpg"
            while True:
                hex_name = f"{rng.getrandbits(48):012x}{ext}"
                if hex_name not in used_names:
                    used_names.add(hex_name)
                    break
            dest_file = out_dir / hex_name
            shutil.copy2(img_path, dest_file)
            mapping[hex_name] = img_path.name

    elif variant == "shuffle":
        # Camera-roll style IMG_0001.. in random order
        shuffled = list(sorted_images)
        rng.shuffle(shuffled)
        for idx, img_path in enumerate(shuffled, start=1):
            ext = img_path.suffix.lower() or ".jpg"
            new_name = f"IMG_{idx:04d}{ext}"
            used_names.add(new_name)
            dest_file = out_dir / new_name
            shutil.copy2(img_path, dest_file)
            mapping[new_name] = img_path.name

    elif variant == "misleading":
        # Names that actively lie: front labeled back, swapped numbers, wrong dishes
        shuffled = list(sorted_images)
        rng.shuffle(shuffled)
        for img_path in shuffled:
            ext = img_path.suffix.lower() or ".jpg"
            dish = rng.choice(LIE_DISH_WORDS)
            role = rng.choice(LIE_ROLE_TOKENS)
            swap = rng.choice(LIE_SWAPPED_NUMBERS)
            recipe_num = rng.randint(1, 99)
            while True:
                salt = f"{rng.getrandbits(24):06x}"
                # Combine lie tokens into misleading name
                # E.g. 'recipe_3_back_beef_stew_card_02_of_01_a1b2c3.jpg'
                new_name = f"recipe_{recipe_num}_{role}_{dish}_{swap}_{salt}{ext}"
                if new_name not in used_names:
                    used_names.add(new_name)
                    break
            dest_file = out_dir / new_name
            shutil.copy2(img_path, dest_file)
            mapping[new_name] = img_path.name

    elif variant == "reencode":
        # JPEG <-> PNG <-> WebP with quality jitter, EXIF stripped, pixels otherwise same
        format_exts = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
        for img_path in sorted_images:
            current_fmt = _detect_format(img_path)
            candidates = [fmt for fmt in ("JPEG", "PNG", "WEBP") if fmt != current_fmt]
            target_fmt = rng.choice(candidates)
            target_ext = format_exts[target_fmt]

            stem = img_path.stem
            candidate_name = f"{stem}{target_ext}"
            if candidate_name in used_names:
                salt = f"_{rng.getrandbits(16):04x}"
                candidate_name = f"{stem}{salt}{target_ext}"
            used_names.add(candidate_name)

            dest_file = out_dir / candidate_name
            with Image.open(img_path) as im:
                orig_size = im.size
                # Convert color mode if needed (JPEG does not support RGBA/alpha)
                out_im = im
                if target_fmt == "JPEG":
                    if im.mode in ("RGBA", "LA", "P"):
                        out_im = im.convert("RGB")
                    elif im.mode != "RGB":
                        out_im = im.convert("RGB")
                elif target_fmt == "WEBP":
                    if im.mode not in ("RGB", "RGBA"):
                        out_im = im.convert("RGB")

                # EXIF is stripped by default because no exif param is passed to save()
                if target_fmt == "JPEG":
                    q = rng.randint(70, 95)
                    out_im.save(dest_file, format="JPEG", quality=q)
                elif target_fmt == "WEBP":
                    q = rng.randint(70, 95)
                    out_im.save(dest_file, format="WEBP", quality=q)
                elif target_fmt == "PNG":
                    comp = rng.randint(1, 9)
                    out_im.save(dest_file, format="PNG", compress_level=comp)

            # Sanity check dimensions preserved
            with Image.open(dest_file) as written_im:
                if written_im.size != orig_size:
                    raise RuntimeError(
                        f"Dimension mismatch after re-encoding {img_path}: "
                        f"{written_im.size} != {orig_size}"
                    )

            mapping[candidate_name] = img_path.name

    return mapping
