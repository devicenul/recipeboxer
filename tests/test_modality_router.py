"""Unit and benchmark evaluation tests for ModalityClassifier and Dynamic Prompt Routing.

Tests:
- Document modality classification across benchmark cards (asserts >= 90% accuracy)
- Prompt branch selection and domain skill routing
- CardModality enum normalization and serialization
"""

import json
from pathlib import Path
from typing import Dict

import pytest
from PIL import Image

from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.vision.router import (
    CardModality,
    ModalityClassificationResult,
    ModalityClassifier,
    PromptRouter,
    RoutedPromptConfig,
    classify_card_modality,
    route_prompt_for_modality,
)


MANIFEST_PATH = Path("tests/fixtures/sample_cards/manifest.json")
SAMPLE_CARDS_DIR = Path("tests/fixtures/sample_cards")


class TestModalityClassifierBenchmark:
    """Evaluates ModalityClassifier accuracy on the 23 curated historical sample cards."""

    @pytest.mark.live
    def test_classification_accuracy_on_benchmark_corpus(self):
        if not MANIFEST_PATH.exists():
            pytest.skip("Sample cards manifest not available.")

        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cards = manifest.get("cards", [])
        assert len(cards) in (23, 48)

        classifier = ModalityClassifier()
        correct_count = 0

        for card_data in cards:
            cid = card_data["card_id"]
            img_path = SAMPLE_CARDS_DIR / card_data["filename"]
            expected_modality = CardModality.normalize(card_data["modality"])

            # Classify using file path
            result = classifier.classify(img_path)
            assert isinstance(result, ModalityClassificationResult)
            assert 0.0 <= result.confidence <= 1.0

            if result.modality == expected_modality or (
                expected_modality == CardModality.PRINTED_CLIPPING_OR_LABEL
                and result.modality in (CardModality.PRINTED_CLIPPING_OR_LABEL, CardModality.PRINTED_CLIPPING)
            ):
                correct_count += 1

        accuracy = float(correct_count) / float(len(cards))
        # Must achieve at least 90% accuracy across benchmark corpus
        assert accuracy >= 0.90, f"Modality accuracy was {accuracy*100:.1f}%, expected >= 90%"

    def test_classify_from_various_input_types(self):
        """Verify ModalityClassifier accepts Path, ScannedCard, VisualFingerprint, bytes, and Image."""
        class MockModalityClient:
            def classify_modality(self, card_input):
                return ModalityClassificationResult(
                    modality=CardModality.TYPEWRITTEN,
                    confidence=0.96,
                    reasoning="Mocked typewriter classification for polymorphism test.",
                )

        classifier = ModalityClassifier(client=MockModalityClient())

        # From PIL Image
        img = Image.new("RGB", (600, 400), color=(255, 255, 255))
        r_img = classifier.classify(img)
        assert r_img.modality == CardModality.TYPEWRITTEN

        # From Path
        r1 = classifier.classify(Path("/dummy/card.jpg"))
        assert r1.modality == CardModality.TYPEWRITTEN

        # From ScannedCard
        sc = ScannedCard(
            card_id="test.jpg",
            file_path=Path("/dummy/test.jpg"),
            filename="test.jpg",
            sha256_hash="dummyhash",
            file_size_bytes=100,
            width=600,
            height=400,
            format="JPEG",
            mode="RGB",
        )
        r2 = classifier.classify(sc)
        assert r2.modality == CardModality.TYPEWRITTEN

        # From VisualFingerprint
        fp = VisualFingerprint(
            card_id="test.jpg",
            image_path="/dummy/test.jpg",
            sha256_hash="dummyhash",
            role=CardRole.SEQUENCE_START,
        )
        r3 = classifier.classify(fp)
        assert r3.modality == CardModality.TYPEWRITTEN

        # From raw bytes
        r4 = classifier.classify(b"dummy_raw_bytes")
        assert r4.modality == CardModality.TYPEWRITTEN


class TestPromptRouter:
    """Tests for dynamic prompt branching and thinking budget allocation."""

    def test_route_vintage_cursive(self):
        cfg = route_prompt_for_modality(CardModality.HANDWRITTEN_CURSIVE)
        assert cfg.modality == CardModality.HANDWRITTEN_CURSIVE
        assert cfg.prompt_branch == "vintage_cursive"
        assert cfg.skill_name == "vintage-handwriting-ocr"
        assert cfg.recommended_thinking_budget == 2048
        assert "deskew" in cfg.recommended_preprocessing
        assert "clahe" in cfg.recommended_preprocessing

    def test_route_vintage_cursive_severe_degradation(self):
        cfg = route_prompt_for_modality(
            CardModality.HANDWRITTEN_CURSIVE, degradation_level="SEVERE"
        )
        # Severe cursive requires higher 4096 thinking budget
        assert cfg.recommended_thinking_budget == 4096

    def test_route_printed_clipping(self):
        cfg = route_prompt_for_modality(CardModality.PRINTED_CLIPPING_OR_LABEL)
        assert cfg.modality == CardModality.PRINTED_CLIPPING_OR_LABEL
        assert cfg.prompt_branch == "printed_clipping"
        assert cfg.skill_name == "print-clipping-extractor"
        assert cfg.recommended_thinking_budget == 1024
        assert "crop_borders" in cfg.recommended_preprocessing

    def test_route_typewritten_clean(self):
        cfg = route_prompt_for_modality(CardModality.TYPEWRITTEN, degradation_level="NONE")
        assert cfg.modality == CardModality.TYPEWRITTEN
        assert cfg.prompt_branch == "typewritten"
        assert cfg.skill_name is None
        # Clean typewritten uses fast 0-token inference
        assert cfg.recommended_thinking_budget == 0

    def test_route_mixed_media(self):
        cfg = route_prompt_for_modality(CardModality.MIXED_MEDIA)
        assert cfg.modality == CardModality.MIXED_MEDIA
        assert cfg.prompt_branch == "mixed_media"
        assert cfg.recommended_thinking_budget >= 2048
