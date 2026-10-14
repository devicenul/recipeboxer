"""Tests for WP-C3: Modality Classifier and Prompt Router remediation.

Validates:
1. CardModality.normalize removes corpus category codes ('CLP', 'DMG', 'TYP', 'VHC', 'MCS', 'STA', etc.)
   and no longer maps them to specialized modalities.
2. CardModality.normalize retains semantic word mapping (CLIPPING, NEWSPRINT, LABEL, MAGAZINE,
   TYPEWRITTEN, TYPE, MONOSPACE, HANDWRITTEN, CURSIVE, SCRIPT, MIXED, DAMAGED, STAIN, DEGRADED, PRINT+HAND).
3. 'TYP' substring removal does not break 'TYPEWRITTEN', 'TYPEWRITER', or 'TYPE'.
4. Classification of a synthetic PIL image works deterministically without any manifest file.
5. Nonexistent paths (including misleading names like 'clipping_typewriter.jpg') return fallback
   confidence 0.0 with modality HANDWRITTEN_CURSIVE, ignoring filename keywords entirely.
6. The router module contains no manifest lookup table, no SHA-256 byte digest caches, and no
   answer key references.
7. VisualFingerprint-based routing uses real domain fingerprint fields (ink_type, physical artifacts).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from recipeboxer.models.domain import (
    ArtifactType,
    CardRole,
    InkInfo,
    InkType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.vision import (
    CardModality,
    ModalityClassificationResult,
    ModalityClassifier,
    PromptRouter,
    RoutedPromptConfig,
    classify_card_modality,
    route_prompt_for_modality,
)
import recipeboxer.vision.router as router_module


class TestWP3CardModalityNormalization:
    """Tests for CardModality normalization without corpus category codes."""

    def test_normalize_category_codes_no_longer_mapped(self) -> None:
        """Category codes must not map to specialized modalities."""
        category_codes = ["CLP", "DMG", "TYP", "VHC", "MCS", "STA", "_vhc_", "_mcs_", "_sta_"]
        for code in category_codes:
            result = CardModality.normalize(code)
            assert result != CardModality.PRINTED_CLIPPING_OR_LABEL
            assert result != CardModality.TYPEWRITTEN
            assert result != CardModality.MIXED_MEDIA
            assert result == CardModality.HANDWRITTEN_CURSIVE

    def test_normalize_semantic_words_still_map(self) -> None:
        """Semantic words used by models and users must still normalize properly."""
        # Printed clipping and labels
        assert CardModality.normalize("CLIPPING") == CardModality.PRINTED_CLIPPING_OR_LABEL
        assert CardModality.normalize("NEWSPRINT") == CardModality.PRINTED_CLIPPING_OR_LABEL
        assert CardModality.normalize("LABEL") == CardModality.PRINTED_CLIPPING_OR_LABEL
        assert CardModality.normalize("MAGAZINE") == CardModality.PRINTED_CLIPPING_OR_LABEL
        assert CardModality.normalize("CUTOUT") == CardModality.PRINTED_CLIPPING_OR_LABEL
        assert CardModality.normalize("NEWSLETTER") == CardModality.PRINTED_CLIPPING_OR_LABEL
        assert CardModality.normalize("magazine_cutout") == CardModality.PRINTED_CLIPPING_OR_LABEL
        assert CardModality.normalize("newspaper_clipping") == CardModality.PRINTED_CLIPPING_OR_LABEL

        # Handwritten print
        assert CardModality.normalize("HANDWRITTEN_PRINT") == CardModality.HANDWRITTEN_PRINT
        assert CardModality.normalize("HAND_PRINT") == CardModality.HANDWRITTEN_PRINT
        assert CardModality.normalize("handprint") == CardModality.HANDWRITTEN_PRINT
        assert CardModality.normalize("MANUSCRIPT_PRINT") == CardModality.HANDWRITTEN_PRINT
        assert CardModality.normalize("PRINT+HAND") == CardModality.HANDWRITTEN_PRINT

        # Mixed media / damaged
        assert CardModality.normalize("MIXED_MEDIA") == CardModality.MIXED_MEDIA
        assert CardModality.normalize("MIXED") == CardModality.MIXED_MEDIA
        assert CardModality.normalize("DAMAGED") == CardModality.MIXED_MEDIA
        assert CardModality.normalize("STAIN") == CardModality.MIXED_MEDIA
        assert CardModality.normalize("stained_card") == CardModality.MIXED_MEDIA
        assert CardModality.normalize("DEGRADED") == CardModality.MIXED_MEDIA

        # Typewritten
        assert CardModality.normalize("TYPEWRITTEN") == CardModality.TYPEWRITTEN
        assert CardModality.normalize("TYPE") == CardModality.TYPEWRITTEN
        assert CardModality.normalize("typewriter") == CardModality.TYPEWRITTEN
        assert CardModality.normalize("MONOSPACE") == CardModality.TYPEWRITTEN

        # Handwritten cursive
        assert CardModality.normalize("HANDWRITTEN_CURSIVE") == CardModality.HANDWRITTEN_CURSIVE
        assert CardModality.normalize("cursive") == CardModality.HANDWRITTEN_CURSIVE
        assert CardModality.normalize("HANDWRITING") == CardModality.HANDWRITTEN_CURSIVE
        assert CardModality.normalize("SCRIPT") == CardModality.HANDWRITTEN_CURSIVE

    def test_normalize_typ_substring_removal_preserves_typewritten(self) -> None:
        """Removing 'TYP' category code must not break TYPEWRITTEN or TYPE matches."""
        assert CardModality.normalize("TYPEWRITTEN") == CardModality.TYPEWRITTEN
        assert CardModality.normalize("TYPEWRITER") == CardModality.TYPEWRITTEN
        assert CardModality.normalize("typewritten_recipe") == CardModality.TYPEWRITTEN
        assert CardModality.normalize("TYPE") == CardModality.TYPEWRITTEN

        # Raw category code TYP must not resolve to TYPEWRITTEN
        assert CardModality.normalize("TYP") != CardModality.TYPEWRITTEN
        assert CardModality.normalize("TYP") == CardModality.HANDWRITTEN_CURSIVE


class TestWP3ModalityClassification:
    """Tests for image-based classification, fallback on failure, and no manifest lookup."""

    def test_synthetic_pil_image_classification_without_manifest(self) -> None:
        """Classification of a synthetic PIL Image works purely from pixel features."""
        img = Image.new("RGB", (300, 200), color=(250, 248, 240))
        classifier = ModalityClassifier()
        res = classifier.classify(img)

        assert isinstance(res, ModalityClassificationResult)
        assert res.confidence > 0.0
        assert res.modality in CardModality
        assert res.features.get("fallback") is not True
        assert len(res.reasoning) > 0

    def test_nonexistent_path_ignores_filename_semantics_and_returns_fallback(self) -> None:
        """Nonexistent paths return fallback confidence 0.0 regardless of filename keywords."""
        classifier = ModalityClassifier()

        misleading_names = [
            "clipping_typewriter.jpg",
            "newspaper_recipe.png",
            "damaged_card_stain.jpg",
            "typewritten_note.jpg",
            "nonexistent_image_123.jpg",
        ]

        for name in misleading_names:
            res = classifier.classify(name)
            assert res.modality == CardModality.HANDWRITTEN_CURSIVE
            assert res.confidence == 0.0
            assert res.reasoning.startswith("Modality unavailable:")
            assert res.features == {"fallback": True}

            res_path = classifier.classify(Path(name))
            assert res_path.modality == CardModality.HANDWRITTEN_CURSIVE
            assert res_path.confidence == 0.0
            assert res_path.reasoning.startswith("Modality unavailable:")
            assert res_path.features == {"fallback": True}

    def test_module_has_no_manifest_or_sha_lookups(self) -> None:
        """Verify the router module contains no manifest lookup table or SHA caches."""
        assert not hasattr(router_module, "_MANIFEST_MODALITY_CACHE")
        assert not hasattr(router_module, "_get_manifest_modality_lookup")

        source_text = Path(router_module.__file__).read_text(encoding="utf-8")
        assert "manifest.json" not in source_text
        assert "sample_cards" not in source_text
        assert "tests/fixtures" not in source_text
        assert "sha256_match" not in source_text
        assert "_MANIFEST_MODALITY_CACHE" not in source_text
        assert "_get_manifest_modality_lookup" not in source_text

    def test_raw_bytes_classification(self) -> None:
        """Synthetic raw image bytes are correctly analyzed through Pillow."""
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw_bytes = buf.getvalue()

        classifier = ModalityClassifier()
        res = classifier.classify(raw_bytes)
        assert isinstance(res, ModalityClassificationResult)
        assert res.confidence > 0.0
        assert res.features.get("fallback") is not True

    def test_client_override_in_modality_classifier(self) -> None:
        """Injected client with classify_modality method overrides classification."""
        class MockClassifierClient:
            def classify_modality(self, card_input: Any) -> ModalityClassificationResult:
                return ModalityClassificationResult(
                    modality=CardModality.TYPEWRITTEN,
                    confidence=0.99,
                    reasoning="Mock classifier override.",
                    features={"mock": True},
                )

        classifier = ModalityClassifier(client=MockClassifierClient())
        res = classifier.classify("dummy_path.jpg")
        assert res.modality == CardModality.TYPEWRITTEN
        assert res.confidence == 0.99
        assert res.features == {"mock": True}


class TestWP3VisualFingerprintRouting:
    """Tests for VisualFingerprint-based routing using real domain attributes."""

    def test_visual_fingerprint_routing_with_real_domain_fields(self) -> None:
        """Validates routing based on ink_type and physical artifacts."""
        classifier = ModalityClassifier()

        # Real artifact: OIL_STAIN -> MIXED_MEDIA
        fp_stain = VisualFingerprint(
            card_id="card_001",
            image_path="",
            sha256_hash="0" * 64,
            role=CardRole.STANDALONE,
            artifacts=[PhysicalArtifact(artifact_type=ArtifactType.OIL_STAIN)],
        )
        res_stain = classifier.classify(fp_stain)
        assert res_stain.modality == CardModality.MIXED_MEDIA
        assert res_stain.confidence == 0.95
        assert "stains" in res_stain.reasoning.lower() or "artifacts" in res_stain.reasoning.lower()

        # Real ink: TYPEWRITER_RIBBON -> TYPEWRITTEN
        fp_type = VisualFingerprint(
            card_id="card_002",
            image_path="",
            sha256_hash="0" * 64,
            role=CardRole.STANDALONE,
            ink=InkInfo(ink_type=InkType.TYPEWRITER_RIBBON),
        )
        res_type = classifier.classify(fp_type)
        assert res_type.modality == CardModality.TYPEWRITTEN
        assert res_type.confidence == 0.96
        assert "typewriter" in res_type.reasoning.lower()

        # Real ink: PRINTED_TYPESET -> PRINTED_CLIPPING_OR_LABEL
        fp_print = VisualFingerprint(
            card_id="card_003",
            image_path="",
            sha256_hash="0" * 64,
            role=CardRole.STANDALONE,
            ink=InkInfo(ink_type=InkType.PRINTED_TYPESET),
        )
        res_print = classifier.classify(fp_print)
        assert res_print.modality == CardModality.PRINTED_CLIPPING_OR_LABEL
        assert res_print.confidence == 0.96
        assert "printed" in res_print.reasoning.lower()

        # Fingerprint without image_path and without identifying attributes -> fallback
        fp_empty = VisualFingerprint(
            card_id="card_004",
            image_path="",
            sha256_hash="0" * 64,
            role=CardRole.STANDALONE,
        )
        res_empty = classifier.classify(fp_empty)
        assert res_empty.modality == CardModality.HANDWRITTEN_CURSIVE
        assert res_empty.confidence == 0.0
        assert res_empty.reasoning.startswith("Modality unavailable:")
        assert res_empty.features == {"fallback": True}


class TestWP3PromptRouter:
    """Tests for prompt configuration routing based on CardModality."""

    def test_prompt_router_configurations(self) -> None:
        """Prompt router selects correct branches and thinking token budgets."""
        cfg_cursive = route_prompt_for_modality(CardModality.HANDWRITTEN_CURSIVE)
        assert cfg_cursive.prompt_branch == "vintage_cursive"
        assert cfg_cursive.recommended_thinking_budget == 2048

        cfg_type = route_prompt_for_modality(CardModality.TYPEWRITTEN)
        assert cfg_type.prompt_branch == "typewritten"
        assert cfg_type.recommended_thinking_budget == 0

        cfg_clip = route_prompt_for_modality(CardModality.PRINTED_CLIPPING_OR_LABEL)
        assert cfg_clip.prompt_branch == "printed_clipping"
        assert cfg_clip.recommended_thinking_budget == 1024

        cfg_mixed = route_prompt_for_modality(CardModality.MIXED_MEDIA)
        assert cfg_mixed.prompt_branch == "mixed_media"
        assert cfg_mixed.recommended_thinking_budget >= 2048
