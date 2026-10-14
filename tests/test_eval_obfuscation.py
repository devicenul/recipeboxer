"""Unit test suite for evals.harness.obfuscation.

Verifies:
1. EXIF metadata sanitization and stripping on scanned card images.
2. Deck obfuscation (randomized opaque filenames, permutation, EXIF stripping).
3. Bidirectional mapping between original corpus identifiers and temporary opaque filenames.
"""

from __future__ import annotations

from pathlib import Path
from PIL import Image

import pytest

from evals.harness.obfuscation import (
    DeckMapping,
    prepare_obfuscated_deck,
    sanitize_image_exif,
)


class TestExifSanitization:
    """Verifies that EXIF metadata is stripped from images."""

    def test_sanitize_image_exif_strips_metadata(self, tmp_path: Path) -> None:
        src = tmp_path / "with_exif.jpg"
        dst = tmp_path / "clean.jpg"

        # Create an image with simulated EXIF
        im = Image.new("RGB", (120, 80), color=(220, 200, 180))
        exif = im.getexif()
        exif[0x010E] = "Secret Recipe: Apple Crisp 1948"  # ImageDescription
        exif[0x0132] = "1948:10:15 12:00:00"              # DateTime
        exif[0x0131] = "Secret Curation Software v1"     # Software
        im.save(src, format="JPEG", exif=exif)

        # Confirm source has EXIF
        with Image.open(src) as raw_im:
            src_exif = raw_im.getexif()
            assert 0x010E in src_exif or len(src_exif) > 0

        # Sanitize
        sanitize_image_exif(src, dst)

        assert dst.exists()
        with Image.open(dst) as clean_im:
            assert clean_im.size == (120, 80)
            clean_exif = clean_im.getexif()
            assert 0x010E not in clean_exif
            assert len(clean_exif) == 0


class TestDeckObfuscation:
    """Verifies deck obfuscation behavior."""

    def test_prepare_obfuscated_deck_opaque(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "corpus"
        src_dir.mkdir()
        cards = [
            src_dir / "rec_vhc_apple_crisp_1948_front.jpg",
            src_dir / "rec_vhc_apple_crisp_1948_back.jpg",
            src_dir / "rec_clp_crisco_chicken_front.jpg",
        ]
        for c in cards:
            im = Image.new("RGB", (50, 50), color=(100, 100, 100))
            im.save(c)

        dst_dir = tmp_path / "obfuscated"
        mapping = prepare_obfuscated_deck(cards, dst_dir, strip_exif=True)

        # Must have same number of files
        obf_files = list(dst_dir.glob("*.jpg"))
        assert len(obf_files) == len(cards)

        # None of original filenames or tokens should appear in destination
        for f in obf_files:
            assert "apple_crisp" not in f.name
            assert "vhc" not in f.name
            assert "crisco" not in f.name
            assert "1948" not in f.name

        # Mapping must contain all originals
        for c in cards:
            temp_name = mapping.map_orig_to_temp(c.name)
            assert temp_name != c.name
            assert (dst_dir / temp_name).exists()
            # Reverse mapping must resolve back to original
            assert mapping.map_temp_to_orig(temp_name) == c.name

    def test_deck_mapping_stem_lookup(self, tmp_path: Path) -> None:
        mapping = DeckMapping(
            deck_dir=tmp_path,
            orig_to_temp={"card_front.jpg": "a1b2c3.jpg", "card_front": "a1b2c3"},
            temp_to_orig={"a1b2c3.jpg": "card_front.jpg", "a1b2c3": "card_front.jpg"},
        )
        assert mapping.map_temp_to_orig("a1b2c3.jpg") == "card_front.jpg"
        assert mapping.map_temp_to_orig("a1b2c3") == "card_front.jpg"
        assert mapping.map_orig_to_temp("card_front.jpg") == "a1b2c3.jpg"
        assert mapping.map_orig_to_temp("card_front") == "a1b2c3"
        # Unknown fallback returns as-is
        assert mapping.map_temp_to_orig("unknown.jpg") == "unknown.jpg"
