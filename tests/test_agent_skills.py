"""Unit and integration tests for Antigravity Analysis Skills and Callable Tools.

Covers:
- vintage-handwriting-ocr: paleography rules, fraction normalization, archaic unit normalization
- print-clipping-extractor: advertisement and coupon noise filtering, multi-column rules
- Antigravity agent tool wrappers: deskew, crop, enhance, palette, stain symmetry, modality
"""

from pathlib import Path

import pytest

from recipeboxer.agent.skills import (
    ALL_AGENT_TOOLS,
    check_stain_symmetry_tool,
    classify_card_modality_tool,
    crop_card_borders_tool,
    deskew_card_image_tool,
    enhance_card_readability_tool,
    extract_paper_palette_tool,
    filter_advertisement_noise,
    get_print_clipping_prompt,
    get_vintage_handwriting_prompt,
    is_promotional_line,
    normalize_archaic_units,
    normalize_fractions,
)


SAMPLE_CARDS_DIR = Path("tests/fixtures/sample_cards")


class TestVintageHandwritingSkill:
    """Tests for vintage cursive handwriting normalization and archaic culinary units."""

    def test_normalize_unicode_fractions(self):
        assert normalize_fractions("1½ cups flour") == "1 1/2 cups flour"
        assert normalize_fractions("½ tsp salt") == "1/2 tsp salt"
        assert normalize_fractions("¾ cup sugar") == "3/4 cup sugar"
        assert normalize_fractions("2⅓ cups milk") == "2 1/3 cups milk"
        assert normalize_fractions("⅛ tsp nutmeg") == "1/8 tsp nutmeg"
        assert normalize_fractions("⅝ cup water") == "5/8 cup water"
        assert normalize_fractions("1-1/2 cups flour") == "1 1/2 cups flour"
        assert normalize_fractions("1 - 1 / 2 cups flour") == "1 1/2 cups flour"
        assert normalize_fractions("2-3/4 tsp cinnamon") == "2 3/4 tsp cinnamon"

    def test_normalize_fraction_ranges_preserved(self):
        assert normalize_fractions("1/4 - 1/2 tsp salt") == "1/4 - 1/2 tsp salt"
        assert normalize_fractions("1/2-3/4 cup milk") == "1/2-3/4 cup milk"
        assert normalize_fractions("1 1/4 - 1 1/2 cups flour") == "1 1/4 - 1 1/2 cups flour"

    def test_normalize_archaic_culinary_units(self):
        # Butter size expressions
        assert "1/4 cup butter" in normalize_archaic_units("butter size of an egg")
        assert "1/4 cup butter" in normalize_archaic_units("butter the size of an egg")
        assert "1/4 cup butter" in normalize_archaic_units("a piece of butter the size of an egg")
        assert "2 tbsp butter" in normalize_archaic_units("a lump of butter size of a walnut")
        assert "2 tbsp butter" in normalize_archaic_units("butter size of a walnut")
        assert "1 tsp butter" in normalize_archaic_units("butter size of a hazelnut")
        assert "1/2 tsp butter" in normalize_archaic_units("butter size of a pea")
        assert "1 1/2 tbsp butter" in normalize_archaic_units("butter size of a hickory nut")

        # Archaic ingredients
        assert "oleomargarine (butter)" in normalize_archaic_units("1/2 cup oleo")
        assert "baking soda (saleratus)" in normalize_archaic_units("1 tsp saleratus")
        assert "whole milk (sweet milk)" in normalize_archaic_units("1 cup sweet milk")
        assert "buttermilk (sour milk)" in normalize_archaic_units("1 cup sour milk")

        # Historical volume units
        assert "1/2 cup" in normalize_archaic_units("1 gill sweet milk")
        assert "1/2 cup" in normalize_archaic_units("a gill of milk")
        assert "2 gal" in normalize_archaic_units("1 peck apples")
        assert "3/4 cup" in normalize_archaic_units("2 teacups flour")
        assert "1/4 cup" in normalize_archaic_units("1 wineglass water")
        assert "1 cup" in normalize_archaic_units("1 tumbler cider")
        assert "1/8 oz" in normalize_archaic_units("1 dram vanilla")
        assert "1/24 oz" in normalize_archaic_units("1 scruple saffron")

    def test_normalize_abbreviations(self):
        assert "2 tbsp sugar" in normalize_archaic_units("2 T sugar")
        assert "2 tbsp sugar" in normalize_archaic_units("2 T. sugar")
        assert "1 tsp vanilla" in normalize_archaic_units("1 t vanilla")
        assert "1 tsp vanilla" in normalize_archaic_units("1 t. vanilla")
        assert "3 tbsp flour" in normalize_archaic_units("3 Tblsp. flour")
        assert "3 tbsp flour" in normalize_archaic_units("3 Tblsp flour")
        assert "2 tbsp sugar" in normalize_archaic_units("2 Tbs sugar")
        assert "2 cup sugar" in normalize_archaic_units("2 C sugar")
        assert "1/2 cup milk" in normalize_archaic_units("1/2 C milk")
        assert "1 pkg yeast" in normalize_archaic_units("1 pkg. yeast")
        assert "1 pint cream" in normalize_archaic_units("1 pt. cream")
        assert "2 pint cream" in normalize_archaic_units("2 pts. cream")
        assert "1 quart milk" in normalize_archaic_units("1 qt. milk")
        assert "3 lb apples" in normalize_archaic_units("3 lbs. apples")
        assert "8 oz cheese" in normalize_archaic_units("8 ozs. cheese")

    def test_type_hints_evaluation_on_skills_modules(self):
        import typing
        import recipeboxer.agent.skills.vintage_handwriting_ocr.parser as v_parser
        import recipeboxer.agent.skills.print_clipping_extractor.parser as c_parser
        import recipeboxer.agent.skills.tools as tools_mod

        v_hints = typing.get_type_hints(v_parser)
        assert len(v_hints) >= 3
        c_hints = typing.get_type_hints(c_parser)
        assert len(c_hints) >= 1
        t_hints = typing.get_type_hints(tools_mod.deskew_card_image_tool)
        assert len(t_hints) >= 1

    def test_skill_markdown_spec_exists(self):
        skill_file = Path("recipeboxer/agent/skills/vintage_handwriting_ocr/SKILL.md")
        assert skill_file.exists()
        content = skill_file.read_text(encoding="utf-8")
        assert "name: vintage-handwriting-ocr" in content
        assert "oleo" in content
        assert "saleratus" in content

    def test_get_vintage_handwriting_prompt(self):
        prompt = get_vintage_handwriting_prompt()
        assert "culinary paleographer" in prompt
        assert "RecipeSchema" in prompt


class TestPrintClippingSkill:
    """Tests for print clipping and package panel extraction and advertising exclusion."""

    def test_is_promotional_line_detection(self):
        assert is_promotional_line("Clip and save this coupon!") is True
        assert is_promotional_line("Save 25¢ on your next purchase of Crisco") is True
        assert is_promotional_line("Save 50 cents on Baker Chocolate") is True
        assert is_promotional_line("Save $1.00 on two boxes") is True
        assert is_promotional_line("Offer expires Dec 31, 1965") is True
        assert is_promotional_line("Send 25c and 2 box tops to Box 414") is True
        assert is_promotional_line("Send 25¢ for our recipe booklet") is True
        assert is_promotional_line("Void where prohibited by law") is True
        assert is_promotional_line("Good Housekeeping Institute Page 42") is True
        assert is_promotional_line("Copyright 1962 General Mills") is True
        assert is_promotional_line("Cut along dotted line for 10¢ off") is True
        assert is_promotional_line("Limit one coupon per customer") is True
        assert is_promotional_line("Cash value 1/20 of 1 cent") is True
        assert is_promotional_line("Valuable store coupon inside") is True
        assert is_promotional_line("Satisfaction guaranteed or your money back") is True

        # Valid recipe lines and culinary directions must NOT be flagged as promotional
        assert is_promotional_line("Save 1 cup for topping") is False
        assert is_promotional_line("Save 2 tbsp for sauce") is False
        assert is_promotional_line("Save 1/2 cup for garnish") is False
        assert is_promotional_line("2 cups all-purpose flour") is False
        assert is_promotional_line("1 can (14 oz) sweetened condensed milk") is False
        assert is_promotional_line("Bake at 350°F for 30 minutes.") is False

    def test_filter_advertisement_noise(self):
        raw_lines = [
            "CRISCO SOUTHERN FRIED CHICKEN",
            "Clip and save this family recipe!",
            "1 cut-up frying chicken (2 1/2 to 3 lbs)",
            "1/2 cup Crisco for frying",
            "1 cup all-purpose flour",
            "Send 25¢ for our complete 48-page recipe book to Box 99",
            "Heat Crisco in heavy skillet. Dip chicken in flour and fry 25 mins.",
            "Offer expires Dec 31, 1958.",
        ]
        cleaned = filter_advertisement_noise(raw_lines)
        assert len(cleaned) == 5
        assert "CRISCO SOUTHERN FRIED CHICKEN" in cleaned
        assert "1 cup all-purpose flour" in cleaned
        assert not any("Clip and save" in line for line in cleaned)
        assert not any("Offer expires" in line for line in cleaned)
        assert not any("Send 25¢" in line for line in cleaned)

    def test_skill_markdown_spec_exists(self):
        skill_file = Path("recipeboxer/agent/skills/print_clipping_extractor/SKILL.md")
        assert skill_file.exists()
        content = skill_file.read_text(encoding="utf-8")
        assert "name: print-clipping-extractor" in content
        assert "Multi-Column" in content or "multi-column" in content

    def test_get_print_clipping_prompt(self):
        prompt = get_print_clipping_prompt()
        assert "newspaper clippings" in prompt
        assert "RecipeSchema" in prompt


class TestAntigravityToolWrappers:
    """Tests for callable Python tools exposed for Google Antigravity SDK agents."""

    def test_all_agent_tools_list(self):
        assert len(ALL_AGENT_TOOLS) >= 5
        tool_names = [t.__name__ for t in ALL_AGENT_TOOLS]
        assert "deskew_card_image_tool" in tool_names
        assert "crop_card_borders_tool" in tool_names
        assert "enhance_card_readability_tool" in tool_names
        assert "extract_paper_palette_tool" in tool_names
        assert "check_stain_symmetry_tool" in tool_names

    def test_deskew_tool_execution(self, tmp_path):
        sample_path = SAMPLE_CARDS_DIR / "rec_typ_beef_stroganoff_1956_front.jpg"
        if not sample_path.exists():
            pytest.skip("Sample card not found.")

        out_file = tmp_path / "deskewed_tool_out.jpg"
        res = deskew_card_image_tool(str(sample_path), output_path=str(out_file))

        assert res["success"] is True
        assert "skew_angle_deg" in res
        assert out_file.exists()

    def test_crop_tool_execution(self, tmp_path):
        sample_path = SAMPLE_CARDS_DIR / "rec_typ_beef_stroganoff_1956_front.jpg"
        if not sample_path.exists():
            pytest.skip("Sample card not found.")

        out_file = tmp_path / "cropped_tool_out.jpg"
        res = crop_card_borders_tool(str(sample_path), padding_px=5, output_path=str(out_file))

        assert res["success"] is True
        assert "bounding_box" in res
        assert out_file.exists()

    def test_enhance_readability_tool(self, tmp_path):
        sample_path = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_front.jpg"
        if not sample_path.exists():
            pytest.skip("Sample card not found.")

        out_file = tmp_path / "enhanced_tool_out.jpg"
        res = enhance_card_readability_tool(str(sample_path), clip_limit=2.0, output_path=str(out_file))

        assert res["success"] is True
        assert out_file.exists()

    def test_extract_palette_tool(self):
        sample_path = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_front.jpg"
        if not sample_path.exists():
            pytest.skip("Sample card not found.")

        res = extract_paper_palette_tool(str(sample_path), num_colors=3)
        assert res["success"] is True
        assert "paper_centroid" in res
        assert "palette" in res

    def test_check_stain_symmetry_tool(self):
        front_path = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_front.jpg"
        back_path = SAMPLE_CARDS_DIR / "rec_vhc_apple_crisp_1948_back.jpg"
        if not front_path.exists() or not back_path.exists():
            pytest.skip("Sample card pair not found.")

        res = check_stain_symmetry_tool(str(front_path), str(back_path))
        assert res["success"] is True
        assert "stain_similarity_score" in res
        assert 0.0 <= res["stain_similarity_score"] <= 1.0

    def test_classify_modality_tool(self):
        sample_path = SAMPLE_CARDS_DIR / "rec_typ_beef_stroganoff_1956_front.jpg"
        if not sample_path.exists():
            pytest.skip("Sample card not found.")

        res = classify_card_modality_tool(str(sample_path))
        assert res["success"] is True
        assert res["modality"] == "TYPEWRITTEN"

    def test_tool_output_path_creates_nested_directories(self, tmp_path):
        sample_path = SAMPLE_CARDS_DIR / "rec_typ_beef_stroganoff_1956_front.jpg"
        if not sample_path.exists():
            pytest.skip("Sample card not found.")

        nested_out = tmp_path / "deeply" / "nested" / "output_test.jpg"
        res = deskew_card_image_tool(str(sample_path), output_path=str(nested_out))
        assert res["success"] is True
        assert nested_out.exists()

    def test_tool_output_path_without_extension(self, tmp_path):
        sample_path = SAMPLE_CARDS_DIR / "rec_typ_beef_stroganoff_1956_front.jpg"
        if not sample_path.exists():
            pytest.skip("Sample card not found.")

        # Create temporary extensionless file
        no_ext_input = tmp_path / "raw_card_scan"
        no_ext_input.write_bytes(sample_path.read_bytes())

        res = deskew_card_image_tool(str(no_ext_input))
        assert res["success"] is True
        assert Path(res["output_path"]).exists()
        assert Path(res["output_path"]).suffix == ".jpg"
