"""Empirical Challenger Suite: Multimodal Prompt Payload Leak & Anti-Cheating Verification.

Role: Prompt Payload Leak Challenger (Milestone 2).

Verifies:
1. Multimodal prompt payloads generated during extraction, pairing, and evaluation.
2. Intercepts raw JSON payload contents and prompt text sent over httpx / API clients.
3. Asserts zero leaks of:
   - Original card filenames (e.g. rec_vhc_apple_crisp_1948_front.jpg)
   - Filename recipe title tokens (e.g. "apple_crisp", "beef_stroganoff", "crisco_fried_chicken")
   - Vintage years in filenames (e.g. 1948, 1956, 1952, 1971, 1963, 1950)
   - Modality codes (vhc, typ, clp, dmg, mcs, sta)
   - Ground truth card sequence roles and raw filenames (rec_*, etc.)
4. Comprehensive testing across all 48 sample cards in manifest.json and adversarial edge cases.
"""

from __future__ import annotations

import json as py_json
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from recipeboxer.ai.gemini import (
    OPAQUE_CARD_ID_PATTERN,
    OPAQUE_SEQUENTIAL_PATTERN,
    sanitize_prompt_card_id,
    sanitize_prompt_text,
)
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.evals.obfuscation import (
    ObfuscatedCard,
    ObfuscatedCardCorpus,
    ObfuscationStrategy,
    resolve_active_obfuscation_id,
    sanitize_image_exif,
)
from recipeboxer.evals.runner import BenchmarkReport, BenchmarkRunner
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.vision.fingerprint import GeminiVisualExtractor
from tests.fixtures.loader import SampleCardCorpus, get_manifest_path, get_sample_card_dir


LEAK_MODALITY_CODES = ["vhc", "typ", "clp", "dmg", "mcs", "sta"]


def extract_all_text_from_payload(payload: Dict[str, Any]) -> str:
    """Recursively extracts all text strings embedded within a Gemini generateContent payload."""
    text_chunks: List[str] = []

    def _recurse(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "text" and isinstance(v, str):
                    text_chunks.append(v)
                else:
                    _recurse(v)
        elif isinstance(obj, list):
            for item in obj:
                _recurse(item)

    _recurse(payload)
    return " \n ".join(text_chunks)


def assert_zero_payload_leaks(
    payload_text: str,
    forbidden_tokens: Set[str],
    context_desc: str = "",
) -> None:
    """Asserts that none of the forbidden ground-truth tokens appear in the intercepted payload text."""
    lower_payload = payload_text.lower()
    for token in forbidden_tokens:
        clean_token = token.strip().lower()
        if not clean_token or len(clean_token) < 3:
            continue
        pattern = re.compile(rf"\b{re.escape(clean_token)}\b", re.IGNORECASE)
        match = pattern.search(lower_payload)
        if match:
            pytest.fail(
                f"Prompt payload leak detected in {context_desc}! "
                f"Found forbidden token '{clean_token}' in payload:\n"
                f"--- Intercepted Payload Snippet ---\n"
                f"{payload_text[:500]}...\n"
                f"--- End Snippet ---"
            )


# ============================================================================
# 1. CORPUS MANIFEST LEAK INTERCEPTION ACROSS ALL 48 CARDS
# ============================================================================

class TestCorpusManifestPromptLeaks:
    """Tests all 48 sample cards from manifest.json to ensure zero ground-truth leakage in prompts."""

    @pytest.fixture
    def corpus(self) -> SampleCardCorpus:
        return SampleCardCorpus(get_sample_card_dir(), get_manifest_path())

    @pytest.mark.asyncio
    async def test_all_48_cards_extraction_payload_leak_free(self, corpus: SampleCardCorpus):
        """Intercepts extractor payloads for every single card in the 48-card golden dataset under obfuscation."""
        extractor = GeminiRecipeExtractor(api_key="mock-api-key", model_name="gemini-3.7-flash")

        with ObfuscatedCardCorpus(corpus.cards, strategy=ObfuscationStrategy.DETERMINISTIC_SHA256) as obf:
            for obf_card in obf.obfuscated_cards:
                orig_card_id = obf_card.original_card_id

                # Forbidden tokens: original card ID, stem, rec_ prefixes, modality codes
                forbidden_tokens: Set[str] = {
                    orig_card_id,
                    Path(orig_card_id).stem,
                    "rec_vhc", "rec_typ", "rec_clp", "rec_dmg", "rec_mcs", "rec_sta",
                }

                # Extract year token from filename if present (e.g. 1948, 1956)
                year_match = re.search(r"\b(19\d\d|20\d\d)\b", orig_card_id)
                if year_match:
                    forbidden_tokens.add(year_match.group(1))

                intercepted_payload: Dict[str, Any] = {}

                async def fake_post(url: str, json: Dict[str, Any] = None, **kwargs: Any) -> MagicMock:
                    nonlocal intercepted_payload
                    intercepted_payload = json or {}
                    mock_resp = MagicMock()
                    mock_resp.json.return_value = {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [{"text": py_json.dumps({"title": "Test", "ingredients": [], "directions": []})}]
                                }
                            }
                        ]
                    }
                    mock_resp.raise_for_status = MagicMock()
                    return mock_resp

                with patch("httpx.AsyncClient.post", side_effect=fake_post):
                    await extractor._call_gemini_extraction_api(
                        resolved_cards=[(b"fake_image_bytes", str(obf_card.file_path), obf_card.obfuscated_id)]
                    )

                payload_text = extract_all_text_from_payload(intercepted_payload)
                assert_zero_payload_leaks(
                    payload_text=payload_text,
                    forbidden_tokens=forbidden_tokens,
                    context_desc=f"Extractor single-card: {orig_card_id}",
                )
                # Verify opaque ID is present
                assert obf_card.obfuscated_id in payload_text

    @pytest.mark.asyncio
    async def test_multi_card_pairs_extraction_payload_leak_free(self, corpus: SampleCardCorpus):
        """Intercepts extractor payloads for multi-card pairings in the 48-card dataset."""
        extractor = GeminiRecipeExtractor(api_key="mock-api-key", model_name="gemini-3.7-flash")

        with ObfuscatedCardCorpus(corpus.cards, strategy=ObfuscationStrategy.DETERMINISTIC_SHA256) as obf:
            # Group by recipe cluster
            clusters: Dict[str, List[ObfuscatedCard]] = {}
            for card in obf.obfuscated_cards:
                rid = card.recipe_cluster.get("recipe_id", "single")
                if rid not in clusters:
                    clusters[rid] = []
                clusters[rid].append(card)

            for rid, rcards in clusters.items():
                if len(rcards) < 2:
                    continue

                sorted_cards = sorted(rcards, key=lambda c: c.recipe_cluster.get("sequence_position", 0))
                resolved = [(b"bytes", str(c.file_path), c.obfuscated_id) for c in sorted_cards]

                forbidden_tokens: Set[str] = {"rec_vhc", "rec_typ", "rec_clp", "rec_dmg", "rec_mcs", "rec_sta"}
                for c in sorted_cards:
                    forbidden_tokens.add(c.original_card_id)
                    forbidden_tokens.add(Path(c.original_card_id).stem)
                    year_m = re.search(r"\b(19\d\d|20\d\d)\b", c.original_card_id)
                    if year_m:
                        forbidden_tokens.add(year_m.group(1))

                intercepted_payload: Dict[str, Any] = {}

                async def fake_post(url: str, json: Dict[str, Any] = None, **kwargs: Any) -> MagicMock:
                    nonlocal intercepted_payload
                    intercepted_payload = json or {}
                    mock_resp = MagicMock()
                    mock_resp.json.return_value = {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [{"text": py_json.dumps({"title": "Test", "ingredients": [], "directions": []})}]
                                }
                            }
                        ]
                    }
                    mock_resp.raise_for_status = MagicMock()
                    return mock_resp

                with patch("httpx.AsyncClient.post", side_effect=fake_post):
                    await extractor._call_gemini_extraction_api(resolved_cards=resolved)

                payload_text = extract_all_text_from_payload(intercepted_payload)
                assert_zero_payload_leaks(
                    payload_text=payload_text,
                    forbidden_tokens=forbidden_tokens,
                    context_desc=f"Extractor multi-card cluster: {rid}",
                )


# ============================================================================
# 2. ADVERSARIAL FILENAMES & SUBSTRING LEAK STRESS TESTS
# ============================================================================

class TestAdversarialPromptPayloadLeaks:
    """Stress-tests prompt generation against highly adversarial filenames and edge cases."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "adv_front,adv_back,secret_token,secret_year,mod_code",
        [
            ("rec_vhc_grandmas_secret_apple_crisp_1948_front.jpg", "rec_vhc_grandmas_secret_apple_crisp_1948_back.jpg", "grandmas_secret", "1948", "vhc"),
            ("rec_typ_beef_stroganoff_deluxe_1956_part1.png", "rec_typ_beef_stroganoff_deluxe_1956_part2.png", "beef_stroganoff", "1956", "typ"),
            ("rec_clp_crisco_fried_chicken_1952_front.jpg", "rec_clp_crisco_fried_chicken_1952_back.jpg", "fried_chicken", "1952", "clp"),
            ("rec_dmg_moms_lasagna_supreme_1971_card1.jpg", "rec_dmg_moms_lasagna_supreme_1971_card2.jpg", "lasagna_supreme", "1971", "dmg"),
            ("rec_mcs_chocolate_fudge_1950_front.jpg", "rec_mcs_chocolate_fudge_1950_back.jpg", "chocolate_fudge", "1950", "mcs"),
            ("rec_sta_cinnamon_rolls_1963_part1.jpg", "rec_sta_cinnamon_rolls_1963_part2.jpg", "cinnamon_rolls", "1963", "sta"),
        ],
    )
    async def test_extractor_sanitizes_adversarial_filenames(
        self,
        adv_front: str,
        adv_back: str,
        secret_token: str,
        secret_year: str,
        mod_code: str,
    ):
        """Tests that raw adversarial filenames are completely sanitized to SHA-256 hashes in extractor prompts."""
        extractor = GeminiRecipeExtractor(api_key="mock-key", model_name="gemini-3.7-flash")

        resolved_cards = [
            (b"front_bytes", f"/path/to/{adv_front}", adv_front),
            (b"back_bytes", f"/path/to/{adv_back}", adv_back),
        ]

        intercepted_payload: Dict[str, Any] = {}

        async def fake_post(url: str, json: Dict[str, Any] = None, **kwargs: Any) -> MagicMock:
            nonlocal intercepted_payload
            intercepted_payload = json or {}
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": py_json.dumps({"title": "Test", "ingredients": [], "directions": []})}]
                        }
                    }
                ]
            }
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch("httpx.AsyncClient.post", side_effect=fake_post):
            await extractor._call_gemini_extraction_api(resolved_cards=resolved_cards)

        payload_text = extract_all_text_from_payload(intercepted_payload)

        # Assert no leaks of secret tokens
        assert adv_front not in payload_text
        assert adv_back not in payload_text
        assert secret_token not in payload_text.lower()
        assert secret_year not in payload_text
        assert f"rec_{mod_code}" not in payload_text
        assert "card_" in payload_text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "adv_front,adv_back,secret_token,secret_year,mod_code",
        [
            ("rec_vhc_grandmas_secret_apple_crisp_1948_front.jpg", "rec_vhc_grandmas_secret_apple_crisp_1948_back.jpg", "grandmas_secret", "1948", "vhc"),
            ("rec_typ_beef_stroganoff_deluxe_1956_part1.png", "rec_typ_beef_stroganoff_deluxe_1956_part2.png", "beef_stroganoff", "1956", "typ"),
            ("rec_clp_crisco_fried_chicken_1952_front.jpg", "rec_clp_crisco_fried_chicken_1952_back.jpg", "crisco_fried", "1952", "clp"),
            ("rec_dmg_moms_lasagna_supreme_1971_card1.jpg", "rec_dmg_moms_lasagna_supreme_1971_card2.jpg", "lasagna_supreme", "1971", "dmg"),
        ],
    )
    async def test_semantic_verifier_sanitizes_adversarial_card_ids(
        self,
        adv_front: str,
        adv_back: str,
        secret_token: str,
        secret_year: str,
        mod_code: str,
    ):
        """Tests that raw adversarial card IDs are not leaked into semantic verifier prompt text."""
        verifier = GeminiSemanticVerifier(api_key="mock-key", model_name="gemini-3.7-flash")

        fp1 = VisualFingerprint(
            card_id=adv_front,
            image_path="test_front.jpg",
            sha256_hash="eac7208bafef79eb02416e847979a611072d854a56e74a8d72f8c28821e530b7",
            role=CardRole.SEQUENCE_START,
        )
        fp2 = VisualFingerprint(
            card_id=adv_back,
            image_path="test_back.jpg",
            sha256_hash="43b26d6ee49257602325992850937a09c2a6bf2a2aa775c7bdf6e44b80b7c7ba",
            role=CardRole.SEQUENCE_CONTINUATION,
        )

        intercepted_payload: Dict[str, Any] = {}

        async def fake_post(url: str, json: Dict[str, Any] = None, **kwargs: Any) -> MagicMock:
            nonlocal intercepted_payload
            intercepted_payload = json or {}
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": py_json.dumps({
                                        "is_pair_match": True,
                                        "semantic_score": 0.95,
                                        "explanation": "Matching directions.",
                                        "recommended_action": "PAIR",
                                    })
                                }
                            ]
                        }
                    }
                ]
            }
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch("httpx.AsyncClient.post", side_effect=fake_post):
            await verifier._call_gemini_semantic_api(fp1, fp2, heuristic_score=0.90)

        payload_text = extract_all_text_from_payload(intercepted_payload)

        # Assert no leaks of secret tokens
        assert adv_front not in payload_text
        assert adv_back not in payload_text
        assert secret_token not in payload_text.lower()
        assert secret_year not in payload_text
        assert f"rec_{mod_code}" not in payload_text


# ============================================================================
# 3. BENCHMARK RUNNER FULL END-TO-END PAYLOAD INTERCEPTION
# ============================================================================

class TestBenchmarkRunnerPayloadInterception:
    """Intersects and audits every single prompt emitted during a full benchmark run."""

    @pytest.mark.asyncio
    async def test_full_benchmark_emits_only_opaque_identifiers(self):
        """Runs the entire benchmark runner and verifies all outgoing prompts contain zero ground truth leaks."""
        intercepted_prompts: List[Dict[str, Any]] = []

        async def fake_post(url: str, json: Dict[str, Any] = None, **kwargs: Any) -> MagicMock:
            if json:
                intercepted_prompts.append(json)
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": py_json.dumps({"title": "Test", "ingredients": [], "directions": []})}]
                        }
                    }
                ]
            }
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch("httpx.AsyncClient.post", side_effect=fake_post):
            runner = BenchmarkRunner(
                manifest_path=get_manifest_path(),
                corpus_dir=get_sample_card_dir(),
                stage="full",
                mock_judge=True,
                obfuscate_filenames=True,
            )
            report = await runner.run()

        assert report.obfuscation_enabled is True
        assert report.total_cards == 48
        assert report.total_recipes == 24
        assert report.pairing_metrics.f1_score >= 0.85

        # Check all intercepted payloads
        forbidden_substrings = ["rec_vhc", "rec_typ", "rec_clp", "rec_dmg", "rec_mcs", "rec_sta"]
        for p in intercepted_prompts:
            text = extract_all_text_from_payload(p)
            for sub in forbidden_substrings:
                assert sub not in text
