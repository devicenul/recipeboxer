"""Printable Archival PDF Cookbook Exporter for RecipeBoxer.

Generates high-resolution, print-ready PDF cookbook documents with elegant heirloom
typography, page headers/footers, metadata tables, ingredients lists, numbered directions,
and embedded card scans with captions.
"""

from __future__ import annotations

import io
import logging
import math
import re
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from PIL import Image, ImageOps

from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger(__name__)

# Standard US Letter dimensions in points (72 points = 1 inch)
PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
MARGIN_LEFT = 54.0   # 0.75 in
MARGIN_RIGHT = 54.0  # 0.75 in
MARGIN_TOP = 54.0    # 0.75 in
MARGIN_BOTTOM = 54.0 # 0.75 in
CONTENT_WIDTH = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT


def _escape_pdf_string(text: str) -> str:
    """Escapes literal string for PDF string literal."""
    if not text:
        return ""
    # Strip literal carriage returns/newlines from single-line drawing text
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")

    replacements = {
        "—": "--",
        "–": "-",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "°": " deg ",
        "½": "1/2",
        "¼": "1/4",
        "¾": "3/4",
        "⅓": "1/3",
        "⅔": "2/3",
        "⅛": "1/8",
        "•": "*",
        "…": "...",
        "±": "+/-",
        "™": "(TM)",
        "®": "(R)",
        "©": "(C)",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)

    cleaned = []
    for ch in text:
        if ord(ch) < 128:
            cleaned.append(ch)
        elif ord(ch) < 256:
            cleaned.append(ch)
        else:
            cleaned.append("?")
    s = "".join(cleaned)

    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _PdfCanvas:
    """Helper canvas accumulating PDF stream commands for a single page."""

    def __init__(self, page_num: int) -> None:
        self.page_num = page_num
        self.commands: List[str] = []
        self.image_names: List[str] = []

    def add_raw(self, cmd: str) -> None:
        self.commands.append(cmd)

    def draw_text(
        self,
        text: str,
        x: float,
        y: float,
        font_name: str,
        font_size: float,
        r: float = 0.0,
        g: float = 0.0,
        b: float = 0.0,
    ) -> None:
        esc = _escape_pdf_string(text)
        self.commands.append(
            f"BT\n"
            f"/{font_name} {font_size:.2f} Tf\n"
            f"{r:.3f} {g:.3f} {b:.3f} rg\n"
            f"{x:.2f} {y:.2f} Td\n"
            f"({esc}) Tj\n"
            f"ET"
        )

    def draw_rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill_rgb: Optional[Tuple[float, float, float]] = None,
        stroke_rgb: Optional[Tuple[float, float, float]] = None,
        line_width: float = 1.0,
    ) -> None:
        cmds = []
        if fill_rgb:
            cmds.append(f"{fill_rgb[0]:.3f} {fill_rgb[1]:.3f} {fill_rgb[2]:.3f} rg")
        if stroke_rgb:
            cmds.append(f"{stroke_rgb[0]:.3f} {stroke_rgb[1]:.3f} {stroke_rgb[2]:.3f} RG")
            cmds.append(f"{line_width:.2f} w")

        cmds.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re")
        if fill_rgb and stroke_rgb:
            cmds.append("B")
        elif fill_rgb:
            cmds.append("f")
        elif stroke_rgb:
            cmds.append("S")

        self.commands.append("\n".join(cmds))

    def draw_line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        r: float = 0.0,
        g: float = 0.0,
        b: float = 0.0,
        width: float = 1.0,
    ) -> None:
        self.commands.append(
            f"{r:.3f} {g:.3f} {b:.3f} RG\n"
            f"{width:.2f} w\n"
            f"{x1:.2f} {y1:.2f} m\n"
            f"{x2:.2f} {y2:.2f} l\n"
            f"S"
        )

    def draw_image(
        self,
        img_name: str,
        x: float,
        y: float,
        w: float,
        h: float,
    ) -> None:
        if img_name not in self.image_names:
            self.image_names.append(img_name)
        self.commands.append(
            f"q\n"
            f"{w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm\n"
            f"/{img_name} Do\n"
            f"Q"
        )

    def get_stream_bytes(self) -> bytes:
        content = "\n".join(self.commands).encode("latin-1", "replace")
        return zlib.compress(content)


def _wrap_text(text: str, max_chars_per_line: int) -> List[str]:
    """Wraps text into lines based on approximate character width."""
    if not text:
        return []
    words = text.split()
    lines: List[str] = []
    cur_line = []
    cur_len = 0

    for w in words:
        if cur_len + len(w) + (1 if cur_line else 0) <= max_chars_per_line:
            cur_line.append(w)
            cur_len += len(w) + (1 if len(cur_line) > 1 else 0)
        else:
            if cur_line:
                lines.append(" ".join(cur_line))
            cur_line = [w]
            cur_len = len(w)

    if cur_line:
        lines.append(" ".join(cur_line))
    return lines


class PdfExporter:
    """Archival cookbook PDF exporter with elegant typography and embedded card scans."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self.encoding = encoding

    def _render_recipe_to_pages(
        self,
        recipe: RecipeSchema,
        pages: List[_PdfCanvas],
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]],
        image_data_store: Dict[str, Tuple[int, int, bytes, str]],  # img_name -> (w, h, data, filter)
    ) -> None:
        """Lays out a single recipe across one or more pages with robust overflow protection."""
        canvas = _PdfCanvas(page_num=len(pages) + 1)
        pages.append(canvas)

        y = PAGE_HEIGHT - MARGIN_TOP

        def new_page(continuation_heading: Optional[str] = None) -> None:
            nonlocal canvas, y
            canvas = _PdfCanvas(page_num=len(pages) + 1)
            pages.append(canvas)
            y = PAGE_HEIGHT - MARGIN_TOP
            if continuation_heading:
                header_text = continuation_heading
            else:
                short_title = recipe.title[:40] if recipe.title and len(recipe.title) > 40 else (recipe.title or "Recipe")
                header_text = f"{short_title} (CONTINUED)"
            canvas.draw_text(header_text[:65], MARGIN_LEFT, y, "F2", 8.0, 0.4, 0.4, 0.4)
            y -= 8.0
            canvas.draw_line(MARGIN_LEFT, y, PAGE_WIDTH - MARGIN_RIGHT, y, 0.75, 0.75, 0.75, 0.5)
            y -= 20.0

        def ensure_space(needed_h: float, continuation_heading: Optional[str] = None) -> None:
            nonlocal canvas, y
            if y - needed_h < MARGIN_BOTTOM + 15.0:
                new_page(continuation_heading)

        short_title = recipe.title[:30] if recipe.title and len(recipe.title) > 30 else (recipe.title or "Recipe")

        # Header rule & text on primary page
        canvas.draw_text("RECIPEBOXER ARCHIVAL COLLECTION", MARGIN_LEFT, y, "F2", 8.0, 0.4, 0.4, 0.4)
        y -= 8.0
        canvas.draw_line(MARGIN_LEFT, y, PAGE_WIDTH - MARGIN_RIGHT, y, 0.75, 0.75, 0.75, 0.5)
        y -= 22.0

        # Title (wrapped for long titles)
        title_lines = _wrap_text(recipe.title or "Untitled Recipe", 40)
        for t_idx, t_line in enumerate(title_lines):
            ensure_space(24.0)
            canvas.draw_text(t_line, MARGIN_LEFT, y, "F5", 18.0, 0.14, 0.26, 0.17)
            y -= 22.0
        y -= 2.0

        # Description (wrapped)
        if recipe.description:
            desc_lines = _wrap_text(recipe.description, 75)
            for line in desc_lines:
                ensure_space(16.0)
                canvas.draw_text(line, MARGIN_LEFT, y, "F6", 10.0, 0.35, 0.35, 0.35)
                y -= 14.0
            y -= 4.0

        # Metadata Table / Box
        box_h = 28.0
        ensure_space(box_h + 16.0)
        canvas.draw_rect(
            MARGIN_LEFT,
            y - box_h + 8.0,
            CONTENT_WIDTH,
            box_h,
            fill_rgb=(0.95, 0.97, 0.95),
            stroke_rgb=(0.85, 0.90, 0.85),
            line_width=0.75,
        )

        col_w = CONTENT_WIDTH / 4.0
        meta_items = [
            ("Yield", recipe.yield_servings or "—"),
            ("Prep", recipe.prep_time or "—"),
            ("Cook", recipe.cook_time or "—"),
            ("Total", recipe.total_time or "—"),
        ]
        for idx, (label, val) in enumerate(meta_items):
            cx = MARGIN_LEFT + (idx * col_w) + 10.0
            canvas.draw_text(f"{label}:", cx, y - 6.0, "F2", 8.5, 0.14, 0.26, 0.17)
            canvas.draw_text(val, cx + 32.0, y - 6.0, "F1", 8.5, 0.2, 0.2, 0.2)

        y -= (box_h + 16.0)

        # Ingredients Heading & Items
        if recipe.ingredients:
            ensure_space(36.0, f"{short_title} - INGREDIENTS (CONT.)")
            canvas.draw_text("INGREDIENTS", MARGIN_LEFT, y, "F2", 11.0, 0.18, 0.35, 0.18)
            y -= 6.0
            canvas.draw_line(MARGIN_LEFT, y, MARGIN_LEFT + 120.0, y, 0.18, 0.35, 0.18, 1.0)
            y -= 14.0

            for item in recipe.ingredients:
                lines = _wrap_text(item, 70)
                for l_idx, line in enumerate(lines):
                    ensure_space(15.0, f"{short_title} - INGREDIENTS (CONT.)")
                    if l_idx == 0:
                        canvas.draw_text("*", MARGIN_LEFT + 4.0, y, "F2", 9.0, 0.18, 0.35, 0.18)
                        canvas.draw_text(line, MARGIN_LEFT + 16.0, y, "F1", 9.5, 0.15, 0.15, 0.15)
                    else:
                        canvas.draw_text(line, MARGIN_LEFT + 16.0, y, "F1", 9.5, 0.15, 0.15, 0.15)
                    y -= 13.0
                y -= 2.0
            y -= 8.0

        # Directions Heading & Steps
        if recipe.directions:
            ensure_space(36.0, f"{short_title} - DIRECTIONS (CONT.)")
            canvas.draw_text("DIRECTIONS", MARGIN_LEFT, y, "F2", 11.0, 0.18, 0.35, 0.18)
            y -= 6.0
            canvas.draw_line(MARGIN_LEFT, y, MARGIN_LEFT + 120.0, y, 0.18, 0.35, 0.18, 1.0)
            y -= 14.0

            for step in recipe.directions:
                lines = _wrap_text(step, 72)
                for line in lines:
                    ensure_space(15.0, f"{short_title} - DIRECTIONS (CONT.)")
                    canvas.draw_text(line, MARGIN_LEFT + 6.0, y, "F1", 9.5, 0.15, 0.15, 0.15)
                    y -= 13.0
                y -= 4.0
            y -= 8.0

        # Tags and Source
        meta_lines = []
        if recipe.tags:
            meta_lines.append(f"Tags: {', '.join(recipe.tags)}")
        if recipe.source_notes:
            meta_lines.append(f"Source: {recipe.source_notes}")

        for note in meta_lines:
            ensure_space(13.0)
            canvas.draw_text(note, MARGIN_LEFT, y, "F3", 8.0, 0.45, 0.45, 0.45)
            y -= 11.0

        # Embedded Card Scans
        resolved_cards: List[Tuple[str, bytes]] = []
        if image_resolver:
            for cid in recipe.card_ids:
                res = image_resolver(cid)
                if res is not None:
                    if isinstance(res, (bytes, bytearray)):
                        resolved_cards.append((cid, bytes(res)))
                    elif isinstance(res, (str, Path)):
                        p = Path(res)
                        if p.exists() and p.is_file():
                            resolved_cards.append((cid, p.read_bytes()))

        if resolved_cards:
            y -= 8.0
            ensure_space(50.0, f"{recipe.title} - ARCHIVAL CARD SCANS")
            canvas.draw_text("ARCHIVAL CARD SCANS", MARGIN_LEFT, y, "F2", 10.0, 0.25, 0.25, 0.25)
            y -= 14.0

            for c_idx, (cid, raw_img_data) in enumerate(resolved_cards, start=1):
                try:
                    pil_img = Image.open(io.BytesIO(raw_img_data))
                    pil_img = ImageOps.exif_transpose(pil_img).convert("RGB")
                    w_orig, h_orig = pil_img.size

                    # Compress to JPEG
                    jpg_buf = io.BytesIO()
                    pil_img.save(jpg_buf, format="JPEG", quality=85)
                    jpg_bytes = jpg_buf.getvalue()

                    img_name = f"Im{len(image_data_store) + 1}"
                    image_data_store[img_name] = (w_orig, h_orig, jpg_bytes, "/DCTDecode")

                    # Scale image into target rectangle
                    max_w = min(CONTENT_WIDTH, 260.0)
                    max_h = 160.0
                    scale = min(max_w / w_orig, max_h / h_orig)
                    draw_w = w_orig * scale
                    draw_h = h_orig * scale

                    ensure_space(draw_h + 28.0, f"{recipe.title} - ARCHIVAL CARD SCANS (CONTINUED)")

                    canvas.draw_image(img_name, MARGIN_LEFT, y - draw_h, draw_w, draw_h)
                    canvas.draw_text(
                        f"Scan #{c_idx}: {cid}",
                        MARGIN_LEFT,
                        y - draw_h - 10.0,
                        "F3",
                        7.5,
                        0.5,
                        0.5,
                        0.5,
                    )
                    y -= (draw_h + 24.0)
                except Exception as exc:
                    logger.warning(f"Could not decode card scan image '{cid}': {exc}")

    def export_to_bytes(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    ) -> bytes:
        """Generates print-ready PDF binary bytes adhering strictly to PDF 1.4 specification."""
        recipe_list = [recipes] if isinstance(recipes, RecipeSchema) else list(recipes)

        pages: List[_PdfCanvas] = []
        image_data_store: Dict[str, Tuple[int, int, bytes, str]] = {}

        for recipe in recipe_list:
            self._render_recipe_to_pages(
                recipe=recipe,
                pages=pages,
                image_resolver=image_resolver,
                image_data_store=image_data_store,
            )

        if not pages:
            pages.append(_PdfCanvas(page_num=1))

        # Add page numbers to footers
        total_pages = len(pages)
        for idx, p in enumerate(pages, start=1):
            footer_text = f"Page {idx} of {total_pages}"
            p.draw_text(footer_text, PAGE_WIDTH - MARGIN_RIGHT - 60.0, MARGIN_BOTTOM - 16.0, "F1", 8.0, 0.5, 0.5, 0.5)
            p.draw_text("RecipeBoxer", MARGIN_LEFT, MARGIN_BOTTOM - 16.0, "F1", 8.0, 0.5, 0.5, 0.5)
            p.draw_line(MARGIN_LEFT, MARGIN_BOTTOM - 6.0, PAGE_WIDTH - MARGIN_RIGHT, MARGIN_BOTTOM - 6.0, 0.8, 0.8, 0.8, 0.5)

        # Assemble PDF Objects sequentially without ID collision
        objects: Dict[int, bytes] = {}
        cur_obj_id = 3

        # Fonts (3..8)
        fonts = {
            "F1": "/Helvetica",
            "F2": "/Helvetica-Bold",
            "F3": "/Helvetica-Oblique",
            "F4": "/Times-Roman",
            "F5": "/Times-Bold",
            "F6": "/Times-Italic",
        }
        font_obj_ids = {}
        for fname, base_font in fonts.items():
            font_obj_ids[fname] = cur_obj_id
            objects[cur_obj_id] = f"<< /Type /Font /Subtype /Type1 /BaseFont {base_font} >>".encode("latin-1")
            cur_obj_id += 1

        # Images (assigned next available IDs)
        image_obj_ids: Dict[str, int] = {}
        for img_name, (w, h, data, filt) in image_data_store.items():
            img_obj_id = cur_obj_id
            cur_obj_id += 1
            image_obj_ids[img_name] = img_obj_id
            stream_header = (
                f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter {filt} "
                f"/Length {len(data)} >>\nstream\n"
            ).encode("latin-1")
            stream_footer = b"\nendstream"
            objects[img_obj_id] = stream_header + data + stream_footer

        # Pages and Content Streams
        page_obj_ids: List[int] = []
        for p in pages:
            page_id = cur_obj_id
            cur_obj_id += 1
            content_id = cur_obj_id
            cur_obj_id += 1
            page_obj_ids.append(page_id)

            stream_data = p.get_stream_bytes()
            content_obj = (
                f"<< /Length {len(stream_data)} /Filter /FlateDecode >>\nstream\n"
            ).encode("latin-1") + stream_data + b"\nendstream"
            objects[content_id] = content_obj

            font_res = " ".join(f"/{f}: {font_obj_ids[f]} 0 R".replace(":", "") for f in fonts)
            xobj_parts = [f"/{name} {image_obj_ids[name]} 0 R" for name in p.image_names if name in image_obj_ids]
            xobj_dict_str = f"/XObject << {' '.join(xobj_parts)} >>" if xobj_parts else ""

            page_dict = (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH:.0f} {PAGE_HEIGHT:.0f}] "
                f"/Contents {content_id} 0 R "
                f"/Resources << /Font << {font_res} >> {xobj_dict_str} >> >>"
            ).encode("latin-1")
            objects[page_id] = page_dict

        # Obj 2: Pages tree
        kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
        objects[2] = f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_obj_ids)} >>".encode("latin-1")

        # Obj 1: Catalog
        objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"

        # Serialize PDF with header, xref table, and trailer
        pdf_out = io.BytesIO()
        pdf_out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        offsets: Dict[int, int] = {}
        all_obj_ids = sorted(objects.keys())
        max_id = max(all_obj_ids)

        for obj_id in all_obj_ids:
            offsets[obj_id] = pdf_out.tell()
            pdf_out.write(f"{obj_id} 0 obj\n".encode("latin-1"))
            pdf_out.write(objects[obj_id])
            pdf_out.write(b"\nendobj\n")

        xref_start = pdf_out.tell()
        pdf_out.write(f"xref\n0 {max_id + 1}\n".encode("latin-1"))
        pdf_out.write(b"0000000000 65535 f \n")

        for i in range(1, max_id + 1):
            if i in offsets:
                pdf_out.write(f"{offsets[i]:010d} 00000 n \n".encode("latin-1"))
            else:
                pdf_out.write(b"0000000000 00000 f \n")

        pdf_out.write(
            f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF\n".encode("latin-1")
        )

        return pdf_out.getvalue()

    def export_to_file(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        output_path: Union[str, Path],
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    ) -> Path:
        """Writes PDF cookbook to file on disk."""
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf_bytes = self.export_to_bytes(recipes, image_resolver=image_resolver)
        path.write_bytes(pdf_bytes)
        count = 1 if isinstance(recipes, RecipeSchema) else len(recipes)
        logger.info(f"Exported {count} recipe(s) to PDF: {path}")
        return path


def export_recipes_to_pdf(
    recipes: Union[RecipeSchema, List[RecipeSchema]],
    image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
) -> bytes:
    """Convenience helper to export recipes as .pdf binary bytes."""
    return PdfExporter().export_to_bytes(recipes, image_resolver=image_resolver)
