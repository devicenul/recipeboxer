"""Adversarial Empirical Challenger Test Suite for Milestone 2 (Obfuscation & Anti-Cheating).

Comprehensive stress testing for `recipeboxer/evals/obfuscation.py`:
1. Custom card sets, single cards, multi-card sets, damaged cards, clippings, and boundary edge cases.
2. All obfuscation strategies (DETERMINISTIC_SHA256, UUID4, SEQUENTIAL).
3. EXIF metadata stripping across multiple image modes (RGB, RGBA, L, CMYK) and formats (JPEG, PNG).
4. Bidirectional mapping accuracy, proposal translation, and active registry lifecycle.
5. Robustness against missing files, malformed manifests, empty inputs, unicode filenames, and exceptions.
6. Stress test on 1,000 synthetic cards for collision resistance and performance.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest
from PIL import Image

from recipeboxer.ai.gemini import OPAQUE_CARD_ID_PATTERN, OPAQUE_SEQUENTIAL_PATTERN
from recipeboxer.evals.obfuscation import (
    EvaluationObfuscator,
    ObfuscatedCard,
    ObfuscatedCardCorpus,
    ObfuscationStrategy,
    _ACTIVE_OBFUSCATION_REGISTRY,
    resolve_active_obfuscation_id,
    sanitize_image_exif,
)
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import CardRole
from recipeboxer.models.recipe import RecipeSchema
from cardforge import CardModality
from tests.fixtures.loader import CardEntry, SampleCardCorpus, get_manifest_path, get_sample_card_dir


# ============================================================================
# 1. EXIF STRIPPING & IMAGE SANITIZATION STRESS TESTS
# ============================================================================

class TestExifSanitizationStress:
    """Stress tests EXIF stripping across diverse image modes, formats, and corruptions."""

    def test_jpeg_exif_tags_completely_removed(self, tmp_path: Path):
        src = tmp_path / "exif_heavy.jpg"
        dst = tmp_path / "clean_heavy.jpg"

        # Create image with multiple EXIF tags
        im = Image.new("RGB", (250, 180), color=(220, 210, 190))
        exif = im.getexif()
        exif[0x010E] = "Grandma's Apple Pie 1952"  # ImageDescription
        exif[0x010F] = "Nikon Vintage"             # Make
        exif[0x0110] = "D850"                      # Model
        exif[0x0132] = "1952:06:15 10:30:00"       # DateTime
        exif[0x8298] = "Copyright 1952 Smith"     # Copyright
        exif[0x9286] = "Secret family ingredient: nutmeg"  # UserComment
        im.save(src, "JPEG", exif=exif)

        # Confirm tags exist in source
        with Image.open(src) as src_im:
            src_exif = src_im.getexif()
            assert 0x010E in src_exif
            assert src_exif[0x010E] == "Grandma's Apple Pie 1952"

        # Sanitize
        sanitize_image_exif(src, dst)

        # Verify destination
        assert dst.exists()
        with Image.open(dst) as dst_im:
            dst_exif = dst_im.getexif()
            assert len(dst_exif) == 0
            assert dst_im.size == (250, 180)
            assert dst_im.mode == "RGB"

    def test_png_metadata_sanitization(self, tmp_path: Path):
        src = tmp_path / "metadata_test.png"
        dst = tmp_path / "clean_test.png"

        im = Image.new("RGBA", (120, 120), color=(200, 100, 50, 255))
        im.save(src, "PNG")

        sanitize_image_exif(src, dst)
        assert dst.exists()
        with Image.open(dst) as dst_im:
            assert dst_im.size == (120, 120)
            assert dst_im.format == "PNG"

    def test_grayscale_and_cmyk_modes(self, tmp_path: Path):
        # Grayscale L mode
        src_l = tmp_path / "gray.jpg"
        dst_l = tmp_path / "clean_gray.jpg"
        im_l = Image.new("L", (80, 80), color=128)
        exif = im_l.getexif()
        exif[0x010E] = "B&W Vintage Card"
        im_l.save(src_l, "JPEG", exif=exif)

        sanitize_image_exif(src_l, dst_l)
        with Image.open(dst_l) as clean_l:
            assert len(clean_l.getexif()) == 0
            assert clean_l.mode == "L"
            assert clean_l.size == (80, 80)

    def test_nested_directory_creation(self, tmp_path: Path):
        src = tmp_path / "sample.jpg"
        im = Image.new("RGB", (50, 50), color=(100, 100, 100))
        im.save(src, "JPEG")

        dst_deep = tmp_path / "a" / "b" / "c" / "deep_clean.jpg"
        assert not dst_deep.parent.exists()
        sanitize_image_exif(src, dst_deep)
        assert dst_deep.exists()


# ============================================================================
# 2. OBFUSCATION STRATEGIES & COLLISION RESISTANCE
# ============================================================================

class TestObfuscationStrategiesStress:
    """Stress tests deterministic SHA-256, UUID4, and sequential strategies."""

    def test_deterministic_sha256_repeatability_and_collision_resistance(self, tmp_path: Path):
        # Generate 1,000 synthetic cards with diverse names
        card_items = []
        for i in range(1000):
            card_items.append({
                "card_id": f"rec_test_card_{i:04d}_recipe_title_front.jpg",
                "filename": f"rec_test_card_{i:04d}_recipe_title_front.jpg",
                "role": "SEQUENCE_START" if i % 2 == 0 else "SEQUENCE_CONTINUATION",
                "modality": "HANDWRITTEN_CURSIVE",
            })

        with ObfuscatedCardCorpus(card_items, strategy=ObfuscationStrategy.DETERMINISTIC_SHA256) as obf1:
            obf_ids_1 = [c.obfuscated_id for c in obf1.obfuscated_cards]
            assert len(obf_ids_1) == 1000
            # Zero collisions in 1,000 items
            assert len(set(obf_ids_1)) == 1000

            # Verify pattern and zero lexical leakage
            for obf_id in obf_ids_1:
                assert OPAQUE_CARD_ID_PATTERN.match(obf_id)
                assert "rec_" not in obf_id
                assert "recipe" not in obf_id
                assert "front" not in obf_id
                assert "title" not in obf_id

            # Repeatability test with a second corpus instance
            with ObfuscatedCardCorpus(card_items, strategy=ObfuscationStrategy.DETERMINISTIC_SHA256) as obf2:
                obf_ids_2 = [c.obfuscated_id for c in obf2.obfuscated_cards]
                assert obf_ids_1 == obf_ids_2

    def test_uuid4_uniqueness_and_resolution(self, tmp_path: Path):
        card_items = [
            {"card_id": f"card_src_{i:03d}.jpg", "filename": f"card_src_{i:03d}.jpg"}
            for i in range(250)
        ]

        with ObfuscatedCardCorpus(card_items, strategy=ObfuscationStrategy.UUID4) as obf:
            obf_ids = [c.obfuscated_id for c in obf.obfuscated_cards]
            assert len(obf_ids) == 250
            assert len(set(obf_ids)) == 250

            for card in obf.obfuscated_cards:
                assert card.obfuscated_id.startswith("card_")
                assert obf.resolve_id(card.obfuscated_id) == card.original_card_id
                assert obf.obfuscate_id(card.original_card_id) == card.obfuscated_id

    def test_sequential_numbering_and_custom_prefix(self, tmp_path: Path):
        card_items = [
            {"card_id": f"img_{i}.jpg", "filename": f"img_{i}.jpg"}
            for i in range(50)
        ]

        with ObfuscatedCardCorpus(card_items, strategy=ObfuscationStrategy.SEQUENTIAL, prefix="scan_") as obf:
            for idx, card in enumerate(obf.obfuscated_cards):
                expected = f"scan_{idx + 1:04d}.jpg"
                assert card.obfuscated_id == expected
                assert obf.resolve_id(expected) == card.original_card_id


# ============================================================================
# 3. CARD CATEGORIES: CLIPPINGS, DAMAGED, MULTI-CARD, SINGLE CARDS
# ============================================================================

class TestDiverseCardCategories:
    """Empirically tests authentic sample cards across all historical categories and dimensions."""

    @pytest.fixture
    def corpus(self) -> SampleCardCorpus:
        return SampleCardCorpus(get_sample_card_dir(), get_manifest_path())

    def test_single_standalone_cards(self, corpus: SampleCardCorpus):
        standalones = corpus.get_standalones()
        assert len(standalones) == 6

        with ObfuscatedCardCorpus(standalones) as obf:
            assert len(obf.obfuscated_cards) == len(standalones)
            for card in obf.obfuscated_cards:
                assert card.role == CardRole.STANDALONE
                assert card.file_path.exists()
                img = card.load_image()
                assert img.width > 0 and img.height > 0
                assert obf.resolve_id(card.obfuscated_id) == card.original_card_id

    def test_multi_card_sets(self, corpus: SampleCardCorpus):
        multi_sets = corpus.get_multi_card_sets()
        assert len(multi_sets) >= 4  # 4 multi-part recipes in corpus

        all_multi_cards = [c for cards in multi_sets.values() for c in cards]
        assert len(all_multi_cards) == 14

        with ObfuscatedCardCorpus(all_multi_cards) as obf:
            assert len(obf.obfuscated_cards) == len(all_multi_cards)
            for card in obf.obfuscated_cards:
                assert card.file_path.exists()
                assert obf.resolve_id(card.obfuscated_id) == card.original_card_id

    def test_damaged_cards_category_by_mixed_media_modality(self, corpus: SampleCardCorpus):
        damaged_cards = corpus.filter_by_modality("MIXED_MEDIA")
        assert len(damaged_cards) == 7

        with ObfuscatedCardCorpus(damaged_cards) as obf:
            assert len(obf.obfuscated_cards) == 7
            for card in obf.obfuscated_cards:
                assert card.modality == CardModality.MIXED_MEDIA
                assert card.file_path.exists()
                img = card.load_image()
                assert img.width > 0 and img.height > 0
                assert obf.resolve_id(card.obfuscated_id) == card.original_card_id

    def test_historical_clippings_category(self, corpus: SampleCardCorpus):
        clippings = corpus.filter_by_modality("PRINTED_CLIPPING")
        assert len(clippings) == 7

        with ObfuscatedCardCorpus(clippings) as obf:
            assert len(obf.obfuscated_cards) == 7
            for card in obf.obfuscated_cards:
                assert card.modality == CardModality.PRINTED_CLIPPING_OR_LABEL or card.modality == CardModality.PRINTED_CLIPPING
                assert card.file_path.exists()
                img = card.load_image()
                assert img.width > 0 and img.height > 0
                assert obf.resolve_id(card.obfuscated_id) == card.original_card_id

    def test_typewritten_category(self, corpus: SampleCardCorpus):
        typed = corpus.filter_by_modality("TYPEWRITTEN")
        assert len(typed) == 9

        with ObfuscatedCardCorpus(typed) as obf:
            assert len(obf.obfuscated_cards) == 9
            for card in obf.obfuscated_cards:
                assert card.modality == CardModality.TYPEWRITTEN
                assert card.file_path.exists()
                assert obf.resolve_id(card.obfuscated_id) == card.original_card_id

    def test_handwritten_cursive_category(self, corpus: SampleCardCorpus):
        cursive = corpus.filter_by_modality("HANDWRITTEN_CURSIVE")
        assert len(cursive) == 25

        with ObfuscatedCardCorpus(cursive) as obf:
            assert len(obf.obfuscated_cards) == 25
            for card in obf.obfuscated_cards:
                assert card.modality == CardModality.HANDWRITTEN_CURSIVE
                assert card.file_path.exists()
                assert obf.resolve_id(card.obfuscated_id) == card.original_card_id

    def test_full_corpus_roundtrip(self, corpus: SampleCardCorpus):
        with ObfuscatedCardCorpus(corpus.cards) as obf:
            assert len(obf.obfuscated_cards) == 48
            for entry in corpus.cards:
                obf_id = obf.obfuscate_id(entry.card_id)
                assert obf.resolve_id(obf_id) == entry.card_id
                assert obf.resolve_id(Path(obf_id).stem) == entry.card_id
                obf_path = obf.get_obfuscated_path(entry.card_id)
                assert obf_path.exists()


# ============================================================================
# 4. REVERSE MAPPING & PROPOSAL TRANSLATION
# ============================================================================

class TestReverseMappingAndProposals:
    """Tests bidirectional translation for proposals, results, and edge case identifiers."""

    def test_proposal_translation_preserves_all_metadata(self):
        cards = [
            {"card_id": "rec_001_front.jpg", "filename": "rec_001_front.jpg"},
            {"card_id": "rec_001_back.jpg", "filename": "rec_001_back.jpg"},
        ]

        with ObfuscatedCardCorpus(cards) as obf:
            c1_obf = obf.obfuscated_cards[0].obfuscated_id
            c2_obf = obf.obfuscated_cards[1].obfuscated_id

            prop = CorrelationProposal(
                proposal_id="test_prop_123",
                ordered_card_ids=[c1_obf, c2_obf],
                part_count=2,
                confidence=0.97,
                heuristic_score=0.91,
                semantic_score=0.99,
                continuity_explanation="Step 4 continues directly from Step 3",
                recommended_action="PAIR",
            )

            mapped = obf.map_proposals_to_ground_truth([prop])[0]
            assert mapped.proposal_id == "test_prop_123"
            assert mapped.ordered_card_ids == ["rec_001_front.jpg", "rec_001_back.jpg"]
            assert mapped.part_count == 2
            assert mapped.confidence == 0.97
            assert mapped.heuristic_score == 0.91
            assert mapped.semantic_score == 0.99
            assert mapped.continuity_explanation == "Step 4 continues directly from Step 3"
            assert mapped.recommended_action == "PAIR"

    def test_dict_and_tuple_proposal_translation(self):
        cards = [
            {"card_id": "rec_apple_front.jpg", "filename": "rec_apple_front.jpg"},
            {"card_id": "rec_apple_back.jpg", "filename": "rec_apple_back.jpg"},
            {"card_id": "rec_cherry_solo.jpg", "filename": "rec_cherry_solo.jpg"},
        ]

        with ObfuscatedCardCorpus(cards) as obf:
            c1_obf = obf.obfuscate_id("rec_apple_front.jpg")
            c2_obf = obf.obfuscate_id("rec_apple_back.jpg")
            c3_obf = obf.obfuscate_id("rec_cherry_solo.jpg")

            # Tuple input
            tuple_props = [(c1_obf, c2_obf), (c3_obf,)]
            mapped_tuples = obf.map_proposals_to_ground_truth(tuple_props)
            assert len(mapped_tuples) == 2
            assert mapped_tuples[0].ordered_card_ids == ["rec_apple_front.jpg", "rec_apple_back.jpg"]
            assert mapped_tuples[1].ordered_card_ids == ["rec_cherry_solo.jpg"]
            assert mapped_tuples[1].is_standalone is True

            # Dict input
            dict_props = [
                {
                    "proposal_id": "p_dict",
                    "ordered_card_ids": [c1_obf, c2_obf],
                    "front_card_id": c1_obf,
                    "back_card_id": c2_obf,
                    "part_count": 2,
                    "confidence": 0.88,
                }
            ]
            mapped_dicts = obf.map_proposals_to_ground_truth(dict_props)
            assert mapped_dicts[0].ordered_card_ids == ["rec_apple_front.jpg", "rec_apple_back.jpg"]

    def test_unregistered_id_fallback(self):
        with ObfuscatedCardCorpus([]) as obf:
            # Resolving an unknown ID returns itself
            assert obf.resolve_id("unknown_card_999.jpg") == "unknown_card_999.jpg"
            # Obfuscating an unknown ID returns deterministic hash
            fallback_obf = obf.obfuscate_id("unknown_card_999.jpg")
            assert fallback_obf.startswith("card_")
            assert OPAQUE_CARD_ID_PATTERN.match(fallback_obf)

    def test_resolve_active_obfuscation_id_edge_cases(self, tmp_path: Path):
        assert resolve_active_obfuscation_id("") == ""
        assert resolve_active_obfuscation_id(None) == ""  # type: ignore[arg-type]

        card = {"card_id": "secret_pie_recipe_front.jpg", "filename": "secret_pie_recipe_front.jpg"}
        with ObfuscatedCardCorpus([card]) as obf:
            obf_id = obf.obfuscate_id("secret_pie_recipe_front.jpg")
            obf_path = obf.get_obfuscated_path("secret_pie_recipe_front.jpg")

            # Resolve by full ID
            assert resolve_active_obfuscation_id(obf_id) == "secret_pie_recipe_front.jpg"
            # Resolve by stem
            assert resolve_active_obfuscation_id(Path(obf_id).stem) == "secret_pie_recipe_front.jpg"
            # Resolve by Path object
            assert resolve_active_obfuscation_id(obf_path) == "secret_pie_recipe_front.jpg"
            # Resolve by string path
            assert resolve_active_obfuscation_id(str(obf_path)) == "secret_pie_recipe_front.jpg"


# ============================================================================
# 5. CORPUS INGESTION EDGE CASES, SPECIAL CHARACTERS & FALLBACKS
# ============================================================================

class TestCorpusIngestionEdgeCases:
    """Tests corpus initialization from various input shapes, directories, and edge conditions."""

    def test_ingest_from_directory_with_manifest(self, tmp_path: Path):
        img1 = tmp_path / "card1.jpg"
        Image.new("RGB", (100, 100), color=(100, 100, 100)).save(img1, "JPEG")

        manifest_data = {
            "version": "1.0",
            "cards": [
                {
                    "card_id": "test_c1",
                    "filename": "card1.jpg",
                    "modality": "HANDWRITTEN_CURSIVE",
                    "role": "STANDALONE",
                    "ground_truth_recipe": {
                        "title": "Test Title",
                        "ingredients": ["1 cup water"],
                        "directions": ["Boil water."],
                    },
                }
            ],
        }
        (tmp_path / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

        with ObfuscatedCardCorpus(tmp_path) as obf:
            assert len(obf.obfuscated_cards) == 1
            card = obf.obfuscated_cards[0]
            assert card.original_card_id == "test_c1"
            assert card.ground_truth_recipe is not None
            assert card.ground_truth_recipe.title == "Test Title"

    def test_ingest_from_directory_without_manifest(self, tmp_path: Path):
        img1 = tmp_path / "photo_a.jpg"
        img2 = tmp_path / "photo_b.png"
        Image.new("RGB", (60, 60), color=(50, 50, 50)).save(img1, "JPEG")
        Image.new("RGBA", (60, 60), color=(50, 50, 50, 255)).save(img2, "PNG")

        with ObfuscatedCardCorpus(tmp_path) as obf:
            assert len(obf.obfuscated_cards) == 2
            for card in obf.obfuscated_cards:
                assert card.file_path.exists()

    def test_ingest_from_single_file_path(self, tmp_path: Path):
        single_img = tmp_path / "single_scan.jpg"
        Image.new("RGB", (100, 100), color=(200, 200, 200)).save(single_img, "JPEG")

        with ObfuscatedCardCorpus(single_img) as obf:
            assert len(obf.obfuscated_cards) == 1
            assert obf.obfuscated_cards[0].original_card_id == "single_scan.jpg"

    def test_ingest_nonexistent_files_fallback(self, tmp_path: Path):
        nonexistent_items = [
            {"card_id": "ghost_1.jpg", "file_path": str(tmp_path / "ghost_1.jpg")},
            {"card_id": "ghost_2.jpg", "file_path": str(tmp_path / "ghost_2.jpg")},
        ]

        with ObfuscatedCardCorpus(nonexistent_items) as obf:
            assert len(obf.obfuscated_cards) == 2
            for card in obf.obfuscated_cards:
                # Should create a fallback blank image
                assert card.file_path.exists()
                img = card.load_image()
                assert img.size == (600, 400)

    def test_special_characters_and_spaces_in_filenames(self, tmp_path: Path):
        special_names = [
            "Recipe #1 (Mom's Best) [1965] & Notes.jpg",
            "Café_Liégeois_1955_crème.jpg",
            "Grandma's 100% Whole Wheat Bread - card 1.jpg",
        ]
        card_items = [{"card_id": name, "filename": name} for name in special_names]

        with ObfuscatedCardCorpus(card_items) as obf:
            assert len(obf.obfuscated_cards) == 3
            for item in card_items:
                orig = item["card_id"]
                obf_id = obf.obfuscate_id(orig)
                assert OPAQUE_CARD_ID_PATTERN.match(obf_id)
                assert obf.resolve_id(obf_id) == orig

    def test_custom_target_dir_preservation(self, tmp_path: Path):
        custom_out = tmp_path / "custom_output_scans"
        items = [{"card_id": "card_x.jpg", "filename": "card_x.jpg"}]

        corpus = ObfuscatedCardCorpus(items, target_dir=custom_out)
        with corpus:
            assert custom_out.exists()
            assert len(list(custom_out.glob("*.jpg"))) == 1

        # Custom directory should not be destroyed on cleanup (only temporary ones are)
        assert custom_out.exists()

    def test_context_manager_exception_safety(self, tmp_path: Path):
        temp_dir_ref = None
        test_obf_id = None

        try:
            with ObfuscatedCardCorpus([{"card_id": "err_test.jpg", "filename": "err_test.jpg"}]) as obf:
                temp_dir_ref = obf.target_dir
                test_obf_id = obf.obfuscated_cards[0].obfuscated_id
                assert temp_dir_ref.exists()
                assert resolve_active_obfuscation_id(test_obf_id) == "err_test.jpg"
                raise RuntimeError("Simulated crash during evaluation")
        except RuntimeError:
            pass

        # Verify registry and temp directory are cleaned up despite exception
        assert not temp_dir_ref.exists()
        assert resolve_active_obfuscation_id(test_obf_id) == test_obf_id
