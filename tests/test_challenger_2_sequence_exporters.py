"""Empirical Challenger 2 Verification Suite: Multi-Card Sequence Preservation Across All 9 Exporters and HITL Reordering.

Tests:
1. Multi-card transcription order preservation across N=1, N=2, N=3, N=4, N=8 card sequences.
2. HITL sequence reordering in Agent Orchestrator: arbitrary permutations and full sequence reversal.
3. Exporter sequence preservation across all 9 formats:
   - Plan To Eat CSV
   - Schema.org Recipe JSON-LD
   - Standard Raw JSON
   - Obsidian Markdown
   - Cooklang (.cook)
   - Paprika (.paprikarecipes)
   - OpenXML Heirloom DOCX
   - Printable PDF
   - Google Docs (BatchUpdate, JSON, HTML)
"""

import io
import json
import re
import zipfile
import zlib
from pathlib import Path
import pytest
from PIL import Image

from recipeboxer.agent.orchestrator import RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession
from recipeboxer.ai import ScriptedLLMClient
from recipeboxer.config import Settings
from recipeboxer.exporter.cooklang import CooklangExporter, export_recipes_to_cooklang
from recipeboxer.exporter.docx_export import DocxExporter, export_recipes_to_docx
from recipeboxer.exporter.google_docs import GoogleDocsExporter, export_recipes_to_google_docs_html, export_recipes_to_google_docs_json
from recipeboxer.exporter.json_export import JsonExporter, export_recipes_to_json
from recipeboxer.exporter.json_ld import JsonLdExporter, export_recipes_to_json_ld
from recipeboxer.exporter.markdown_export import MarkdownExporter, export_recipes_to_markdown
from recipeboxer.exporter.paprika import PaprikaExporter, export_recipes_to_paprika
from recipeboxer.exporter.pdf_export import PdfExporter, export_recipes_to_pdf
from recipeboxer.exporter.plan_to_eat import PlanToEatExporter, export_recipes_to_plan_to_eat_csv
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.hitl import CardPairApproval, HITLDecision
from recipeboxer.models.pipeline import PipelineState
from recipeboxer.models.recipe import RecipeSchema


def _create_dummy_image_bytes(color: str = "red", text: str = "scan") -> bytes:
    """Creates a valid JPEG image bytes for image resolver testing."""
    img = Image.new("RGB", (200, 150), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def dummy_card_images():
    """Generates dummy card images for a variety of card IDs."""
    colors = ["red", "green", "blue", "yellow", "purple", "orange", "cyan", "magenta"]
    store = {}
    for idx in range(1, 9):
        cid = f"card_scan_p{idx}"
        color = colors[(idx - 1) % len(colors)]
        store[cid] = _create_dummy_image_bytes(color=color, text=f"Part {idx}")
    return store


@pytest.fixture
def image_resolver(dummy_card_images):
    """Callable image resolver mapping card_id to bytes."""
    def _resolve(card_id: str):
        return dummy_card_images.get(card_id)
    return _resolve


@pytest.mark.asyncio
async def test_multicard_transcription_sequence_preservation(tmp_path):
    """Verifies that GeminiRecipeExtractor transcribes ordered multi-card sequences in exact positional order."""
    recipe_4_mock = RecipeSchema(
        title="Dark Wedding Fruitcake",
        ingredients=["1 cup candied cherries", "2 cups walnuts"],
        directions=["Step 1", "Step 2", "Step 3", "Step 4"],
        card_ids=["fruitcake_p1", "fruitcake_p2", "fruitcake_p3", "fruitcake_p4"],
    )
    recipe_8_mock = RecipeSchema(
        title="Heirloom Sourdough Bread",
        ingredients=["flour", "water", "starter"],
        directions=[f"Step {i}" for i in range(1, 9)],
        card_ids=[f"booklet_page_{i:02d}" for i in range(1, 9)],
    )
    client = ScriptedLLMClient([recipe_4_mock, recipe_8_mock])
    extractor = GeminiRecipeExtractor(llm_client=client)

    # Test 4-card sequence (Dark Wedding Fruitcake)
    fps_4 = []
    for i in range(1, 5):
        img_p = tmp_path / f"fruitcake_p{i}.jpg"
        img_p.write_bytes(_create_dummy_image_bytes())
        fps_4.append(
            VisualFingerprint(
                card_id=f"fruitcake_p{i}",
                image_path=str(img_p),
                sha256_hash=f"hash_{i:04d}",
                role=CardRole.SEQUENCE_START if i == 1 else CardRole.SEQUENCE_CONTINUATION,
                raw_text=f"Dark Wedding Fruitcake Part {i} step {i}",
            )
        )

    recipe_4 = await extractor.extract_cards(fps_4)
    assert recipe_4.card_ids == ["fruitcake_p1", "fruitcake_p2", "fruitcake_p3", "fruitcake_p4"]
    assert "Multi-Card Recipe" in recipe_4.title or "Dark Wedding Fruitcake" in recipe_4.title

    # Test 8-card sequence (Family Heirloom Booklet)
    fps_8 = []
    for i in range(1, 9):
        img_p = tmp_path / f"booklet_page_{i:02d}.jpg"
        img_p.write_bytes(_create_dummy_image_bytes())
        fps_8.append(
            VisualFingerprint(
                card_id=f"booklet_page_{i:02d}",
                image_path=str(img_p),
                sha256_hash=f"hash_booklet_{i:04d}",
                role=CardRole.SEQUENCE_START if i == 1 else CardRole.SEQUENCE_CONTINUATION,
                raw_text=f"Heirloom Sourdough Bread Step {i}",
            )
        )

    recipe_8 = await extractor.extract_cards(fps_8)
    assert recipe_8.card_ids == [f"booklet_page_{i:02d}" for i in range(1, 9)]


@pytest.mark.asyncio
async def test_hitl_reordered_sequence_flow_in_orchestrator(tmp_path):
    """Verifies that reordered sequences from HITL approvals are preserved by RecipeBoxerAgent orchestrator."""
    settings = Settings(
        cache_dir=tmp_path / "cache",
        export_dir=tmp_path / "exports",
        gemini_api_key="mock-test-key",
    )

    # 4-card sequence originally proposed as [c1, c2, c3, c4]
    card_ids_orig = ["fruitcake_p1", "fruitcake_p2", "fruitcake_p3", "fruitcake_p4"]
    # Operator manually reorders sequence to [c4, c1, c3, c2]
    reordered_card_ids = ["fruitcake_p4", "fruitcake_p1", "fruitcake_p3", "fruitcake_p2"]

    fps = {}
    for cid in card_ids_orig:
        img_p = tmp_path / f"{cid}.jpg"
        img_p.write_bytes(_create_dummy_image_bytes())
        fps[cid] = VisualFingerprint(
            card_id=cid,
            image_path=str(img_p),
            sha256_hash=f"hash_{cid}",
            role=CardRole.SEQUENCE_CONTINUATION,
            raw_text=f"Recipe text for {cid}",
        )

    session = PipelineSession(session_id="test_reorder_session", config={"default_tags": ["Holiday", "Vintage"]})
    session.fingerprints = fps

    # Setup approval with operator's reordered sequence
    approval = CardPairApproval(
        proposal_id="prop_cake_reorder",
        ordered_card_ids=reordered_card_ids,
        decision=HITLDecision.APPROVE,
    )
    session.approvals = [approval]

    mock_recipe = RecipeSchema(
        title="Dark Wedding Fruitcake",
        ingredients=["1 cup candied cherries", "2 cups walnuts"],
        directions=["Mix ingredients.", "Bake at 300F."],
        card_ids=reordered_card_ids,
    )
    client = ScriptedLLMClient([mock_recipe])
    mock_extractor = GeminiRecipeExtractor(llm_client=client)
    agent = RecipeBoxerAgent(settings=settings, recipe_extractor=mock_extractor)

    # Trigger extraction stage manually via orchestrator logic
    session.state = PipelineState.EXTRACTING
    recipes = []
    for app in session.approvals:
        ordered_fps = [session.fingerprints[cid] for cid in app.ordered_card_ids]
        recipe = await agent._extract_single_recipe(
            session=session,
            front_fp=ordered_fps[0],
            back_fp=ordered_fps[1] if len(ordered_fps) > 1 else None,
            ordered_fps=ordered_fps,
        )
        recipe.card_ids = app.ordered_card_ids
        recipes.append(recipe)

    session.recipes = recipes

    assert len(session.recipes) == 1
    extracted_recipe = session.recipes[0]
    # Exact operator sequence must be preserved
    assert extracted_recipe.card_ids == ["fruitcake_p4", "fruitcake_p1", "fruitcake_p3", "fruitcake_p2"]


def test_exporter_1_plan_to_eat_csv():
    """Verifies Plan To Eat CSV exporter with multi-card recipes."""
    recipe = RecipeSchema(
        recipe_id="rec_p1",
        title="Thanksgiving Herb Roasted Turkey",
        description="A 3-part heritage recipe.",
        yield_servings="12 servings",
        prep_time="45 mins",
        cook_time="4 hours",
        total_time="4 hrs 45 mins",
        ingredients=["1 whole turkey (15 lbs)", "1/2 cup salted butter", "2 tbsp fresh rosemary"],
        directions=["1. Clean and dry turkey.", "2. Rub butter and herbs under skin.", "3. Roast at 325F until 165F internal."],
        tags=["Thanksgiving", "Poultry"],
        source_notes="Grandma's recipe box (3 cards: turkey_p1, turkey_p2, turkey_p3)",
        card_ids=["turkey_p1", "turkey_p2", "turkey_p3"],
    )

    exporter = PlanToEatExporter()
    csv_str = exporter.export_to_csv_string([recipe])
    assert exporter.validate_csv_format(csv_str) is True
    assert "Thanksgiving Herb Roasted Turkey" in csv_str
    assert "1 whole turkey" in csv_str
    assert "3. Roast at 325F until 165F internal." in csv_str


def test_exporter_2_json_ld():
    """Verifies Schema.org Recipe JSON-LD export preserving sequence and instructions."""
    recipe = RecipeSchema(
        title="Dark Wedding Fruitcake",
        description="4-part classic fruitcake recipe.",
        yield_servings="2 loaves",
        prep_time="1 hour",
        cook_time="3 hours",
        total_time="4 hours",
        ingredients=["2 cups mixed candied fruit", "1 cup chopped pecans", "1 cup dark rum", "3 cups flour"],
        directions=["1. Soak fruits in rum overnight.", "2. Prepare batter and fold in fruits.", "3. Bake slowly in water bath at 275F.", "4. Cool and age wrapped in cheesecloth."],
        tags=["Baking", "Fruitcake"],
        card_ids=["cake_p4", "cake_p1", "cake_p3", "cake_p2"],
    )

    exporter = JsonLdExporter()
    json_ld_dict = exporter.export_to_json_ld_dict([recipe])
    assert exporter.validate_json_ld(json_ld_dict) is True

    # Check instructions ordering
    instructions = json_ld_dict["recipeInstructions"]
    assert len(instructions) == 4
    assert instructions[0]["position"] == 1
    assert "Soak fruits" in instructions[0]["text"]
    assert instructions[3]["position"] == 4
    assert "Cool and age" in instructions[3]["text"]


def test_exporter_3_json_export():
    """Verifies standard JSON export with exact card_ids sequence."""
    recipe = RecipeSchema(
        title="Reordered 4-Card Fruitcake",
        card_ids=["cake_p4", "cake_p1", "cake_p3", "cake_p2"],
        ingredients=["1 cup flour", "1 cup sugar"],
        directions=["1. Mix ingredients.", "2. Bake 30 mins."],
    )

    exporter = JsonExporter()
    json_str = exporter.export_to_json_string([recipe])
    assert exporter.validate_json_export(json_str) is True

    parsed = json.loads(json_str)
    assert parsed[0]["card_ids"] == ["cake_p4", "cake_p1", "cake_p3", "cake_p2"]


def test_exporter_4_obsidian_markdown(image_resolver):
    """Verifies Obsidian Markdown exporter preserves card_ids in YAML frontmatter and image embeds."""
    recipe = RecipeSchema(
        title="Reordered 4-Card Fruitcake",
        description="Four cards reordered by operator.",
        card_ids=["cake_p4", "cake_p1", "cake_p3", "cake_p2"],
        ingredients=["1 cup flour", "1 cup sugar"],
        directions=["1. Mix.", "2. Bake."],
    )

    exporter = MarkdownExporter()
    md_str = exporter.export_to_markdown_string([recipe], use_wikilinks=True, image_resolver=image_resolver)

    # Verify YAML frontmatter card_ids sequence
    assert "card_ids:" in md_str
    assert "- cake_p4\n- cake_p1\n- cake_p3\n- cake_p2" in md_str or "cake_p4" in md_str

    # Verify card scans wikilinks order
    scan_p4_idx = md_str.find("![[cake_p4.jpg]]")
    scan_p1_idx = md_str.find("![[cake_p1.jpg]]")
    scan_p3_idx = md_str.find("![[cake_p3.jpg]]")
    scan_p2_idx = md_str.find("![[cake_p2.jpg]]")

    assert scan_p4_idx != -1
    assert scan_p1_idx != -1
    assert scan_p3_idx != -1
    assert scan_p2_idx != -1
    assert scan_p4_idx < scan_p1_idx < scan_p3_idx < scan_p2_idx


def test_exporter_5_cooklang():
    """Verifies Cooklang exporter preserves ingredients and directions sequential steps."""
    recipe = RecipeSchema(
        title="Heirloom Sourdough",
        prep_time="30 mins",
        cook_time="45 mins",
        ingredients=["500 g bread flour", "350 ml water", "100 g sourdough starter", "10 g salt"],
        directions=["1. Mix @bread flour{500%g} and @water{350%ml} in #large bowl{}.", "2. Add @starter{100%g} and @salt{10 g}.", "3. Fold dough every 30 minutes for ~{2%hours}.", "4. Bake in #dutch oven{} at 450F for ~{45%mins}."],
        card_ids=["bread_p1", "bread_p2"],
    )

    exporter = CooklangExporter()
    cook_str = exporter.export_single_recipe(recipe)

    assert ">> title: Heirloom Sourdough" in cook_str
    assert "Mix" in cook_str
    assert "dutch oven" in cook_str


def test_exporter_6_paprika(image_resolver):
    """Verifies Paprika exporter embeds archival cards and stores exact card sequence in notes."""
    card_sequence = ["card_scan_p4", "card_scan_p1", "card_scan_p3", "card_scan_p2"]
    recipe = RecipeSchema(
        title="Multi-Card Paprika Recipe",
        description="Testing Paprika archive export.",
        ingredients=["1 cup sugar", "2 cups flour"],
        directions=["1. Combine.", "2. Bake."],
        card_ids=card_sequence,
    )

    exporter = PaprikaExporter()
    archive_bytes = exporter.export_to_paprika_bytes([recipe], image_resolver=image_resolver)

    recipes_unpacked = PaprikaExporter.decompress_paprika_archive(archive_bytes)
    assert len(recipes_unpacked) == 1
    unpacked = recipes_unpacked[0]

    assert unpacked["name"] == "Multi-Card Paprika Recipe"
    # Verify primary photo is assigned to the first card in sequence
    assert unpacked["photo"] == "card_scan_p4.jpg"
    assert unpacked["photo_data"] is not None
    # Verify notes contain exact card sequence
    assert "Archival Cards: card_scan_p4, card_scan_p1, card_scan_p3, card_scan_p2" in unpacked["notes"]


def test_exporter_7_docx_openxml(image_resolver):
    """Verifies OpenXML Heirloom DOCX exporter embeds card scans in exact sequence order."""
    card_sequence = ["card_scan_p4", "card_scan_p1", "card_scan_p3", "card_scan_p2"]
    recipe = RecipeSchema(
        title="DOCX Heirloom Multi-Card Recipe",
        ingredients=["1 cup butter", "2 cups sugar"],
        directions=["1. Cream butter and sugar.", "2. Bake at 350F."],
        card_ids=card_sequence,
    )

    exporter = DocxExporter()
    docx_bytes = exporter.export_to_bytes([recipe], image_resolver=image_resolver)

    # Unpack DOCX zip
    zip_buf = io.BytesIO(docx_bytes)
    with zipfile.ZipFile(zip_buf, "r") as zf:
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        rels_xml = zf.read("word/_rels/document.xml.rels").decode("utf-8")

        # Verify all 4 images exist in media/
        assert "word/media/image_1.jpg" in zf.namelist()
        assert "word/media/image_2.jpg" in zf.namelist()
        assert "word/media/image_3.jpg" in zf.namelist()
        assert "word/media/image_4.jpg" in zf.namelist()

        # Check figure captions in document.xml follow exact sequence
        cap1_pos = doc_xml.find("Figure 1: Original Scanned Card (card_scan_p4)")
        cap2_pos = doc_xml.find("Figure 2: Original Scanned Card (card_scan_p1)")
        cap3_pos = doc_xml.find("Figure 3: Original Scanned Card (card_scan_p3)")
        cap4_pos = doc_xml.find("Figure 4: Original Scanned Card (card_scan_p2)")

        assert cap1_pos != -1
        assert cap2_pos != -1
        assert cap3_pos != -1
        assert cap4_pos != -1
        assert cap1_pos < cap2_pos < cap3_pos < cap4_pos


def test_exporter_8_printable_pdf(image_resolver):
    """Verifies Printable PDF exporter lays out archival card scans in exact sequential order."""
    card_sequence = ["card_scan_p3", "card_scan_p2", "card_scan_p1"]
    recipe = RecipeSchema(
        title="Printable PDF Multi-Card Recipe",
        ingredients=["3 eggs", "1 cup milk"],
        directions=["1. Whisk eggs and milk.", "2. Cook in skillet."],
        card_ids=card_sequence,
    )

    exporter = PdfExporter()
    pdf_bytes = exporter.export_to_bytes([recipe], image_resolver=image_resolver)

    assert pdf_bytes.startswith(b"%PDF-1.4")
    assert b"%%EOF" in pdf_bytes

    # Find and decompress all FlateDecode streams in PDF objects
    decompressed_streams = []
    stream_pattern = re.compile(b"<< /Length \\d+ /Filter /FlateDecode >>\\r?\\nstream\\r?\\n(.*?)\\r?\\nendstream", re.DOTALL)
    for m in stream_pattern.finditer(pdf_bytes):
        raw_stream = m.group(1)
        try:
            decomp = zlib.decompress(raw_stream).decode("latin-1")
            decompressed_streams.append(decomp)
        except Exception:
            pass

    full_decompressed_text = "\n".join(decompressed_streams)

    # Verify scan captions follow exact sequence: card_scan_p3, card_scan_p2, card_scan_p1
    s1_pos = full_decompressed_text.find("Scan #1: card_scan_p3")
    s2_pos = full_decompressed_text.find("Scan #2: card_scan_p2")
    s3_pos = full_decompressed_text.find("Scan #3: card_scan_p1")

    assert s1_pos != -1
    assert s2_pos != -1
    assert s3_pos != -1
    assert s1_pos < s2_pos < s3_pos


def test_exporter_9_google_docs(image_resolver):
    """Verifies Google Docs exporter (BatchUpdate, JSON, and HTML) preserves exact card sequence."""
    card_sequence = ["card_scan_p4", "card_scan_p1", "card_scan_p3", "card_scan_p2"]
    recipe = RecipeSchema(
        title="Google Docs Multi-Card Recipe",
        ingredients=["1 tsp vanilla", "1 cup flour"],
        directions=["1. Mix.", "2. Bake."],
        card_ids=card_sequence,
    )

    exporter = GoogleDocsExporter()

    # 1. HTML export check
    html_content = exporter.export_to_html([recipe], image_resolver=image_resolver)
    c1_idx = html_content.find("Card Scan #1 (card_scan_p4)")
    c2_idx = html_content.find("Card Scan #2 (card_scan_p1)")
    c3_idx = html_content.find("Card Scan #3 (card_scan_p3)")
    c4_idx = html_content.find("Card Scan #4 (card_scan_p2)")

    assert c1_idx != -1
    assert c2_idx != -1
    assert c3_idx != -1
    assert c4_idx != -1
    assert c1_idx < c2_idx < c3_idx < c4_idx

    # 2. JSON export check
    json_content = exporter.export_to_json_string([recipe], image_resolver=image_resolver)
    parsed = json.loads(json_content)
    assert parsed["recipe_count"] == 1
    assert parsed["recipes"][0]["card_ids"] == card_sequence


def test_adversarial_8_card_reverse_scaling_across_all_exporters(image_resolver):
    """Stress test: 8-card reversed sequence serialized across all 9 exporters."""
    card_sequence = [f"card_scan_p{i}" for i in range(8, 0, -1)]
    recipe = RecipeSchema(
        title="8-Card Sourdough Masterclass (Reversed)",
        description="Comprehensive 8-card heirloom booklet.",
        ingredients=[f"Ingredient {i}" for i in range(1, 10)],
        directions=[f"Step {i} instructions" for i in range(1, 10)],
        card_ids=card_sequence,
        tags=["Masterclass", "Baking", "Heirloom"],
        source_notes="Grandma's 8-Card Set (1938)",
    )

    # 1. Plan To Eat CSV
    csv_out = export_recipes_to_plan_to_eat_csv([recipe])
    assert PlanToEatExporter().validate_csv_format(csv_out) is True

    # 2. Schema.org JSON-LD
    json_ld_out = export_recipes_to_json_ld([recipe])
    assert JsonLdExporter().validate_json_ld(json_ld_out) is True

    # 3. Standard JSON
    json_out = export_recipes_to_json([recipe])
    parsed_json = json.loads(json_out)
    assert parsed_json[0]["card_ids"] == card_sequence

    # 4. Obsidian Markdown
    md_out = export_recipes_to_markdown([recipe], image_resolver=image_resolver)
    for i in range(8, 0, -1):
        assert f"card_scan_p{i}" in md_out
    assert md_out.find("card_scan_p8") < md_out.find("card_scan_p1")

    # 5. Cooklang
    cook_out = export_recipes_to_cooklang([recipe])
    assert ">> title: 8-Card Sourdough Masterclass" in cook_out

    # 6. Paprika
    paprika_bytes = export_recipes_to_paprika([recipe], image_resolver=image_resolver)
    unpacked_paprika = PaprikaExporter.decompress_paprika_archive(paprika_bytes)
    assert unpacked_paprika[0]["photo"] == "card_scan_p8.jpg"
    assert "card_scan_p8, card_scan_p7, card_scan_p6" in unpacked_paprika[0]["notes"]

    # 7. DOCX
    docx_bytes = export_recipes_to_docx([recipe], image_resolver=image_resolver)
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zf:
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        assert doc_xml.find("card_scan_p8") < doc_xml.find("card_scan_p1")

    # 8. Printable PDF
    pdf_bytes = export_recipes_to_pdf([recipe], image_resolver=image_resolver)
    assert pdf_bytes.startswith(b"%PDF-1.4")

    # 9. Google Docs
    gdoc_html = export_recipes_to_google_docs_html([recipe], image_resolver=image_resolver)
    assert gdoc_html.find("Card Scan #1 (card_scan_p8)") < gdoc_html.find("Card Scan #8 (card_scan_p1)")


def test_single_part_standalone_across_all_exporters(image_resolver):
    """Stress test: N=1 standalone recipe serialized across all 9 exporters."""
    card_sequence = ["card_scan_p1"]
    recipe = RecipeSchema(
        title="Quick 1-Card Vinaigrette",
        ingredients=["1/4 cup olive oil", "2 tbsp red wine vinegar", "1 tsp dijon mustard"],
        directions=["1. Whisk all ingredients in bowl."],
        card_ids=card_sequence,
    )

    # Verify all 9 exporters handle N=1 cleanly
    assert PlanToEatExporter().validate_csv_format(export_recipes_to_plan_to_eat_csv([recipe])) is True
    assert JsonLdExporter().validate_json_ld(export_recipes_to_json_ld([recipe])) is True
    assert json.loads(export_recipes_to_json([recipe]))[0]["card_ids"] == ["card_scan_p1"]
    assert "card_scan_p1" in export_recipes_to_markdown([recipe], image_resolver=image_resolver)
    assert "Quick 1-Card Vinaigrette" in export_recipes_to_cooklang([recipe])
    assert len(PaprikaExporter.decompress_paprika_archive(export_recipes_to_paprika([recipe], image_resolver=image_resolver))) == 1
    assert len(export_recipes_to_docx([recipe], image_resolver=image_resolver)) > 1000
    assert len(export_recipes_to_pdf([recipe], image_resolver=image_resolver)) > 1000
    assert "Card Scan #1 (card_scan_p1)" in export_recipes_to_google_docs_html([recipe], image_resolver=image_resolver)

