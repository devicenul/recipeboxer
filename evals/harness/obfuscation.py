"""Evaluation deck preparation and filename obfuscation (WP-E3).

Sanitizes recipe card scans to opaque identifiers (e.g. `card_<token_hex>.jpg`),
strips embedded EXIF metadata, and maintains a strict bidirectional mapping between
opaque identifiers and ground-truth corpus image IDs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from PIL import Image

logger = logging.getLogger("evals.harness.obfuscation")


def sanitize_image_exif(source_path: Union[str, Path], destination_path: Union[str, Path]) -> None:
    """Copies an image to destination while stripping all EXIF metadata tags."""
    src = Path(source_path).resolve()
    dst = Path(destination_path).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as im:
        im_format = im.format or "JPEG"
        if im_format.upper() not in ("JPEG", "JPG", "PNG", "WEBP"):
            im_format = "JPEG"

        clean_im = Image.new(im.mode, im.size)
        clean_im.putdata(list(im.getdata()))

        save_kwargs: Dict[str, Any] = {}
        if im_format.upper() in ("JPEG", "JPG"):
            save_kwargs["quality"] = 95
            save_kwargs["optimize"] = False

        clean_im.save(dst, format=im_format, **save_kwargs)


@dataclass
class DeckMapping:
    """Maintains mapping between original image filenames and temporary opaque filenames."""
    deck_dir: Path
    orig_to_temp: Dict[str, str] = field(default_factory=dict)
    temp_to_orig: Dict[str, str] = field(default_factory=dict)

    def map_temp_to_orig(self, temp_name: str) -> str:
        """Resolves temporary file name back to original corpus filename."""
        clean = Path(temp_name).name
        if clean in self.temp_to_orig:
            return self.temp_to_orig[clean]
        stem = Path(clean).stem
        if stem in self.temp_to_orig:
            return self.temp_to_orig[stem]
        return clean

    def map_orig_to_temp(self, orig_name: str) -> str:
        """Resolves original corpus filename to temporary obfuscated filename."""
        clean = Path(orig_name).name
        if clean in self.orig_to_temp:
            return self.orig_to_temp[clean]
        stem = Path(clean).stem
        if stem in self.orig_to_temp:
            return self.orig_to_temp[stem]
        return clean


def prepare_obfuscated_deck(
    image_paths: Sequence[Union[str, Path]],
    target_dir: Union[str, Path],
    strip_exif: bool = False,
    seed: Optional[int] = None,
) -> DeckMapping:
    """Copies images into target directory with random names in random order.

    Args:
        image_paths: Sequence of source image file paths.
        target_dir: Destination temporary directory.
        strip_exif: Whether to re-encode and strip EXIF (default False, plain copy).
        seed: Optional RNG seed for deterministic shuffling.

    Returns:
        DeckMapping recording the bidirectional filename translation.
    """
    dst_dir = Path(target_dir).resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)

    items = [Path(p) for p in image_paths if Path(p).is_file()]
    rng = random.Random(seed)
    rng.shuffle(items)

    mapping = DeckMapping(deck_dir=dst_dir)
    used_names: set[str] = set()

    for orig_path in items:
        orig_name = orig_path.name
        ext = orig_path.suffix.lower() or ".jpg"

        while True:
            token = secrets.token_hex(6)
            temp_name = f"{token}{ext}"
            if temp_name not in used_names:
                used_names.add(temp_name)
                break

        dst_path = dst_dir / temp_name

        if strip_exif:
            sanitize_image_exif(orig_path, dst_path)
        else:
            shutil.copyfile(orig_path, dst_path)

        mapping.orig_to_temp[orig_name] = temp_name
        mapping.orig_to_temp[orig_path.stem] = Path(temp_name).stem
        mapping.temp_to_orig[temp_name] = orig_name
        mapping.temp_to_orig[Path(temp_name).stem] = orig_name

    return mapping
