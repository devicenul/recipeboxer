"""Tests for WP-E2: evals/variants.py.

Verifies:
- apply_variant mapping is a bijection for all supported variants
- 'misleading' names contain required lie tokens
- 'reencode' keeps original dimensions and strips EXIF
- Determinism per seed across all variants
- Different seeds produce different outputs
- Unsupported variants raise ValueError
- No recipeboxer imports
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import List

import pytest
from PIL import ExifTags, Image

import evals.variants as variants
from evals.variants import LIE_DISH_WORDS, LIE_ROLE_TOKENS, LIE_SWAPPED_NUMBERS, LIE_TOKENS, apply_variant


def _create_sample_images(tmp_dir: Path, count: int = 6) -> List[Path]:
    """Helper to create dummy images of various formats and dimensions."""
    images = []
    formats = [("JPEG", ".jpg"), ("PNG", ".png"), ("WEBP", ".webp")]
    for idx in range(count):
        fmt, ext = formats[idx % len(formats)]
        w, h = 100 + idx * 20, 150 + idx * 10
        # Varied colors
        color = ((idx * 40) % 256, (idx * 80) % 256, (idx * 120) % 256)
        im = Image.new("RGB", (w, h), color=color)

        # Add dummy EXIF tags
        exif = im.getexif()
        exif[ExifTags.Base.Artist] = f"Photographer {idx}"
        exif[ExifTags.Base.ImageDescription] = f"Sample Card {idx}"

        p = tmp_dir / f"card_{idx:02d}{ext}"
        if fmt == "JPEG":
            im.save(p, format="JPEG", exif=exif)
        elif fmt == "PNG":
            im.save(p, format="PNG")
        elif fmt == "WEBP":
            im.save(p, format="WEBP")
        images.append(p)
    return images


def test_variants_bijection_all_types(tmp_path: Path):
    """Mapping returned by apply_variant must be a strict bijection."""
    img_dir = tmp_path / "src_images"
    img_dir.mkdir()
    images = _create_sample_images(img_dir, count=8)
    orig_names = {p.name for p in images}

    for variant in ("opaque", "misleading", "shuffle", "reencode"):
        out_dir = tmp_path / f"out_{variant}"
        mapping = apply_variant(images, variant=variant, seed=42, out_dir=out_dir)

        # 1. Total count matches
        assert len(mapping) == len(images), f"Count mismatch for variant {variant}"

        # 2. All original image names are mapped (surjection onto orig_names)
        assert set(mapping.values()) == orig_names, f"Value set mismatch for variant {variant}"

        # 3. All generated keys are distinct (injection)
        assert len(set(mapping.keys())) == len(images), f"Duplicate new_names in variant {variant}"

        # 4. All generated files exist in out_dir and are non-empty
        for new_name, orig_id in mapping.items():
            out_file = out_dir / new_name
            assert out_file.exists(), f"File {new_name} was not written"
            assert out_file.stat().st_size > 0, f"File {new_name} is empty"


def test_misleading_names_contain_lie_tokens(tmp_path: Path):
    """Misleading variant must actively lie using neutral dish words, role tokens, and swapped numbers."""
    img_dir = tmp_path / "src_images"
    img_dir.mkdir()
    images = _create_sample_images(img_dir, count=10)

    out_dir = tmp_path / "out_misleading"
    mapping = apply_variant(images, variant="misleading", seed=101, out_dir=out_dir)

    assert len(mapping) == 10
    for new_name in mapping.keys():
        # Name should contain at least one dish word
        has_dish = any(dish in new_name for dish in LIE_DISH_WORDS)
        # Name should contain at least one role lie token
        has_role = any(role in new_name for role in LIE_ROLE_TOKENS)
        # Name should contain at least one swapped number pattern
        has_swap = any(swap in new_name for swap in LIE_SWAPPED_NUMBERS)

        assert has_dish, f"Misleading name '{new_name}' lacks a neutral dish token"
        assert has_role, f"Misleading name '{new_name}' lacks a role lie token"
        assert has_swap, f"Misleading name '{new_name}' lacks a swapped number token"
        assert any(token in new_name for token in LIE_TOKENS)


def test_reencode_keeps_dimensions_and_strips_exif(tmp_path: Path):
    """Reencode variant must preserve image dimensions and strip EXIF metadata."""
    img_dir = tmp_path / "src_images"
    img_dir.mkdir()

    # Create images with specific dimensions and EXIF
    images = []
    sizes = [(320, 240), (640, 480), (800, 600), (1024, 768)]
    for idx, (w, h) in enumerate(sizes):
        p = img_dir / f"orig_{idx}.jpg"
        im = Image.new("RGB", (w, h), color=(100, 150, 200))
        exif = im.getexif()
        exif[ExifTags.Base.Artist] = "Secret Author"
        exif[ExifTags.Base.Software] = "RecipeBoxer v1"
        im.save(p, format="JPEG", exif=exif)
        images.append(p)

    out_dir = tmp_path / "out_reencode"
    mapping = apply_variant(images, variant="reencode", seed=42, out_dir=out_dir)

    for new_name, orig_name in mapping.items():
        out_file = out_dir / new_name
        orig_file = img_dir / orig_name

        with Image.open(orig_file) as orig_im, Image.open(out_file) as new_im:
            # 1. Dimensions strictly preserved
            assert new_im.size == orig_im.size, (
                f"Dimension changed for {orig_name} -> {new_name}: {orig_im.size} vs {new_im.size}"
            )
            # 2. EXIF metadata stripped
            new_exif = new_im.getexif()
            assert ExifTags.Base.Artist not in new_exif
            assert ExifTags.Base.Software not in new_exif


def test_shuffle_camera_roll_names(tmp_path: Path):
    """Shuffle variant produces IMG_0001.. format in random order."""
    img_dir = tmp_path / "src_images"
    img_dir.mkdir()
    images = _create_sample_images(img_dir, count=5)

    out_dir = tmp_path / "out_shuffle"
    mapping = apply_variant(images, variant="shuffle", seed=77, out_dir=out_dir)

    expected_prefixes = {f"IMG_{i:04d}" for i in range(1, 6)}
    actual_prefixes = {p.split(".")[0] for p in mapping.keys()}
    assert actual_prefixes == expected_prefixes


def test_determinism_same_seed(tmp_path: Path):
    """Identical seed must yield identical mappings and byte-for-byte identical files."""
    img_dir = tmp_path / "src_images"
    img_dir.mkdir()
    images = _create_sample_images(img_dir, count=4)

    for variant in ("opaque", "misleading", "shuffle", "reencode"):
        dir1 = tmp_path / f"det_{variant}_1"
        dir2 = tmp_path / f"det_{variant}_2"

        m1 = apply_variant(images, variant=variant, seed=12345, out_dir=dir1)
        m2 = apply_variant(images, variant=variant, seed=12345, out_dir=dir2)

        assert m1 == m2, f"Mapping mismatch under same seed for variant {variant}"

        for fname in m1.keys():
            b1 = (dir1 / fname).read_bytes()
            b2 = (dir2 / fname).read_bytes()
            assert hashlib.sha256(b1).hexdigest() == hashlib.sha256(b2).hexdigest(), (
                f"File content differed for {fname} under variant {variant}"
            )


def test_determinism_different_seed(tmp_path: Path):
    """Different seeds should generate different permutations or names."""
    img_dir = tmp_path / "src_images"
    img_dir.mkdir()
    images = _create_sample_images(img_dir, count=6)

    dir1 = tmp_path / "seed1"
    dir2 = tmp_path / "seed2"

    m1 = apply_variant(images, variant="opaque", seed=111, out_dir=dir1)
    m2 = apply_variant(images, variant="opaque", seed=999, out_dir=dir2)

    # Generated hex names will differ with high probability
    assert set(m1.keys()) != set(m2.keys())


def test_unknown_variant_raises_value_error(tmp_path: Path):
    """Unknown variant name must raise ValueError."""
    img_dir = tmp_path / "src_images"
    img_dir.mkdir()
    images = _create_sample_images(img_dir, count=2)

    with pytest.raises(ValueError, match="Unknown variant"):
        apply_variant(images, variant="unsupported_variant", seed=1, out_dir=tmp_path / "out")


def test_empty_images_list(tmp_path: Path):
    """Empty image list should cleanly return empty mapping."""
    res = apply_variant([], variant="opaque", seed=1, out_dir=tmp_path / "out")
    assert res == {}


def test_no_recipeboxer_imports():
    """evals.variants must never import from recipeboxer.*."""
    import sys

    # Check imported modules inside evals.variants
    for mod_name in sys.modules:
        if mod_name.startswith("evals.variants"):
            mod = sys.modules[mod_name]
            for attr in dir(mod):
                val = getattr(mod, attr)
                if hasattr(val, "__module__") and str(val.__module__).startswith("recipeboxer"):
                    pytest.fail(f"evals.variants imports from recipeboxer: {attr} -> {val.__module__}")
