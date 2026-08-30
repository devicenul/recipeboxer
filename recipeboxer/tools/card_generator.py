"""Procedural synthetic recipe card generator for RecipeBoxer.

Generates photorealistic synthetic recipe cards and front/back pairs across
historical modalities:
- Vintage Handwritten Cursive (1940s-1970s index cards with aged paper, fountain pen bleed)
- Typewritten & Stamped (manual typewriter ribbon wear, character bounce, date stamps)
- Magazine Clippings & Package Labels (glued newsprint, multi-column layouts, tape strips)
- Stained / Damaged / Edge-Worn (oil smudges, coffee rings, dog-ears, edge tears)
- Multi-Card Sets (3-4 cards with continuous narrative flow)
- Standalone Single-Sided Cards
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from recipeboxer.models.domain import (
    ArtifactType,
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
)
from recipeboxer.models.recipe import RecipeSchema


# ============================================================================
# 1. ENUMS & CONFIGURATION MODELS
# ============================================================================

class PaperSubstrate(str, Enum):
    """Categorical substrate paper types."""
    AGED_YELLOW = "AGED_YELLOW"
    WHITE_LINEN = "WHITE_LINEN"
    RULED_INDEX = "RULED_INDEX"
    GRID_GRAPH = "GRID_GRAPH"
    KRAFT_BROWN = "KRAFT_BROWN"
    BLANK = "BLANK"


class CardModality(str, Enum):
    """Visual and historical rendering modality."""
    HANDWRITTEN_CURSIVE = "HANDWRITTEN_CURSIVE"
    TYPEWRITTEN = "TYPEWRITTEN"
    PRINTED_CLIPPING = "PRINTED_CLIPPING"
    MIXED_MEDIA = "MIXED_MEDIA"


class DegradationLevel(str, Enum):
    """Physical damage and age degradation intensity."""
    NONE = "NONE"
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"


@dataclass
class DegradationConfig:
    """Detailed physical artifact and degradation parameters."""
    level: DegradationLevel = DegradationLevel.NONE
    oil_smudges: int = 0
    coffee_rings: int = 0
    ink_fade: float = 0.0
    edge_tears: bool = False
    dog_ears: bool = False
    rotation_skew_deg: float = 0.0
    stamps: bool = False
    tape_residue: bool = False
    bleed_through: bool = False

    @classmethod
    def from_level(
        cls,
        level: DegradationLevel,
        rng: Optional[random.Random] = None,
    ) -> DegradationConfig:
        """Constructs a realistic DegradationConfig corresponding to an intensity level."""
        r = rng or random.Random()
        if level == DegradationLevel.NONE:
            return cls(level=DegradationLevel.NONE)
        elif level == DegradationLevel.MILD:
            return cls(
                level=DegradationLevel.MILD,
                oil_smudges=1 if r.random() < 0.5 else 0,
                coffee_rings=1 if r.random() < 0.3 else 0,
                ink_fade=round(r.uniform(0.05, 0.15), 2),
                edge_tears=False,
                dog_ears=r.random() < 0.4,
                rotation_skew_deg=round(r.uniform(-1.0, 1.0), 2),
                stamps=r.random() < 0.2,
                tape_residue=False,
                bleed_through=r.random() < 0.4,
            )
        elif level == DegradationLevel.MODERATE:
            return cls(
                level=DegradationLevel.MODERATE,
                oil_smudges=r.randint(1, 2),
                coffee_rings=1 if r.random() < 0.6 else 0,
                ink_fade=round(r.uniform(0.15, 0.30), 2),
                edge_tears=r.random() < 0.5,
                dog_ears=r.random() < 0.6,
                rotation_skew_deg=round(r.uniform(-2.5, 2.5), 2),
                stamps=r.random() < 0.4,
                tape_residue=r.random() < 0.4,
                bleed_through=True,
            )
        else:  # SEVERE
            return cls(
                level=DegradationLevel.SEVERE,
                oil_smudges=r.randint(2, 3),
                coffee_rings=r.randint(1, 2),
                ink_fade=round(r.uniform(0.30, 0.50), 2),
                edge_tears=True,
                dog_ears=True,
                rotation_skew_deg=round(r.uniform(-4.0, 4.0), 2),
                stamps=r.random() < 0.5,
                tape_residue=True,
                bleed_through=True,
            )


@dataclass
class GeneratedCard:
    """Container for a procedurally generated card scan image and ground-truth metadata."""
    image: Image.Image
    card_id: str
    filename: str
    modality: CardModality
    role: CardRole
    recipe_cluster: Dict[str, Any]
    physical_characteristics: Dict[str, Any]
    recipe: RecipeSchema


# ============================================================================
# 2. FONT MANAGEMENT & FALLBACKS
# ============================================================================

_FONT_CACHE: Dict[Tuple[str, int], ImageFont.ImageFont] = {}

def get_font(
    style: str = "default",
    size: int = 16,
) -> ImageFont.ImageFont:
    """Resolves font with cross-platform fallback."""
    key = (style, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    # Candidate font paths by style and platform
    candidates = []
    if style == "cursive":
        candidates = [
            "/System/Library/Fonts/Supplemental/SnellRoundhand.ttc",
            "/System/Library/Fonts/Supplemental/Brush Script.ttf",
            "/System/Library/Fonts/Supplemental/Zapfino.ttf",
            "/System/Library/Fonts/Supplemental/Comic Sans MS.ttf",
            "/usr/share/fonts/truetype/msttcorefonts/Comic_Sans_MS.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
            "/Library/Fonts/Comic Sans MS.ttf",
            "C:\\Windows\\Fonts\\comic.ttf",
            "C:\\Windows\\Fonts\\segoesc.ttf",
        ]
    elif style == "typewriter":
        candidates = [
            "/System/Library/Fonts/Supplemental/Courier New.ttf",
            "/System/Library/Fonts/Courier.dfont",
            "/System/Library/Fonts/Menlo.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
            "C:\\Windows\\Fonts\\cour.ttf",
            "C:\\Windows\\Fonts\\consola.ttf",
        ]
    elif style == "headline":
        candidates = [
            "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
            "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
            "C:\\Windows\\Fonts\\georgiab.ttf",
            "C:\\Windows\\Fonts\\timesbd.ttf",
        ]
    elif style == "print":
        candidates = [
            "/System/Library/Fonts/Supplemental/Georgia.ttf",
            "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Geneva.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
            "C:\\Windows\\Fonts\\georgia.ttf",
            "C:\\Windows\\Fonts\\times.ttf",
        ]

    for path in candidates:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size=size)
                _FONT_CACHE[key] = font
                return font
            except Exception:
                continue

    try:
        font = ImageFont.load_default(size=size)
    except Exception:
        try:
            font = ImageFont.load_default_imagefont()
        except Exception:
            font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ============================================================================
# 3. PROCEDURAL SUBSTRATE & TEXT RENDERING
# ============================================================================

def create_paper_substrate(
    width: int,
    height: int,
    substrate: PaperSubstrate,
    rng: random.Random,
) -> Tuple[Image.Image, Tuple[int, int, int]]:
    """Creates a realistic textured paper canvas."""
    if substrate == PaperSubstrate.AGED_YELLOW:
        base_color = (
            rng.randint(242, 248),
            rng.randint(230, 238),
            rng.randint(200, 212),
        )
    elif substrate == PaperSubstrate.WHITE_LINEN:
        c = rng.randint(250, 255)
        base_color = (c, c - rng.randint(0, 2), c - rng.randint(2, 6))
    elif substrate == PaperSubstrate.KRAFT_BROWN:
        base_color = (
            rng.randint(210, 222),
            rng.randint(185, 198),
            rng.randint(155, 170),
        )
    elif substrate == PaperSubstrate.RULED_INDEX:
        base_color = (
            rng.randint(245, 252),
            rng.randint(238, 246),
            rng.randint(215, 228),
        )
    elif substrate == PaperSubstrate.GRID_GRAPH:
        base_color = (
            rng.randint(246, 252),
            rng.randint(246, 252),
            rng.randint(240, 248),
        )
    else:  # BLANK
        base_color = (250, 246, 236)

    img = Image.new("RGB", (width, height), color=base_color)
    draw = ImageDraw.Draw(img, "RGBA")

    # Add procedural paper grain texture
    noise_overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    noise_draw = ImageDraw.Draw(noise_overlay)
    for _ in range(1500):
        nx = rng.randint(0, width - 1)
        ny = rng.randint(0, height - 1)
        n_alpha = rng.randint(4, 18)
        is_dark = rng.random() < 0.65
        n_fill = (40, 30, 20, n_alpha) if is_dark else (255, 255, 255, n_alpha)
        noise_draw.point((nx, ny), fill=n_fill)

    img = Image.alpha_composite(img.convert("RGBA"), noise_overlay).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    # Draw ruling lines if specified
    if substrate in (PaperSubstrate.RULED_INDEX, PaperSubstrate.AGED_YELLOW):
        line_spacing = 26
        header_y = 60
        margin_x = 35
        # Red header line
        draw.line(
            [(margin_x, header_y), (width - margin_x, header_y)],
            fill=(215, 80, 80, 180),
            width=2,
        )
        # Blue ruling lines
        ruling_color = (160, 190, 220, 140)
        for y in range(header_y + line_spacing, height - 25, line_spacing):
            draw.line(
                [(margin_x, y), (width - margin_x, y)],
                fill=ruling_color,
                width=1,
            )
    elif substrate == PaperSubstrate.GRID_GRAPH:
        grid_spacing = 20
        grid_color = (180, 210, 230, 120)
        for x in range(20, width - 20, grid_spacing):
            draw.line([(x, 20), (x, height - 20)], fill=grid_color, width=1)
        for y in range(20, height - 20, grid_spacing):
            draw.line([(20, y), (width - 20, y)], fill=grid_color, width=1)

    return img, base_color


def render_handwritten_lines(
    draw: ImageDraw.ImageDraw,
    lines: List[str],
    start_x: int,
    start_y: int,
    max_width: int,
    max_height: int,
    ink_color: Tuple[int, int, int],
    font_size: int,
    rng: random.Random,
    font_style: str = "cursive",
) -> int:
    """Renders text simulating natural handwriting with baseline wander and ink variation."""
    font = get_font(font_style, size=font_size)
    y = start_y
    line_height = int(font_size * 1.45)

    for line in lines:
        if y + line_height > max_height - 15:
            break
        # Baseline wobble per line
        y_wobble = rng.randint(-1, 2)
        x = start_x + rng.randint(-2, 2)

        # Draw line with subtle stroke alpha jitter
        alpha = rng.randint(215, 255)
        color = (*ink_color, alpha)
        draw.text((x, y + y_wobble), line, fill=color, font=font)
        y += line_height

    return y


def render_typewritten_lines(
    draw: ImageDraw.ImageDraw,
    lines: List[str],
    start_x: int,
    start_y: int,
    max_width: int,
    max_height: int,
    ink_color: Tuple[int, int, int],
    font_size: int,
    rng: random.Random,
) -> int:
    """Renders text simulating a mechanical typewriter with character bounce and ribbon wear."""
    font = get_font("typewriter", size=font_size)
    y = start_y
    line_height = int(font_size * 1.4)

    for line in lines:
        if y + line_height > max_height - 15:
            break
        x = start_x
        # Character by character bounce and strike variability
        for char in line:
            if char == " ":
                x += int(font_size * 0.6)
                continue
            char_wobble_y = rng.choice([-1, 0, 0, 0, 1])
            strike_alpha = rng.randint(185, 255)
            draw.text((x, y + char_wobble_y), char, fill=(*ink_color, strike_alpha), font=font)
            # Advance character width
            try:
                bbox = font.getbbox(char)
                char_w = max(bbox[2] - bbox[0], int(font_size * 0.55))
            except Exception:
                char_w = int(font_size * 0.6)
            x += char_w + 1
        y += line_height

    return y


def render_clipping_block(
    image: Image.Image,
    title: str,
    ingredients: List[str],
    directions: List[str],
    rng: random.Random,
) -> Image.Image:
    """Renders a magazine clipping / newsprint cutout pasted onto the card substrate."""
    w, h = image.size
    # Clipping rectangle dimensions
    clip_margin_x = rng.randint(30, 45)
    clip_margin_y = rng.randint(25, 40)
    clip_w = w - 2 * clip_margin_x
    clip_h = h - 2 * clip_margin_y

    # Create newsprint surface (slightly gray/yellowed newsprint)
    news_color = (
        rng.randint(232, 240),
        rng.randint(228, 235),
        rng.randint(215, 222),
    )
    news_clip = Image.new("RGBA", (clip_w, clip_h), color=(*news_color, 250))
    clip_draw = ImageDraw.Draw(news_clip)

    # Newsprint border line
    clip_draw.rectangle(
        [(4, 4), (clip_w - 5, clip_h - 5)],
        outline=(100, 90, 80, 160),
        width=1,
    )

    # Headline
    head_font = get_font("headline", size=18)
    body_font = get_font("print", size=13)
    title_y = 12
    clip_draw.text((15, title_y), title, fill=(20, 20, 20, 255), font=head_font)

    # Sub-rule under title
    clip_draw.line([(15, 36), (clip_w - 15, 36)], fill=(80, 80, 80, 200), width=1)

    # 2-column or structured column layout
    col1_w = int(clip_w * 0.45)
    col2_x = col1_w + 25

    # Column 1: Ingredients
    cur_y = 44
    clip_draw.text((15, cur_y), "INGREDIENTS:", fill=(40, 40, 40, 255), font=get_font("headline", size=12))
    cur_y += 18
    for ing in ingredients[:8]:
        if cur_y > clip_h - 20:
            break
        clip_draw.text((15, cur_y), f"• {ing}", fill=(30, 30, 30, 240), font=body_font)
        cur_y += 16

    # Column 2: Directions
    cur_y2 = 44
    clip_draw.text((col2_x, cur_y2), "METHOD:", fill=(40, 40, 40, 255), font=get_font("headline", size=12))
    cur_y2 += 18
    for d in directions[:6]:
        if cur_y2 > clip_h - 20:
            break
        text_d = d if len(d) <= 45 else d[:42] + "..."
        clip_draw.text((col2_x, cur_y2), text_d, fill=(30, 30, 30, 240), font=body_font)
        cur_y2 += 18

    # Slight angle rotation for realistic glued clipping
    angle = rng.uniform(-1.0, 1.0)
    rotated_clip = news_clip.rotate(angle, resample=Image.BICUBIC, expand=True)

    # Paste onto substrate
    res_img = image.convert("RGBA")
    res_img.paste(rotated_clip, (clip_margin_x, clip_margin_y), rotated_clip)

    # Simulated cellophane tape strips holding clipping
    tape_draw = ImageDraw.Draw(res_img)
    tape_color = (220, 200, 140, 160)
    tape_draw.rectangle(
        [(clip_margin_x + 40, clip_margin_y - 8), (clip_margin_x + 90, clip_margin_y + 12)],
        fill=tape_color,
        outline=(190, 170, 110, 180),
    )
    tape_draw.rectangle(
        [(w - clip_margin_x - 90, h - clip_margin_y - 12), (w - clip_margin_x - 40, h - clip_margin_y + 8)],
        fill=tape_color,
        outline=(190, 170, 110, 180),
    )

    return res_img.convert("RGB")


# ============================================================================
# 4. PHYSICAL ARTIFACTS & DEGRADATION SIMULATORS
# ============================================================================

def apply_coffee_ring(
    image: Image.Image,
    cx: int,
    cy: int,
    radius: int,
    rng: Optional[random.Random] = None,
    alpha: int = 150,
) -> Image.Image:
    """Applies a realistic coffee/tea cup stain ring with darker outer edge and inner puddle."""
    r = rng or random.Random()
    w, h = image.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    rx = radius + r.randint(-3, 3)
    ry = radius + r.randint(-3, 3)
    ring_box = [cx - rx, cy - ry, cx + rx, cy + ry]

    # Inner subtle wash
    draw.ellipse(ring_box, fill=(160, 110, 50, int(alpha * 0.18)))

    # Outer dark edge rim
    for delta in range(-2, 3):
        cur_box = [ring_box[0] + delta, ring_box[1] + delta, ring_box[2] - delta, ring_box[3] - delta]
        edge_alpha = int(alpha * r.uniform(0.65, 0.95))
        draw.ellipse(cur_box, outline=(120, 75, 30, edge_alpha), width=2)

    for _ in range(r.randint(2, 6)):
        dx = cx + int(rx * r.uniform(0.8, 1.3) * r.choice([-1, 1]))
        dy = cy + int(ry * r.uniform(0.8, 1.3) * r.choice([-1, 1]))
        dr = r.randint(2, 5)
        draw.ellipse([dx - dr, dy - dr, dx + dr, dy + dr], fill=(130, 85, 35, int(alpha * 0.6)))

    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=1.2))
    base_rgba = image.convert("RGBA")
    return Image.alpha_composite(base_rgba, overlay).convert("RGB")


def apply_oil_smudge(
    image: Image.Image,
    bbox: Tuple[int, int, int, int],
    rng: Optional[random.Random] = None,
    alpha: int = 110,
) -> Image.Image:
    """Applies a translucent amber/yellow organic oil grease stain."""
    r = rng or random.Random()
    w, h = image.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    x1, y1, x2, y2 = bbox
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    bw = (x2 - x1) // 2
    bh = (y2 - y1) // 2

    for _ in range(r.randint(4, 6)):
        ox = cx + r.randint(-bw // 3, bw // 3)
        oy = cy + r.randint(-bh // 3, bh // 3)
        orx = r.randint(int(bw * 0.6), bw)
        ory = r.randint(int(bh * 0.6), bh)
        draw.ellipse(
            [ox - orx, oy - ory, ox + orx, oy + ory],
            fill=(210, 175, 75, r.randint(int(alpha * 0.4), alpha)),
        )

    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=8.0))
    base_rgba = image.convert("RGBA")
    return Image.alpha_composite(base_rgba, overlay).convert("RGB")


def apply_ink_fade(
    image: Image.Image,
    fade_factor: float = 0.2,
    rng: Optional[random.Random] = None,
) -> Image.Image:
    """Simulates vintage ink fading, sunlight bleaching, and reduced contrast over time."""
    if fade_factor <= 0.001:
        return image
    factor = min(max(fade_factor, 0.0), 1.0)
    contrast_enhancer = ImageEnhance.Contrast(image)
    faded = contrast_enhancer.enhance(max(0.4, 1.0 - (factor * 0.45)))
    brightness_enhancer = ImageEnhance.Brightness(faded)
    faded = brightness_enhancer.enhance(1.0 + (factor * 0.12))
    return faded


def apply_dog_ear(
    image: Image.Image,
    corner: str = "TOP_RIGHT",
    size: int = 45,
    bg_color: Tuple[int, int, int] = (230, 220, 200),
) -> Image.Image:
    """Simulates a folded index card corner with underside shading."""
    w, h = image.size
    res = image.convert("RGBA")
    draw = ImageDraw.Draw(res)

    if corner == "TOP_RIGHT":
        pts_missing = [(w - size, 0), (w, 0), (w, size)]
        pts_fold = [(w - size, 0), (w - size, size), (w, size)]
        draw.polygon(pts_missing, fill=(255, 255, 255, 0))
        draw.polygon(pts_fold, fill=(*bg_color, 240), outline=(120, 110, 100, 220))
    elif corner == "TOP_LEFT":
        pts_missing = [(0, 0), (size, 0), (0, size)]
        pts_fold = [(size, 0), (size, size), (0, size)]
        draw.polygon(pts_missing, fill=(255, 255, 255, 0))
        draw.polygon(pts_fold, fill=(*bg_color, 240), outline=(120, 110, 100, 220))
    elif corner == "BOTTOM_RIGHT":
        pts_missing = [(w - size, h), (w, h), (w, h - size)]
        pts_fold = [(w - size, h), (w - size, h - size), (w, h - size)]
        draw.polygon(pts_missing, fill=(255, 255, 255, 0))
        draw.polygon(pts_fold, fill=(*bg_color, 240), outline=(120, 110, 100, 220))
    else:  # BOTTOM_LEFT
        pts_missing = [(0, h), (size, h), (0, h - size)]
        pts_fold = [(size, h), (size, h - size), (0, h - size)]
        draw.polygon(pts_missing, fill=(255, 255, 255, 0))
        draw.polygon(pts_fold, fill=(*bg_color, 240), outline=(120, 110, 100, 220))

    return res.convert("RGB")


def apply_edge_tear(
    image: Image.Image,
    edge: str = "RIGHT",
    rng: Optional[random.Random] = None,
) -> Image.Image:
    """Applies a ragged torn edge / missing chunk from card perimeter."""
    r = rng or random.Random()
    w, h = image.size
    res = image.convert("RGBA")
    draw = ImageDraw.Draw(res)
    edge_norm = edge.upper().strip()

    if edge_norm in ("RIGHT", "EDGE_RIGHT"):
        mid_y = r.randint(h // 4, 3 * h // 4)
        tear_w = r.randint(25, 45)
        tear_h = r.randint(30, 60)
        pts = [
            (w, mid_y - tear_h // 2),
            (w - tear_w, mid_y - tear_h // 4),
            (w - int(tear_w * 0.8), mid_y),
            (w - tear_w + 5, mid_y + tear_h // 4),
            (w, mid_y + tear_h // 2),
        ]
        draw.polygon(pts, fill=(255, 255, 255, 255), outline=(160, 150, 140, 200))
    elif edge_norm in ("LEFT", "EDGE_LEFT"):
        mid_y = r.randint(h // 4, 3 * h // 4)
        tear_w = r.randint(25, 45)
        tear_h = r.randint(30, 60)
        pts = [
            (0, mid_y - tear_h // 2),
            (tear_w, mid_y - tear_h // 4),
            (int(tear_w * 0.8), mid_y),
            (tear_w - 5, mid_y + tear_h // 4),
            (0, mid_y + tear_h // 2),
        ]
        draw.polygon(pts, fill=(255, 255, 255, 255), outline=(160, 150, 140, 200))
    elif edge_norm in ("TOP", "EDGE_TOP"):
        mid_x = r.randint(w // 4, 3 * w // 4)
        tear_w = r.randint(30, 60)
        tear_h = r.randint(20, 40)
        pts = [
            (mid_x - tear_w // 2, 0),
            (mid_x - tear_w // 4, tear_h),
            (mid_x, int(tear_h * 0.8)),
            (mid_x + tear_w // 4, tear_h - 5),
            (mid_x + tear_w // 2, 0),
        ]
        draw.polygon(pts, fill=(255, 255, 255, 255), outline=(160, 150, 140, 200))
    elif edge_norm in ("BOTTOM", "EDGE_BOTTOM"):
        mid_x = r.randint(w // 4, 3 * w // 4)
        tear_w = r.randint(30, 60)
        tear_h = r.randint(20, 40)
        pts = [
            (mid_x - tear_w // 2, h),
            (mid_x - tear_w // 4, h - tear_h),
            (mid_x, h - int(tear_h * 0.8)),
            (mid_x + tear_w // 4, h - tear_h + 5),
            (mid_x + tear_w // 2, h),
        ]
        draw.polygon(pts, fill=(255, 255, 255, 255), outline=(160, 150, 140, 200))

    return res.convert("RGB")


def apply_date_stamp(
    image: Image.Image,
    x: int,
    y: int,
    text: str,
    rng: Optional[random.Random] = None,
) -> Image.Image:
    """Applies a distressed vintage ink rubber stamp."""
    r = rng or random.Random()
    stamp_img = Image.new("RGBA", (180, 50), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(stamp_img)
    stamp_color = (180, 40, 40, r.randint(180, 230))

    sdraw.rectangle([(2, 2), (176, 46)], outline=stamp_color, width=2)
    sfont = get_font("typewriter", size=12)
    sdraw.text((10, 16), text, fill=stamp_color, font=sfont)

    angle = r.uniform(-8.0, 8.0)
    rotated_stamp = stamp_img.rotate(angle, resample=Image.BICUBIC, expand=True)

    base = image.convert("RGBA")
    base.paste(rotated_stamp, (x, y), rotated_stamp)
    return base.convert("RGB")


def apply_tape_residue(
    image: Image.Image,
    bbox: Optional[Tuple[int, int, int, int]] = None,
    corner: Optional[str] = "TOP_RIGHT",
    rng: Optional[random.Random] = None,
    alpha: int = 160,
) -> Image.Image:
    """Simulates aged, yellowed cellophane tape strip and adhesive residue."""
    r = rng or random.Random()
    w, h = image.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    if bbox:
        x1, y1, x2, y2 = bbox
    else:
        # Default corner or edge placement
        if corner == "TOP_RIGHT":
            x1, y1, x2, y2 = w - 110, 10, w - 20, 35
        elif corner == "TOP_LEFT":
            x1, y1, x2, y2 = 20, 10, 110, 35
        elif corner == "BOTTOM_RIGHT":
            x1, y1, x2, y2 = w - 110, h - 35, w - 20, h - 10
        else:  # BOTTOM_LEFT or EDGE
            x1, y1, x2, y2 = 20, h - 35, 110, h - 10

    # Draw translucent aged amber cellophane strip
    tape_color = (225, 205, 135, alpha)
    border_color = (195, 175, 105, min(255, int(alpha * 1.2)))
    draw.rectangle([(x1, y1), (x2, y2)], fill=tape_color, outline=border_color, width=1)

    # Add subtle adhesive degradation bubbles / dirt along edges
    for _ in range(r.randint(3, 7)):
        bx = r.randint(x1 + 2, x2 - 2)
        by = r.choice([y1 + 1, y2 - 1, (y1 + y2) // 2])
        draw.point((bx, by), fill=(160, 140, 80, int(alpha * 0.9)))

    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=0.5))
    base_rgba = image.convert("RGBA")
    return Image.alpha_composite(base_rgba, overlay).convert("RGB")


def apply_rotation_skew(
    image: Image.Image,
    angle_deg: float,
    bg_color: Tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Rotates card with high-quality resampling and expands canvas."""
    if abs(angle_deg) < 0.1:
        return image
    rgba = image.convert("RGBA")
    rotated = rgba.rotate(angle_deg, resample=Image.BICUBIC, expand=True)
    canvas = Image.new("RGBA", rotated.size, color=(*bg_color, 255))
    canvas.paste(rotated, (0, 0), rotated)
    return canvas.convert("RGB")


# ============================================================================
# 5. SYNTHETIC RECIPE CARD GENERATOR CLASS
# ============================================================================

class SyntheticCardGenerator:
    """Procedural generator for paired and standalone recipe card datasets."""

    def __init__(
        self,
        card_width: int = 680,
        card_height: int = 440,
        seed: Optional[int] = None,
    ):
        self.card_width = card_width
        self.card_height = card_height
        self.seed = seed
        self.rng = random.Random(seed)

    def set_seed(self, seed: Optional[int]) -> None:
        """Sets random seed for deterministic generation."""
        self.seed = seed
        self.rng = random.Random(seed)

    def generate_card_pair(
        self,
        recipe: RecipeSchema,
        modality: CardModality = CardModality.HANDWRITTEN_CURSIVE,
        substrate: PaperSubstrate = PaperSubstrate.RULED_INDEX,
        degradation: Optional[DegradationConfig] = None,
        front_card_id: Optional[str] = None,
        back_card_id: Optional[str] = None,
    ) -> Tuple[GeneratedCard, GeneratedCard]:
        """Generates a synchronized front/back card pair with matching paper, ink, and mirrored stains."""
        deg = degradation or DegradationConfig.from_level(DegradationLevel.MILD, self.rng)
        rec_id = recipe.recipe_id or f"rec_{uuid.uuid4().hex[:8]}"

        f_id = front_card_id or f"{rec_id}_front.jpg"
        b_id = back_card_id or f"{rec_id}_back.jpg"

        # 1. Coordinate physical characteristics
        img_front, base_color = create_paper_substrate(self.card_width, self.card_height, substrate, self.rng)
        img_back, _ = create_paper_substrate(self.card_width, self.card_height, substrate, self.rng)

        # Coordinate ink color
        if modality == CardModality.HANDWRITTEN_CURSIVE:
            ink_color = (20, 35, 115) if self.rng.random() < 0.7 else (30, 30, 30)
            ink_type = InkType.BLUE_BALLPOINT if ink_color[0] < ink_color[2] else InkType.BLACK_BALLPOINT
        elif modality == CardModality.TYPEWRITTEN:
            ink_color = (25, 25, 25)
            ink_type = InkType.TYPEWRITER_RIBBON
        else:
            ink_color = (15, 15, 15)
            ink_type = InkType.PRINTED_TYPESET

        # 2. Render Text Content for Front & Back
        draw_f = ImageDraw.Draw(img_front, "RGBA")
        draw_b = ImageDraw.Draw(img_back, "RGBA")

        total_dirs = recipe.directions or ["1. Prepare ingredients.", "2. Mix and bake."]
        mid_idx = max(1, len(total_dirs) // 2)
        front_dirs = total_dirs[:mid_idx]
        back_dirs = total_dirs[mid_idx:]

        # Front Text Rendering
        if modality == CardModality.HANDWRITTEN_CURSIVE:
            draw_f.text((40, 22), recipe.title, fill=(*ink_color, 255), font=get_font("headline", size=20))
            y_f = 70
            draw_f.text((40, y_f), "Ingredients:", fill=(*ink_color, 240), font=get_font("headline", size=14))
            y_f += 22
            y_f = render_handwritten_lines(
                draw_f, recipe.ingredients[:7], 40, y_f, self.card_width, self.card_height, ink_color, 14, self.rng
            )
            if front_dirs and y_f < self.card_height - 90:
                y_f += 10
                draw_f.text((40, y_f), "Method:", fill=(*ink_color, 240), font=get_font("headline", size=14))
                y_f += 20
                y_f = render_handwritten_lines(
                    draw_f, front_dirs, 40, y_f, self.card_width, self.card_height, ink_color, 13, self.rng
                )
            draw_f.text((self.card_width - 130, self.card_height - 32), "(over ->)", fill=(*ink_color, 255), font=get_font("cursive", size=15))

            # Back Text Rendering
            draw_b.text((40, 22), f"(Directions continued - {recipe.title[:25]}...)", fill=(*ink_color, 230), font=get_font("cursive", size=15))
            y_b = 68
            y_b = render_handwritten_lines(
                draw_b, back_dirs, 40, y_b, self.card_width, self.card_height, ink_color, 14, self.rng
            )
            if recipe.yield_servings or recipe.prep_time:
                notes_line = f"Yield: {recipe.yield_servings or '4 servings'} | Total Time: {recipe.total_time or '45 mins'}"
                draw_b.text((40, self.card_height - 40), notes_line, fill=(*ink_color, 220), font=get_font("cursive", size=13))

        elif modality == CardModality.TYPEWRITTEN:
            draw_f.text((45, 24), recipe.title.upper(), fill=(*ink_color, 255), font=get_font("typewriter", size=18))
            y_f = 68
            draw_f.text((45, y_f), "--- INGREDIENTS ---", fill=(*ink_color, 240), font=get_font("typewriter", size=13))
            y_f += 20
            y_f = render_typewritten_lines(
                draw_f, [f"- {ing}" for ing in recipe.ingredients[:7]], 45, y_f, self.card_width, self.card_height, ink_color, 13, self.rng
            )
            if front_dirs and y_f < self.card_height - 85:
                y_f += 10
                draw_f.text((45, y_f), "--- PREPARATION ---", fill=(*ink_color, 240), font=get_font("typewriter", size=13))
                y_f += 20
                y_f = render_typewritten_lines(
                    draw_f, front_dirs, 45, y_f, self.card_width, self.card_height, ink_color, 12, self.rng
                )
            draw_f.text((self.card_width - 140, self.card_height - 30), "PTO >>>", fill=(*ink_color, 255), font=get_font("typewriter", size=14))

            draw_b.text((45, 24), "(CONTINUED FROM FRONT)", fill=(*ink_color, 240), font=get_font("typewriter", size=14))
            y_b = 68
            y_b = render_typewritten_lines(
                draw_b, back_dirs, 45, y_b, self.card_width, self.card_height, ink_color, 13, self.rng
            )
            if recipe.yield_servings or recipe.total_time:
                notes_line = f"YIELD: {recipe.yield_servings or '4 SERVINGS'}  |  TIME: {recipe.total_time or '45 MINS'}"
                draw_b.text((45, self.card_height - 38), notes_line, fill=(*ink_color, 220), font=get_font("typewriter", size=12))

        else:  # PRINTED_CLIPPING or MIXED_MEDIA
            img_front = render_clipping_block(img_front, recipe.title, recipe.ingredients, front_dirs, self.rng)
            draw_b = ImageDraw.Draw(img_back, "RGBA")
            draw_b.text((40, 25), "(Directions continued)", fill=(*ink_color, 240), font=get_font("headline", size=16))
            y_b = 70
            render_typewritten_lines(
                draw_b, back_dirs, 40, y_b, self.card_width, self.card_height, ink_color, 13, self.rng
            )

        # 3. Apply Physical Artifacts with Mirrored Front/Back Coordination
        front_artifacts: List[Dict[str, Any]] = []
        back_artifacts: List[Dict[str, Any]] = []

        # Oil smudges
        if deg.oil_smudges > 0:
            stain_box_f = (self.card_width - 160, 20, self.card_width - 30, 130)
            img_front = apply_oil_smudge(img_front, stain_box_f, self.rng, alpha=110)
            front_artifacts.append({
                "artifact_type": "OIL_GREASE_STAIN",
                "quadrant": "TOP_RIGHT",
                "severity": "MODERATE",
                "description": "Grease stain near upper right edge",
            })
            if deg.bleed_through:
                stain_box_b = (30, 20, 160, 130)
                img_back = apply_oil_smudge(img_back, stain_box_b, self.rng, alpha=70)
                back_artifacts.append({
                    "artifact_type": "OIL_GREASE_STAIN",
                    "quadrant": "TOP_LEFT",
                    "severity": "MILD",
                    "description": "Mirrored grease bleed-through from front",
                })

        # Coffee rings
        if deg.coffee_rings > 0:
            ring_cx = self.rng.randint(int(self.card_width * 0.55), int(self.card_width * 0.75))
            ring_cy = self.rng.randint(70, 180)
            ring_r = self.rng.randint(45, 65)
            img_front = apply_coffee_ring(img_front, ring_cx, ring_cy, ring_r, self.rng, alpha=140)
            front_artifacts.append({
                "artifact_type": "COFFEE_TEA_RING",
                "quadrant": "TOP_RIGHT",
                "severity": "MODERATE",
                "description": "Coffee cup stain ring",
            })
            if deg.bleed_through:
                img_back = apply_coffee_ring(img_back, self.card_width - ring_cx, ring_cy, ring_r, self.rng, alpha=75)
                back_artifacts.append({
                    "artifact_type": "COFFEE_TEA_RING",
                    "quadrant": "TOP_LEFT",
                    "severity": "MILD",
                    "description": "Mirrored coffee ring bleed-through",
                })

        # Dog-ears
        if deg.dog_ears:
            img_front = apply_dog_ear(img_front, "TOP_RIGHT", size=40)
            img_back = apply_dog_ear(img_back, "TOP_LEFT", size=40)
            front_artifacts.append({
                "artifact_type": "CORNER_DOG_EAR_FOLD",
                "quadrant": "TOP_RIGHT",
                "severity": "MILD",
            })
            back_artifacts.append({
                "artifact_type": "CORNER_DOG_EAR_FOLD",
                "quadrant": "TOP_LEFT",
                "severity": "MILD",
            })

        # Edge tears
        if deg.edge_tears:
            img_front = apply_edge_tear(img_front, "RIGHT", self.rng)
            img_back = apply_edge_tear(img_back, "LEFT", self.rng)
            front_artifacts.append({
                "artifact_type": "TEAR_EDGE",
                "quadrant": "EDGE_RIGHT",
                "severity": "MODERATE",
            })
            back_artifacts.append({
                "artifact_type": "TEAR_EDGE",
                "quadrant": "EDGE_LEFT",
                "severity": "MODERATE",
            })

        # Stamps
        if deg.stamps:
            if "swedish_meatballs" in rec_id:
                stamp_txt = "BETTER HOMES CLUB OCT 1961"
            elif "lemon_chiffon" in rec_id:
                stamp_txt = "LADIES AUXILIARY 1958"
            elif "beef_stroganoff" in rec_id:
                stamp_txt = "RECIPE CLUB 1956"
            else:
                stamp_txt = f"RECIPE CLUB {self.rng.randint(1952, 1974)}"
            img_front = apply_date_stamp(img_front, 40, self.card_height - 65, stamp_txt, self.rng)
            front_artifacts.append({
                "artifact_type": "RUBBER_DATE_STAMP",
                "quadrant": "BOTTOM_LEFT",
                "severity": "MILD",
                "description": f"Vintage ink stamp: {stamp_txt}",
            })

        # Tape residue
        if deg.tape_residue:
            img_front = apply_tape_residue(img_front, corner="TOP_RIGHT", rng=self.rng)
            front_artifacts.append({
                "artifact_type": "TAPE_RESIDUE_YELLOWED",
                "quadrant": "TOP_RIGHT",
                "severity": "MILD",
                "description": "Yellowed cellophane tape residue",
            })
            if deg.bleed_through:
                img_back = apply_tape_residue(img_back, corner="TOP_LEFT", rng=self.rng, alpha=100)
                back_artifacts.append({
                    "artifact_type": "TAPE_RESIDUE_YELLOWED",
                    "quadrant": "TOP_LEFT",
                    "severity": "MILD",
                    "description": "Mirrored tape adhesive discoloration",
                })

        # Ink fade
        if deg.ink_fade > 0:
            img_front = apply_ink_fade(img_front, deg.ink_fade, self.rng)
            img_back = apply_ink_fade(img_back, deg.ink_fade, self.rng)

        # Skew
        if abs(deg.rotation_skew_deg) > 0.1:
            img_front = apply_rotation_skew(img_front, deg.rotation_skew_deg)
            img_back = apply_rotation_skew(img_back, deg.rotation_skew_deg)

        # Recipe metadata attachment
        recipe.card_ids = [f_id, b_id]

        card_front = GeneratedCard(
            image=img_front,
            card_id=f_id,
            filename=f_id,
            modality=modality,
            role=CardRole.SEQUENCE_START,
            recipe_cluster={
                "recipe_id": rec_id,
                "sequence_position": 0,
                "cluster_role": "card1_front",
                "total_cards": 2,
                "continuation_cue": "(over ->)" if modality == CardModality.HANDWRITTEN_CURSIVE else "PTO >>>",
            },
            physical_characteristics={
                "paper_tone": substrate.value,
                "ruling_type": "STANDARD_RULED_BLUE" if substrate in (PaperSubstrate.RULED_INDEX, PaperSubstrate.AGED_YELLOW) else "UNRULED_BLANK",
                "ink_type": ink_type.value,
                "artifacts": front_artifacts,
            },
            recipe=recipe,
        )

        card_back = GeneratedCard(
            image=img_back,
            card_id=b_id,
            filename=b_id,
            modality=modality,
            role=CardRole.SEQUENCE_CONTINUATION,
            recipe_cluster={
                "recipe_id": rec_id,
                "sequence_position": 1,
                "cluster_role": "card1_back",
                "total_cards": 2,
                "continuation_cue": None,
            },
            physical_characteristics={
                "paper_tone": substrate.value,
                "ruling_type": "STANDARD_RULED_BLUE" if substrate in (PaperSubstrate.RULED_INDEX, PaperSubstrate.AGED_YELLOW) else "UNRULED_BLANK",
                "ink_type": ink_type.value,
                "artifacts": back_artifacts,
            },
            recipe=recipe,
        )

        return card_front, card_back

    def generate_standalone_card(
        self,
        recipe: RecipeSchema,
        modality: CardModality = CardModality.HANDWRITTEN_CURSIVE,
        substrate: PaperSubstrate = PaperSubstrate.RULED_INDEX,
        degradation: Optional[DegradationConfig] = None,
        card_id: Optional[str] = None,
    ) -> GeneratedCard:
        """Generates a standalone single-sided recipe card scan."""
        deg = degradation or DegradationConfig.from_level(DegradationLevel.MILD, self.rng)
        rec_id = recipe.recipe_id or f"rec_{uuid.uuid4().hex[:8]}"
        cid = card_id or f"{rec_id}_standalone.jpg"

        img, base_color = create_paper_substrate(self.card_width, self.card_height, substrate, self.rng)
        draw = ImageDraw.Draw(img, "RGBA")

        if modality == CardModality.HANDWRITTEN_CURSIVE:
            ink_color = (25, 25, 25)
            ink_type = InkType.BLACK_BALLPOINT
            draw.text((40, 22), recipe.title, fill=(*ink_color, 255), font=get_font("headline", size=20))
            y = 66
            draw.text((40, y), "Ingredients:", fill=(*ink_color, 240), font=get_font("headline", size=13))
            y += 18
            y = render_handwritten_lines(
                draw, recipe.ingredients[:5], 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
            )
            y += 8
            draw.text((40, y), "Directions:", fill=(*ink_color, 240), font=get_font("headline", size=13))
            y += 18
            render_handwritten_lines(
                draw, recipe.directions[:4], 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
            )
        elif modality == CardModality.TYPEWRITTEN:
            ink_color = (20, 20, 20)
            ink_type = InkType.TYPEWRITER_RIBBON
            draw.text((40, 24), recipe.title.upper(), fill=(*ink_color, 255), font=get_font("typewriter", size=17))
            y = 66
            y = render_typewritten_lines(
                draw, [f"• {ing}" for ing in recipe.ingredients[:5]], 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
            )
            y += 10
            render_typewritten_lines(
                draw, recipe.directions[:4], 40, y, self.card_width, self.card_height, ink_color, 12, self.rng
            )
        else:  # PRINTED_CLIPPING
            img = render_clipping_block(img, recipe.title, recipe.ingredients, recipe.directions, self.rng)
            ink_type = InkType.PRINTED_TYPESET

        artifacts: List[Dict[str, Any]] = []
        if deg.oil_smudges > 0:
            img = apply_oil_smudge(img, (self.card_width - 150, 20, self.card_width - 30, 120), self.rng)
            artifacts.append({"artifact_type": "OIL_GREASE_STAIN", "quadrant": "TOP_RIGHT", "severity": "MILD"})
        if deg.coffee_rings > 0:
            img = apply_coffee_ring(img, 120, self.card_height - 110, 50, self.rng)
            artifacts.append({"artifact_type": "COFFEE_TEA_RING", "quadrant": "BOTTOM_LEFT", "severity": "MODERATE"})
        if deg.dog_ears:
            img = apply_dog_ear(img, "BOTTOM_RIGHT", size=35)
            artifacts.append({"artifact_type": "CORNER_DOG_EAR_FOLD", "quadrant": "BOTTOM_RIGHT", "severity": "MILD"})
        if deg.edge_tears:
            img = apply_edge_tear(img, "RIGHT", self.rng)
            artifacts.append({"artifact_type": "TEAR_EDGE", "quadrant": "EDGE_RIGHT", "severity": "MILD"})
        if deg.tape_residue:
            img = apply_tape_residue(img, corner="TOP_LEFT", rng=self.rng)
            artifacts.append({"artifact_type": "TAPE_RESIDUE_YELLOWED", "quadrant": "TOP_LEFT", "severity": "MILD"})
        if deg.stamps:
            stamp_txt = f"RECIPE CLUB {self.rng.randint(1952, 1974)}"
            img = apply_date_stamp(img, 40, self.card_height - 65, stamp_txt, self.rng)
            artifacts.append({
                "artifact_type": "RUBBER_DATE_STAMP",
                "quadrant": "BOTTOM_LEFT",
                "severity": "MILD",
                "description": f"Vintage ink stamp: {stamp_txt}",
            })
        if deg.ink_fade > 0:
            img = apply_ink_fade(img, deg.ink_fade, self.rng)
        if abs(deg.rotation_skew_deg) > 0.1:
            img = apply_rotation_skew(img, deg.rotation_skew_deg)

        recipe.card_ids = [cid]

        return GeneratedCard(
            image=img,
            card_id=cid,
            filename=cid,
            modality=modality,
            role=CardRole.STANDALONE,
            recipe_cluster={
                "recipe_id": rec_id,
                "sequence_position": 0,
                "cluster_role": "standalone",
                "total_cards": 1,
                "continuation_cue": None,
            },
            physical_characteristics={
                "paper_tone": substrate.value,
                "ruling_type": "STANDARD_RULED_BLUE" if substrate == PaperSubstrate.RULED_INDEX else "UNRULED_BLANK",
                "ink_type": ink_type.value,
                "artifacts": artifacts,
            },
            recipe=recipe,
        )

    def generate_multi_card_set(
        self,
        recipe: RecipeSchema,
        num_cards: int = 3,
        modality: CardModality = CardModality.HANDWRITTEN_CURSIVE,
        substrate: PaperSubstrate = PaperSubstrate.RULED_INDEX,
        degradation: Optional[DegradationConfig] = None,
    ) -> List[GeneratedCard]:
        """Generates a 3 or 4-card sequence (e.g. Card 1 Front, Card 1 Back, Card 2 Front) with continuous narrative flow."""
        deg = degradation or DegradationConfig.from_level(DegradationLevel.MILD, self.rng)
        rec_id = recipe.recipe_id or f"rec_{uuid.uuid4().hex[:8]}"

        cards: List[GeneratedCard] = []
        cluster_roles = ["card1_front", "card1_back", "card2_front", "card2_back"][:num_cards]
        card_roles = [
            CardRole.SEQUENCE_START if i == 0 else CardRole.SEQUENCE_CONTINUATION
            for i in range(num_cards)
        ]

        total_dirs = recipe.directions or ["1. Step 1", "2. Step 2", "3. Step 3", "4. Step 4", "5. Step 5", "6. Step 6"]
        chunk_size = max(1, math.ceil(len(total_dirs) / num_cards))

        all_cids = [f"{rec_id}_{role}.jpg" for role in cluster_roles]
        recipe.card_ids = all_cids

        for idx, (c_role, role) in enumerate(zip(cluster_roles, card_roles)):
            cid = all_cids[idx]
            img, _ = create_paper_substrate(self.card_width, self.card_height, substrate, self.rng)
            draw = ImageDraw.Draw(img, "RGBA")

            if modality == CardModality.TYPEWRITTEN:
                ink_color = (25, 25, 25)
                ink_type = InkType.TYPEWRITER_RIBBON.value
            elif modality in (CardModality.PRINTED_CLIPPING, CardModality.MIXED_MEDIA):
                ink_color = (20, 20, 20)
                ink_type = InkType.PRINTED_TYPESET.value
            else:
                ink_color = (25, 35, 110)
                ink_type = InkType.FOUNTAIN_PEN.value

            dirs_chunk = total_dirs[idx * chunk_size : (idx + 1) * chunk_size]

            if idx == 0:
                if modality == CardModality.TYPEWRITTEN:
                    draw.text((45, 24), recipe.title.upper(), fill=(*ink_color, 255), font=get_font("typewriter", size=18))
                    y = 66
                    draw.text((45, y), "--- INGREDIENTS ---", fill=(*ink_color, 240), font=get_font("typewriter", size=13))
                    y += 20
                    y = render_typewritten_lines(
                        draw, [f"- {ing}" for ing in recipe.ingredients[:6]], 45, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    if dirs_chunk and y < self.card_height - 85:
                        y += 10
                        draw.text((45, y), "--- PREPARATION (PART 1) ---", fill=(*ink_color, 240), font=get_font("typewriter", size=13))
                        y += 20
                        y = render_typewritten_lines(
                            draw, dirs_chunk, 45, y, self.card_width, self.card_height, ink_color, 12, self.rng
                        )
                    cue = "PTO TO CARD 1 BACK >>>"
                elif modality in (CardModality.PRINTED_CLIPPING, CardModality.MIXED_MEDIA):
                    draw.text((40, 22), recipe.title, fill=(*ink_color, 255), font=get_font("headline", size=19))
                    y = 66
                    draw.text((40, y), "INGREDIENTS:", fill=(*ink_color, 240), font=get_font("headline", size=13))
                    y += 18
                    y = render_typewritten_lines(
                        draw, [f"• {ing}" for ing in recipe.ingredients[:6]], 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    if dirs_chunk and y < self.card_height - 85:
                        y += 10
                        draw.text((40, y), "DIRECTIONS (PART 1):", fill=(*ink_color, 240), font=get_font("headline", size=13))
                        y += 18
                        y = render_typewritten_lines(
                            draw, dirs_chunk, 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
                        )
                    cue = "(over to Card 1 Back ->)"
                else:  # HANDWRITTEN_CURSIVE
                    draw.text((40, 22), recipe.title, fill=(*ink_color, 255), font=get_font("headline", size=19))
                    y = 66
                    draw.text((40, y), "Ingredients:", fill=(*ink_color, 240), font=get_font("headline", size=13))
                    y += 18
                    y = render_handwritten_lines(
                        draw, recipe.ingredients[:6], 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    if dirs_chunk and y < self.card_height - 85:
                        y += 10
                        draw.text((40, y), "Directions (Part 1):", fill=(*ink_color, 240), font=get_font("headline", size=13))
                        y += 18
                        y = render_handwritten_lines(
                            draw, dirs_chunk, 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
                        )
                    cue = "(over to Card 1 Back ->)"
            elif idx == 1:
                if modality == CardModality.TYPEWRITTEN:
                    draw.text((45, 24), f"(CONTINUED - {recipe.title.upper()[:20]})", fill=(*ink_color, 240), font=get_font("typewriter", size=14))
                    y = 66
                    draw.text((45, y), "--- PREPARATION (PART 2) ---", fill=(*ink_color, 240), font=get_font("typewriter", size=13))
                    y += 20
                    render_typewritten_lines(
                        draw, dirs_chunk, 45, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    cue = "SEE CARD 2 FRONT >>>" if num_cards > 2 else None
                elif modality in (CardModality.PRINTED_CLIPPING, CardModality.MIXED_MEDIA):
                    draw.text((40, 22), f"(Continued - {recipe.title[:20]}...)", fill=(*ink_color, 230), font=get_font("print", size=15))
                    y = 66
                    draw.text((40, y), "Directions (Part 2):", fill=(*ink_color, 240), font=get_font("headline", size=13))
                    y += 20
                    render_typewritten_lines(
                        draw, dirs_chunk, 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    cue = "(See Card 2 Front ->)" if num_cards > 2 else None
                else:  # HANDWRITTEN_CURSIVE
                    draw.text((40, 22), f"(Continued - {recipe.title[:20]}...)", fill=(*ink_color, 230), font=get_font("cursive", size=15))
                    y = 66
                    draw.text((40, y), "Directions (Part 2):", fill=(*ink_color, 240), font=get_font("headline", size=13))
                    y += 20
                    render_handwritten_lines(
                        draw, dirs_chunk, 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    cue = "(See Card 2 Front ->)" if num_cards > 2 else None
            elif idx == 2:
                if modality == CardModality.TYPEWRITTEN:
                    draw.text((45, 24), f"CARD 2: {recipe.title.upper()} (PART 3)", fill=(*ink_color, 255), font=get_font("typewriter", size=16))
                    y = 66
                    draw.text((45, y), "--- PREPARATION (PART 3) ---", fill=(*ink_color, 240), font=get_font("typewriter", size=13))
                    y += 20
                    render_typewritten_lines(
                        draw, dirs_chunk, 45, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    cue = "PTO TO CARD 2 BACK >>>" if num_cards > 3 else None
                    if not cue:
                        notes_line = f"YIELD: {recipe.yield_servings or '8-10 SERVINGS'}  |  TIME: {recipe.total_time or '3 HRS'}"
                        draw.text((45, self.card_height - 38), notes_line, fill=(*ink_color, 220), font=get_font("typewriter", size=12))
                elif modality in (CardModality.PRINTED_CLIPPING, CardModality.MIXED_MEDIA):
                    draw.text((40, 22), f"Card 2: {recipe.title} (Part 3)", fill=(*ink_color, 255), font=get_font("headline", size=17))
                    y = 66
                    draw.text((40, y), "Directions (Part 3):", fill=(*ink_color, 240), font=get_font("headline", size=13))
                    y += 20
                    render_typewritten_lines(
                        draw, dirs_chunk, 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    cue = "(over to Card 2 Back ->)" if num_cards > 3 else None
                    if not cue:
                        draw.text((40, self.card_height - 40), f"Yield: {recipe.yield_servings or '8-10 servings'} | {recipe.total_time or '3 hrs'}", fill=(*ink_color, 220), font=get_font("print", size=13))
                else:  # HANDWRITTEN_CURSIVE
                    draw.text((40, 22), f"Card 2: {recipe.title} (Part 3)", fill=(*ink_color, 255), font=get_font("headline", size=17))
                    y = 66
                    draw.text((40, y), "Directions (Part 3 & Gravy):", fill=(*ink_color, 240), font=get_font("headline", size=13))
                    y += 20
                    render_handwritten_lines(
                        draw, dirs_chunk, 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    cue = "(over to Card 2 Back ->)" if num_cards > 3 else None
                    if not cue:
                        draw.text((40, self.card_height - 40), f"Yield: {recipe.yield_servings or '8-10 servings'} | {recipe.total_time or '3 hrs'}", fill=(*ink_color, 220), font=get_font("cursive", size=13))
            else:  # idx == 3
                if modality == CardModality.TYPEWRITTEN:
                    draw.text((45, 24), f"CARD 2 BACK: {recipe.title.upper()} (FINISHING)", fill=(*ink_color, 255), font=get_font("typewriter", size=16))
                    y = 66
                    draw.text((45, y), "--- FINISHING & CARVING ---", fill=(*ink_color, 240), font=get_font("typewriter", size=13))
                    y += 20
                    render_typewritten_lines(
                        draw, dirs_chunk, 45, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    draw.text((45, self.card_height - 38), f"YIELD: {recipe.yield_servings or '8-10 SERVINGS'}  |  TIME: {recipe.total_time or '3 HRS'}", fill=(*ink_color, 220), font=get_font("typewriter", size=12))
                    cue = None
                elif modality in (CardModality.PRINTED_CLIPPING, CardModality.MIXED_MEDIA):
                    draw.text((40, 22), f"Card 2 Back: {recipe.title} (Finishing)", fill=(*ink_color, 255), font=get_font("headline", size=17))
                    y = 66
                    draw.text((40, y), "Directions (Finishing):", fill=(*ink_color, 240), font=get_font("headline", size=13))
                    y += 20
                    render_typewritten_lines(
                        draw, dirs_chunk, 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    draw.text((40, self.card_height - 40), f"Yield: {recipe.yield_servings or '8-10 servings'} | {recipe.total_time or '3 hrs'}", fill=(*ink_color, 220), font=get_font("print", size=13))
                    cue = None
                else:  # HANDWRITTEN_CURSIVE
                    draw.text((40, 22), f"Card 2 Back: {recipe.title} (Finishing)", fill=(*ink_color, 255), font=get_font("headline", size=17))
                    y = 66
                    draw.text((40, y), "Directions (Finishing & Carving):", fill=(*ink_color, 240), font=get_font("headline", size=13))
                    y += 20
                    render_handwritten_lines(
                        draw, dirs_chunk, 40, y, self.card_width, self.card_height, ink_color, 13, self.rng
                    )
                    draw.text((40, self.card_height - 40), f"Yield: {recipe.yield_servings or '8-10 servings'} | {recipe.total_time or '3 hrs'}", fill=(*ink_color, 220), font=get_font("cursive", size=13))
                    cue = None

            if cue:
                cue_font = get_font("typewriter" if modality == CardModality.TYPEWRITTEN else "cursive", size=13)
                draw.text((self.card_width - 200, self.card_height - 30), cue, fill=(*ink_color, 255), font=cue_font)

            # Apply degradation artifacts to multi-card set
            card_artifacts = []
            if deg.ink_fade > 0:
                img = apply_ink_fade(img, deg.ink_fade, self.rng)
            if deg.oil_smudges > 0 and idx == 0:
                img = apply_oil_smudge(img, (self.card_width - 160, 20, self.card_width - 30, 130), self.rng, alpha=100)
                card_artifacts.append({"artifact_type": "OIL_GREASE_STAIN", "quadrant": "TOP_RIGHT", "severity": "MILD"})
            if deg.coffee_rings > 0 and idx == 1:
                img = apply_coffee_ring(img, int(self.card_width * 0.7), 120, 50, self.rng, alpha=120)
                card_artifacts.append({"artifact_type": "COFFEE_TEA_RING", "quadrant": "TOP_RIGHT", "severity": "MILD"})
            if deg.dog_ears and idx == 0:
                img = apply_dog_ear(img, "TOP_RIGHT", size=35)
                card_artifacts.append({"artifact_type": "CORNER_DOG_EAR_FOLD", "quadrant": "TOP_RIGHT", "severity": "MILD"})
            if deg.edge_tears and idx == num_cards - 1:
                img = apply_edge_tear(img, "RIGHT", self.rng)
                card_artifacts.append({"artifact_type": "TEAR_EDGE", "quadrant": "EDGE_RIGHT", "severity": "MILD"})
            if deg.tape_residue and idx == 0:
                img = apply_tape_residue(img, corner="TOP_LEFT", rng=self.rng)
                card_artifacts.append({"artifact_type": "TAPE_RESIDUE_YELLOWED", "quadrant": "TOP_LEFT", "severity": "MILD"})
            if abs(deg.rotation_skew_deg) > 0.1:
                img = apply_rotation_skew(img, deg.rotation_skew_deg)

            cards.append(GeneratedCard(
                image=img,
                card_id=cid,
                filename=cid,
                modality=modality,
                role=role,
                recipe_cluster={
                    "recipe_id": rec_id,
                    "sequence_position": idx,
                    "cluster_role": c_role,
                    "total_cards": num_cards,
                    "continuation_cue": cue,
                },
                physical_characteristics={
                    "paper_tone": substrate.value,
                    "ruling_type": "STANDARD_RULED_BLUE" if substrate == PaperSubstrate.RULED_INDEX else "UNRULED_BLANK",
                    "ink_type": ink_type,
                    "artifacts": card_artifacts,
                },
                recipe=recipe,
            ))

        return cards


# ============================================================================
# 6. RECIPE TEMPLATES & CORPUS BUILDER
# ============================================================================

ARCHIVAL_RECIPE_TEMPLATES: List[Dict[str, Any]] = [
    # Modality 1: Vintage Handwritten Cursive (1940s–1970s)
    {
        "recipe_id": "rec_vhc_apple_crisp_1948",
        "title": "Grandma's 1948 Old-Fashioned Apple Crisp",
        "description": "Vintage handwritten family recipe on aged yellowed index card.",
        "yield_servings": "6 servings",
        "prep_time": "20 mins",
        "cook_time": "35 mins",
        "total_time": "55 mins",
        "ingredients": [
            "5 medium baking apples, peeled & sliced",
            "1 tbsp lemon juice",
            "1/2 cup granulated sugar",
            "1 tsp ground cinnamon",
            "3/4 cup all-purpose flour",
            "1/2 cup rolled oats",
            "1/2 cup brown sugar, packed",
            "1/3 cup cold butter, cubed",
            "1/4 tsp salt",
        ],
        "directions": [
            "1. Preheat oven to 375°F and grease an 8-inch square dish.",
            "2. Toss apples with lemon juice, sugar, and cinnamon.",
            "3. Combine flour, oats, brown sugar, and salt in bowl.",
            "4. Cut in butter until mixture resembles coarse crumbs.",
            "5. Spread topping over apples and bake 35 mins until golden.",
            "6. Serve warm with fresh cream or vanilla ice cream.",
        ],
        "tags": ["Dessert", "Baking", "Vintage", "Fall"],
        "source_notes": "Handwritten 1948 Index Card",
        "dimension": "Vintage Handwritten Cursive (1940s–1970s)",
        "historical_date": "1948",
        "provenance": "Midwestern Family Heirloom Kitchen Tin",
        "archive_series": "Heritage Handwritten Series",
    },
    {
        "recipe_id": "rec_vhc_rhubarb_pie_1965",
        "title": "Aunt Clara's 1965 Rhubarb Custard Pie",
        "description": "Vintage handwritten fountain pen script on aged cream card.",
        "yield_servings": "8 slices",
        "prep_time": "25 mins",
        "cook_time": "50 mins",
        "total_time": "1 hr 15 mins",
        "ingredients": [
            "1 unbaked 9-inch pie shell",
            "3 cups fresh rhubarb, chopped into 1/2-inch pieces",
            "2 large eggs, beaten",
            "1 cup granulated sugar",
            "3 tbsp all-purpose flour",
            "1/4 tsp ground nutmeg",
            "1 tbsp melted butter",
        ],
        "directions": [
            "1. Preheat oven to 400°F.",
            "2. Arrange chopped rhubarb evenly in unbaked pie shell.",
            "3. Whisk eggs, sugar, flour, nutmeg, and melted butter until smooth.",
            "4. Pour custard mixture over rhubarb.",
            "5. Bake at 400°F for 15 minutes, then reduce heat to 350°F and bake 35 minutes longer until set.",
            "6. Cool completely on wire rack before serving.",
        ],
        "tags": ["Dessert", "Pie", "Rhubarb", "Vintage"],
        "source_notes": "Handwritten 1965 Index Card (Blue Fountain Pen)",
        "dimension": "Vintage Handwritten Cursive (1940s–1970s)",
        "historical_date": "1965",
        "provenance": "Aunt Clara's Recipe Box (Cedar Rapids, IA)",
        "archive_series": "Heritage Handwritten Series",
    },
    {
        "recipe_id": "rec_vhc_strawberry_rhubarb_jam_1954",
        "title": "Eleanor's 1954 Summer Strawberry Rhubarb Jam",
        "description": "Vintage handwritten preserve recipe from 1954 county fair box.",
        "yield_servings": "6 half-pint jars",
        "prep_time": "20 mins",
        "cook_time": "25 mins",
        "total_time": "45 mins",
        "ingredients": [
            "4 cups fresh strawberries, hulled and crushed",
            "4 cups fresh rhubarb, finely chopped",
            "1 package (1.75 oz) powdered fruit pectin",
            "7 cups granulated sugar",
            "1/2 tsp unsalted butter (to reduce foaming)",
        ],
        "directions": [
            "1. Combine crushed strawberries, chopped rhubarb, and powdered pectin in large heavy bottom pot.",
            "2. Bring mixture to rolling boil over high heat, stirring constantly.",
            "3. Add sugar quickly and return to rolling boil for 1 full minute.",
            "4. Remove from heat, skim foam, ladle into sterilized jars leaving 1/4 inch headspace.",
            "5. Process jars in boiling water bath for 10 minutes.",
        ],
        "tags": ["Preserves", "Canning", "Vintage", "Summer"],
        "source_notes": "Eleanor Vance (1954) Iowa County Fair Recipe Box (Blue Fountain Pen Script)",
        "dimension": "Vintage Handwritten Cursive (1940s–1970s)",
        "historical_date": "1954",
        "provenance": "Eleanor Vance Iowa County Fair Collection",
        "archive_series": "Heritage Handwritten Series",
    },
    {
        "recipe_id": "rec_vhc_amish_cinnamon_bread_1973",
        "title": "Amish Friendship Cinnamon Streusel Bread (1973)",
        "description": "Traditional Amish friendship sourdough bread with cinnamon sugar streusel.",
        "yield_servings": "2 loaves (16 slices)",
        "prep_time": "25 mins",
        "cook_time": "55 mins",
        "total_time": "1 hr 20 mins",
        "ingredients": [
            "1 cup Amish friendship bread starter",
            "2/3 cup vegetable oil",
            "3 large eggs",
            "2 cups all-purpose flour",
            "1 cup granulated sugar",
            "1 1/2 tsp baking powder",
            "1/2 tsp baking soda",
            "1/2 tsp salt",
            "1 box (3.4 oz) vanilla instant pudding mix",
            "1 tbsp ground cinnamon",
            "1/2 cup brown sugar (for streusel topping)",
        ],
        "directions": [
            "1. Preheat oven to 325°F; grease and sugar two 8x4-inch loaf pans.",
            "2. In large bowl, combine starter, oil, eggs, flour, sugar, baking powder, baking soda, salt, and pudding mix.",
            "3. In small bowl, mix cinnamon and brown sugar for streusel.",
            "4. Pour half batter into pans, sprinkle half streusel, add remaining batter, and top with remaining streusel.",
            "5. Bake at 325°F for 50-55 minutes until toothpick inserted in center comes out clean.",
            "6. Cool in pans 10 minutes before turning out onto wire racks.",
        ],
        "tags": ["Baking", "Bread", "Amish", "Cinnamon", "Vintage"],
        "source_notes": "Handwritten 1973 Church Community Exchange Card",
        "dimension": "Vintage Handwritten Cursive (1940s–1970s)",
        "historical_date": "1973",
        "provenance": "Lancaster County Church Community Exchange",
        "archive_series": "Heritage Handwritten Series",
    },

    # Modality 2: Typewritten & Stamped (1950s–1970s)
    {
        "recipe_id": "rec_typ_beef_stroganoff_1956",
        "title": "1956 Classic Beef Stroganoff",
        "description": "Manual typewritten card from vintage community recipe club collection.",
        "yield_servings": "4 servings",
        "prep_time": "15 mins",
        "cook_time": "25 mins",
        "total_time": "40 mins",
        "ingredients": [
            "1 1/2 lbs beef sirloin, cut into thin strips",
            "1/2 cup butter",
            "1 medium yellow onion, thinly sliced",
            "1/2 lb fresh button mushrooms, sliced",
            "2 tbsp all-purpose flour",
            "1 1/2 cups beef broth",
            "1 tbsp tomato paste",
            "1 tsp Dijon mustard",
            "3/4 cup sour cream",
            "12 oz egg noodles, cooked and buttered",
        ],
        "directions": [
            "1. Melt 2 tbsp butter in heavy skillet over high heat.",
            "2. Sear beef strips quickly 2-3 mins until browned; remove.",
            "3. Sauté onions and mushrooms in remaining butter until tender.",
            "4. Stir in flour, then gradually whisk in beef broth.",
            "5. Simmer 5 mins until sauce thickens; blend in mustard and tomato paste.",
            "6. Reduce heat to low, fold in sour cream and returned beef.",
            "7. Heat gently without boiling; serve over hot buttered egg noodles.",
        ],
        "tags": ["Dinner", "Beef", "Vintage", "Classic"],
        "source_notes": "Typewritten Card (July 1956 Recipe Club Stamp)",
        "dimension": "Typewritten & Stamped (1950s–1970s)",
        "historical_date": "1956",
        "provenance": "Suburban Homemakers Recipe Club",
        "archive_series": "Mid-Century Typewritten Series",
    },
    {
        "recipe_id": "rec_typ_tomato_soup_cake_1952",
        "title": "1952 Tomato Soup Mystery Spice Cake",
        "description": "Post-war vintage pantry mystery cake recipe on typewritten card.",
        "yield_servings": "12 servings",
        "prep_time": "20 mins",
        "cook_time": "35 mins",
        "total_time": "55 mins",
        "ingredients": [
            "2 cups sifted all-purpose flour",
            "1 1/3 cups granulated sugar",
            "4 tsp baking powder",
            "1 tsp baking soda",
            "1 1/2 tsp ground cinnamon",
            "1/2 tsp ground cloves & nutmeg",
            "1 can (10 3/4 oz) condensed tomato soup",
            "1/2 cup shortening",
            "2 large eggs",
            "1/4 cup water",
            "1 cup raisins or chopped walnuts",
        ],
        "directions": [
            "1. Preheat oven to 350°F; grease and flour two 8-inch round cake pans.",
            "2. Sift flour, sugar, baking powder, baking soda, and spices into large bowl.",
            "3. Add tomato soup, shortening, eggs, and water; beat on medium speed 2 mins.",
            "4. Fold in raisins and walnuts.",
            "5. Pour into prepared pans and bake at 350°F for 30-35 mins.",
            "6. Cool and frost with cream cheese frosting.",
        ],
        "tags": ["Baking", "Cake", "Vintage", "Pantry"],
        "source_notes": "Typewritten Pantry Card (1952)",
        "dimension": "Typewritten & Stamped (1950s–1970s)",
        "historical_date": "1952",
        "provenance": "Post-War Community Kitchen Archive",
        "archive_series": "Mid-Century Typewritten Series",
    },
    {
        "recipe_id": "rec_typ_swedish_meatballs_1961",
        "title": "1961 Scandinavian Swedish Meatballs",
        "description": "Typewritten classic spiced meatballs in rich sour cream gravy.",
        "yield_servings": "6 servings (30 meatballs)",
        "prep_time": "30 mins",
        "cook_time": "25 mins",
        "total_time": "55 mins",
        "ingredients": [
            "1 lb ground chuck beef",
            "1/2 lb ground pork",
            "1/2 cup fine dry breadcrumbs",
            "1/2 cup heavy cream",
            "1 small onion, grated fine",
            "1 large egg",
            "1/4 tsp ground allspice & nutmeg",
            "1 tsp salt & 1/2 tsp white pepper",
            "3 tbsp butter for pan frying",
            "2 tbsp flour & 1 1/2 cups beef broth for cream gravy",
            "1/2 cup sour cream for finishing",
        ],
        "directions": [
            "1. Soak breadcrumbs in heavy cream in large bowl for 10 minutes.",
            "2. Add ground meats, grated onion, egg, allspice, nutmeg, salt, and white pepper; mix thoroughly.",
            "3. Shape into 1-inch uniform meatballs.",
            "4. Melt butter in heavy skillet; brown meatballs on all sides in batches, transfer to dish.",
            "5. Whisk flour into pan drippings, gradually add beef broth and simmer until thickened.",
            "6. Stir in sour cream, return meatballs to skillet, and simmer gently 10 minutes.",
            "7. Serve hot over buttered egg noodles with lingonberry jam.",
        ],
        "tags": ["Dinner", "Swedish", "Meatballs", "Classic", "Vintage"],
        "source_notes": "Typewritten Card (October 1961 Better Homes Homemakers Stamp)",
        "dimension": "Typewritten & Stamped (1950s–1970s)",
        "historical_date": "1961",
        "provenance": "Better Homes Homemakers Club Archive",
        "archive_series": "Mid-Century Typewritten Series",
    },
    {
        "recipe_id": "rec_typ_lemon_chiffon_pie_1958",
        "title": "1958 Mile-High Lemon Chiffon Pie",
        "description": "Airy chilled lemon gelatin meringue chiffon pie in flaky crust.",
        "yield_servings": "8 slices",
        "prep_time": "30 mins",
        "cook_time": "10 mins",
        "total_time": "3 hrs 40 mins",
        "ingredients": [
            "1 envelope unflavored gelatin",
            "1/4 cup cold water",
            "4 large eggs, separated",
            "1 cup granulated sugar, divided",
            "1/2 cup fresh lemon juice",
            "1 tbsp grated lemon zest",
            "1/2 tsp salt",
            "1 pre-baked 9-inch pastry crust",
            "1 cup whipped cream for topping",
        ],
        "directions": [
            "1. Soften gelatin in 1/4 cup cold water in small bowl.",
            "2. In top of double boiler, beat egg yolks with 1/2 cup sugar, lemon juice, zest, and salt.",
            "3. Cook over simmering water, stirring constantly until thick and custardy (about 8 mins).",
            "4. Remove from heat; stir in softened gelatin until completely dissolved; cool until mixture begins to congeal.",
            "5. Beat egg whites until stiff peaks form, gradually adding remaining 1/2 cup sugar.",
            "6. Fold meringue gently into cooled lemon gelatin mixture.",
            "7. Pile high into baked pastry shell and refrigerate at least 3 hours until set.",
        ],
        "tags": ["Dessert", "Pie", "Lemon", "Vintage", "Chiffon"],
        "source_notes": "Typewritten Ladies Auxiliary Bake Sale 1958 Archive",
        "dimension": "Typewritten & Stamped (1950s–1970s)",
        "historical_date": "1958",
        "provenance": "Ladies Auxiliary Bake Sale Archive",
        "archive_series": "Mid-Century Typewritten Series",
    },

    # Modality 3: Magazine Clippings & Package Labels (1960s–1980s)
    {
        "recipe_id": "rec_clp_crisco_fried_chicken",
        "title": "Crisco Golden Southern Fried Chicken",
        "description": "1960s magazine clipping pasted onto index card with visible glue borders.",
        "yield_servings": "4-6 servings",
        "prep_time": "20 mins",
        "cook_time": "30 mins",
        "total_time": "50 mins",
        "ingredients": [
            "1 whole chicken (3 lbs), cut into pieces",
            "1 cup buttermilk",
            "2 cups all-purpose flour",
            "2 tsp paprika",
            "1 tsp garlic powder",
            "1 1/2 tsp salt",
            "1 tsp black pepper",
            "Crisco shortening for deep skillet frying",
        ],
        "directions": [
            "1. Soak chicken pieces in buttermilk for at least 15 minutes.",
            "2. In paper bag or bowl, combine flour, paprika, garlic powder, salt, and pepper.",
            "3. Heat 1/2 inch Crisco in cast iron skillet to 365°F.",
            "4. Shake chicken in seasoned flour to coat thoroughly.",
            "5. Fry chicken pieces 12-15 mins per side until golden brown and crisp.",
            "6. Drain on brown paper bags before serving.",
        ],
        "tags": ["Dinner", "Chicken", "Southern", "Magazine Clipping"],
        "source_notes": "1962 Magazine Advertisement Cutout",
        "dimension": "Magazine Clippings & Package Labels (1960s–1980s)",
        "historical_date": "1962",
        "provenance": "Southern Living Magazine Clipping",
        "archive_series": "Period Clippings & Ephemera",
    },
    {
        "recipe_id": "rec_clp_condensed_milk_pie_1962",
        "title": "1962 Borden's Eagle Brand Magic Chocolate Pie",
        "description": "Vintage condensed milk can label package recipe clipping.",
        "yield_servings": "8 servings",
        "prep_time": "15 mins",
        "cook_time": "0 mins",
        "total_time": "3 hrs 15 mins",
        "ingredients": [
            "1 can (14 oz) Borden Eagle Brand Sweetened Condensed Milk",
            "1 1/2 cups semi-sweet chocolate morsels",
            "1/2 cup hot water",
            "1 tsp vanilla extract",
            "1 pre-baked 9-inch graham cracker crust",
            "1 cup heavy whipping cream, whipped",
        ],
        "directions": [
            "1. In top of double boiler, melt chocolate morsels with sweetened condensed milk, stirring until smooth.",
            "2. Gradually blend in hot water and vanilla extract.",
            "3. Remove from heat; cool to room temperature.",
            "4. Pour into prepared graham cracker crust.",
            "5. Chill in refrigerator at least 3 hours until set.",
            "6. Garnish with fresh whipped cream and chocolate curls.",
        ],
        "tags": ["Dessert", "Chocolate", "Pie", "Package Clipping"],
        "source_notes": "1962 Eagle Brand Can Label Clipping",
        "dimension": "Magazine Clippings & Package Labels (1960s–1980s)",
        "historical_date": "1962",
        "provenance": "Borden Eagle Brand Sweetened Condensed Milk Can Label",
        "archive_series": "Period Clippings & Ephemera",
    },
    {
        "recipe_id": "rec_clp_green_bean_casserole_1968",
        "title": "1968 Campbell's Classic Green Bean Casserole",
        "description": "Vintage holiday magazine clipping with mushroom soup and crispy fried onions.",
        "yield_servings": "6 servings",
        "prep_time": "10 mins",
        "cook_time": "30 mins",
        "total_time": "40 mins",
        "ingredients": [
            "1 can (10 1/2 oz) Campbell's Condensed Cream of Mushroom Soup",
            "1/2 cup whole milk",
            "1 tsp soy sauce",
            "1 dash black pepper",
            "4 cups cooked cut green beans (fresh or frozen)",
            "1 1/3 cups French's Crispy Fried Onions, divided",
        ],
        "directions": [
            "1. Preheat oven to 350°F.",
            "2. In 1 1/2-quart baking dish, mix mushroom soup, milk, soy sauce, black pepper, green beans, and 2/3 cup fried onions.",
            "3. Bake at 350°F for 25 minutes until hot and bubbling.",
            "4. Stir casserole gently; scatter remaining 2/3 cup crispy fried onions over top.",
            "5. Bake 5 minutes longer until golden brown.",
        ],
        "tags": ["Casserole", "Holiday", "Vegetables", "Vintage Clipping"],
        "source_notes": "1968 Holiday Magazine Advertisement Clipping with Cellophane Tape",
        "dimension": "Magazine Clippings & Package Labels (1960s–1980s)",
        "historical_date": "1968",
        "provenance": "Campbell's Soup Holiday Magazine Advertisement",
        "archive_series": "Period Clippings & Ephemera",
    },
    {
        "recipe_id": "rec_clp_toll_house_cookies_1975",
        "title": "1975 Original Toll House Chocolate Chip Cookies",
        "description": "Original morsels bag recipe clipping for classic chocolate chip cookies.",
        "yield_servings": "60 cookies (5 dozen)",
        "prep_time": "15 mins",
        "cook_time": "12 mins",
        "total_time": "27 mins",
        "ingredients": [
            "2 1/4 cups all-purpose flour",
            "1 tsp baking soda",
            "1 tsp salt",
            "1 cup (2 sticks) butter, softened",
            "3/4 cup granulated sugar",
            "3/4 cup packed brown sugar",
            "1 tsp vanilla extract",
            "2 large eggs",
            "2 cups (12 oz package) Nestlé Toll House Semi-Sweet Morsels",
            "1 cup chopped walnuts (optional)",
        ],
        "directions": [
            "1. Preheat oven to 375°F.",
            "2. Combine flour, baking soda, and salt in small bowl.",
            "3. Beat softened butter, granulated sugar, brown sugar, and vanilla extract in large bowl until creamy.",
            "4. Add eggs one at a time, beating well after each addition.",
            "5. Gradually beat in flour mixture; stir in chocolate morsels and nuts.",
            "6. Drop by rounded tablespoon onto ungreased baking sheets.",
            "7. Bake at 375°F for 9 to 11 minutes until golden brown; cool on wire racks.",
        ],
        "tags": ["Cookies", "Chocolate", "Baking", "Package Clipping"],
        "source_notes": "1975 Semi-Sweet Morsels Yellow Package Bag Cutout",
        "dimension": "Magazine Clippings & Package Labels (1960s–1980s)",
        "historical_date": "1975",
        "provenance": "Nestlé Toll House Semi-Sweet Morsels Package Back",
        "archive_series": "Period Clippings & Ephemera",
    },

    # Modality 4: Stained / Damaged / Edge-Worn Substrates
    {
        "recipe_id": "rec_dmg_moms_lasagna_1971",
        "title": "Mom's Sunday Homemade Lasagna",
        "description": "Heavily stained recipe card with oil spots and coffee ring bleed-through.",
        "yield_servings": "8 servings",
        "prep_time": "30 mins",
        "cook_time": "50 mins",
        "total_time": "1 hr 20 mins",
        "ingredients": [
            "1 lb ground Italian sweet sausage",
            "1 lb lean ground beef",
            "1 large onion, minced",
            "3 cloves garlic, minced",
            "2 cans (28 oz each) crushed tomatoes",
            "2 tbsp tomato paste",
            "1 tbsp dried oregano & basil",
            "15 oz whole milk ricotta cheese",
            "1 large egg, beaten",
            "16 oz mozzarella cheese, shredded",
            "1 cup freshly grated parmesan",
            "12 lasagna noodles, boiled al dente",
        ],
        "directions": [
            "1. Brown sausage and ground beef with onion and garlic; drain excess fat.",
            "2. Stir in crushed tomatoes, tomato paste, oregano, and basil; simmer 30 mins.",
            "3. Combine ricotta, beaten egg, and 1/2 cup parmesan in bowl.",
            "4. Spread 1 cup meat sauce in bottom of 9x13 inch baking pan.",
            "5. Layer 4 noodles, 1/3 ricotta mixture, mozzarella, and meat sauce.",
            "6. Repeat layers twice, finishing with mozzarella and parmesan.",
            "7. Cover loosely with foil; bake at 375°F for 35 mins.",
            "8. Remove foil and bake 15 mins more until bubbling and golden.",
        ],
        "tags": ["Dinner", "Italian", "Pasta", "Family Favorite"],
        "source_notes": "Stained & Dog-Eared 1971 Ruled Card",
        "dimension": "Stained / Damaged / Edge-Worn Substrates",
        "historical_date": "1971",
        "provenance": "Family Kitchen Recipe Box with Sauce Smudges",
        "archive_series": "Working Kitchen Artifacts",
    },
    {
        "recipe_id": "rec_dmg_butter_shortbread",
        "title": "Traditional Scottish Butter Shortbread",
        "description": "Edge-worn, butter-stained handwritten card from 1958.",
        "yield_servings": "16 triangles",
        "prep_time": "15 mins",
        "cook_time": "30 mins",
        "total_time": "45 mins",
        "ingredients": [
            "1 cup (2 sticks) salted butter, softened",
            "1/2 cup superfine caster sugar",
            "2 cups all-purpose flour",
            "1/4 cup cornstarch",
            "1/4 tsp pure almond extract",
            "Granulated sugar for sprinkling",
        ],
        "directions": [
            "1. Preheat oven to 325°F (165°C).",
            "2. Cream softened butter and caster sugar together until pale and fluffy.",
            "3. Sift flour and cornstarch together; fold into butter mixture with almond extract until dough comes together.",
            "4. Press dough evenly into ungreased 9-inch round shortbread pan or cake tin.",
            "5. Prick surface all over with tines of a fork in decorative pattern.",
            "6. Bake at 325°F for 30-35 minutes until pale golden.",
            "7. Cut into 16 wedges while still warm; sprinkle lightly with sugar.",
        ],
        "tags": ["Baking", "Cookies", "Scottish", "Damaged Card"],
        "source_notes": "1958 Butter-Stained Index Card with Edge Tear",
        "dimension": "Stained / Damaged / Edge-Worn Substrates",
        "historical_date": "1958",
        "provenance": "Scottish Immigrant Family Heirloom Card",
        "archive_series": "Working Kitchen Artifacts",
    },
    {
        "recipe_id": "rec_dmg_buttermilk_pancakes_1969",
        "title": "Sunday Morning Sourdough Buttermilk Pancakes (1969)",
        "description": "Butter and syrup stained index card for fluffy heirloom buttermilk pancakes.",
        "yield_servings": "16 pancakes",
        "prep_time": "15 mins",
        "cook_time": "15 mins",
        "total_time": "30 mins",
        "ingredients": [
            "2 cups all-purpose flour",
            "2 tbsp granulated sugar",
            "2 tsp baking powder",
            "1 tsp baking soda",
            "1/2 tsp salt",
            "2 cups thick buttermilk",
            "2 large eggs",
            "1/4 cup unsalted butter, melted & cooled",
            "1 tsp pure vanilla extract",
            "Warm pure maple syrup for serving",
        ],
        "directions": [
            "1. In large bowl, whisk flour, sugar, baking powder, baking soda, and salt.",
            "2. In separate bowl, beat buttermilk, eggs, melted butter, and vanilla.",
            "3. Pour wet ingredients into dry; whisk gently just until combined (batter should remain lumpy).",
            "4. Heat cast-iron griddle over medium heat and grease lightly with butter.",
            "5. Pour 1/4 cup batter per pancake onto hot griddle.",
            "6. Cook until bubbles pop on surface and edges look set (2-3 mins); flip and cook 1-2 mins longer until golden.",
            "7. Serve warm immediately with whipped butter and maple syrup.",
        ],
        "tags": ["Breakfast", "Pancakes", "Vintage", "Damaged Card"],
        "source_notes": "1969 Butter-Stained Sunday Pancake Card with Torn Edge",
        "dimension": "Stained / Damaged / Edge-Worn Substrates",
        "historical_date": "1969",
        "provenance": "Sunday Breakfast Collection with Syrup & Butter Smudges",
        "archive_series": "Working Kitchen Artifacts",
    },
    {
        "recipe_id": "rec_dmg_beef_bourguignon_1974",
        "title": "Heirloom French Beef Bourguignon (1974)",
        "description": "Dog-eared, wine-splattered recipe card for slow-braised beef in Burgundy wine.",
        "yield_servings": "6 servings",
        "prep_time": "35 mins",
        "cook_time": "2 hrs 30 mins",
        "total_time": "3 hrs 05 mins",
        "ingredients": [
            "3 lbs beef chuck roast, cut into 2-inch chunks",
            "6 oz thick-cut bacon, diced",
            "1 large yellow onion, sliced",
            "2 large carrots, sliced into 1/2-inch rounds",
            "3 cloves garlic, minced",
            "1 bottle (750 ml) dry red Burgundy wine",
            "2 cups beef stock",
            "2 tbsp tomato paste",
            "1 bouquet garni (thyme, parsley, bay leaf)",
            "1 lb fresh button mushrooms, quartered & sautéed in butter",
            "18 small pearl onions, peeled",
        ],
        "directions": [
            "1. Sauté diced bacon in large Dutch oven over medium heat until crisp; remove with slotted spoon.",
            "2. Dry beef chunks thoroughly; sear in hot bacon fat in batches until deeply browned on all sides; remove.",
            "3. Sauté sliced onion and carrots in pot 5 mins; stir in minced garlic and tomato paste for 1 min.",
            "4. Return beef and bacon to pot; pour in red wine and beef stock to cover; add bouquet garni.",
            "5. Cover tightly and simmer over low heat (or in 325°F oven) for 2 to 2 1/2 hours until beef is fork-tender.",
            "6. Fold in sautéed mushrooms and glazed pearl onions; simmer 15 minutes longer until sauce is glossy.",
        ],
        "tags": ["Dinner", "French", "Beef", "Stew", "Classic"],
        "source_notes": "1974 Dog-Eared French Cookery Card with Coffee Ring Stain",
        "dimension": "Stained / Damaged / Edge-Worn Substrates",
        "historical_date": "1974",
        "provenance": "French Cooking Class Recipe Card with Wine & Coffee Ring Stains",
        "archive_series": "Working Kitchen Artifacts",
    },

    # Modality 5: Multi-Card Narrative Sets
    {
        "recipe_id": "rec_mcs_turkey_stuffing_feast",
        "title": "Grandma's Thanksgiving Roast Turkey & Sage Stuffing",
        "description": "3-card multi-part archival holiday feast preparation.",
        "yield_servings": "10-12 servings",
        "prep_time": "45 mins",
        "cook_time": "3 hrs 30 mins",
        "total_time": "4 hrs 15 mins",
        "ingredients": [
            "1 whole turkey (12-14 lbs), thawed and giblets removed",
            "1/2 cup unsalted butter, softened",
            "1 tbsp fresh rosemary, minced",
            "1 tbsp fresh thyme, minced",
            "1 loaf day-old white bread, cubed (8 cups)",
            "1 cup celery, diced",
            "1 large yellow onion, diced",
            "2 tbsp rubbed sage",
            "1 1/2 cups turkey or chicken broth",
            "1/4 cup pan drippings for gravy",
            "3 tbsp flour for gravy",
            "2 cups hot turkey stock for gravy",
        ],
        "directions": [
            "1. Rinse turkey and pat dry inside and out with paper towels.",
            "2. Sauté onion and celery in butter until tender; toss with bread cubes, sage, and broth.",
            "3. Stuff turkey cavity loosely with prepared bread stuffing.",
            "4. Rub softened butter, minced rosemary, and thyme under turkey skin and over breast.",
            "5. Truss legs with kitchen twine; place breast-side up on roasting rack in roasting pan.",
            "6. Roast at 325°F for 3 1/2 to 4 hours (basting every 30 mins) until thermometer reads 165°F in thigh.",
            "7. Transfer turkey to carving board; let rest 25 mins before carving.",
            "8. Pan Gravy: Pour pan drippings into skillet, whisk in flour over medium heat 2 mins, slowly whisk in hot stock until rich and smooth.",
        ],
        "tags": ["Holiday", "Turkey", "Thanksgiving", "Multi-Card"],
        "source_notes": "Multi-Card 3-Part Handwritten Holiday Set",
        "dimension": "Multi-Card Narrative Sets",
        "historical_date": "1950",
        "provenance": "Grandma's Thanksgiving Holiday Feast Card Set (3 Cards)",
        "archive_series": "Multi-Part Culinary Manuscripts",
    },
    {
        "recipe_id": "rec_mcs_wedding_fruitcake_1942",
        "title": "1942 Heirloom Dark Holiday Wedding Fruitcake",
        "description": "4-card comprehensive heirloom fruitcake preparation and aging procedure.",
        "yield_servings": "24 servings",
        "prep_time": "1 hr",
        "cook_time": "3 hrs",
        "total_time": "4 hrs",
        "ingredients": [
            "2 cups dark seedless raisins",
            "2 cups golden sultanas",
            "1 cup candied orange peel, diced",
            "1 cup glacé cherries, halved",
            "1/2 cup dark rum or brandy for maceration",
            "1 cup unsalted butter, softened",
            "1 cup dark muscovado sugar",
            "4 large eggs",
            "2 1/4 cups all-purpose flour",
            "1 tsp ground allspice & nutmeg",
            "1/2 cup dark molasses",
            "Cheesecloth and brandy for weekly curing",
        ],
        "directions": [
            "1. Combine raisins, sultanas, orange peel, and cherries in bowl with rum; soak overnight covered.",
            "2. Preheat oven to 300°F (150°C); double line 9-inch springform pan with buttered parchment paper.",
            "3. Cream softened butter with dark muscovado sugar until fluffy; beat in eggs one at a time.",
            "4. Sift flour with allspice and nutmeg; fold half into creamed mixture.",
            "5. Toss soaked fruits with remaining flour, then gently fold into batter with dark molasses.",
            "6. Spoon into prepared pan, smoothing top with wet spatula.",
            "7. Bake at 300°F for 2 1/2 to 3 hours until skewer inserted in center comes out clean.",
            "8. Cool cake in tin for 24 hours. Wrap in brandy-soaked cheesecloth and foil; store in cool tin for 6 weeks.",
        ],
        "tags": ["Baking", "Fruitcake", "Heirloom", "Multi-Card"],
        "source_notes": "4-Card Heirloom 1942 Handwritten Wedding Recipe",
        "dimension": "Multi-Card Narrative Sets",
        "historical_date": "1942",
        "provenance": "Heirloom Dark Wedding Fruitcake 4-Card Set",
        "archive_series": "Multi-Part Culinary Manuscripts",
    },
    {
        "recipe_id": "rec_mcs_sunday_pot_roast_gravy",
        "title": "Grandma's All-Day Sunday Pot Roast & Root Vegetables",
        "description": "3-card continuous heirloom recipe for slow-braised chuck roast with rich pan gravy.",
        "yield_servings": "8 servings",
        "prep_time": "30 mins",
        "cook_time": "3 hrs 30 mins",
        "total_time": "4 hrs",
        "ingredients": [
            "4 lbs boneless beef chuck roast, tied",
            "2 tbsp vegetable oil",
            "2 tsp coarse kosher salt & 1 tsp freshly cracked black pepper",
            "2 large yellow onions, quartered",
            "4 cloves garlic, smashed",
            "2 cups rich beef bone broth",
            "1 cup dry red wine",
            "2 tbsp Worcestershire sauce",
            "1 lb small red potatoes, halved",
            "1 lb whole carrots, peeled and cut into 2-inch lengths",
            "3 stalks celery, cut into chunks",
            "3 tbsp all-purpose flour for gravy reduction",
        ],
        "directions": [
            "1. Preheat oven to 300°F (150°C).",
            "2. Season chuck roast aggressively on all surfaces with coarse salt and black pepper.",
            "3. Heat oil in heavy cast-iron Dutch oven over high heat; sear roast 4-5 mins per side until deep mahogany crust develops.",
            "4. Remove beef; add quartered onions and smashed garlic to pot, cooking 3 mins until fragrant.",
            "5. Pour in red wine, scraping up browned fond from bottom of pot; add beef broth and Worcestershire sauce.",
            "6. Return roast to pot, cover with tight-fitting lid, and braise at 300°F for 2 hours.",
            "7. Scatter red potatoes, carrots, and celery around roast in pot, spooning braising juices over vegetables.",
            "8. Cover and return to oven for 1 to 1 1/2 hours longer until beef is melt-in-the-mouth tender.",
            "9. Transfer beef and vegetables to large warm serving platter; tent loosely with foil.",
            "10. Skim fat from pot juices; bring to a simmer over medium heat, whisk in flour slurry, and reduce 5 mins until rich, velvety brown gravy forms.",
        ],
        "tags": ["Dinner", "Beef", "Roast", "Heirloom", "Multi-Card"],
        "source_notes": "Multi-Card 3-Part Heirloom Pot Roast Manuscript (Grandma's 1958 Cedar Box)",
        "dimension": "Multi-Card Narrative Sets",
        "historical_date": "1958",
        "provenance": "Grandma's Cedar Box Pot Roast 3-Card Manuscript",
        "archive_series": "Multi-Part Culinary Manuscripts",
    },
    {
        "recipe_id": "rec_mcs_traditional_panettone_1955",
        "title": "1955 Artisanal Milanese Holiday Panettone",
        "description": "4-card traditional sourdough panettone recipe with multi-stage doughs and fruit curing.",
        "yield_servings": "2 large panettoni (20 servings)",
        "prep_time": "2 hrs",
        "cook_time": "45 mins",
        "total_time": "18 hrs",
        "ingredients": [
            "1 cup active sourdough starter (pasta madre)",
            "4 1/2 cups Manitoba / high-protein bread flour",
            "1 cup whole milk, lukewarm",
            "1 cup unsalted European butter, softened",
            "1 cup granulated sugar",
            "6 large egg yolks",
            "1 vanilla bean, split and scraped",
            "1 tsp fine sea salt",
            "1 1/2 cups golden raisins, rum-soaked",
            "1 cup candied orange and citron peel, diced",
            "1 tbsp honey and 1 orange zest",
            "Melted butter for scoring cross",
        ],
        "directions": [
            "1. Part 1 (Primo Impasto): Dissolve sourdough starter in lukewarm milk with 1 cup flour; proof 3 hours at 80°F until tripled.",
            "2. Beat 3 egg yolks with 1/2 cup sugar; knead into proofed starter with 2 cups flour and 1/2 cup softened butter until smooth and elastic.",
            "3. Cover dough tightly and let rise overnight in warm draft-free place (12 hours) until quadrupled in volume.",
            "4. Part 2 (Secondo Impasto): Deflate first dough; knead in honey, orange zest, scraped vanilla bean, remaining 3 egg yolks, 1/2 cup sugar, and salt.",
            "5. Gradually work in remaining 1 1/2 cups flour and remaining 1/2 cup softened butter until shiny dough forms windowpane.",
            "6. Drain rum-soaked raisins and toss with candied citron; gently fold fruits evenly throughout dough.",
            "7. Divide dough into 2 equal portions; shape into tight boules and place into tall 6-inch panettone paper molds.",
            "8. Proof in warm humid area 4-6 hours until dough reaches 1/2 inch below mold rim.",
            "9. Cut shallow cross in top of each dome with sharp razor; place pat of butter in center of cross.",
            "10. Bake at 350°F (175°C) for 40-45 minutes until deep mahogany and internal temp reaches 190°F.",
            "11. Part 3 (Curing & Hanging): Pierce bottom of each panettone with two long metal skewers; hang upside down suspended between chairs for 10 hours to prevent collapse.",
        ],
        "tags": ["Baking", "Panettone", "Holiday", "Italian", "Multi-Card", "Heirloom"],
        "source_notes": "4-Card Heirloom Milanese Holiday Panettone Archival Set (1955)",
        "dimension": "Multi-Card Narrative Sets",
        "historical_date": "1955",
        "provenance": "Milanese Bakery Family Heirloom 4-Card Manuscript",
        "archive_series": "Multi-Part Culinary Manuscripts",
    },

    # Modality 6: Standalone Single-Sided Cards
    {
        "recipe_id": "rec_sta_quick_chocolate_fudge",
        "title": "10-Minute Quick Chocolate Fudge",
        "description": "Standalone single-sided vintage chocolate candy recipe.",
        "yield_servings": "36 squares",
        "prep_time": "10 mins",
        "cook_time": "5 mins",
        "total_time": "2 hrs 15 mins",
        "ingredients": [
            "3 cups semi-sweet chocolate chips",
            "1 can (14 oz) sweetened condensed milk",
            "1 tsp pure vanilla extract",
            "Pinch of sea salt",
            "1/2 cup chopped walnuts (optional)",
        ],
        "directions": [
            "1. Line an 8x8 inch baking pan with aluminum foil and lightly butter.",
            "2. Combine chocolate chips and sweetened condensed milk in heavy saucepan over low heat.",
            "3. Stir constantly until chocolate is completely melted and smooth.",
            "4. Remove from heat; stir in vanilla, salt, and optional walnuts.",
            "5. Spread evenly into prepared pan; chill in refrigerator 2 hours until firm.",
        ],
        "tags": ["Candy", "Chocolate", "Quick", "Standalone"],
        "source_notes": "Standalone Index Card",
        "dimension": "Standalone Single-Sided Cards",
        "historical_date": "1960",
        "provenance": "Quick Confections Recipe Tin",
        "archive_series": "Quick Pantry Standalones",
    },
    {
        "recipe_id": "rec_sta_southern_pecan_pie",
        "title": "Southern Pecan Pie",
        "description": "Classic single-sided kraft substrate southern pie recipe.",
        "yield_servings": "8 servings",
        "prep_time": "15 mins",
        "cook_time": "60 mins",
        "total_time": "1 hr 15 mins",
        "ingredients": [
            "1 unbaked 9-inch deep-dish pie crust",
            "3 large eggs",
            "1 cup dark corn syrup",
            "1 cup granulated sugar",
            "2 tbsp unsalted butter, melted",
            "1 tsp pure vanilla extract",
            "1 1/2 cups pecan halves",
        ],
        "directions": [
            "1. Preheat oven to 350°F.",
            "2. In mixing bowl, beat eggs, dark corn syrup, sugar, melted butter, and vanilla until smooth.",
            "3. Fold in pecan halves and pour filling into unbaked pie crust.",
            "4. Bake at 350°F for 55-60 minutes until center is slightly set with gentle jiggle.",
            "5. Cool completely on wire rack 2 hours before slicing.",
        ],
        "tags": ["Dessert", "Pie", "Southern", "Standalone"],
        "source_notes": "Standalone Kraft Paper Card",
        "dimension": "Standalone Single-Sided Cards",
        "historical_date": "1965",
        "provenance": "Georgia Country Kitchen Kraft Paper Card",
        "archive_series": "Quick Pantry Standalones",
    },
    {
        "recipe_id": "rec_sta_classic_banana_bread",
        "title": "Classic Moist Walnut Banana Bread",
        "description": "Standalone single-card quick bread recipe written on grid graph paper.",
        "yield_servings": "1 loaf (10 slices)",
        "prep_time": "15 mins",
        "cook_time": "60 mins",
        "total_time": "1 hr 15 mins",
        "ingredients": [
            "3 very ripe bananas, mashed (about 1 1/2 cups)",
            "1/3 cup melted unsalted butter",
            "3/4 cup granulated sugar",
            "1 large egg, beaten",
            "1 tsp pure vanilla extract",
            "1 tsp baking soda",
            "Pinch of salt",
            "1 1/2 cups all-purpose flour",
            "1/2 cup toasted walnuts, chopped",
        ],
        "directions": [
            "1. Preheat oven to 350°F; butter an 8x4-inch loaf pan.",
            "2. In large bowl, mash ripe bananas with a fork until smooth; stir in melted butter.",
            "3. Mix in sugar, beaten egg, and vanilla extract.",
            "4. Sprinkle baking soda and salt over mixture; fold in flour until just combined.",
            "5. Fold in chopped walnuts; pour batter into prepared pan.",
            "6. Bake at 350°F for 55-60 minutes until golden brown and cake tester comes out clean.",
        ],
        "tags": ["Baking", "Bread", "Banana", "Quick", "Standalone"],
        "source_notes": "Single-Sided Grid Graph Recipe Card (1964)",
        "dimension": "Standalone Single-Sided Cards",
        "historical_date": "1964",
        "provenance": "Midwestern School Bake Sale Grid Paper Card",
        "archive_series": "Quick Pantry Standalones",
    },
    {
        "recipe_id": "rec_sta_old_fashioned_lemonade",
        "title": "Old-Fashioned Country Fair Lemonade",
        "description": "Standalone kraft paper card with classic boiled simple syrup lemonade recipe.",
        "yield_servings": "8 glasses (2 quarts)",
        "prep_time": "15 mins",
        "cook_time": "5 mins",
        "total_time": "1 hr 20 mins",
        "ingredients": [
            "1 1/2 cups freshly squeezed lemon juice (from 6-8 large lemons)",
            "1 1/4 cups granulated sugar",
            "1 cup boiling water (for simple syrup)",
            "5 cups ice-cold filtered water",
            "Lemon wheels and fresh mint sprigs for garnish",
            "Plenty of ice cubes",
        ],
        "directions": [
            "1. In heatproof pitcher, stir sugar into 1 cup boiling water until completely dissolved to create simple syrup.",
            "2. Strain fresh lemon juice to remove seeds and pulp; add to simple syrup in pitcher.",
            "3. Stir in 5 cups cold filtered water; chill in refrigerator at least 1 hour.",
            "4. Serve over ice with lemon slices and mint.",
        ],
        "tags": ["Beverage", "Summer", "Lemonade", "Classic", "Standalone"],
        "source_notes": "Standalone Kraft Paper Country Fair Refreshment Card",
        "dimension": "Standalone Single-Sided Cards",
        "historical_date": "1955",
        "provenance": "County Fair Refreshment Stand Kraft Card",
        "archive_series": "Quick Pantry Standalones",
    },
]


def generate_synthetic_corpus(
    output_dir: Union[str, Path],
    count: Optional[int] = None,
    modality: Optional[CardModality] = None,
    substrate: Optional[PaperSubstrate] = None,
    degradation_level: DegradationLevel = DegradationLevel.MODERATE,
    seed: int = 42,
    generate_manifest: bool = True,
    image_format: str = "JPEG",
) -> List[GeneratedCard]:
    """Generates a complete curated synthetic recipe card corpus and writes image scans and manifest to disk."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    generator = SyntheticCardGenerator(seed=seed)
    rng = random.Random(seed)

    all_cards: List[GeneratedCard] = []
    manifest_entries: List[Dict[str, Any]] = []

    templates = ARCHIVAL_RECIPE_TEMPLATES
    if count and count < len(templates):
        templates = templates[:count]

    for template_dict in templates:
        rec_data = dict(template_dict)
        dimension = rec_data.pop("dimension", "Vintage Culinary Archive")
        provenance = rec_data.pop("provenance", "Curated Archival Card")
        historical_date = rec_data.pop("historical_date", "")
        archive_series = rec_data.pop("archive_series", "Historical Recipe Corpus")

        rec = RecipeSchema(**rec_data)
        rec_id = rec.recipe_id

        # Determine modality and substrate
        m = modality or (
            CardModality.HANDWRITTEN_CURSIVE if (
                "vhc" in rec_id or "apple" in rec_id or "rhubarb" in rec_id or
                "turkey" in rec_id or "fruitcake" in rec_id or "jam" in rec_id or
                "amish" in rec_id or "pot_roast" in rec_id or "panettone" in rec_id or
                "banana" in rec_id or "fudge" in rec_id or "pecan" in rec_id
            )
            else CardModality.TYPEWRITTEN if ("typ" in rec_id or "lemonade" in rec_id)
            else CardModality.PRINTED_CLIPPING if "clp" in rec_id
            else CardModality.MIXED_MEDIA if "dmg" in rec_id
            else CardModality.HANDWRITTEN_CURSIVE
        )

        s = substrate or (
            PaperSubstrate.GRID_GRAPH if "banana" in rec_id
            else PaperSubstrate.KRAFT_BROWN if ("pecan" in rec_id or "lemonade" in rec_id)
            else PaperSubstrate.WHITE_LINEN if ("clp" in rec_id or "shortbread" in rec_id or "lemon_chiffon" in rec_id)
            else PaperSubstrate.AGED_YELLOW if ("typ" in rec_id or "fruitcake" in rec_id or "amish" in rec_id or "bourguignon" in rec_id or "panettone" in rec_id)
            else PaperSubstrate.RULED_INDEX if ("apple" in rec_id or "lasagna" in rec_id or "turkey" in rec_id or "rhubarb" in rec_id or "strawberry" in rec_id or "pancakes" in rec_id or "pot_roast" in rec_id or "fudge" in rec_id)
            else PaperSubstrate.RULED_INDEX
        )

        # Custom degradation per recipe
        deg = DegradationConfig.from_level(degradation_level, rng)
        if "swedish_meatballs" in rec_id or "lemon_chiffon" in rec_id or "beef_stroganoff" in rec_id:
            deg.stamps = True
        if "green_bean" in rec_id or "toll_house" in rec_id:
            deg.tape_residue = True
        if "buttermilk_pancakes" in rec_id:
            deg.oil_smudges = 2
            deg.edge_tears = True
            deg.bleed_through = True
        if "beef_bourguignon" in rec_id:
            deg.coffee_rings = 1
            deg.dog_ears = True
            deg.bleed_through = True
        if "moms_lasagna" in rec_id:
            deg.oil_smudges = 1
            deg.coffee_rings = 1
            deg.bleed_through = True
            deg.dog_ears = True

        # Generate according to cluster type
        if "fruitcake" in rec_id or "panettone" in rec_id:
            cards = generator.generate_multi_card_set(rec, num_cards=4, modality=m, substrate=s, degradation=deg)
        elif "mcs" in rec_id or "turkey" in rec_id or "pot_roast" in rec_id:
            cards = generator.generate_multi_card_set(rec, num_cards=3, modality=m, substrate=s, degradation=deg)
        elif "sta" in rec_id or "shortbread" in rec_id or "condensed_milk" in rec_id or "banana" in rec_id or "lemonade" in rec_id:
            cards = [generator.generate_standalone_card(rec, modality=m, substrate=s, degradation=deg)]
        else:
            cards = list(generator.generate_card_pair(rec, modality=m, substrate=s, degradation=deg))

        ext = ".jpg" if image_format.upper() == "JPEG" else ".png"

        for card in cards:
            file_name = f"{Path(card.filename).stem}{ext}"
            card.filename = file_name
            card.card_id = file_name
            img_file_path = out_path / file_name

            if image_format.upper() == "JPEG":
                card.image.save(img_file_path, "JPEG", quality=92)
            else:
                card.image.save(img_file_path, "PNG")

            all_cards.append(card)

            # Compute image sha256
            raw_bytes = img_file_path.read_bytes()
            sha256_hex = hashlib.sha256(raw_bytes).hexdigest()

            # Synchronize card_ids list on the ground truth recipe
            card.recipe.card_ids = [f"{Path(c).stem}{ext}" for c in card.recipe.card_ids]

            # Build Manifest Entry with full attribution and provenance details
            cluster_info = dict(card.recipe_cluster)
            cluster_info.update({
                "dimension": dimension,
                "provenance": provenance,
                "historical_date": historical_date,
                "archive_series": archive_series,
            })

            manifest_entries.append({
                "card_id": file_name,
                "filename": file_name,
                "sha256_hash": sha256_hex,
                "modality": card.modality.value,
                "role": card.role.value,
                "recipe_cluster": cluster_info,
                "physical_characteristics": card.physical_characteristics,
                "ground_truth_recipe": card.recipe.model_dump(),
            })

    if generate_manifest:
        manifest_data = {
            "version": "1.0.0",
            "dataset_name": "RecipeBoxer Curated Historical Recipe Card Corpus",
            "description": "Curated ground-truth benchmark dataset covering historical handwritten cursive, typewriter, magazine clipping, and multi-part recipe card scans.",
            "total_cards": len(manifest_entries),
            "total_recipes": len(set(e["recipe_cluster"]["recipe_id"] for e in manifest_entries)),
            "cards": manifest_entries,
        }
        manifest_path = out_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    return all_cards



# ============================================================================
# 7. CLI COMMAND-LINE INTERFACE
# ============================================================================

def create_parser() -> argparse.ArgumentParser:
    """Creates CLI argument parser for synthetic card generator."""
    parser = argparse.ArgumentParser(
        prog="recipeboxer-card-generator",
        description="Procedural synthetic recipe card generator for RecipeBoxer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        default="tests/fixtures/sample_cards",
        help="Output directory to save generated card scan images and manifest",
    )
    parser.add_argument(
        "-n",
        "--count",
        "--batch-size",
        type=int,
        default=None,
        help="Number of recipes/sets to generate (defaults to all templates)",
    )
    parser.add_argument(
        "-m",
        "--modality",
        type=lambda s: str(s).upper().strip().replace("-", "_"),
        choices=["HANDWRITTEN_CURSIVE", "TYPEWRITTEN", "PRINTED_CLIPPING", "MIXED_MEDIA", "ALL"],
        default="ALL",
        help="Card rendering modality filter",
    )
    parser.add_argument(
        "-s",
        "--substrate",
        type=lambda s: str(s).upper().strip().replace("-", "_"),
        choices=["AGED_YELLOW", "WHITE_LINEN", "RULED_INDEX", "GRID_GRAPH", "KRAFT_BROWN", "BLANK", "ALL"],
        default="ALL",
        help="Paper substrate type filter",
    )
    parser.add_argument(
        "-d",
        "--degradation",
        type=lambda s: str(s).upper().strip().replace("-", "_"),
        choices=["NONE", "MILD", "MODERATE", "SEVERE"],
        default="MODERATE",
        help="Physical degradation and artifact intensity",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Integer seed for deterministic reproduction",
    )
    parser.add_argument(
        "--format",
        type=lambda s: str(s).upper().strip(),
        choices=["JPEG", "PNG"],
        default="JPEG",
        help="Output image encoding format",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        default=False,
        help="Disable manifest.json generation",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for recipe card generator."""
    parser = create_parser()
    args = parser.parse_args(args=argv)

    mod = CardModality(args.modality) if args.modality != "ALL" else None
    sub = PaperSubstrate(args.substrate) if args.substrate != "ALL" else None
    deg = DegradationLevel(args.degradation)

    out_dir = Path(args.output_dir)
    print(f"Generating synthetic recipe card dataset in: {out_dir}")

    cards = generate_synthetic_corpus(
        output_dir=out_dir,
        count=args.count,
        modality=mod,
        substrate=sub,
        degradation_level=deg,
        seed=args.seed,
        generate_manifest=not args.no_manifest,
        image_format=args.format,
    )

    print(f"Successfully generated {len(cards)} card scans into {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
