"""
conftest.py - Common fixtures, synthetic card scan generators, mock Antigravity Gemini clients,
and FastAPI AsyncClient test fixtures for RecipeBoxer E2E testing.
"""

import asyncio
import csv
import hashlib
import io
import os
import re
import shutil
import tempfile
import time
import uuid
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

import httpx
import pytest
from httpx import ASGITransport
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Register sample card fixtures
from tests.fixtures.loader import (  # noqa: E402
    ground_truth_manifest,
    sample_card_corpus,
    sample_card_multi_sets,
    sample_card_pairs,
    sample_card_standalones,
)


# ============================================================================
# 1. DOMAIN MODELS & SCHEMAS (Authoritative Interface Contracts)
# ============================================================================

class CardRole(str, Enum):
    FRONT_ONLY = "FRONT_ONLY"
    BACK_ONLY = "BACK_ONLY"
    STANDALONE = "STANDALONE"
    UNKNOWN = "UNKNOWN"


class PaperColorTone(str, Enum):
    WHITE = "WHITE"
    OFF_WHITE_CREAM = "OFF_WHITE_CREAM"
    YELLOWED_AGED = "YELLOWED_AGED"
    KRAFT_BROWN = "KRAFT_BROWN"
    PASTEL_YELLOW = "PASTEL_YELLOW"
    PASTEL_PINK = "PASTEL_PINK"
    PASTEL_BLUE = "PASTEL_BLUE"
    PASTEL_GREEN = "PASTEL_GREEN"
    OTHER_DARK = "OTHER_DARK"


class PaperRulingType(str, Enum):
    UNRULED_BLANK = "UNRULED_BLANK"
    STANDARD_RULED_BLUE = "STANDARD_RULED_BLUE"
    NARROW_RULED = "NARROW_RULED"
    WIDE_RULED = "WIDE_RULED"
    INDEX_CARD_HEADER_RULED = "INDEX_CARD_HEADER_RULED"
    GRID_GRAPH = "GRID_GRAPH"
    DOTTED = "DOTTED"
    OTHER = "OTHER"


class InkType(str, Enum):
    BLUE_BALLPOINT = "BLUE_BALLPOINT"
    BLACK_BALLPOINT = "BLACK_BALLPOINT"
    FOUNTAIN_PEN = "FOUNTAIN_PEN"
    PENCIL_GRAPHITE = "PENCIL_GRAPHITE"
    TYPEWRITER_RIBBON = "TYPEWRITER_RIBBON"
    FELT_TIP_MARKER = "FELT_TIP_MARKER"
    PRINTED_TYPESET = "PRINTED_TYPESET"
    UNKNOWN = "UNKNOWN"


class InkColor(str, Enum):
    BLUE = "BLUE"
    BLACK = "BLACK"
    BLUE_BLACK = "BLUE_BLACK"
    RED = "RED"
    GREEN = "GREEN"
    PURPLE = "PURPLE"
    GRAPHITE_GRAY = "GRAPHITE_GRAY"
    UNKNOWN = "UNKNOWN"


class ArtifactType(str, Enum):
    OIL_GREASE_STAIN = "OIL_GREASE_STAIN"
    COFFEE_TEA_RING = "COFFEE_TEA_RING"
    FOOD_SPLATTER_SAUCE = "FOOD_SPLATTER_SAUCE"
    CORNER_DOG_EAR_FOLD = "CORNER_DOG_EAR_FOLD"
    HORIZONTAL_CREASE = "HORIZONTAL_CREASE"
    VERTICAL_CREASE = "VERTICAL_CREASE"
    TEAR_EDGE = "TEAR_EDGE"
    TAPE_RESIDUE_YELLOWED = "TAPE_RESIDUE_YELLOWED"
    PUNCH_HOLE_TOP = "PUNCH_HOLE_TOP"
    PUNCH_HOLE_SIDE = "PUNCH_HOLE_SIDE"
    PAPERCLIP_INDENT = "PAPERCLIP_INDENT"


class CardQuadrant(str, Enum):
    TOP_LEFT = "TOP_LEFT"
    TOP_RIGHT = "TOP_RIGHT"
    BOTTOM_LEFT = "BOTTOM_LEFT"
    BOTTOM_RIGHT = "BOTTOM_RIGHT"
    CENTER = "CENTER"
    EDGE_TOP = "EDGE_TOP"
    EDGE_BOTTOM = "EDGE_BOTTOM"
    EDGE_LEFT = "EDGE_LEFT"
    EDGE_RIGHT = "EDGE_RIGHT"


class PhysicalArtifact(BaseModel):
    artifact_type: ArtifactType
    quadrant: CardQuadrant
    severity: str = "MODERATE"
    description: Optional[str] = None


class PaperColorInfo(BaseModel):
    tone_name: str = "Aged Cream"
    hex_approx: str = "#F4E8C1"
    lab_l: float = 90.0
    lab_a: float = 2.0
    lab_b: float = 18.0
    tone_enum: PaperColorTone = PaperColorTone.OFF_WHITE_CREAM


class PaperRulingInfo(BaseModel):
    has_ruling: bool = True
    ruling_type: PaperRulingType = PaperRulingType.STANDARD_RULED_BLUE
    line_spacing_px: Optional[float] = 24.0


class InkInfo(BaseModel):
    ink_color: InkColor = InkColor.BLUE
    ink_type: InkType = InkType.BLUE_BALLPOINT


class BoundaryText(BaseModel):
    first_line: str = ""
    last_line: str = ""
    has_continuation_symbol: bool = False
    is_cut_off_mid_sentence: bool = False


class VisualFingerprint(BaseModel):
    card_id: str
    image_path: str
    sha256_hash: str
    role: CardRole
    role_confidence: float = 0.95
    paper_color: PaperColorInfo = Field(default_factory=PaperColorInfo)
    ruling: PaperRulingInfo = Field(default_factory=PaperRulingInfo)
    ink: InkInfo = Field(default_factory=InkInfo)
    artifacts: List[PhysicalArtifact] = Field(default_factory=list)
    boundary_text: BoundaryText = Field(default_factory=BoundaryText)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CorrelationProposal(BaseModel):
    proposal_id: str
    front_card_id: str
    back_card_id: Optional[str] = None
    heuristic_score: float = 0.0
    semantic_score: float = 0.0
    confidence: float = 0.0
    is_standalone: bool = False
    continuity_explanation: str = ""
    recommended_action: str = "PAIR"  # "PAIR", "STANDALONE", "UNCERTAIN"


class CorrelationResult(BaseModel):
    proposals: List[CorrelationProposal] = Field(default_factory=list)
    unmatched_card_ids: List[str] = Field(default_factory=list)


class HITLDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    OVERRIDE = "OVERRIDE"


class CardPairApproval(BaseModel):
    proposal_id: str
    front_card_id: str
    back_card_id: Optional[str] = None
    decision: HITLDecision = HITLDecision.APPROVE
    notes: Optional[str] = None


class ApprovalPayload(BaseModel):
    session_id: str
    approvals: List[CardPairApproval]
    continue_pipeline: bool = True


class RecipeSchema(BaseModel):
    recipe_id: str = Field(default_factory=lambda: f"rec_{uuid.uuid4().hex[:8]}")
    title: str
    description: Optional[str] = ""
    yield_servings: Optional[str] = ""
    prep_time: Optional[str] = ""
    cook_time: Optional[str] = ""
    total_time: Optional[str] = ""
    ingredients: List[str]
    directions: List[str]
    tags: List[str] = Field(default_factory=list)
    source_notes: Optional[str] = ""


# ============================================================================
# 2. SYNTHETIC IMAGE GENERATION ENGINE (PIL / PILLOW)
# ============================================================================

def create_synthetic_card_image(
    output_path: Path,
    title: Optional[str] = None,
    body_lines: Optional[List[str]] = None,
    continuation_cue: Optional[str] = None,
    bg_color: Tuple[int, int, int] = (244, 232, 193),  # Aged cream
    has_ruling: bool = True,
    ruling_color: Tuple[int, int, int] = (180, 200, 220),  # Soft blue
    ink_color: Tuple[int, int, int] = (20, 30, 110),  # Vintage blue ballpoint
    stain_quadrant: Optional[CardQuadrant] = None,
    stain_type: ArtifactType = ArtifactType.OIL_GREASE_STAIN,
    width: int = 600,
    height: int = 400,
) -> Path:
    """Generates a realistic synthetic recipe card scan image using Pillow."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(image, "RGBA")

    # 1. Draw Ruling Lines
    if has_ruling:
        line_spacing = 24
        header_y = 50
        # Draw red header line
        draw.line([(30, header_y), (width - 30, header_y)], fill=(220, 100, 100, 180), width=2)
        # Draw horizontal ruling lines
        for y in range(header_y + line_spacing, height - 20, line_spacing):
            draw.line([(30, y), (width - 30, y)], fill=(*ruling_color, 140), width=1)

    # 2. Draw Physical Stains / Artifacts
    if stain_quadrant:
        # Determine quadrant bounding box
        quad_boxes = {
            CardQuadrant.TOP_LEFT: (40, 20, 180, 140),
            CardQuadrant.TOP_RIGHT: (width - 180, 20, width - 40, 140),
            CardQuadrant.BOTTOM_LEFT: (40, height - 140, 180, height - 20),
            CardQuadrant.BOTTOM_RIGHT: (width - 180, height - 140, width - 40, height - 20),
            CardQuadrant.CENTER: (width // 2 - 80, height // 2 - 60, width // 2 + 80, height // 2 + 60),
            CardQuadrant.EDGE_TOP: (width // 2 - 60, 5, width // 2 + 60, 45),
            CardQuadrant.EDGE_BOTTOM: (width // 2 - 60, height - 45, width // 2 + 60, height - 5),
            CardQuadrant.EDGE_LEFT: (5, height // 2 - 60, 45, height // 2 + 60),
            CardQuadrant.EDGE_RIGHT: (width - 45, height // 2 - 60, width - 5, height // 2 + 60),
        }
        bbox = quad_boxes.get(stain_quadrant, (50, 50, 150, 150))

        if stain_type == ArtifactType.OIL_GREASE_STAIN:
            # Translucent golden/amber grease patch
            draw.ellipse(bbox, fill=(210, 170, 70, 90), outline=(180, 140, 50, 120))
        elif stain_type == ArtifactType.COFFEE_TEA_RING:
            # Dark brown circular coffee ring
            draw.ellipse(bbox, fill=(160, 110, 60, 30), outline=(120, 75, 30, 200), width=4)
        elif stain_type == ArtifactType.CORNER_DOG_EAR_FOLD:
            # Triangular shaded dog-ear fold
            x1, y1, x2, y2 = bbox
            draw.polygon([(x1, y1), (x2, y1), (x1, y2)], fill=(180, 170, 140, 200), outline=(100, 90, 80, 255))
        elif stain_type == ArtifactType.PUNCH_HOLE_TOP:
            # Binder hole cutout
            cx, cy = (bbox[0] + bbox[2]) // 2, bbox[1] + 15
            draw.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), fill=(60, 60, 60, 255))

    # 3. Draw Text (Title, Body, Continuation)
    y_text = 25
    if title:
        draw.text((40, y_text), title, fill=(*ink_color, 255))
        y_text += 35

    if body_lines:
        for line in body_lines:
            if y_text > height - 40:
                break
            draw.text((40, y_text), line, fill=(*ink_color, 230))
            y_text += 22

    if continuation_cue:
        draw.text((width - 120, height - 30), continuation_cue, fill=(*ink_color, 255))

    image.save(output_path, "JPEG", quality=90)
    return output_path


def create_corrupted_image_file(output_path: Path) -> Path:
    """Creates a corrupted non-image file with a .jpg extension."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00CORRUPT_HEADER_TRUNCATED_BYTES")
    return output_path


@dataclass
class SyntheticDeckInfo:
    directory: Path
    all_files: List[Path]
    pairs: List[Tuple[str, str]]  # (front_name, back_name)
    standalones: List[str]
    unknowns: List[str]
    corrupted: List[str]
    expected_recipes: List[Dict[str, Any]]


@pytest.fixture
def synthetic_card_deck(tmp_path: Path) -> SyntheticDeckInfo:
    """
    Generates a rich, realistic test directory containing:
    - 2 Front/Back pairs (Vintage Cinnamon Rolls with grease stain bleed-through; Lemon Glaze Bars)
    - 2 Standalone single-sided recipes (Chocolate Fudge; Southern Pecan Pie)
    - 1 Blank / divider noise card (UNKNOWN role)
    - 1 Corrupted file
    """
    scans_dir = tmp_path / "card_scans"
    scans_dir.mkdir(parents=True, exist_ok=True)

    # Recipe 1: Vintage Cinnamon Rolls (Two-sided pair, ruled card, grease stain top-right on front, top-left on back)
    p1_front = scans_dir / "01_cinnamon_rolls_front.jpg"
    create_synthetic_card_image(
        output_path=p1_front,
        title="Grandma's Cinnamon Rolls",
        body_lines=[
            "1 pkg active dry yeast",
            "1/2 cup warm water (105F)",
            "1/2 cup lukewarm milk",
            "1/3 cup sugar, 1/3 cup butter",
            "1 tsp salt, 1 egg",
            "3 1/2 to 4 cups all-purpose flour",
            "Filling: 1/3 cup butter, 1 cup brown sugar",
            "3 tbsp ground cinnamon",
            "Dissolve yeast in warm water. Mix milk, sugar, butter.",
            "Add 2 cups flour, beat until smooth.",
        ],
        continuation_cue="(over ->)",
        bg_color=(246, 236, 205),
        has_ruling=True,
        ink_color=(20, 40, 130),  # Blue ink
        stain_quadrant=CardQuadrant.TOP_RIGHT,
        stain_type=ArtifactType.OIL_GREASE_STAIN,
    )

    p1_back = scans_dir / "02_cinnamon_rolls_back.jpg"
    create_synthetic_card_image(
        output_path=p1_back,
        title=None,
        body_lines=[
            "(Directions continued)",
            "Knead dough on floured surface 5 mins until smooth.",
            "Place in greased bowl, cover, let rise 1 hour.",
            "Punch down. Roll into 12x18 inch rectangle.",
            "Spread with softened butter, sprinkle brown sugar & cinnamon.",
            "Roll up jelly-roll style from long side. Slice into 12 rolls.",
            "Place in greased 9x13 inch baking pan.",
            "Let rise 45 mins. Bake at 375°F for 20-25 mins.",
            "Glaze with 1 cup powdered sugar + 2 tbsp milk.",
        ],
        continuation_cue=None,
        bg_color=(246, 236, 205),
        has_ruling=True,
        ink_color=(20, 40, 130),  # Matching blue ink
        stain_quadrant=CardQuadrant.TOP_LEFT,  # Mirrored bleed-through!
        stain_type=ArtifactType.OIL_GREASE_STAIN,
    )

    # Recipe 2: Lemon Glaze Bars (Two-sided pair, unruled white card, felt tip ink)
    p2_front = scans_dir / "03_lemon_bars_front.jpg"
    create_synthetic_card_image(
        output_path=p2_front,
        title="Luscious Lemon Glaze Bars",
        body_lines=[
            "Crust:",
            "2 cups all-purpose flour",
            "1/2 cup powdered sugar",
            "1 cup cold butter, cubed",
            "Filling:",
            "4 large eggs",
            "1 1/2 cups granulated sugar",
            "1/4 cup flour",
            "1/2 cup fresh lemon juice",
        ],
        continuation_cue="PTO...",
        bg_color=(255, 255, 255),
        has_ruling=False,
        ink_color=(10, 10, 10),  # Black felt tip
    )

    p2_back = scans_dir / "04_lemon_bars_back.jpg"
    create_synthetic_card_image(
        output_path=p2_back,
        title=None,
        body_lines=[
            "1. Preheat oven to 350F. Grease 9x13 pan.",
            "2. Combine crust ingredients, press into pan.",
            "3. Bake crust 20 mins until golden.",
            "4. Whisk eggs, sugar, flour, lemon juice.",
            "5. Pour over warm crust.",
            "6. Bake 25 mins until set. Dust with powdered sugar.",
        ],
        continuation_cue=None,
        bg_color=(255, 255, 255),
        has_ruling=False,
        ink_color=(10, 10, 10),
    )

    # Standalone Recipe 1: 10-Minute Chocolate Fudge
    s1 = scans_dir / "05_chocolate_fudge_standalone.jpg"
    create_synthetic_card_image(
        output_path=s1,
        title="10-Minute Quick Chocolate Fudge",
        body_lines=[
            "3 cups semi-sweet chocolate chips",
            "1 can (14 oz) sweetened condensed milk",
            "1 tsp vanilla extract, dash salt",
            "1. Melt chips with condensed milk in saucepan on low.",
            "2. Stir in vanilla and salt until glossy.",
            "3. Pour into 8x8 foil-lined pan. Chill 2 hrs.",
            "Yield: 36 squares.",
        ],
        continuation_cue=None,
        bg_color=(250, 245, 235),
        has_ruling=True,
        ink_color=(10, 10, 10),
    )

    # Standalone Recipe 2: Southern Pecan Pie
    s2 = scans_dir / "06_pecan_pie_standalone.jpg"
    create_synthetic_card_image(
        output_path=s2,
        title="Southern Pecan Pie",
        body_lines=[
            "1 unbaked 9-inch pie shell",
            "3 eggs, 1 cup dark corn syrup",
            "1 cup sugar, 2 tbsp butter melted",
            "1 tsp vanilla, 1 1/2 cups pecans",
            "1. Beat eggs, syrup, sugar, butter, vanilla.",
            "2. Stir in pecans, pour into pie shell.",
            "3. Bake at 350F for 60 mins until set.",
        ],
        continuation_cue=None,
        bg_color=(240, 220, 190),  # Kraft tone
        has_ruling=False,
        ink_color=(50, 40, 30),
    )

    # Noise / Divider card
    noise = scans_dir / "07_divider_tab.jpg"
    create_synthetic_card_image(
        output_path=noise,
        title="DESSERTS & PASTRIES",
        body_lines=["Section 4: Family Holiday Favorites"],
        bg_color=(255, 240, 180),
        has_ruling=False,
        ink_color=(180, 60, 20),
    )

    # Corrupt file
    corrupt = scans_dir / "08_corrupted_scan.jpg"
    create_corrupted_image_file(corrupt)

    all_files = [p1_front, p1_back, p2_front, p2_back, s1, s2, noise, corrupt]

    expected_recipes = [
        {
            "title": "Grandma's Cinnamon Rolls",
            "ingredients": [
                "1 pkg active dry yeast",
                "1/2 cup warm water (105F)",
                "1/2 cup lukewarm milk",
                "1/3 cup sugar",
                "1/3 cup butter",
                "1 tsp salt",
                "1 egg",
                "3 1/2 to 4 cups all-purpose flour",
                "1/3 cup butter, softened",
                "1 cup brown sugar",
                "3 tbsp ground cinnamon",
                "1 cup powdered sugar",
                "2 tbsp milk",
            ],
            "directions": [
                "1. Dissolve yeast in warm water. Mix milk, sugar, and butter.",
                "2. Add 2 cups flour, beating until smooth.",
                "3. Knead dough on floured surface 5 minutes until smooth.",
                "4. Place in greased bowl, cover, and let rise 1 hour.",
                "5. Punch down and roll into a 12x18 inch rectangle.",
                "6. Spread with softened butter, sprinkle brown sugar and cinnamon.",
                "7. Roll up jelly-roll style from long side and slice into 12 rolls.",
                "8. Place in greased 9x13 inch baking pan and let rise 45 minutes.",
                "9. Bake at 375°F for 20-25 minutes.",
                "10. Glaze with powdered sugar and milk mixture.",
            ],
            "yield_servings": "12 rolls",
            "prep_time": "30 mins",
            "total_time": "2 hrs 15 mins",
            "tags": ["Dessert", "Baking", "Breakfast"],
            "source_notes": "Grandma's Recipe Box (Ruled Card)",
        },
        {
            "title": "Luscious Lemon Glaze Bars",
            "ingredients": [
                "2 cups all-purpose flour",
                "1/2 cup powdered sugar",
                "1 cup cold butter, cubed",
                "4 large eggs",
                "1 1/2 cups granulated sugar",
                "1/4 cup flour",
                "1/2 cup fresh lemon juice",
            ],
            "directions": [
                "1. Preheat oven to 350°F. Grease a 9x13 inch pan.",
                "2. Combine crust ingredients and press firmly into pan.",
                "3. Bake crust 20 minutes until lightly golden.",
                "4. Whisk eggs, sugar, flour, and lemon juice until blended.",
                "5. Pour filling over warm baked crust.",
                "6. Bake 25 minutes until set, then dust with powdered sugar.",
            ],
            "yield_servings": "24 bars",
            "prep_time": "15 mins",
            "total_time": "1 hour",
            "tags": ["Dessert", "Baking", "Lemon"],
            "source_notes": "Handwritten Card",
        },
        {
            "title": "10-Minute Quick Chocolate Fudge",
            "ingredients": [
                "3 cups semi-sweet chocolate chips",
                "1 can (14 oz) sweetened condensed milk",
                "1 tsp vanilla extract",
                "Dash of salt",
            ],
            "directions": [
                "1. Melt chocolate chips with condensed milk in saucepan over low heat.",
                "2. Stir in vanilla and salt until mixture is smooth and glossy.",
                "3. Pour into 8x8 foil-lined square pan and chill 2 hours until firm.",
            ],
            "yield_servings": "36 squares",
            "prep_time": "10 mins",
            "total_time": "2 hrs 10 mins",
            "tags": ["Candy", "Chocolate", "Quick"],
            "source_notes": "Standalone Card",
        },
        {
            "title": "Southern Pecan Pie",
            "ingredients": [
                "1 unbaked 9-inch pie shell",
                "3 large eggs",
                "1 cup dark corn syrup",
                "1 cup sugar",
                "2 tbsp butter, melted",
                "1 tsp vanilla extract",
                "1 1/2 cups pecan halves",
            ],
            "directions": [
                "1. Preheat oven to 350°F.",
                "2. Beat eggs, dark corn syrup, sugar, melted butter, and vanilla until smooth.",
                "3. Stir in pecans and pour into unbaked pie shell.",
                "4. Bake at 350°F for 60 minutes until set.",
            ],
            "yield_servings": "8 servings",
            "prep_time": "15 mins",
            "total_time": "1 hr 15 mins",
            "tags": ["Dessert", "Pie", "Southern"],
            "source_notes": "Kraft Card",
        },
    ]

    return SyntheticDeckInfo(
        directory=scans_dir,
        all_files=all_files,
        pairs=[
            ("01_cinnamon_rolls_front.jpg", "02_cinnamon_rolls_back.jpg"),
            ("03_lemon_bars_front.jpg", "04_lemon_bars_back.jpg"),
        ],
        standalones=[
            "05_chocolate_fudge_standalone.jpg",
            "06_pecan_pie_standalone.jpg",
        ],
        unknowns=["07_divider_tab.jpg"],
        corrupted=["08_corrupted_scan.jpg"],
        expected_recipes=expected_recipes,
    )


# ============================================================================
# 3. MOCK GEMINI CLIENT & HARNESS SIMULATOR
# ============================================================================

class MockGeminiClient:
    """
    Deterministic mock Gemini multimodal client for testing RecipeBoxer offline.
    Produces accurate fingerprints, correlations, and recipe schemas based on synthetic card inputs.
    """
    def __init__(self, api_key: str = "mock-key", simulate_latency: float = 0.0):
        self.api_key = api_key
        self.simulate_latency = simulate_latency
        self.call_count = 0
        self.token_usage = {
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "thoughts_tokens": 0,
            "total_tokens": 0,
        }

    async def analyze_fingerprint(self, image_path: Path, sha256_hash: str) -> VisualFingerprint:
        self.call_count += 1
        if self.simulate_latency > 0:
            await asyncio.sleep(self.simulate_latency)

        self.token_usage["prompt_tokens"] += 450
        self.token_usage["candidates_tokens"] += 150
        self.token_usage["total_tokens"] += 600

        fname = image_path.name.lower()

        # Handle corrupted images
        if "corrupt" in fname:
            raise ValueError("Corrupted image file: Cannot decode image bytes")

        # Classify by filename semantics for deterministic testing
        if "front" in fname:
            role = CardRole.FRONT_ONLY
            is_cut_off = True
            has_cue = True
            first_line = "Recipe Header & Ingredients"
            last_line = "Mix until smooth (over ->)"
        elif "back" in fname:
            role = CardRole.BACK_ONLY
            is_cut_off = False
            has_cue = False
            first_line = "(Directions continued)"
            last_line = "Bake and glaze completely."
        elif "standalone" in fname:
            role = CardRole.STANDALONE
            is_cut_off = False
            has_cue = False
            first_line = "Title & Quick Ingredients"
            last_line = "Chill or bake until set."
        elif "divider" in fname or "blank" in fname or "noise" in fname:
            role = CardRole.UNKNOWN
            is_cut_off = False
            has_cue = False
            first_line = "Section 4: Divider"
            last_line = "End of Tab"
        else:
            role = CardRole.STANDALONE
            is_cut_off = False
            has_cue = False
            first_line = "General Recipe Card"
            last_line = "Enjoy your dish."

        # Stains & Artifacts
        artifacts = []
        if "cinnamon" in fname and "front" in fname:
            artifacts.append(PhysicalArtifact(
                artifact_type=ArtifactType.OIL_GREASE_STAIN,
                quadrant=CardQuadrant.TOP_RIGHT,
                description="Grease stain on top right corner",
            ))
        elif "cinnamon" in fname and "back" in fname:
            artifacts.append(PhysicalArtifact(
                artifact_type=ArtifactType.OIL_GREASE_STAIN,
                quadrant=CardQuadrant.TOP_LEFT,  # Mirrored
                description="Grease stain bleed-through top left",
            ))

        # Paper color tone
        tone = PaperColorTone.OFF_WHITE_CREAM
        hex_color = "#F6ECD0"
        if "lemon" in fname:
            tone = PaperColorTone.WHITE
            hex_color = "#FFFFFF"
        elif "pecan" in fname:
            tone = PaperColorTone.KRAFT_BROWN
            hex_color = "#D2B48C"

        return VisualFingerprint(
            card_id=image_path.name,
            image_path=str(image_path),
            sha256_hash=sha256_hash,
            role=role,
            role_confidence=0.98 if role != CardRole.UNKNOWN else 0.50,
            paper_color=PaperColorInfo(
                tone_name=tone.value,
                hex_approx=hex_color,
                lab_l=90.0 if tone != PaperColorTone.KRAFT_BROWN else 75.0,
                lab_a=2.0,
                lab_b=15.0,
                tone_enum=tone,
            ),
            ruling=PaperRulingInfo(
                has_ruling="lemon" not in fname and "pecan" not in fname,
                ruling_type=PaperRulingType.STANDARD_RULED_BLUE if "lemon" not in fname and "pecan" not in fname else PaperRulingType.UNRULED_BLANK,
            ),
            ink=InkInfo(
                ink_color=InkColor.BLUE if "cinnamon" in fname else InkColor.BLACK,
                ink_type=InkType.BLUE_BALLPOINT if "cinnamon" in fname else InkType.FELT_TIP_MARKER,
            ),
            artifacts=artifacts,
            boundary_text=BoundaryText(
                first_line=first_line,
                last_line=last_line,
                has_continuation_symbol=has_cue,
                is_cut_off_mid_sentence=is_cut_off,
            ),
        )

    async def verify_correlation(
        self, front_fp: VisualFingerprint, back_fp: VisualFingerprint, heuristic_score: float
    ) -> CorrelationProposal:
        self.call_count += 1
        if self.simulate_latency > 0:
            await asyncio.sleep(self.simulate_latency)

        self.token_usage["prompt_tokens"] += 800
        self.token_usage["candidates_tokens"] += 200
        self.token_usage["total_tokens"] += 1000

        # Check semantic continuity
        f_name = front_fp.card_id.lower()
        b_name = back_fp.card_id.lower()

        # Exact matching pairs by prefix or keywords
        f_prefix = f_name.replace("front", "").replace("01_", "").replace("02_", "").replace("03_", "").replace("04_", "").strip("._ ")
        b_prefix = b_name.replace("back", "").replace("01_", "").replace("02_", "").replace("03_", "").replace("04_", "").strip("._ ")

        is_match = (
            (f_prefix and f_prefix == b_prefix) or
            ("cinnamon" in f_name and "cinnamon" in b_name) or
            ("lemon" in f_name and "lemon" in b_name) or
            ("rolls" in f_name and "rolls" in b_name)
        )

        semantic_score = 0.96 if is_match else 0.05
        confidence = 0.30 * heuristic_score + 0.70 * semantic_score

        return CorrelationProposal(
            proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
            front_card_id=front_fp.card_id,
            back_card_id=back_fp.card_id,
            heuristic_score=heuristic_score,
            semantic_score=semantic_score,
            confidence=confidence,
            is_standalone=False,
            continuity_explanation=(
                "Syntactic continuity matches: dough rising instructions on front continue with kneading/baking on back."
                if is_match else "Unrelated recipe content and incompatible ingredients."
            ),
            recommended_action="PAIR" if confidence >= 0.80 else ("UNCERTAIN" if confidence >= 0.50 else "REJECT"),
        )

    async def extract_recipe(
        self, front_fp: VisualFingerprint, back_fp: Optional[VisualFingerprint] = None
    ) -> RecipeSchema:
        self.call_count += 1
        if self.simulate_latency > 0:
            await asyncio.sleep(self.simulate_latency)

        self.token_usage["prompt_tokens"] += 1200
        self.token_usage["candidates_tokens"] += 400
        self.token_usage["total_tokens"] += 1600

        fname = front_fp.card_id.lower()

        if "cinnamon" in fname or "rolls" in fname:
            return RecipeSchema(
                recipe_id=f"rec_{uuid.uuid4().hex[:8]}",
                title="Grandma's Cinnamon Rolls",
                description="Vintage handwritten family recipe on ruled index card.",
                yield_servings="12 rolls",
                prep_time="30 mins",
                cook_time="25 mins",
                total_time="2 hrs 15 mins",
                ingredients=[
                    "1 pkg active dry yeast",
                    "1/2 cup warm water (105F)",
                    "1/2 cup lukewarm milk",
                    "1/3 cup sugar",
                    "1/3 cup butter",
                    "1 tsp salt",
                    "1 egg",
                    "3 1/2 to 4 cups all-purpose flour",
                    "1/3 cup butter, softened",
                    "1 cup brown sugar",
                    "3 tbsp ground cinnamon",
                ],
                directions=[
                    "1. Dissolve yeast in warm water. Mix milk, sugar, butter.",
                    "2. Add 2 cups flour, beat until smooth.",
                    "3. Knead dough on floured surface 5 mins until smooth.",
                    "4. Place in greased bowl, cover, let rise 1 hour.",
                    "5. Punch down. Roll into 12x18 inch rectangle.",
                    "6. Spread with softened butter, sprinkle brown sugar & cinnamon.",
                    "7. Roll up jelly-roll style and slice into 12 rolls.",
                    "8. Place in greased pan, let rise 45 mins, bake at 375°F for 25 mins.",
                ],
                tags=["Dessert", "Baking", "Breakfast"],
                source_notes="Grandma's Recipe Box",
            )
        elif "lemon" in fname:
            return RecipeSchema(
                recipe_id=f"rec_{uuid.uuid4().hex[:8]}",
                title="Luscious Lemon Glaze Bars",
                description="Tangy, sweet lemon bars with buttery shortbread crust.",
                yield_servings="24 bars",
                prep_time="15 mins",
                cook_time="45 mins",
                total_time="1 hour",
                ingredients=[
                    "2 cups all-purpose flour",
                    "1/2 cup powdered sugar",
                    "1 cup cold butter, cubed",
                    "4 large eggs",
                    "1 1/2 cups granulated sugar",
                    "1/4 cup flour",
                    "1/2 cup fresh lemon juice",
                ],
                directions=[
                    "1. Preheat oven to 350°F. Grease 9x13 pan.",
                    "2. Combine crust ingredients, press into pan.",
                    "3. Bake crust 20 mins until golden.",
                    "4. Whisk eggs, sugar, flour, lemon juice.",
                    "5. Pour over warm crust and bake 25 mins until set.",
                ],
                tags=["Dessert", "Baking", "Lemon"],
                source_notes="Family Cookbook",
            )
        elif "fudge" in fname:
            return RecipeSchema(
                recipe_id=f"rec_{uuid.uuid4().hex[:8]}",
                title="10-Minute Quick Chocolate Fudge",
                description="Fast and rich chocolate fudge.",
                yield_servings="36 squares",
                prep_time="10 mins",
                cook_time="0 mins",
                total_time="2 hrs 10 mins",
                ingredients=[
                    "3 cups semi-sweet chocolate chips",
                    "1 can (14 oz) sweetened condensed milk",
                    "1 tsp vanilla extract",
                    "Dash of salt",
                ],
                directions=[
                    "1. Melt chips with condensed milk in saucepan on low heat.",
                    "2. Stir in vanilla and salt until glossy.",
                    "3. Pour into 8x8 foil-lined pan and chill 2 hours.",
                ],
                tags=["Candy", "Chocolate", "Quick"],
                source_notes="Standalone Card",
            )
        elif "pecan" in fname:
            return RecipeSchema(
                recipe_id=f"rec_{uuid.uuid4().hex[:8]}",
                title="Southern Pecan Pie",
                description="Classic Southern holiday pecan pie.",
                yield_servings="8 servings",
                prep_time="15 mins",
                cook_time="60 mins",
                total_time="1 hr 15 mins",
                ingredients=[
                    "1 unbaked 9-inch pie shell",
                    "3 eggs",
                    "1 cup dark corn syrup",
                    "1 cup sugar",
                    "2 tbsp butter, melted",
                    "1 tsp vanilla extract",
                    "1 1/2 cups pecan halves",
                ],
                directions=[
                    "1. Preheat oven to 350°F.",
                    "2. Beat eggs, syrup, sugar, butter, and vanilla.",
                    "3. Stir in pecans and pour into pie shell.",
                    "4. Bake at 350°F for 60 mins until set.",
                ],
                tags=["Dessert", "Pie", "Southern"],
                source_notes="Kraft Card",
            )
        else:
            return RecipeSchema(
                recipe_id=f"rec_{uuid.uuid4().hex[:8]}",
                title=f"Recipe from {front_fp.card_id}",
                description="Transcribed physical card recipe.",
                yield_servings="4 servings",
                prep_time="10 mins",
                total_time="30 mins",
                ingredients=["1 cup ingredient A", "2 tbsp ingredient B"],
                directions=["1. Mix ingredients.", "2. Cook and serve."],
                tags=["General"],
            )


@pytest.fixture
def mock_gemini_client() -> MockGeminiClient:
    """Fixture providing a deterministic MockGeminiClient."""
    return MockGeminiClient()


# ============================================================================
# 4. SQLITE WAL CACHE FIXTURE
# ============================================================================

import sqlite3

class SQLiteFingerprintCache:
    """
    Standalone SQLite WAL fingerprint cache matching PROJECT.md interface contract.
    """
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS card_fingerprints (
                    image_hash TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    card_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    fingerprint_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            conn.commit()

    def get(self, image_bytes: bytes, image_path: str) -> Optional[VisualFingerprint]:
        sha = hashlib.sha256(image_bytes).hexdigest()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT fingerprint_json FROM card_fingerprints WHERE image_hash = ?",
                (sha,),
            )
            row = cursor.fetchone()
            if row:
                return VisualFingerprint.model_validate_json(row[0])
        return None

    def put(self, fingerprint: VisualFingerprint) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO card_fingerprints
                (image_hash, file_path, card_id, role, fingerprint_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    fingerprint.sha256_hash,
                    fingerprint.image_path,
                    fingerprint.card_id,
                    fingerprint.role.value,
                    fingerprint.model_dump_json(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def contains(self, sha256_hash: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM card_fingerprints WHERE image_hash = ?",
                (sha256_hash,),
            )
            return cursor.fetchone() is not None

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM card_fingerprints")
            return cursor.fetchone()[0]


@pytest.fixture
def test_cache(tmp_path: Path) -> SQLiteFingerprintCache:
    """Fixture providing an isolated temporary SQLite WAL fingerprint cache."""
    db_file = tmp_path / "cache" / "fingerprints_test.db"
    return SQLiteFingerprintCache(db_file)


# ============================================================================
# 5. HEURISTIC DISTANCE & PLAN TO EAT CSV SERIALIZER (Standard Algorithms)
# ============================================================================

def compute_heuristic_visual_score(
    front: VisualFingerprint, back: VisualFingerprint, scan_index_diff: int = 1
) -> float:
    """
    Computes Stage 1 Heuristic visual distance score S_heuristic in [0.0, 1.0].
    Follows authoritative 7-dimension formula from PROJECT.md & TEST_INFRA.md.
    """
    # 1. Role Score (Weight 0.25)
    if front.role == CardRole.FRONT_ONLY and back.role == CardRole.BACK_ONLY:
        s_role = 1.0
    elif (front.role == CardRole.FRONT_ONLY and back.role == CardRole.UNKNOWN) or \
         (front.role == CardRole.UNKNOWN and back.role == CardRole.BACK_ONLY):
        s_role = 0.8
    elif front.role == CardRole.UNKNOWN and back.role == CardRole.UNKNOWN:
        s_role = 0.5
    else:
        # Immediate hard prune for incompatible roles
        return 0.0

    # 2. Proximity Score (Weight 0.25)
    d = abs(scan_index_diff)
    if d == 1:
        s_prox = 1.0
    elif d == 2:
        s_prox = 0.7
    elif 1 <= d <= 5:
        s_prox = 0.4
    elif 5 < d <= 20:
        s_prox = 0.2
    else:
        s_prox = 0.1

    # 3. Paper Color Delta E (Weight 0.15)
    delta_e = ((front.paper_color.lab_l - back.paper_color.lab_l) ** 2 +
               (front.paper_color.lab_a - back.paper_color.lab_a) ** 2 +
               (front.paper_color.lab_b - back.paper_color.lab_b) ** 2) ** 0.5
    s_color_raw = max(0.0, 1.0 - (delta_e / 35.0))
    tone_bonus = 1.10 if front.paper_color.tone_enum == back.paper_color.tone_enum else 0.85
    s_color = min(1.0, s_color_raw * tone_bonus)

    # 4. Paper Ruling Match (Weight 0.15)
    if front.ruling.ruling_type == back.ruling.ruling_type and front.ruling.has_ruling == back.ruling.has_ruling:
        s_ruling = 1.0
    elif front.ruling.ruling_type == back.ruling.ruling_type:
        s_ruling = 0.8
    elif front.ruling.ruling_type == PaperRulingType.UNRULED_BLANK and back.ruling.ruling_type == PaperRulingType.UNRULED_BLANK:
        s_ruling = 0.5
    else:
        s_ruling = 0.0

    # 5. Ink Type & Color Match (Weight 0.10)
    s_ink = 0.6 * (1.0 if front.ink.ink_type == back.ink.ink_type else 0.0) + \
            0.4 * (1.0 if front.ink.ink_color == back.ink.ink_color else 0.0)

    # 6. Physical Artifact Mirrored Bleed-Through (Weight 0.05)
    mirror_map = {
        CardQuadrant.TOP_LEFT: CardQuadrant.TOP_RIGHT,
        CardQuadrant.TOP_RIGHT: CardQuadrant.TOP_LEFT,
        CardQuadrant.BOTTOM_LEFT: CardQuadrant.BOTTOM_RIGHT,
        CardQuadrant.BOTTOM_RIGHT: CardQuadrant.BOTTOM_LEFT,
        CardQuadrant.CENTER: CardQuadrant.CENTER,
        CardQuadrant.EDGE_LEFT: CardQuadrant.EDGE_RIGHT,
        CardQuadrant.EDGE_RIGHT: CardQuadrant.EDGE_LEFT,
        CardQuadrant.EDGE_TOP: CardQuadrant.EDGE_TOP,
        CardQuadrant.EDGE_BOTTOM: CardQuadrant.EDGE_BOTTOM,
    }
    has_mirror_match = False
    for a in front.artifacts:
        for b in back.artifacts:
            if a.artifact_type == b.artifact_type and mirror_map.get(a.quadrant) == b.quadrant:
                has_mirror_match = True
                break

    if has_mirror_match:
        s_stain = 1.0
    elif not front.artifacts and not back.artifacts:
        s_stain = 0.6  # Clean cards neutral score
    elif (front.artifacts and not back.artifacts) or (not front.artifacts and back.artifacts):
        s_stain = 0.3
    else:
        s_stain = 0.1

    # 7. Style & Aspect Ratio (Weight 0.05)
    s_style = 0.8  # Default style baseline

    s_heuristic = (
        0.25 * s_role +
        0.25 * s_prox +
        0.15 * s_color +
        0.15 * s_ruling +
        0.10 * s_ink +
        0.05 * s_stain +
        0.05 * s_style
    )
    return round(s_heuristic, 4)


def serialize_recipes_to_plan_to_eat_csv(recipes: List[RecipeSchema]) -> str:
    """
    Serializes a list of RecipeSchema objects to RFC 4180 Plan To Eat CSV string.
    Columns: Title,Description,Ingredients,Directions,Prep Time,Total Time,Yield,Tags,Source
    """
    headers = [
        "Title",
        "Description",
        "Ingredients",
        "Directions",
        "Prep Time",
        "Total Time",
        "Yield",
        "Tags",
        "Source",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=headers,
        delimiter=",",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writeheader()

    for r in recipes:
        # Join ingredients and numbered directions with standard \n
        ing_str = "\n".join(r.ingredients)
        dir_str = "\n".join(r.directions)
        tags_str = ", ".join(r.tags) if r.tags else ""

        writer.writerow({
            "Title": r.title or "",
            "Description": r.description or "",
            "Ingredients": ing_str,
            "Directions": dir_str,
            "Prep Time": r.prep_time or "",
            "Total Time": r.total_time or "",
            "Yield": r.yield_servings or "",
            "Tags": tags_str,
            "Source": r.source_notes or "",
        })

    return output.getvalue()


# ============================================================================
# 6. FASTAPI TEST APP & PIPELINE SESSION HARNESS
# ============================================================================

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

class PipelineState(str, Enum):
    IDLE = "IDLE"
    FINGERPRINTING = "FINGERPRINTING"
    CORRELATING = "CORRELATING"
    HITL_PENDING = "HITL_PENDING"
    EXTRACTING = "EXTRACTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PipelineSession:
    """Stateful pipeline session manager with asyncio.Event HITL bridge."""
    def __init__(self, session_id: str, config: Dict[str, Any], mock_client: MockGeminiClient, cache: SQLiteFingerprintCache):
        self.session_id = session_id
        self.config = config
        self.mock_client = mock_client
        self.cache = cache
        self.state = PipelineState.IDLE
        self.approval_event = asyncio.Event()
        self.cancellation_event = asyncio.Event()

        self.scanned_paths: List[Path] = []
        self.fingerprints: Dict[str, VisualFingerprint] = {}
        self.proposals: List[CorrelationProposal] = []
        self.unassigned_cards: List[VisualFingerprint] = []
        self.approved_items: List[CardPairApproval] = []
        self.rejected_card_ids: List[str] = []
        self.extracted_recipes: List[RecipeSchema] = []
        self.csv_output: Optional[str] = None
        self.error_message: Optional[str] = None

        self.progress_counts = {
            "total_scans": 0,
            "fingerprinted": 0,
            "proposals": 0,
            "approved": 0,
            "extracted": 0,
        }
        self.start_time: float = time.time()
        self.pipeline_task: Optional[asyncio.Task] = None

    async def run(self):
        try:
            # Step 1: Scan Directory
            scans_dir = Path(self.config["scans_dir"])
            if not scans_dir.exists() or not scans_dir.is_dir():
                raise HTTPException(status_code=400, detail="Invalid scans directory")

            valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".tiff"}
            all_files = sorted([p for p in scans_dir.iterdir() if p.suffix.lower() in valid_exts])
            if not all_files:
                raise HTTPException(status_code=400, detail="No valid card scans found in directory")

            self.scanned_paths = all_files
            self.progress_counts["total_scans"] = len(all_files)
            self.state = PipelineState.FINGERPRINTING

            # Step 2: Fingerprinting (Parallel or Slow-Batch)
            mode = self.config.get("mode", "parallel")
            if mode == "slow_batch":
                batch_size = self.config.get("slow_batch_size", 2)
                interval = self.config.get("slow_batch_interval_seconds", 0.05)
                for i in range(0, len(all_files), batch_size):
                    batch = all_files[i : i + batch_size]
                    for p in batch:
                        await self._fingerprint_card(p)
                    if i + batch_size < len(all_files) and interval > 0:
                        await asyncio.sleep(interval)
            else:
                concurrency = self.config.get("max_concurrency", 4)
                sem = asyncio.Semaphore(concurrency)

                async def _worker(p: Path):
                    async with sem:
                        await self._fingerprint_card(p)

                await asyncio.gather(*[_worker(p) for p in all_files])

            # Step 3: Correlation (Heuristic + Semantic)
            self.state = PipelineState.CORRELATING
            fronts = [fp for fp in self.fingerprints.values() if fp.role == CardRole.FRONT_ONLY]
            backs = [fp for fp in self.fingerprints.values() if fp.role == CardRole.BACK_ONLY]
            standalones = [fp for fp in self.fingerprints.values() if fp.role == CardRole.STANDALONE]
            unknowns = [fp for fp in self.fingerprints.values() if fp.role == CardRole.UNKNOWN]

            matched_backs = set()
            for f in fronts:
                best_proposal = None
                best_score = -1.0
                for b in backs:
                    if b.card_id in matched_backs:
                        continue
                    # Heuristic score
                    idx_diff = 1 if abs(self.scanned_paths.index(Path(f.image_path)) - self.scanned_paths.index(Path(b.image_path))) == 1 else 3
                    h_score = compute_heuristic_visual_score(f, b, idx_diff)
                    if h_score >= 0.40:
                        proposal = await self.mock_client.verify_correlation(f, b, h_score)
                        if proposal.confidence > best_score:
                            best_score = proposal.confidence
                            best_proposal = proposal

                if best_proposal and best_proposal.confidence >= 0.50:
                    self.proposals.append(best_proposal)
                    matched_backs.add(best_proposal.back_card_id)
                else:
                    self.unassigned_cards.append(f)

            # Add remaining backs to unassigned
            for b in backs:
                if b.card_id not in matched_backs:
                    self.unassigned_cards.append(b)

            # Add standalones to proposals
            for s in standalones:
                self.proposals.append(CorrelationProposal(
                    proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                    front_card_id=s.card_id,
                    back_card_id=None,
                    heuristic_score=1.0,
                    semantic_score=1.0,
                    confidence=1.0,
                    is_standalone=True,
                    continuity_explanation="Single-sided complete recipe.",
                    recommended_action="STANDALONE",
                ))

            # Add unknowns to unassigned
            self.unassigned_cards.extend(unknowns)
            self.progress_counts["proposals"] = len(self.proposals)

            # Step 4: Pause for HITL
            self.state = PipelineState.HITL_PENDING
            self.approval_event.clear()

            # Wait until approval or cancellation
            wait_approval = asyncio.create_task(self.approval_event.wait())
            wait_cancel = asyncio.create_task(self.cancellation_event.wait())
            done, pending = await asyncio.wait(
                [wait_approval, wait_cancel],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            if self.cancellation_event.is_set():
                self.state = PipelineState.CANCELLED
                return

            # Step 5: Resume to Extraction
            self.state = PipelineState.EXTRACTING
            for app_item in self.approved_items:
                if app_item.decision == HITLDecision.REJECT:
                    continue
                front_fp = self.fingerprints.get(app_item.front_card_id)
                back_fp = self.fingerprints.get(app_item.back_card_id) if app_item.back_card_id else None
                if front_fp:
                    rec = await self.mock_client.extract_recipe(front_fp, back_fp)
                    self.extracted_recipes.append(rec)
                    self.progress_counts["extracted"] += 1

            # Step 6: Plan To Eat CSV Serialization
            self.csv_output = serialize_recipes_to_plan_to_eat_csv(self.extracted_recipes)
            self.state = PipelineState.COMPLETED

        except Exception as exc:
            self.state = PipelineState.FAILED
            self.error_message = str(exc)

    async def _fingerprint_card(self, p: Path):
        try:
            image_bytes = p.read_bytes()
            sha = hashlib.sha256(image_bytes).hexdigest()
            # Check SQLite Cache
            cached = self.cache.get(image_bytes, str(p))
            if cached:
                self.fingerprints[p.name] = cached
            else:
                fp = await self.mock_client.analyze_fingerprint(p, sha)
                self.cache.put(fp)
                self.fingerprints[p.name] = fp
            self.progress_counts["fingerprinted"] += 1
        except Exception as e:
            # Handle corrupt card gracefully
            pass

    def submit_approval(self, approvals: List[CardPairApproval], rejected_ids: Optional[List[str]] = None):
        if self.state != PipelineState.HITL_PENDING:
            raise ValueError(f"Cannot approve session in state {self.state}")
        self.approved_items = approvals
        self.rejected_card_ids = rejected_ids or []
        self.progress_counts["approved"] = len(approvals)
        self.approval_event.set()

    def cancel(self):
        self.cancellation_event.set()


def create_test_fastapi_app(mock_client: MockGeminiClient, test_cache: SQLiteFingerprintCache) -> FastAPI:
    """Creates a FastAPI test instance with fully functional HITL pipeline endpoints."""
    app = FastAPI(title="RecipeBoxer Test Bridge", version="1.0.0")

    sessions: Dict[str, PipelineSession] = {}
    active_session_id_ref: List[Optional[str]] = [None]

    class StartRequest(BaseModel):
        scans_dir: str
        mode: str = "parallel"
        max_concurrency: int = 4
        slow_batch_size: int = 2
        slow_batch_interval_seconds: float = 0.05
        fingerprint_model: Optional[str] = "gemini-3.7-flash"
        correlation_model: Optional[str] = "gemini-3.7-flash"
        extraction_model: Optional[str] = "gemini-3.7-flash"
        thinking_mode: bool = False

    class ApproveRequest(BaseModel):
        session_id: Optional[str] = None
        approvals: List[CardPairApproval]
        rejected_card_ids: List[str] = Field(default_factory=list)

    @app.post("/api/pipeline/start")
    async def start_pipeline(req: StartRequest):
        scans_path = Path(req.scans_dir)
        if not scans_path.exists() or not scans_path.is_dir():
            raise HTTPException(status_code=400, detail="Invalid scans directory path")
        valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".tiff"}
        files = [f for f in scans_path.iterdir() if f.suffix.lower() in valid_exts]
        if not files:
            raise HTTPException(status_code=400, detail="No card images found in directory")

        sid = f"sess_{uuid.uuid4().hex[:8]}"
        session = PipelineSession(sid, req.model_dump(), mock_client, test_cache)
        sessions[sid] = session
        active_session_id_ref[0] = sid

        # Start async task
        session.pipeline_task = asyncio.create_task(session.run())

        # Yield to let pipeline reach initial state or HITL_PENDING
        await asyncio.sleep(0.01)

        return {
            "session_id": sid,
            "status": session.state.value,
            "mode": req.mode,
            "message": "Pipeline initiated successfully",
        }

    @app.get("/api/hitl/pending")
    async def get_hitl_pending(session_id: Optional[str] = Query(None)):
        sid = session_id or active_session_id_ref[0]
        if not sid or sid not in sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        session = sessions[sid]
        if session.state != PipelineState.HITL_PENDING:
            raise HTTPException(status_code=400, detail=f"Pipeline state is '{session.state.value}', not 'HITL_PENDING'")

        return {
            "session_id": sid,
            "status": session.state.value,
            "proposed_pairs": [p.model_dump() for p in session.proposals],
            "unassigned_cards": [u.model_dump() for u in session.unassigned_cards],
            "total_proposals": len(session.proposals),
            "total_unassigned": len(session.unassigned_cards),
        }

    @app.post("/api/hitl/approve")
    async def approve_hitl(req: ApproveRequest):
        sid = req.session_id or active_session_id_ref[0]
        if not sid or sid not in sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        session = sessions[sid]
        if session.state != PipelineState.HITL_PENDING:
            raise HTTPException(status_code=400, detail=f"Cannot approve in state '{session.state.value}'")

        session.submit_approval(req.approvals, req.rejected_card_ids)

        # Allow task to progress into extracting
        await asyncio.sleep(0.02)

        return {
            "session_id": sid,
            "status": session.state.value,
            "approved_count": len(req.approvals),
            "rejected_count": len(req.rejected_card_ids),
            "message": "HITL approval received. Pipeline resumed.",
        }

    @app.get("/api/status")
    async def get_status(session_id: Optional[str] = Query(None)):
        sid = session_id or active_session_id_ref[0]
        if not sid or sid not in sessions:
            return {"status": "IDLE", "message": "No active session"}
        session = sessions[sid]
        return {
            "session_id": sid,
            "status": session.state.value,
            "progress": session.progress_counts,
            "token_usage": session.mock_client.token_usage,
            "elapsed_seconds": round(time.time() - session.start_time, 2),
            "error": session.error_message,
        }

    @app.get("/api/export/csv")
    async def export_csv(session_id: Optional[str] = Query(None)):
        sid = session_id or active_session_id_ref[0]
        if not sid or sid not in sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        session = sessions[sid]
        if session.state != PipelineState.COMPLETED or not session.csv_output:
            raise HTTPException(status_code=400, detail="Extraction not completed or CSV not generated")

        return Response(
            content=session.csv_output,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="recipeboxer_export.csv"'},
        )

    return app


import pytest_asyncio


@pytest.fixture
def test_app(mock_gemini_client: MockGeminiClient, test_cache: SQLiteFingerprintCache) -> FastAPI:
    """Fixture providing configured FastAPI test app."""
    return create_test_fastapi_app(mock_gemini_client, test_cache)


@pytest_asyncio.fixture
async def async_client(test_app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Fixture providing httpx.AsyncClient with ASGITransport for FastAPI route testing."""
    transport = ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
