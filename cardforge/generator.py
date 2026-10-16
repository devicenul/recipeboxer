"""Procedural synthetic recipe card generator.

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
import io
import json
import math
import os
import random
import secrets
import sys
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from cardforge.models import (
    CardModality,
    CardRole,
    CardStock,
    CardWriter,
    DegradationConfig,
    DegradationLevel,
    GeneratedCard,
    InkType,
    PaperSubstrate,
    RecipeSchema,
    RulingStyle,
)
from cardforge.templates_t0 import ARCHIVAL_RECIPE_TEMPLATES

_FONTS_DIR = Path(__file__).resolve().parent / "fonts"

try:
    from fontTools.ttLib import TTFont
except ImportError:
    TTFont = None


class MissingGlyphError(ValueError):
    """Raised when rendered text contains a character with no glyph in the font."""
    pass


# ============================================================================
# 2. FONT MANAGEMENT, GLYPH VALIDATION & FALLBACKS
# ============================================================================

_FONT_CACHE: Dict[Tuple[str, int], ImageFont.ImageFont] = {}
_CMAP_CACHE: Dict[str, Set[int]] = {}

FRACTION_TRANSLITERATION: Dict[str, str] = {
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}

SYMBOL_TRANSLITERATION: Dict[str, str] = {
    "–": "-",    # en dash
    "—": "--",   # em dash
    "“": '"',    # left double quote
    "”": '"',    # right double quote
    "‘": "'",    # left single quote
    "’": "'",    # right single quote
    "•": "-",    # bullet
    "×": "x",    # multiplication sign
    "°": " deg", # degree
}


def get_font_cmap(font_or_path: Union[ImageFont.ImageFont, str, Path]) -> Optional[Set[int]]:
    """Retrieves the Unicode character map (cmap) codepoints for a font, path, or style name."""
    p: Optional[str] = None
    if isinstance(font_or_path, Path):
        p = str(font_or_path)
    elif isinstance(font_or_path, str):
        if os.path.exists(font_or_path):
            p = font_or_path
        else:
            f = get_font(font_or_path)
            if hasattr(f, "path") and f.path:
                p = str(f.path)
    elif hasattr(font_or_path, "path") and font_or_path.path:
        p = str(font_or_path.path)

    if p and p in _CMAP_CACHE:
        return _CMAP_CACHE[p]

    if p and TTFont and os.path.exists(p):
        try:
            tt = TTFont(p)
            cmap = set(tt.getBestCmap().keys())
            _CMAP_CACHE[p] = cmap
            return cmap
        except Exception:
            pass
    return None


def check_font_missing_glyphs(
    text: str,
    font: ImageFont.ImageFont,
    font_style: str = "",
) -> List[str]:
    """Returns a list of characters in text lacking a glyph in font."""
    if not text:
        return []
    cmap = get_font_cmap(font)
    missing = []
    if cmap is not None:
        for c in text:
            if c in ("\n", "\r", "\t"):
                continue
            if ord(c) not in cmap:
                missing.append(c)
    else:
        # Raster fallback (fontTools unavailable): a char is missing if it renders blank OR renders
        # exactly like the font's .notdef glyph (some fonts, e.g. Caveat, draw a visible tofu box).
        notdef_sig = _notdef_signature(font)
        for c in text:
            if c in ("\n", "\r", "\t", " "):
                continue
            try:
                mask = font.getmask(c)
                if mask.getbbox() is None:
                    missing.append(c)
                elif notdef_sig is not None and (mask.size, bytes(mask)) == notdef_sig:
                    missing.append(c)
            except Exception:
                missing.append(c)
    return missing


# A Unicode noncharacter-adjacent private-use codepoint that no real font maps: renders as .notdef.
_NOTDEF_PROBE = "\U0010FFFD"


def _notdef_signature(font: ImageFont.ImageFont) -> Optional[Tuple[Tuple[int, int], bytes]]:
    """Returns (size, bytes) of the font's rendered .notdef glyph, or None if it renders blank."""
    try:
        mask = font.getmask(_NOTDEF_PROBE)
        if mask.getbbox() is None:
            return None
        return mask.size, bytes(mask)
    except Exception:
        return None


def validate_font_glyphs(
    text: str,
    font: ImageFont.ImageFont,
    font_style: str = "",
) -> None:
    """Cardforge self-check: fails generation if any char lacks a glyph in font (no silent tofu/blank)."""
    missing = check_font_missing_glyphs(text, font, font_style)
    if missing:
        unique_missing = list(dict.fromkeys(missing))
        desc = ", ".join(f"'{c}' (U+{ord(c):04X})" for c in unique_missing)
        font_name = font_style or getattr(font, "path", "default_font")
        raise MissingGlyphError(
            f"Font '{font_name}' lacks glyph(s) for {desc} in text: {text!r}"
        )


def adapt_text_for_font(
    text: str,
    font_style: str = "default",
    font: Optional[ImageFont.ImageFont] = None,
) -> str:
    """Adapts text for a specific font style so rendered text never silently drops characters.

    Applies consistent transliteration for characters unsupported by the font:
    - If a font lacks vulgar fractions (e.g. marck_script, special_elite), transliterates
      all vulgar fractions to consistent ASCII representations (e.g. '1/2', '1/3').
    - If a font lacks dashes, quotes, or special symbols, transliterates them to ASCII equivalents.
    """
    if not text:
        return text

    style_lower = font_style.lower().strip()
    f = font or get_font(font_style, size=16)
    cmap = get_font_cmap(f)

    # Determine whether font lacks fractions
    # marck_script lacks all fractions; special_elite lacks thirds/eighths.
    # To keep fraction rendering consistent on cards of that font, transliterate all fractions.
    needs_fraction_transliteration = False
    if style_lower in ("marck_script", "vintage_cursive", "script", "special_elite", "typewriter_distressed", "vintage_typewriter"):
        needs_fraction_transliteration = True
    elif cmap is not None:
        for char in FRACTION_TRANSLITERATION:
            if char in text and ord(char) not in cmap:
                needs_fraction_transliteration = True
                break

    result = []
    for c in text:
        if c in FRACTION_TRANSLITERATION and needs_fraction_transliteration:
            result.append(FRACTION_TRANSLITERATION[c])
        elif cmap is not None and ord(c) not in cmap and c not in ("\n", "\r", "\t"):
            if c in SYMBOL_TRANSLITERATION:
                result.append(SYMBOL_TRANSLITERATION[c])
            elif c in FRACTION_TRANSLITERATION:
                result.append(FRACTION_TRANSLITERATION[c])
            else:
                result.append(c)
        else:
            result.append(c)

    return "".join(result)


def draw_text_with_fallback(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    font_style: str,
    fill: Any,
    fallback_font: Optional[ImageFont.ImageFont] = None,
) -> None:
    """Renders text with validation and optional per-character fallback font."""
    missing = check_font_missing_glyphs(text, font, font_style)
    if not missing:
        draw.text(xy, text, fill=fill, font=font)
        return
    if fallback_font is not None:
        cmap = get_font_cmap(font) or set()
        cur_x, cur_y = xy
        for c in text:
            char_font = fallback_font if ord(c) not in cmap else font
            validate_font_glyphs(c, char_font, "fallback")
            draw.text((cur_x, cur_y), c, fill=fill, font=char_font)
            try:
                bbox = char_font.getbbox(c)
                cur_x += max(bbox[2] - bbox[0], 8)
            except Exception:
                cur_x += 10
        return
    validate_font_glyphs(text, font, font_style)


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
    style_lower = style.lower().strip()

    if style_lower in ("caveat", "caveat_cursive"):
        fpath = _FONTS_DIR / "caveat.ttf"
        if fpath.exists():
            candidates.append(str(fpath))
    elif style_lower in ("special_elite", "typewriter_distressed", "vintage_typewriter"):
        fpath = _FONTS_DIR / "special_elite.ttf"
        if fpath.exists():
            candidates.append(str(fpath))
    elif style_lower in ("marck_script", "vintage_cursive", "script"):
        fpath = _FONTS_DIR / "marck_script.ttf"
        if fpath.exists():
            candidates.append(str(fpath))
    elif style_lower in ("dejavu_serif_italic", "serif_italic"):
        candidates.append("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
    elif style_lower in ("dejavu_sans", "sans"):
        candidates.append("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    elif style_lower in ("dejavu_mono", "mono"):
        candidates.append("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    elif style == "cursive":
        candidates = [
            str(_FONTS_DIR / "caveat.ttf"),
            str(_FONTS_DIR / "marck_script.ttf"),
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
            str(_FONTS_DIR / "special_elite.ttf"),
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
    ruling: Optional[Union[RulingStyle, str]] = None,
    base_color_override: Optional[Tuple[int, int, int]] = None,
) -> Tuple[Image.Image, Tuple[int, int, int]]:
    """Creates a realistic textured paper canvas."""
    if base_color_override is not None:
        base_color = base_color_override
    elif substrate == PaperSubstrate.AGED_YELLOW:
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
    if ruling is not None:
        r_str = str(getattr(ruling, "value", ruling)).upper().strip()
        if r_str == "STANDARD_RULED":
            line_spacing = 26
            header_y = 60
            margin_x = 35
            draw.line([(margin_x, header_y), (width - margin_x, header_y)], fill=(215, 80, 80, 180), width=2)
            for y in range(header_y + line_spacing, height - 25, line_spacing):
                draw.line([(margin_x, y), (width - margin_x, y)], fill=(160, 190, 220, 140), width=1)
        elif r_str == "WIDE_RULED":
            line_spacing = 32
            header_y = 70
            margin_x = 35
            draw.line([(margin_x, header_y), (width - margin_x, header_y)], fill=(215, 80, 80, 180), width=2)
            for y in range(header_y + line_spacing, height - 25, line_spacing):
                draw.line([(margin_x, y), (width - margin_x, y)], fill=(160, 190, 220, 140), width=1)
        elif r_str == "NARROW_RULED":
            line_spacing = 20
            header_y = 50
            margin_x = 30
            draw.line([(margin_x, header_y), (width - margin_x, header_y)], fill=(215, 80, 80, 180), width=2)
            draw.line([(45, 15), (45, height - 15)], fill=(215, 80, 80, 140), width=1)
            for y in range(header_y + line_spacing, height - 20, line_spacing):
                draw.line([(margin_x, y), (width - margin_x, y)], fill=(160, 190, 220, 130), width=1)
        elif r_str == "BLUE_NO_HEADER":
            line_spacing = 24
            margin_x = 30
            for y in range(35, height - 25, line_spacing):
                draw.line([(margin_x, y), (width - margin_x, y)], fill=(160, 190, 220, 140), width=1)
        elif r_str == "DOUBLE_HEADER":
            line_spacing = 25
            margin_x = 35
            draw.line([(margin_x, 55), (width - margin_x, 55)], fill=(215, 80, 80, 180), width=2)
            draw.line([(margin_x, 65), (width - margin_x, 65)], fill=(215, 80, 80, 180), width=1)
            for y in range(90, height - 25, line_spacing):
                draw.line([(margin_x, y), (width - margin_x, y)], fill=(160, 190, 220, 140), width=1)
        elif r_str == "FAINT_RULED":
            line_spacing = 25
            header_y = 60
            margin_x = 35
            draw.line([(margin_x, header_y), (width - margin_x, header_y)], fill=(190, 110, 110, 100), width=1)
            for y in range(header_y + line_spacing, height - 25, line_spacing):
                draw.line([(margin_x, y), (width - margin_x, y)], fill=(180, 200, 220, 65), width=1)
        elif r_str == "GREEN_LEDGER":
            line_spacing = 24
            margin_x = 25
            draw.line([(160, 20), (160, height - 20)], fill=(130, 175, 140, 160), width=1)
            for y in range(45, height - 25, line_spacing):
                draw.line([(margin_x, y), (width - margin_x, y)], fill=(140, 180, 150, 120), width=1)
        elif r_str == "GRID_GRAPH":
            grid_spacing = 20
            grid_color = (180, 210, 230, 120)
            for x in range(20, width - 20, grid_spacing):
                draw.line([(x, 20), (x, height - 20)], fill=grid_color, width=1)
            for y in range(20, height - 20, grid_spacing):
                draw.line([(20, y), (width - 20, y)], fill=grid_color, width=1)
        elif r_str == "DOT_GRID":
            dot_spacing = 20
            dot_color = (170, 190, 210, 140)
            for x in range(20, width - 20, dot_spacing):
                for y in range(20, height - 20, dot_spacing):
                    draw.point((x, y), fill=dot_color)
        elif r_str == "UNRULED":
            pass
    elif substrate in (PaperSubstrate.RULED_INDEX, PaperSubstrate.AGED_YELLOW):
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
        validate_font_glyphs(line, font, font_style)
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
    font_style: str = "typewriter",
) -> int:
    """Renders text simulating a mechanical typewriter with character bounce and ribbon wear."""
    font = get_font(font_style, size=font_size)
    y = start_y
    line_height = int(font_size * 1.4)

    for line in lines:
        if y + line_height > max_height - 15:
            break
        validate_font_glyphs(line, font, font_style)
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


def find_perspective_coeffs(
    pa: List[Tuple[float, float]],
    pb: List[Tuple[float, float]],
) -> Tuple[float, ...]:
    """Pure-Python solver for 8 perspective transformation coefficients."""
    matrix = []
    b = []
    for (x, y), (u, v) in zip(pa, pb):
        matrix.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        b.append(float(u))
        matrix.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        b.append(float(v))

    n = 8
    for i in range(n):
        max_row = max(range(i, n), key=lambda r: abs(matrix[r][i]))
        matrix[i], matrix[max_row] = matrix[max_row], matrix[i]
        b[i], b[max_row] = b[max_row], b[i]

        pivot = matrix[i][i]
        if abs(pivot) < 1e-12:
            return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        for j in range(i, n):
            matrix[i][j] /= pivot
        b[i] /= pivot

        for r in range(n):
            if r != i and abs(matrix[r][i]) > 1e-12:
                factor = matrix[r][i]
                for c in range(i, n):
                    matrix[r][c] -= factor * matrix[i][c]
                b[r] -= factor * b[i]
    return tuple(b)


def apply_perspective_warp(
    image: Image.Image,
    magnitude: float = 0.025,
    rng: Optional[random.Random] = None,
    bg_color: Tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Applies subtle perspective quad warp simulating photographing at an angle."""
    if magnitude <= 0.001:
        return image
    r = rng or random.Random()
    w, h = image.size
    dx1 = r.uniform(-magnitude, magnitude) * w
    dy1 = r.uniform(-magnitude, magnitude) * h
    dx2 = r.uniform(-magnitude, magnitude) * w
    dy2 = r.uniform(-magnitude, magnitude) * h
    dx3 = r.uniform(-magnitude, magnitude) * w
    dy3 = r.uniform(-magnitude, magnitude) * h
    dx4 = r.uniform(-magnitude, magnitude) * w
    dy4 = r.uniform(-magnitude, magnitude) * h

    pts_src = [(0, 0), (w, 0), (w, h), (0, h)]
    pts_dst = [
        (max(0, dx1), max(0, dy1)),
        (min(w, w + dx2), max(0, dy2)),
        (min(w, w + dx3), min(h, h + dy3)),
        (max(0, dx4), min(h, h + dy4)),
    ]
    try:
        coeffs = find_perspective_coeffs(pts_dst, pts_src)
        return image.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=bg_color)
    except Exception:
        return image


def apply_crease(
    image: Image.Image,
    count: int = 1,
    orientation: str = "HORIZONTAL",
    rng: Optional[random.Random] = None,
) -> Image.Image:
    """Simulates realistic paper fold creases across the card."""
    if count <= 0:
        return image
    r = rng or random.Random()
    w, h = image.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for _ in range(count):
        if orientation == "HORIZONTAL" or (orientation == "RANDOM" and r.random() < 0.5):
            cy = r.randint(int(h * 0.25), int(h * 0.75))
            draw.line([(0, cy), (w, cy + r.randint(-2, 2))], fill=(80, 60, 40, 75), width=1)
            draw.line([(0, cy + 1), (w, cy + 1 + r.randint(-2, 2))], fill=(255, 255, 255, 80), width=1)
        elif orientation == "VERTICAL" or (orientation == "RANDOM" and r.random() < 0.75):
            cx = r.randint(int(w * 0.25), int(w * 0.75))
            draw.line([(cx, 0), (cx + r.randint(-2, 2), h)], fill=(80, 60, 40, 75), width=1)
            draw.line([(cx + 1, 0), (cx + 1 + r.randint(-2, 2), h)], fill=(255, 255, 255, 80), width=1)
        else:
            draw.line([(0, r.randint(0, h // 2)), (w, r.randint(h // 2, h))], fill=(80, 60, 40, 60), width=1)
            draw.line([(0, r.randint(0, h // 2) + 1), (w, r.randint(h // 2, h) + 1)], fill=(255, 255, 255, 70), width=1)

    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=0.6))
    base_rgba = image.convert("RGBA")
    return Image.alpha_composite(base_rgba, overlay).convert("RGB")


def apply_glare(
    image: Image.Image,
    cx: Optional[int] = None,
    cy: Optional[int] = None,
    radius: Optional[int] = None,
    alpha: int = 65,
    rng: Optional[random.Random] = None,
) -> Image.Image:
    """Simulates camera flash or lamp specular glare on paper surface."""
    r = rng or random.Random()
    w, h = image.size
    cx = cx or r.randint(int(w * 0.3), int(w * 0.7))
    cy = cy or r.randint(int(h * 0.25), int(h * 0.7))
    rx = radius or r.randint(int(w * 0.2), int(w * 0.35))
    ry = int(rx * r.uniform(0.7, 1.1))

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for step in range(5, 0, -1):
        frac = step / 5.0
        cur_rx = int(rx * frac)
        cur_ry = int(ry * frac)
        step_alpha = int(alpha * (1.1 - frac))
        draw.ellipse(
            [cx - cur_rx, cy - cur_ry, cx + cur_rx, cy + cur_ry],
            fill=(255, 255, 250, step_alpha),
        )

    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=rx * 0.45))
    base_rgba = image.convert("RGBA")
    return Image.alpha_composite(base_rgba, overlay).convert("RGB")


def apply_jpeg_noise(
    image: Image.Image,
    quality: int = 55,
) -> Image.Image:
    """Simulates JPEG compression artifacts and discrete cosine transform noise."""
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).copy().convert("RGB")


def apply_show_through(
    front_img: Image.Image,
    back_img: Image.Image,
    opacity: float = 0.08,
) -> Tuple[Image.Image, Image.Image]:
    """Applies authentic show-through: faint mirrored ghost text from reverse side."""
    flipped_front = front_img.transpose(Image.FLIP_LEFT_RIGHT)
    flipped_back = back_img.transpose(Image.FLIP_LEFT_RIGHT)
    ghost_front = flipped_front.filter(ImageFilter.GaussianBlur(radius=1.5))
    ghost_back = flipped_back.filter(ImageFilter.GaussianBlur(radius=1.5))
    res_back = Image.blend(back_img, ghost_front, opacity)
    res_front = Image.blend(front_img, ghost_back, opacity)
    return res_front, res_back


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

    def generate_recipe_card_faces(
        self,
        recipe: RecipeSchema,
        num_faces: int = 2,
        stock: Optional[CardStock] = None,
        writer: Optional[CardWriter] = None,
        degradation: Optional[DegradationConfig] = None,
        face_ids: Optional[List[str]] = None,
        continuation_cue: Optional[bool] = None,
    ) -> List[GeneratedCard]:
        """Generates 1 to 5 synchronized card faces for a recipe with exact verbatim tracking.

        Supports physical pairing coordination for front/back pairs (mirrored stains, dog-ears,
        tears, creases, and show-through), stock_id / writer_id attachment, and configurable
        continuation cues (present or absent).
        """
        stk = stock or CardStock()
        wtr = writer or CardWriter()
        deg = degradation or DegradationConfig.from_level(stk.aging_level, self.rng, include_advanced=True)
        num_faces = max(1, min(5, num_faces))
        rec_id = recipe.recipe_id or f"rec_{secrets.token_hex(4)}"

        if face_ids is None or len(face_ids) != num_faces:
            face_ids = [f"{secrets.token_hex(6)}.jpg" for _ in range(num_faces)]
        recipe.card_ids = face_ids

        cw = stk.width
        ch = stk.height

        if continuation_cue is None:
            has_cue = (self.rng.random() < 0.65) if num_faces > 1 else False
        else:
            has_cue = bool(continuation_cue) if num_faces > 1 else False

        ink_col = wtr.ink_color
        modality = wtr.modality
        font_style = wtr.font_style

        # Split recipe content across faces, adapting text for font
        raw_ings = list(recipe.ingredients) or ["1 cup flour", "1/2 cup sugar"]
        raw_dirs = list(recipe.directions) or ["Mix well and bake."]
        raw_title = recipe.title or "Historical Recipe"

        ings = [adapt_text_for_font(ig, font_style) for ig in raw_ings]
        dirs = [adapt_text_for_font(d, font_style) for d in raw_dirs]
        title = adapt_text_for_font(raw_title, font_style)

        face_plans = []
        if num_faces == 1:
            face_plans.append({
                "has_title": True,
                "header": None,
                "ings": ings[:7],
                "dirs": dirs[:5],
                "cue": None,
                "show_meta": True,
            })
        elif num_faces == 2:
            face_plans.append({
                "has_title": True,
                "header": None,
                "ings": ings,
                "dirs": dirs[:max(1, len(dirs) // 2)] if len(ings) <= 6 else [],
                "cue": ("(over ->)" if modality == CardModality.HANDWRITTEN_CURSIVE else "PTO >>>") if has_cue else None,
                "show_meta": False,
            })
            face_plans.append({
                "has_title": False,
                "header": (f"({title[:24]} cont.)" if modality == CardModality.HANDWRITTEN_CURSIVE else f"(CONTINUED - {title[:20].upper()})") if (has_cue and self.rng.random() < 0.75) else None,
                "ings": [],
                "dirs": dirs[max(1, len(dirs) // 2):] if len(ings) <= 6 else dirs,
                "cue": None,
                "show_meta": True,
            })
        elif num_faces == 3:
            mid_ing = max(1, len(ings) * 2 // 3)
            mid_dir = max(1, len(dirs) // 2)
            face_plans.append({
                "has_title": True,
                "header": None,
                "ings": ings[:mid_ing],
                "dirs": [],
                "cue": ("(over ->)" if modality == CardModality.HANDWRITTEN_CURSIVE else "PTO >>>") if has_cue else None,
                "show_meta": False,
            })
            face_plans.append({
                "has_title": False,
                "header": (f"(Directions - {title[:18]} cont.)") if has_cue else None,
                "ings": ings[mid_ing:],
                "dirs": dirs[:mid_dir],
                "cue": ("(see Card 2 ->)" if modality == CardModality.HANDWRITTEN_CURSIVE else "SEE CARD 2 >>>") if has_cue else None,
                "show_meta": False,
            })
            face_plans.append({
                "has_title": False,
                "header": (f"Card 2: {title[:22]}") if has_cue else None,
                "ings": [],
                "dirs": dirs[mid_dir:],
                "cue": None,
                "show_meta": True,
            })
        elif num_faces == 4:
            q_dir = max(1, len(dirs) // 3)
            face_plans.append({
                "has_title": True,
                "header": None,
                "ings": ings[:max(1, len(ings) // 2)],
                "dirs": [],
                "cue": ("(over ->)" if modality == CardModality.HANDWRITTEN_CURSIVE else "PTO >>>") if has_cue else None,
                "show_meta": False,
            })
            face_plans.append({
                "has_title": False,
                "header": "(Continued from front)" if has_cue else None,
                "ings": ings[max(1, len(ings) // 2):],
                "dirs": dirs[:q_dir],
                "cue": ("(turn to Card 2 ->)" if modality == CardModality.HANDWRITTEN_CURSIVE else "SEE CARD 2 >>>") if has_cue else None,
                "show_meta": False,
            })
            face_plans.append({
                "has_title": False,
                "header": f"Card 2: {title[:20]} (cont.)" if has_cue else None,
                "ings": [],
                "dirs": dirs[q_dir: 2 * q_dir],
                "cue": ("(over to Card 2 back ->)" if modality == CardModality.HANDWRITTEN_CURSIVE else "PTO >>>") if has_cue else None,
                "show_meta": False,
            })
            face_plans.append({
                "has_title": False,
                "header": f"Card 2 Back: {title[:18]} (finishing)" if has_cue else None,
                "ings": [],
                "dirs": dirs[2 * q_dir:],
                "cue": None,
                "show_meta": True,
            })
        else:  # num_faces == 5
            q_dir = max(1, len(dirs) // 4)
            face_plans.append({
                "has_title": True,
                "header": None,
                "ings": ings[:max(1, len(ings) // 2)],
                "dirs": [],
                "cue": ("(over ->)" if modality == CardModality.HANDWRITTEN_CURSIVE else "PTO >>>") if has_cue else None,
                "show_meta": False,
            })
            face_plans.append({
                "has_title": False,
                "header": "(Continued from front)" if has_cue else None,
                "ings": ings[max(1, len(ings) // 2):],
                "dirs": dirs[:q_dir],
                "cue": "(see Card 2 ->)" if has_cue else None,
                "show_meta": False,
            })
            face_plans.append({
                "has_title": False,
                "header": f"Card 2: {title[:20]}" if has_cue else None,
                "ings": [],
                "dirs": dirs[q_dir: 2 * q_dir],
                "cue": "(over to Card 2 back ->)" if has_cue else None,
                "show_meta": False,
            })
            face_plans.append({
                "has_title": False,
                "header": "Card 2 Back (cont.)" if has_cue else None,
                "ings": [],
                "dirs": dirs[2 * q_dir: 3 * q_dir],
                "cue": "(see Card 3 ->)" if has_cue else None,
                "show_meta": False,
            })
            face_plans.append({
                "has_title": False,
                "header": f"Card 3: {title[:20]} (finishing)" if has_cue else None,
                "ings": [],
                "dirs": dirs[3 * q_dir:],
                "cue": None,
                "show_meta": True,
            })

        # Render each face canvas
        raw_faces: List[Tuple[Image.Image, str, List[Dict[str, Any]]]] = []

        for idx, fp in enumerate(face_plans):
            img, _ = create_paper_substrate(
                cw, ch, stk.substrate, self.rng,
                ruling=stk.ruling,
                base_color_override=stk.base_color,
            )
            draw = ImageDraw.Draw(img, "RGBA")
            verbatim_lines: List[str] = []
            y = 22

            if fp["has_title"]:
                title_font_style = "headline" if modality == CardModality.PRINTED_CLIPPING else font_style
                title_font = get_font(title_font_style, size=19)
                t_str = title.upper() if modality == CardModality.TYPEWRITTEN else title
                validate_font_glyphs(t_str, title_font, title_font_style)
                draw.text((40, y), t_str, fill=(*ink_col, 255), font=title_font)
                verbatim_lines.append(t_str)
                y = 66
            elif fp["header"]:
                hdr_font = get_font(font_style, size=14)
                validate_font_glyphs(fp["header"], hdr_font, font_style)
                draw.text((40, y), fp["header"], fill=(*ink_col, 230), font=hdr_font)
                verbatim_lines.append(fp["header"])
                y = 58
            else:
                y = 38

            # Ingredients
            if fp["ings"]:
                ing_hdr = "--- INGREDIENTS ---" if modality == CardModality.TYPEWRITTEN else "Ingredients:"
                if fp["has_title"] or len(face_plans) > 1:
                    hdr_style = "typewriter" if modality == CardModality.TYPEWRITTEN else "headline"
                    hdr_font = get_font(hdr_style, size=13)
                    validate_font_glyphs(ing_hdr, hdr_font, hdr_style)
                    draw.text((40, y), ing_hdr, fill=(*ink_col, 240), font=hdr_font)
                    verbatim_lines.append(ing_hdr)
                    y += 20
                if modality == CardModality.TYPEWRITTEN:
                    y = render_typewritten_lines(
                        draw, [f"- {ig}" for ig in fp["ings"]], 40, y, cw, ch, ink_col, 13, self.rng, font_style=font_style
                    )
                    for ig in fp["ings"]:
                        verbatim_lines.append(f"- {ig}")
                elif modality == CardModality.PRINTED_CLIPPING:
                    y = render_typewritten_lines(
                        draw, [f"• {ig}" for ig in fp["ings"]], 40, y, cw, ch, ink_col, 13, self.rng, font_style="print"
                    )
                    for ig in fp["ings"]:
                        verbatim_lines.append(f"• {ig}")
                else:
                    y = render_handwritten_lines(
                        draw, fp["ings"], 40, y, cw, ch, ink_col, 13, self.rng, font_style=font_style
                    )
                    for ig in fp["ings"]:
                        verbatim_lines.append(ig)

            # Directions
            if fp["dirs"]:
                if fp["ings"]:
                    y += 8
                dir_hdr = "--- DIRECTIONS ---" if modality == CardModality.TYPEWRITTEN else "Directions:"
                if fp["has_title"] or len(face_plans) > 1:
                    hdr_style = "typewriter" if modality == CardModality.TYPEWRITTEN else "headline"
                    hdr_font = get_font(hdr_style, size=13)
                    validate_font_glyphs(dir_hdr, hdr_font, hdr_style)
                    draw.text((40, y), dir_hdr, fill=(*ink_col, 240), font=hdr_font)
                    verbatim_lines.append(dir_hdr)
                    y += 20
                if modality == CardModality.TYPEWRITTEN:
                    y = render_typewritten_lines(
                        draw, fp["dirs"], 40, y, cw, ch, ink_col, 12, self.rng, font_style=font_style
                    )
                elif modality == CardModality.PRINTED_CLIPPING:
                    y = render_typewritten_lines(
                        draw, fp["dirs"], 40, y, cw, ch, ink_col, 12, self.rng, font_style="print"
                    )
                else:
                    y = render_handwritten_lines(
                        draw, fp["dirs"], 40, y, cw, ch, ink_col, 13, self.rng, font_style=font_style
                    )
                for d in fp["dirs"]:
                    verbatim_lines.append(d)

            # Notes / Yield at bottom
            if fp["show_meta"] and (recipe.yield_servings or recipe.total_time):
                meta_line = f"Yield: {recipe.yield_servings or '4 servings'} | Time: {recipe.total_time or '45 mins'}"
                if modality == CardModality.TYPEWRITTEN:
                    meta_line = meta_line.upper()
                meta_font = get_font(font_style, size=12)
                validate_font_glyphs(meta_line, meta_font, font_style)
                draw.text((40, ch - 38), meta_line, fill=(*ink_col, 215), font=meta_font)
                verbatim_lines.append(meta_line)

            # Continuation Cue
            if fp["cue"]:
                c_style = "typewriter" if modality == CardModality.TYPEWRITTEN else font_style
                c_font = get_font(c_style, size=13)
                validate_font_glyphs(fp["cue"], c_font, c_style)
                draw.text((cw - 220, ch - 28), fp["cue"], fill=(*ink_col, 245), font=c_font)
                verbatim_lines.append(fp["cue"])

            raw_faces.append((img, "\n".join(verbatim_lines), []))

        # Coordinate Front/Back physical pairing degradation
        # Pairs: (0, 1), (2, 3)
        processed_images: List[Image.Image] = [f[0] for f in raw_faces]

        for p_front in [0, 2]:
            p_back = p_front + 1
            if p_back < len(processed_images):
                f_img = processed_images[p_front]
                b_img = processed_images[p_back]

                # Mirrored oil smudge
                if deg.oil_smudges > 0:
                    f_box = (cw - 150, 25, cw - 30, 125)
                    b_box = (30, 25, 150, 125)
                    f_img = apply_oil_smudge(f_img, f_box, self.rng, alpha=105)
                    b_img = apply_oil_smudge(b_img, b_box, self.rng, alpha=65)

                # Mirrored coffee ring
                if deg.coffee_rings > 0:
                    rcx = self.rng.randint(int(cw * 0.55), int(cw * 0.75))
                    rcy = self.rng.randint(70, 160)
                    rr = self.rng.randint(45, 60)
                    f_img = apply_coffee_ring(f_img, rcx, rcy, rr, self.rng, alpha=130)
                    b_img = apply_coffee_ring(b_img, cw - rcx, rcy, rr, self.rng, alpha=70)

                # Mirrored dog ear
                if deg.dog_ears:
                    f_img = apply_dog_ear(f_img, "TOP_RIGHT", size=36)
                    b_img = apply_dog_ear(b_img, "TOP_LEFT", size=36)

                # Mirrored edge tear
                if deg.edge_tears:
                    f_img = apply_edge_tear(f_img, "RIGHT", self.rng)
                    b_img = apply_edge_tear(b_img, "LEFT", self.rng)

                # Mirrored crease
                if deg.creases > 0:
                    f_img = apply_crease(f_img, count=deg.creases, orientation="HORIZONTAL", rng=self.rng)
                    b_img = apply_crease(b_img, count=deg.creases, orientation="HORIZONTAL", rng=self.rng)

                # Mirrored show-through / bleed-through
                if deg.bleed_through:
                    f_img, b_img = apply_show_through(f_img, b_img, opacity=0.065)

                processed_images[p_front] = f_img
                processed_images[p_back] = b_img

        # Apply global degradation to each face
        final_cards: List[GeneratedCard] = []
        for idx in range(num_faces):
            c_img = processed_images[idx]
            cid = face_ids[idx]
            v_text = raw_faces[idx][1]

            if deg.ink_fade > 0:
                c_img = apply_ink_fade(c_img, deg.ink_fade, self.rng)
            if deg.glare and idx == 0:
                c_img = apply_glare(c_img, rng=self.rng)
            if abs(deg.rotation_skew_deg) > 0.1:
                c_img = apply_rotation_skew(c_img, deg.rotation_skew_deg)
            if deg.perspective_warp > 0.001:
                c_img = apply_perspective_warp(c_img, deg.perspective_warp, self.rng)
            if deg.jpeg_noise > 0.01:
                q_val = max(35, int(85 - deg.jpeg_noise * 70))
                c_img = apply_jpeg_noise(c_img, quality=q_val)

            if num_faces == 1:
                role = CardRole.STANDALONE
                cluster_role = "standalone"
            elif idx == 0:
                role = CardRole.SEQUENCE_START
                cluster_role = "card1_front"
            else:
                role = CardRole.SEQUENCE_CONTINUATION
                c_num = (idx // 2) + 1
                c_face = "front" if (idx % 2 == 0) else "back"
                cluster_role = f"card{c_num}_{c_face}"

            cue_val = face_plans[idx].get("cue")

            final_cards.append(GeneratedCard(
                image=c_img,
                card_id=cid,
                filename=cid,
                modality=modality,
                role=role,
                stock_id=stk.stock_id,
                writer_id=wtr.writer_id,
                verbatim_text=v_text,
                face_index=idx,
                total_faces=num_faces,
                recipe_cluster={
                    "recipe_id": rec_id,
                    "sequence_position": idx,
                    "cluster_role": cluster_role,
                    "total_cards": num_faces,
                    "continuation_cue": cue_val,
                },
                physical_characteristics={
                    "stock_id": stk.stock_id,
                    "writer_id": wtr.writer_id,
                    "paper_tone": stk.substrate.value,
                    "ruling_type": str(stk.ruling),
                    "ink_type": wtr.ink_type.value,
                    "dimensions": {
                        "width_px": cw,
                        "height_px": ch,
                        "aspect_ratio": round(cw / ch, 3),
                    },
                    "artifacts": [],
                },
                recipe=recipe,
            ))

        return final_cards




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

            # Populate dimensions metadata if not present
            if "dimensions" not in card.physical_characteristics:
                w_px = card.image.width
                h_px = card.image.height
                card.physical_characteristics["dimensions"] = {
                    "width_px": w_px,
                    "height_px": h_px,
                    "width_mm": 127.0,
                    "height_mm": round(127.0 * (h_px / w_px), 1),
                    "aspect_ratio": round(w_px / h_px, 3),
                }

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
        prog="cardforge",
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
