"""Empirical Challenger 2 Verification Suite for Milestone 1.

Tests cover:
1. RecipeSchema edge cases: Unicode, emojis, fractions, multi-line strings,
   messy direction prefixes ("Step 1:", "(a)", "1 - ", etc.), missing optional fields,
   tag deduplication, and JSON/dict roundtrips.
2. VisualFingerprint edge cases: CIELAB color boundaries (0, 100, -128, 128),
   hex variations, quadrant handling, role confidence boundaries, missing/default submodels.
3. Settings environment variable overrides & alias matching: GEMINI_API_KEY alias,
   RECIPEBOXER_ prefix overrides, model resolution helpers, type coercion, and validation bounds.
4. Sequential numbering verification: Consecutive numbering when empty/whitespace steps appear in directions.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import pytest
from pydantic import ValidationError

from recipeboxer.config import Settings, get_settings
from recipeboxer.models.domain import (
    ArtifactType,
    BoundaryText,
    CardQuadrant,
    CardRole,
    InkColor,
    InkInfo,
    InkType,
    PaperColorInfo,
    PaperColorTone,
    PaperRulingInfo,
    PaperRulingType,
    PhysicalArtifact,
    VisualFingerprint,
)
from recipeboxer.models.recipe import RecipeSchema


# ============================================================================
# PART 1: RECIPESCHEMA EDGE CASES & ADVERSARIAL INPUTS
# ============================================================================

class TestRecipeSchemaEdgeCases:
    """Stress-tests for RecipeSchema parsing, normalization, and validation."""

    def test_unicode_and_emojis_in_all_fields(self):
        """RecipeSchema preserves complex multilingual Unicode, accents, and emojis."""
        recipe = RecipeSchema(
            title="🍰 Grand-mère's Crème Brûlée & 抹茶 Soufflé 🍵",
            description="Recette traditionnelle d'Alsace & 京都 — 100% délicieux! 🍓✨",
            yield_servings="6 portions / 6人前",
            prep_time="25 mins (environ)",
            cook_time="45 mins @ 180°C",
            total_time="1 hr 10 mins",
            ingredients=[
                "500 ml crème fraîche liquide 30% MG",
                "6 jaunes d'œufs fermiers 🥚",
                "75 g sucre de canne non raffiné",
                "1 gousse de vanille Bourbon de Madagascar",
                "2 tbsp 宇治抹茶 (Uji matcha powder) 🍵",
                "Pinch of fleur de sel 🧂",
            ],
            directions=[
                "1. Préchauffer le four à 100°C (th. 3-4).",
                "Step 2: Battre les jaunes avec le sucre jusqu'à blanchiment. 🥣",
                "3 - Fendre la gousse de vanille et infuser dans la crème chaude.",
                "(4) Verser dans des ramequins et enfourner 45 min.",
                "5) Laisser refroidir puis caraméliser au chalumeau 🔥.",
            ],
            tags=["Dessert", "Français", "日本語", "Pâtisserie", "Gourmet ✨", "dessert"],
            source_notes="Cahier de recettes manuscrit de Mémé Suzanne (1954) — Strasbourg",
        )

        assert "🍰 Grand-mère's Crème Brûlée" in recipe.title
        assert "京都" in recipe.description
        assert "🥚" in recipe.ingredients[1]
        assert "宇治抹茶" in recipe.ingredients[4]
        assert recipe.directions[0] == "1. Préchauffer le four à 100°C (th. 3-4)."
        assert recipe.directions[1] == "2. Battre les jaunes avec le sucre jusqu'à blanchiment. 🥣"
        assert recipe.directions[2] == "3. Fendre la gousse de vanille et infuser dans la crème chaude."
        assert recipe.directions[3] == "4. Verser dans des ramequins et enfourner 45 min."
        assert recipe.directions[4] == "5. Laisser refroidir puis caraméliser au chalumeau 🔥."
        # Verify tag deduplication with case-insensitivity
        assert recipe.tags == ["Dessert", "Français", "日本語", "Pâtisserie", "Gourmet ✨"]

    def test_messy_direction_prefixes(self):
        """RecipeSchema cleans various numbered step prefixes and renumbers sequentially."""
        raw_steps = [
            "Step 1: Preheat the oven to 375°F.",
            "STEP 2 - Beat butter and sugar in a large bowl.",
            "3. Gradually stir in dry ingredients.",
            "(4) Fold in chocolate chips.",
            "5) Drop by rounded tablespoons onto ungreased baking sheets.",
            "6 : Bake 9 to 11 minutes or until golden brown.",
            "Step 7. Cool on wire rack completely.",
            "8- Store in airtight container.",
            "Enjoy with cold milk!",  # Unnumbered step
        ]

        recipe = RecipeSchema(
            title="Chocolate Chip Cookies",
            ingredients=["2 cups flour", "1 cup chocolate chips"],
            directions=raw_steps,
        )

        expected = [
            "1. Preheat the oven to 375°F.",
            "2. Beat butter and sugar in a large bowl.",
            "3. Gradually stir in dry ingredients.",
            "4. Fold in chocolate chips.",
            "5. Drop by rounded tablespoons onto ungreased baking sheets.",
            "6. Bake 9 to 11 minutes or until golden brown.",
            "7. Cool on wire rack completely.",
            "8. Store in airtight container.",
            "9. Enjoy with cold milk!",
        ]
        assert recipe.directions == expected

    def test_alphabetical_and_bullet_direction_prefixes(self):
        """Test how RecipeSchema handles lettered/bulleted steps e.g. '(a)', '•', '-'."""
        raw_steps = [
            "(a) Mix the dry ingredients.",
            "(b) Add wet ingredients.",
            "• Fold gently until combined.",
            "- Bake at 350F for 20 minutes.",
        ]
        recipe = RecipeSchema(
            title="Pancake Batter",
            ingredients=["1 cup mix", "1 cup milk"],
            directions=raw_steps,
        )
        assert len(recipe.directions) == 4
        assert recipe.directions[0] == "1. (a) Mix the dry ingredients."
        assert recipe.directions[1] == "2. (b) Add wet ingredients."
        assert recipe.directions[2] == "3. • Fold gently until combined."
        assert recipe.directions[3] == "4. - Bake at 350F for 20 minutes."

    def test_multiline_ingredients_and_directions_cleaning(self):
        """Test ingredients with internal newlines, carriage returns, and tabs."""
        recipe = RecipeSchema(
            title="Multiline Test Cake",
            ingredients=[
                "  1 cup all-purpose flour\n(sifted twice)  ",
                "\t2 tbsp granulated sugar\r\n(divided)\t",
                "   ",  # Whitespace only -> discarded
                "1/2 tsp kosher salt",
                "",  # Empty string -> discarded
            ],
            directions=[
                "\n  Step 1:\n  Sift flour and sugar together.  \n",
                "\r\n2. Whisk eggs and vanilla in a separate bowl.\r\n",
                "3. Combine wet and dry mixtures.",
            ],
        )

        assert len(recipe.ingredients) == 3
        assert recipe.ingredients[0] == "1 cup all-purpose flour\n(sifted twice)"
        assert recipe.ingredients[1] == "2 tbsp granulated sugar\r\n(divided)"
        assert recipe.ingredients[2] == "1/2 tsp kosher salt"

        assert len(recipe.directions) == 3
        assert recipe.directions[0] == "1. Sift flour and sugar together."
        assert recipe.directions[1] == "2. Whisk eggs and vanilla in a separate bowl."
        assert recipe.directions[2] == "3. Combine wet and dry mixtures."

    def test_reproduce_bug_interleaved_empty_directions_numbering_gap(self):
        """Verify interleaved empty lines in directions do not cause numbering gaps.

        Step counter idx is incremented only for non-empty/non-whitespace steps,
        ensuring strictly consecutive numbering ('1. ', '2. ', '3. ').
        """
        raw_directions = [
            "Step 1: First step",
            "Step 2: Second step",
            "   \n\t  \r\n",  # Empty / whitespace line in the middle
            "Step 3: Third step",
        ]
        recipe = RecipeSchema(
            title="Interleaved Empty Directions Fix",
            ingredients=["1 cup water"],
            directions=raw_directions,
        )

        actual_directions = recipe.directions
        assert len(actual_directions) == 3
        assert actual_directions == [
            "1. First step",
            "2. Second step",
            "3. Third step",
        ]

    def test_missing_optional_fields_defaults(self):
        """Verify defaults when all optional fields are omitted."""
        recipe = RecipeSchema(
            title="Minimalist Recipe",
        )
        assert recipe.recipe_id.startswith("rec_")
        assert recipe.title == "Minimalist Recipe"
        assert recipe.description == ""
        assert recipe.yield_servings == ""
        assert recipe.prep_time == ""
        assert recipe.cook_time == ""
        assert recipe.total_time == ""
        assert recipe.ingredients == []
        assert recipe.directions == []
        assert recipe.tags == []
        assert recipe.source_notes == ""

    def test_explicit_none_for_optional_fields(self):
        """Verify passing None for Optional[str] fields behaves gracefully."""
        recipe = RecipeSchema(
            title="Explicit None Fields",
            description=None,
            yield_servings=None,
            prep_time=None,
            cook_time=None,
            total_time=None,
            source_notes=None,
        )
        assert recipe.title == "Explicit None Fields"

    def test_empty_or_whitespace_title_rejected(self):
        """Title must contain at least one non-whitespace character."""
        with pytest.raises(ValidationError):
            RecipeSchema(title="")

        with pytest.raises(ValidationError):
            RecipeSchema(title="   \t\n  ")

    def test_fractions_and_special_vulgar_fractions(self):
        """Fractions like ½, ⅓, ¼, ¾, 1½ are preserved accurately."""
        recipe = RecipeSchema(
            title="Traditional Shortbread",
            ingredients=[
                "1 ½ cups unbleached all-purpose flour",
                "½ cup confectioners' sugar",
                "¾ cup salted butter, softened",
                "¼ tsp pure almond extract",
                "⅛ tsp fine sea salt",
            ],
            directions=["1. Cream butter and sugar.", "2. Mix in flour.", "3. Bake at 300°F."],
        )
        assert "1 ½ cups" in recipe.ingredients[0]
        assert "½ cup" in recipe.ingredients[1]
        assert "¾ cup" in recipe.ingredients[2]
        assert "¼ tsp" in recipe.ingredients[3]
        assert "⅛ tsp" in recipe.ingredients[4]

    def test_json_roundtrip_fidelity(self):
        """RecipeSchema survives JSON serialization and deserialization without loss."""
        orig = RecipeSchema(
            recipe_id="rec_custom123",
            title="Apple Tarte Tatin",
            description="Upside-down pastry with caramelized apples",
            yield_servings="8 slices",
            prep_time="30 mins",
            cook_time="45 mins",
            total_time="1 hr 15 mins",
            ingredients=["6 Honeycrisp apples", "100g butter", "150g sugar", "1 sheet puff pastry"],
            directions=["1. Caramelize sugar in pan.", "2. Arrange apples.", "3. Top with pastry and bake."],
            tags=["French", "Pastry", "Apples"],
            source_notes="Escoffier Guide Culinaire p. 412",
        )
        json_repr = orig.model_dump_json()
        restored = RecipeSchema.model_validate_json(json_repr)
        assert restored.model_dump() == orig.model_dump()


# ============================================================================
# PART 2: VISUALFINGERPRINT & SUBMODEL EDGE CASES
# ============================================================================

class TestVisualFingerprintEdgeCases:
    """Stress-tests for VisualFingerprint and its constituent substrate models."""

    def test_cielab_exact_boundary_values(self):
        """CIELAB boundary values: L (0.0 to 100.0), a (-128.0 to 128.0), b (-128.0 to 128.0)."""
        # Absolute minimums
        color_min = PaperColorInfo(lab_l=0.0, lab_a=-128.0, lab_b=-128.0, hex_approx="#000000")
        assert color_min.lab_l == 0.0
        assert color_min.lab_a == -128.0
        assert color_min.lab_b == -128.0

        # Absolute maximums
        color_max = PaperColorInfo(lab_l=100.0, lab_a=128.0, lab_b=128.0, hex_approx="#FFFFFF")
        assert color_max.lab_l == 100.0
        assert color_max.lab_a == 128.0
        assert color_max.lab_b == 128.0

        # Midpoint / Neutral Gray
        color_mid = PaperColorInfo(lab_l=50.0, lab_a=0.0, lab_b=0.0, hex_approx="#808080")
        assert color_mid.lab_l == 50.0

    @pytest.mark.parametrize(
        "invalid_kwargs",
        [
            {"lab_l": -0.01},
            {"lab_l": 100.01},
            {"lab_a": -128.01},
            {"lab_a": 128.01},
            {"lab_b": -128.01},
            {"lab_b": 128.01},
        ],
    )
    def test_cielab_out_of_bounds_rejection(self, invalid_kwargs):
        """Values exceeding CIELAB limits trigger ValidationError."""
        with pytest.raises(ValidationError):
            PaperColorInfo(**invalid_kwargs)

    @pytest.mark.parametrize(
        "hex_input, expected_normalized",
        [
            ("#ffffff", "#FFFFFF"),
            ("ffffff", "#FFFFFF"),
            ("#000000", "#000000"),
            ("000000", "#000000"),
            ("#f4e8c1", "#F4E8C1"),
            ("F4E8C1", "#F4E8C1"),
            ("#A1B2C3", "#A1B2C3"),
            ("  #1a2b3c  ", "#1A2B3C"),
        ],
    )
    def test_hex_normalization(self, hex_input, expected_normalized):
        """Hex strings are stripped, uppercased, and ensured with '#' prefix."""
        color = PaperColorInfo(hex_approx=hex_input)
        assert color.hex_approx == expected_normalized

    @pytest.mark.parametrize(
        "invalid_hex",
        [
            "",
            "   ",
            "#FFF",       # 3-digit hex not allowed
            "FFF",
            "#FFFFFFF",   # 7 digits
            "#GGGGGG",   # Non-hex characters
            "not_a_hex",
            "#12 34",
        ],
    )
    def test_invalid_hex_rejection(self, invalid_hex):
        """Invalid hex strings fail validation."""
        with pytest.raises(ValidationError):
            PaperColorInfo(hex_approx=invalid_hex)

    def test_physical_artifact_quadrants_and_types(self):
        """PhysicalArtifact accepts enum members, valid strings, and custom descriptors."""
        # Standard enum quadrant
        art1 = PhysicalArtifact(
            artifact_type=ArtifactType.OIL_STAIN,
            quadrant=CardQuadrant.TOP_LEFT,
            description="Olive oil drop",
        )
        assert art1.quadrant == CardQuadrant.TOP_LEFT

        # String quadrant representation
        art2 = PhysicalArtifact(
            artifact_type="COFFEE_TEA_RING",
            quadrant="BOTTOM_RIGHT",
            severity="SEVERE",
            description="Mug ring mark",
        )
        assert art2.quadrant == "BOTTOM_RIGHT"
        assert art2.artifact_type == "COFFEE_TEA_RING"

        # Edge quadrant positions
        art3 = PhysicalArtifact(
            artifact_type=ArtifactType.TEAR_EDGE,
            quadrant=CardQuadrant.EDGE_TOP,
            description="1cm rip along top edge",
        )
        assert art3.quadrant == CardQuadrant.EDGE_TOP

    def test_card_role_and_role_confidence_boundaries(self):
        """VisualFingerprint role_confidence is bounded between 0.0 and 1.0."""
        # Lower bound confidence 0.0
        fp_min = VisualFingerprint(
            card_id="card_unknown.jpg",
            image_path="/scans/card_unknown.jpg",
            sha256_hash="00" * 32,
            role=CardRole.UNKNOWN,
            role_confidence=0.0,
        )
        assert fp_min.role_confidence == 0.0

        # Upper bound confidence 1.0
        fp_max = VisualFingerprint(
            card_id="card_front.jpg",
            image_path="/scans/card_front.jpg",
            sha256_hash="ff" * 32,
            role=CardRole.FRONT_ONLY,
            role_confidence=1.0,
        )
        assert fp_max.role_confidence == 1.0

        # Out of bounds confidence
        with pytest.raises(ValidationError):
            VisualFingerprint(
                card_id="c",
                image_path="/p",
                sha256_hash="a" * 64,
                role=CardRole.STANDALONE,
                role_confidence=-0.01,
            )

        with pytest.raises(ValidationError):
            VisualFingerprint(
                card_id="c",
                image_path="/p",
                sha256_hash="a" * 64,
                role=CardRole.STANDALONE,
                role_confidence=1.01,
            )

    def test_visual_fingerprint_default_submodels(self):
        """Omitting paper_color, ruling, ink, artifacts, and boundary_text populates valid defaults."""
        fp = VisualFingerprint(
            card_id="minimal_card.jpg",
            image_path="/scans/minimal_card.jpg",
            sha256_hash="abcd" * 16,
            role=CardRole.STANDALONE,
        )
        assert fp.paper_color.hex_approx == "#FFFFFF"
        assert fp.paper_color.lab_l == 100.0
        assert fp.ruling.has_ruling is False
        assert fp.ruling.ruling_type == "NONE"
        assert fp.ink.ink_color == "BLACK"
        assert fp.artifacts == []
        assert fp.boundary_text.first_line == ""
        assert fp.boundary_text.last_line == ""
        assert isinstance(fp.extracted_at, datetime)

    def test_boundary_text_flags(self):
        """BoundaryText continuation flags correctly represent physical card cues."""
        bt = BoundaryText(
            first_line="Ingredients: Flour, Sugar...",
            last_line="Continue directions on back (PTO) ->",
            has_continuation_symbol=True,
            is_cut_off_mid_sentence=True,
        )
        assert bt.has_continuation_symbol is True
        assert bt.is_cut_off_mid_sentence is True


# ============================================================================
# PART 3: SETTINGS ENV OVERRIDES & ALIAS MATCHING
# ============================================================================

class TestSettingsOverridesAndAliases:
    """Stress-tests for Pydantic Settings env overrides, aliases, and validation constraints."""

    def test_gemini_api_key_alias_priority(self):
        """Test both GEMINI_API_KEY and RECIPEBOXER_GEMINI_API_KEY aliases."""
        # 1. Unprefixed standard GEMINI_API_KEY
        with patch.dict(os.environ, {"GEMINI_API_KEY": "standard_env_key_123"}, clear=False):
            os.environ.pop("RECIPEBOXER_GEMINI_API_KEY", None)
            s = Settings()
            assert s.gemini_api_key == "standard_env_key_123"

        # 2. Prefixed RECIPEBOXER_GEMINI_API_KEY
        with patch.dict(os.environ, {"RECIPEBOXER_GEMINI_API_KEY": "prefixed_env_key_456"}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            s = Settings()
            assert s.gemini_api_key == "prefixed_env_key_456"

        # 3. Both present -> AliasChoices prioritizes the first alias in declaration
        with patch.dict(
            os.environ,
            {
                "RECIPEBOXER_GEMINI_API_KEY": "prefixed_key_winner",
                "GEMINI_API_KEY": "standard_key_runner_up",
            },
            clear=False,
        ):
            s = Settings()
            assert s.gemini_api_key == "prefixed_key_winner"

    def test_comprehensive_env_variable_overrides(self):
        """All configuration fields can be overridden via RECIPEBOXER_ prefixed env vars."""
        custom_env = {
            "RECIPEBOXER_DEFAULT_MODEL": "gemini-2.5-flash",
            "RECIPEBOXER_FINGERPRINT_MODEL": "gemini-custom-fp",
            "RECIPEBOXER_CORRELATION_MODEL": "gemini-custom-corr",
            "RECIPEBOXER_EXTRACTION_MODEL": "gemini-custom-extract",
            "RECIPEBOXER_THINKING_MODE": "true",
            "RECIPEBOXER_THINKING_BUDGET_TOKENS": "2048",
            "RECIPEBOXER_MAX_CONCURRENCY": "20",
            "RECIPEBOXER_SLOW_BATCH_SIZE": "10",
            "RECIPEBOXER_SLOW_BATCH_INTERVAL_SECONDS": "4.5",
            "RECIPEBOXER_CACHE_DIR": "/tmp/custom_cache",
            "RECIPEBOXER_CACHE_DB_FILENAME": "custom_store.db",
            "RECIPEBOXER_EXPORT_DIR": "/tmp/custom_exports",
            "RECIPEBOXER_API_HOST": "0.0.0.0",
            "RECIPEBOXER_API_PORT": "8888",
            "RECIPEBOXER_DEBUG": "true",
            "RECIPEBOXER_HEURISTIC_SCREEN_THRESHOLD": "0.65",
            "RECIPEBOXER_HEURISTIC_WEIGHT": "0.40",
            "RECIPEBOXER_SEMANTIC_WEIGHT": "0.60",
        }

        with patch.dict(os.environ, custom_env, clear=False):
            s = Settings()
            assert s.default_model == "gemini-2.5-flash"
            assert s.fingerprint_model == "gemini-custom-fp"
            assert s.correlation_model == "gemini-custom-corr"
            assert s.extraction_model == "gemini-custom-extract"
            assert s.thinking_mode is True
            assert s.thinking_budget_tokens == 2048
            assert s.max_concurrency == 20
            assert s.slow_batch_size == 10
            assert s.slow_batch_interval_seconds == 4.5
            assert s.cache_dir == Path("/tmp/custom_cache")
            assert s.cache_db_filename == "custom_store.db"
            assert s.export_dir == Path("/tmp/custom_exports")
            assert s.api_host == "0.0.0.0"
            assert s.api_port == 8888
            assert s.debug is True
            assert s.heuristic_screen_threshold == 0.65
            assert s.heuristic_weight == 0.40
            assert s.semantic_weight == 0.60

            # Verify resolved helper methods
            assert s.get_fingerprint_model() == "gemini-custom-fp"
            assert s.get_correlation_model() == "gemini-custom-corr"
            assert s.get_extraction_model() == "gemini-custom-extract"
            assert s.cache_db_path == Path("/tmp/custom_cache/custom_store.db")

    def test_settings_validation_bounds(self):
        """Settings enforces numeric boundary constraints."""
        # max_concurrency < 1
        with pytest.raises(ValidationError):
            Settings(max_concurrency=0)

        # max_concurrency > 50
        with pytest.raises(ValidationError):
            Settings(max_concurrency=51)

        # slow_batch_size < 1
        with pytest.raises(ValidationError):
            Settings(slow_batch_size=0)

        # slow_batch_interval_seconds < 0
        with pytest.raises(ValidationError):
            Settings(slow_batch_interval_seconds=-1.0)

        # api_port < 1024
        with pytest.raises(ValidationError):
            Settings(api_port=80)

        # heuristic_screen_threshold > 1.0
        with pytest.raises(ValidationError):
            Settings(heuristic_screen_threshold=1.5)

        # heuristic_weight < 0.0
        with pytest.raises(ValidationError):
            Settings(heuristic_weight=-0.1)

    def test_boolean_env_coercion(self):
        """Pydantic Settings properly parses various boolean string representations."""
        for true_val in ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"]:
            with patch.dict(os.environ, {"RECIPEBOXER_THINKING_MODE": true_val}, clear=False):
                s = Settings()
                assert s.thinking_mode is True, f"Failed for truthy value: {true_val}"

        for false_val in ["0", "false", "False", "FALSE", "no", "NO", "off", "OFF"]:
            with patch.dict(os.environ, {"RECIPEBOXER_THINKING_MODE": false_val}, clear=False):
                s = Settings()
                assert s.thinking_mode is False, f"Failed for falsy value: {false_val}"


# ============================================================================
# PART 5: MILESTONE 1 BATCH DEFAULT TAGS & EXTRACTION PROPAGATION
# ============================================================================

class TestMilestone1BatchDefaultTagsAndOrchestration:
    """Tests covering batch default tags propagation during pipeline ingestion and recipe extraction."""

    def test_pipeline_start_request_default_tags_field(self):
        """PipelineStartRequest properly accepts and defaults default_tags list."""
        from recipeboxer.models.pipeline import PipelineStartRequest
        req1 = PipelineStartRequest(scans_dir="/tmp/scans")
        assert req1.default_tags == []

        req2 = PipelineStartRequest(
            scans_dir="/tmp/scans",
            default_tags=["Baking", "Dessert", "1950s"],
        )
        assert req2.default_tags == ["Baking", "Dessert", "1950s"]

    def test_resolve_ingestion_config_propagates_default_tags(self):
        """RecipeBoxerAgent._resolve_ingestion_config extracts default_tags from dict or object."""
        from recipeboxer.agent.orchestrator import RecipeBoxerAgent
        agent = RecipeBoxerAgent()

        cfg_dict = {
            "mode": "parallel",
            "default_tags": ["Family Recipes", "Vintage"],
        }
        ingest_cfg = agent._resolve_ingestion_config(cfg_dict)
        assert ingest_cfg.default_tags == ["Family Recipes", "Vintage"]

        # Comma string format in dict
        cfg_str_dict = {
            "mode": "parallel",
            "default_tags": "Holiday, Cookies, Winter",
        }
        ingest_cfg2 = agent._resolve_ingestion_config(cfg_str_dict)
        assert ingest_cfg2.default_tags == ["Holiday", "Cookies", "Winter"]

    @pytest.mark.asyncio
    async def test_agent_recipe_extraction_merges_session_default_tags(self):
        """RecipeBoxerAgent extraction loop merges session batch default tags into recipe tags."""
        from recipeboxer.agent.orchestrator import RecipeBoxerAgent
        from recipeboxer.agent.session import PipelineSession
        from recipeboxer.models.domain import CardRole, PaperColorInfo, PaperRulingInfo, VisualFingerprint
        from recipeboxer.models.hitl import CardPairApproval, HITLDecision
        from recipeboxer.models.pipeline import PipelineStartRequest

        config = PipelineStartRequest(
            scans_dir="/tmp/scans",
            default_tags=["Grandma's Box", "Vintage 1960s"],
        )
        session = PipelineSession(session_id="sess_tags_test", config=config)

        fp1 = VisualFingerprint(
            card_id="card_01.jpg",
            image_path="/tmp/card_01.jpg",
            sha256_hash="hash1",
            role=CardRole.FRONT_ONLY,
            role_confidence=0.9,
            paper_color=PaperColorInfo(tone_name="White"),
            ruling=PaperRulingInfo(has_ruling=False),
        )
        session.fingerprints["card_01.jpg"] = fp1

        # Mock recipe extractor returns a recipe with its own tags
        extracted_recipe = RecipeSchema(
            title="Peach Cobbler",
            ingredients=["4 peaches", "1 cup sugar", "1 cup flour"],
            directions=["1. Slice peaches.", "2. Mix batter and bake at 350F."],
            tags=["Dessert", "Fruit", "Grandma's Box"],  # contains a duplicate tag with different position
        )

        async def mock_extract(front, back):
            return extracted_recipe

        session.recipe_extractor_fn = mock_extract

        session.approvals = [
            CardPairApproval(
                proposal_id="prop_01",
                front_card_id="card_01.jpg",
                decision=HITLDecision.APPROVE,
            )
        ]

        # Execute extraction stage manually or run agent
        agent = RecipeBoxerAgent()

        recipe = await agent._extract_single_recipe(session, fp1, None)
        assert recipe is not None

        # Verify session default tags are combined and deduplicated
        session_tags = session.config.default_tags
        combined_tags = list(session_tags) + list(recipe.tags or [])
        final_tags = RecipeSchema.clean_and_deduplicate_tags(combined_tags)

        assert final_tags == ["Grandma's Box", "Vintage 1960s", "Dessert", "Fruit"]
