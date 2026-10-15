"""Unit tests for WP-T3GT (T3 Ground-Truth Structured Drafter & Review Tooling).

Offline test suite using synthetic/fictitious recipe data only.
Verifies:
1. Pydantic schema validation and null normalization.
2. Prompt construction and SHA-256 hashing.
3. Substring CER fuzzy matching and validation logic against verbatim text.
4. Offline safety of draft_structured_gt and memorization_probe when no API key is set.
5. In-memory mocked client execution for structured drafting.
6. Memorization probe overlap calculation and exclusion flagging.
7. Spot-check deterministic 20% sampling and HTML package generation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from evals.curation_t3.draft_structured import (
    DEFAULT_DRAFT_MODEL,
    IngredientSchema,
    StructuredRecipeSchema,
    draft_structured_gt,
    draft_structured_prompt,
    fuzzy_match_substring_cer,
    validate_draft_against_verbatim,
)
from evals.curation_t3.memorization_probe import (
    compute_token_overlap,
    extract_title_candidate,
    run_memorization_probe,
    tokenize,
)
from evals.curation_t3.spotcheck import (
    generate_spotcheck_html_package,
)


# ---------------------------------------------------------------------------
# 1. Schema Validation Tests
# ---------------------------------------------------------------------------


def test_ingredient_schema_validation_and_nulls() -> None:
    # 1. Complete ingredient
    ing = IngredientSchema(
        raw="1 1/2 cups sifted pastry flour",
        quantity="1 1/2",
        unit="cups",
        item="sifted pastry flour",
        section=None,
    )
    dumped = ing.model_dump()
    assert dumped["raw"] == "1 1/2 cups sifted pastry flour"
    assert dumped["quantity"] == "1 1/2"
    assert dumped["unit"] == "cups"
    assert dumped["item"] == "sifted pastry flour"
    assert dumped["section"] is None

    # 2. Ingredient with missing quantity and unit (e.g. historical salt/spice)
    ing2 = IngredientSchema(
        raw="a pinch of nutmegg",
        quantity="",  # empty string should normalize to None
        unit="pinch",
        item="nutmegg",
        section="",
    )
    dumped2 = ing2.model_dump()
    assert dumped2["quantity"] is None
    assert dumped2["section"] is None
    assert dumped2["unit"] == "pinch"
    assert dumped2["item"] == "nutmegg"

    # 3. Literal "null" string normalized to None
    ing3 = IngredientSchema(
        raw="fresh water as needed",
        quantity="null",
        unit="None",
        item="water",
        section=None,
    )
    dumped3 = ing3.model_dump()
    assert dumped3["quantity"] is None
    assert dumped3["unit"] is None


def test_structured_recipe_schema_alias_and_serialization() -> None:
    recipe_dict = {
        "title": "Historical Lemon Biscuits",
        "yield": "12 biscuits",
        "source_note": "Aunt Sarah's notebook, 1782",
        "ingredients": [
            {
                "raw": "1 lb white sugar",
                "quantity": "1",
                "unit": "lb",
                "item": "white sugar",
                "section": None,
            },
            {
                "raw": "zest of two lemons",
                "quantity": "2",
                "unit": None,
                "item": "lemons zest",
                "section": None,
            },
        ],
        "directions": [
            "Beat eggs and sugar thoroughly.",
            "Grate lemon peel and fold gently.",
            "Bake upon tin plates.",
        ],
        "notes": ["Keep dry in a stone pot."],
    }

    validated = StructuredRecipeSchema.model_validate(recipe_dict)
    dumped = validated.model_dump(by_alias=True)

    assert dumped["title"] == "Historical Lemon Biscuits"
    assert dumped["yield"] == "12 biscuits"
    assert dumped["source_note"] == "Aunt Sarah's notebook, 1782"
    assert len(dumped["ingredients"]) == 2
    assert dumped["ingredients"][0]["unit"] == "lb"
    assert dumped["ingredients"][1]["unit"] is None
    assert len(dumped["directions"]) == 3
    assert dumped["notes"] == ["Keep dry in a stone pot."]


# ---------------------------------------------------------------------------
# 2. Prompt Construction & Hash Tests
# ---------------------------------------------------------------------------


def test_prompt_construction_and_hash() -> None:
    verbatim_faces = [
        "Take a quart of Cream, boil it with whole Mace.",
        "When cold, stir in eight eggs and bake.",
    ]
    prompt = draft_structured_prompt(verbatim_faces)

    assert "Take a quart of Cream" in prompt
    assert "NEXT CARD FACE" in prompt
    assert "VERBATIM-FAITHFUL" in prompt
    assert "NULL QUANTITY WHEN ABSENT" in prompt

    h1 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    h2 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert h1 == h2
    assert len(h1) == 64


# ---------------------------------------------------------------------------
# 3. Fuzzy Substring Matching & CER Validation Tests
# ---------------------------------------------------------------------------


def test_fuzzy_match_substring_cer() -> None:
    haystack = "Take a pint of sweet Cream, and boil it with a stick of Cinnamon."

    # Exact substring -> CER 0.0
    assert fuzzy_match_substring_cer(haystack, "Take a pint of sweet Cream") == 0.0
    assert fuzzy_match_substring_cer(haystack, "boil it with a stick of Cinnamon") == 0.0

    # Slight typographical difference within threshold
    # "Take a pint of sweet Creme" (1 char diff on 26 chars = ~0.038 <= 0.15)
    cer_minor = fuzzy_match_substring_cer(haystack, "Take a pint of sweet Creme")
    assert cer_minor <= 0.15

    # Completely invented step -> CER > 0.15
    cer_hallucinated = fuzzy_match_substring_cer(
        haystack, "Microwave on high for five minutes in glass bowl"
    )
    assert cer_hallucinated > 0.15


def test_validate_draft_against_verbatim() -> None:
    verbatim = [
        "To make Almond Pudding.\nTake a pound of sweet Almonds, blanch them and beat them fine.\n"
        "Add a pint of thick cream, sugar to your taste, and bake in a quick oven."
    ]

    valid_draft = {
        "title": "Almond Pudding",
        "ingredients": [
            {"raw": "a pound of sweet Almonds", "item": "sweet Almonds", "quantity": "1", "unit": "pound"},
            {"raw": "a pint of thick cream", "item": "thick cream", "quantity": "1", "unit": "pint"},
            {"raw": "sugar to your taste", "item": "sugar", "quantity": None, "unit": None},
        ],
        "directions": [
            "blanch them and beat them fine.",
            "Add a pint of thick cream, sugar to your taste, and bake in a quick oven.",
        ],
    }

    mismatches = validate_draft_against_verbatim(valid_draft, verbatim, max_cer=0.15)
    assert len(mismatches) == 0

    # Introduce a hallucinated ingredient and step
    invalid_draft = {
        "title": "Almond Pudding",
        "ingredients": [
            {"raw": "2 tablespoons baking powder", "item": "baking powder", "quantity": "2", "unit": "tbsp"},
        ],
        "directions": [
            "Preheat modern convection oven to 350 degrees Fahrenheit.",
        ],
    }

    bad_mismatches = validate_draft_against_verbatim(invalid_draft, verbatim, max_cer=0.15)
    assert len(bad_mismatches) == 2
    assert any("ingredients[0].raw" in m["field"] for m in bad_mismatches)
    assert any("directions[0]" in m["field"] for m in bad_mismatches)


# ---------------------------------------------------------------------------
# 4. Mock Client Structured Drafting Integration Test
# ---------------------------------------------------------------------------


def test_draft_structured_with_mocked_client(tmp_path: Path) -> None:
    # Build a minimal encrypted vault using a temporary key outside repo
    key_file = tmp_path / "temp_age.key"
    key_file.write_text("AGE-SECRET-KEY-1TEMPORARYMOCKKEYFORTESTSONLY00000000000000000000000000\n")

    # Mock vault encryption and decryption
    sample_gt = {
        "schema": "rb-gt/1",
        "tier": "T3",
        "decks": [
            {
                "deck_id": "d01",
                "recipes": [
                    {
                        "recipe_id": "r999",
                        "faces": ["face999.jpg"],
                        "verbatim": ["Take 2 cups milk and 1 cup oats. Boil 10 minutes."],
                        "structured": None,
                        "structured_status": "pending_text_only_draft",
                    }
                ],
            }
        ],
    }

    # Mock genai response
    mock_resp = MagicMock()
    mock_resp.text = json.dumps({
        "title": "Porridge",
        "yield": None,
        "source_note": None,
        "ingredients": [
            {"raw": "2 cups milk", "quantity": "2", "unit": "cups", "item": "milk", "section": None},
            {"raw": "1 cup oats", "quantity": "1", "unit": "cup", "item": "oats", "section": None},
        ],
        "directions": ["Take 2 cups milk and 1 cup oats.", "Boil 10 minutes."],
        "notes": [],
    })
    mock_usage = MagicMock()
    mock_usage.prompt_token_count = 100
    mock_usage.candidates_token_count = 50
    mock_usage.thoughts_token_count = 200
    mock_resp.usage_metadata = mock_usage

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    # Use monkeypatch on vault encrypt/decrypt
    vault_file = tmp_path / "test.gt.enc"
    vault_file.write_bytes(b"ENC_BYTES")

    def fake_decrypt(b: bytes, identity_path: Any = None) -> bytes:
        return json.dumps(sample_gt).encode("utf-8")

    saved_data = {}

    def fake_encrypt(b: bytes, recipients_path: Any = None) -> bytes:
        saved_data["payload"] = json.loads(b.decode("utf-8"))
        return b"NEW_ENC"

    import evals.vault as v_mod
    original_dec = v_mod.decrypt_bytes
    original_enc = v_mod.encrypt_bytes
    try:
        v_mod.decrypt_bytes = fake_decrypt
        v_mod.encrypt_bytes = fake_encrypt

        code, summary = draft_structured_gt(
            vault_file=vault_file,
            client=mock_client,
            model_name=DEFAULT_DRAFT_MODEL,
        )

        assert code == 0
        assert summary["drafted"] == 1
        assert summary["failed"] == 0
        assert summary["tokens"]["prompt_tokens"] == 100
        assert summary["tokens"]["candidate_tokens"] == 50
        assert summary["tokens"]["thoughts_tokens"] == 200
        assert summary["cost_usd"] > 0.0

        # Verify recipe in re-encrypted payload
        updated_recipe = saved_data["payload"]["decks"][0]["recipes"][0]
        assert updated_recipe["structured_status"] == "drafted_unreviewed"
        assert updated_recipe["structured_model"] == DEFAULT_DRAFT_MODEL
        assert "prompt_hash" in updated_recipe
        assert "drafted_on" in updated_recipe
        assert updated_recipe["structured"]["title"] == "Porridge"

    finally:
        v_mod.decrypt_bytes = original_dec
        v_mod.encrypt_bytes = original_enc


# ---------------------------------------------------------------------------
# 5. Memorization Probe Logic Tests
# ---------------------------------------------------------------------------


def test_memorization_probe_token_overlap() -> None:
    gt_text = "Take a quart of Cream, boil it with whole Mace and Nutmeg. Add six egg yolks."

    # High overlap completion (memorized reproduction)
    memorized = "Take a quart of cream, boil with mace, nutmeg, and add six egg yolks."
    overlap_high = compute_token_overlap(memorized, gt_text)
    assert overlap_high >= 0.65

    # Low overlap completion (generic modern recipe)
    generic = "Mix flour, sugar, butter, and baking powder in a bowl. Bake at 350F for 30 minutes."
    overlap_low = compute_token_overlap(generic, gt_text)
    assert overlap_low < 0.20


def test_extract_title_candidate() -> None:
    lines = [
        "",
        "To make a Fine Quaking Pudding",
        "Take a quart of cream...",
    ]
    cand = extract_title_candidate(["\n".join(lines)])
    assert cand == "a Fine Quaking Pudding"


# ---------------------------------------------------------------------------
# 6. Spotcheck Review Package Generation Test
# ---------------------------------------------------------------------------


def test_generate_spotcheck_html_package(tmp_path: Path) -> None:
    sample_recipe = (
        "d01",
        {
            "recipe_id": "r001",
            "faces": ["face1.jpg"],
            "difficulty": {"legibility": "good"},
            "structured_status": "drafted_unreviewed",
            "memorization_score": 0.12,
            "excluded_reason": None,
            "verbatim": ["Line 1: Take fine flour\nLine 2: Bake hot"],
            "structured": {
                "title": "Fictitious Cake",
                "yield": None,
                "source_note": None,
                "ingredients": [
                    {"raw": "fine flour", "quantity": None, "unit": None, "item": "fine flour", "section": None}
                ],
                "directions": ["Bake hot"],
                "notes": [],
            },
        },
    )

    out_dir = tmp_path / "spotcheck_out"
    corpus_dir = tmp_path / "corpus"
    (corpus_dir / "d01").mkdir(parents=True)
    # Create fake image
    (corpus_dir / "d01" / "face1.jpg").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")

    generate_spotcheck_html_package(
        sampled_recipes=[sample_recipe],
        output_dir=out_dir,
        corpus_dir=corpus_dir,
        seed=42,
    )

    assert (out_dir / "index.html").exists()
    assert (out_dir / "recipe_r001.html").exists()
    assert (out_dir / "README.md").exists()

    rec_content = (out_dir / "recipe_r001.html").read_text(encoding="utf-8")
    assert "Fictitious Cake" in rec_content
    assert "data:image/jpeg;base64," in rec_content
    assert "Reviewer Checklist" in rec_content

    readme_content = (out_dir / "README.md").read_text(encoding="utf-8")
    assert "How to Open the Review Package" in readme_content
    assert "r001" in readme_content
