"""Offline DOCX Archival Cookbook Exporter for RecipeBoxer.

Generates standalone OpenXML (.docx) heirloom cookbook files with styled headings,
metadata tables, bulleted ingredients, numbered directions, tags, and embedded card scan photos.
"""

from __future__ import annotations

import io
import logging
import os
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from PIL import Image

from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger(__name__)

# Standard EMU dimensions (1 inch = 914,400 EMUs)
# Max printable width: 5.5 inches = 5,029,200 EMUs
# Max printable height: 4.0 inches = 3,657,600 EMUs
MAX_EMU_WIDTH = 5029200
MAX_EMU_HEIGHT = 3657600


def _escape_xml(text: str) -> str:
    """Escapes special XML characters and strips illegal XML 1.0 control characters."""
    if not text:
        return ""
    # Filter out invalid XML 1.0 control characters
    valid_chars = []
    for ch in text:
        cp = ord(ch)
        if (
            cp == 0x9
            or cp == 0xA
            or cp == 0xD
            or (0x20 <= cp <= 0xD7FF)
            or (0xE000 <= cp <= 0xFFFD)
            or (0x10000 <= cp <= 0x10FFFF)
        ):
            valid_chars.append(ch)
    clean_text = "".join(valid_chars)
    return (
        clean_text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


class DocxExporter:
    """Builds standard OpenXML .docx cookbook documents from RecipeSchema collections."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self.encoding = encoding

    def _build_content_types_xml(self, media_files: List[Tuple[str, str, bytes]]) -> str:
        """Generates [Content_Types].xml for the docx package."""
        extensions = {"rels", "xml"}
        for fname, _, _ in media_files:
            ext = Path(fname).suffix.lstrip(".").lower()
            if ext:
                extensions.add(ext)

        xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
        xml.append('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">')
        xml.append('  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>')
        xml.append('  <Default Extension="xml" ContentType="application/xml"/>')
        if "png" in extensions:
            xml.append('  <Default Extension="png" ContentType="image/png"/>')
        if "jpg" in extensions or "jpeg" in extensions:
            xml.append('  <Default Extension="jpg" ContentType="image/jpeg"/>')
            xml.append('  <Default Extension="jpeg" ContentType="image/jpeg"/>')
        if "webp" in extensions:
            xml.append('  <Default Extension="webp" ContentType="image/webp"/>')
        if "gif" in extensions:
            xml.append('  <Default Extension="gif" ContentType="image/gif"/>')
        if "bmp" in extensions:
            xml.append('  <Default Extension="bmp" ContentType="image/bmp"/>')
        if "tif" in extensions or "tiff" in extensions:
            xml.append('  <Default Extension="tif" ContentType="image/tiff"/>')
            xml.append('  <Default Extension="tiff" ContentType="image/tiff"/>')

        xml.append('  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>')
        xml.append('  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>')
        xml.append('</Types>')
        return "\n".join(xml)

    def _build_root_rels_xml(self) -> str:
        """Generates _rels/.rels."""
        return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

    def _build_document_rels_xml(self, image_relationships: List[Tuple[str, str]]) -> str:
        """Generates word/_rels/document.xml.rels."""
        xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
        xml.append('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">')
        xml.append('  <Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        for rel_id, target in image_relationships:
            xml.append(f'  <Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{target}"/>')
        xml.append('</Relationships>')
        return "\n".join(xml)

    def _build_styles_xml(self) -> str:
        """Generates word/styles.xml defining heirloom typography styles."""
        return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Georgia" w:hAnsi="Georgia" w:cs="Georgia"/>
        <w:sz w:val="22"/>
        <w:color w:val="222222"/>
      </w:rPr>
    </w:rPrDefault>
  </w:docDefaults>

  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:pPr>
      <w:spacing w:after="120" w:line="276" w:lineRule="auto"/>
    </w:pPr>
  </w:style>

  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:pPr>
      <w:keepNext/>
      <w:spacing w:before="240" w:after="120"/>
    </w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Georgia" w:hAnsi="Georgia"/>
      <w:b/>
      <w:color w:val="23432B"/>
      <w:sz w:val="36"/>
    </w:rPr>
  </w:style>

  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:pPr>
      <w:keepNext/>
      <w:spacing w:before="180" w:after="80"/>
    </w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
      <w:b/>
      <w:color w:val="2D5A2D"/>
      <w:sz w:val="26"/>
    </w:rPr>
  </w:style>

  <w:style w:type="paragraph" w:styleId="Heading3">
    <w:name w:val="heading 3"/>
    <w:pPr>
      <w:keepNext/>
      <w:spacing w:before="140" w:after="60"/>
    </w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>
      <w:b/>
      <w:color w:val="444444"/>
      <w:sz w:val="22"/>
    </w:rPr>
  </w:style>
</w:styles>"""

    def _generate_drawing_xml(
        self, rel_id: str, doc_img_idx: int, cx: int = MAX_EMU_WIDTH, cy: int = MAX_EMU_HEIGHT
    ) -> str:
        """Generates DrawingML OpenXML element for embedding an image."""
        doc_pr_id = doc_img_idx + 100
        return f"""<w:r>
  <w:drawing>
    <wp:inline distT="0" distB="0" distL="0" distR="0" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
      <wp:extent cx="{cx}" cy="{cy}"/>
      <wp:docPr id="{doc_pr_id}" name="Picture {doc_pr_id}"/>
      <wp:cNvGraphicFramePr>
        <a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/>
      </wp:cNvGraphicFramePr>
      <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
        <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
          <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:nvPicPr>
              <pic:cNvPr id="{doc_pr_id}" name="CardScan_{doc_pr_id}"/>
              <pic:cNvPicPr/>
            </pic:nvPicPr>
            <pic:blipFill>
              <a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="{rel_id}"/>
              <a:stretch>
                <a:fillRect/>
              </a:stretch>
            </pic:blipFill>
            <pic:spPr>
              <a:xfrm>
                <a:off x="0" y="0"/>
                <a:ext cx="{cx}" cy="{cy}"/>
              </a:xfrm>
              <a:prstGeom prst="rect">
                <a:avLst/>
              </a:prstGeom>
            </pic:spPr>
          </pic:pic>
        </a:graphicData>
      </a:graphic>
    </wp:inline>
  </w:drawing>
</w:r>"""

    def _build_document_xml(
        self,
        recipes: List[RecipeSchema],
        image_map: Dict[str, Tuple[Any, ...]],  # card_id -> (rel_id, media_target, cx, cy)
    ) -> str:
        """Constructs word/document.xml with heirloom cookbook formatting."""
        p_elements: List[str] = []
        doc_img_counter = 1

        for r_idx, recipe in enumerate(recipes):
            if r_idx > 0:
                # Page break between recipes
                p_elements.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

            # 1. Recipe Title (Heading 1)
            title_escaped = _escape_xml(recipe.title)
            p_elements.append(f"""<w:p>
  <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
  <w:r><w:t>{title_escaped}</w:t></w:r>
</w:p>""")

            # 2. Description
            if recipe.description:
                desc_escaped = _escape_xml(recipe.description)
                p_elements.append(f"""<w:p>
  <w:pPr><w:spacing w:after="160"/></w:pPr>
  <w:r><w:rPr><w:i/><w:color w:val="555555"/></w:rPr><w:t>{desc_escaped}</w:t></w:r>
</w:p>""")

            # 3. Metadata Table (Yield, Prep Time, Cook Time, Total Time)
            meta_cells = [
                ("Yield", recipe.yield_servings or "—"),
                ("Prep Time", recipe.prep_time or "—"),
                ("Cook Time", recipe.cook_time or "—"),
                ("Total Time", recipe.total_time or "—"),
            ]
            tbl_xml = ['<w:tbl>',
                       '  <w:tblPr>',
                       '    <w:tblW w:w="5000" w:type="pct"/>',
                       '    <w:tblBorders>',
                       '      <w:top w:val="single" w:sz="6" w:space="0" w:color="DBE5DB"/>',
                       '      <w:bottom w:val="single" w:sz="6" w:space="0" w:color="DBE5DB"/>',
                       '      <w:insideH w:val="none"/>',
                       '      <w:insideV w:val="single" w:sz="4" w:space="0" w:color="E5EAE5"/>',
                       '    </w:tblBorders>',
                       '    <w:tblCellMar><w:top w:w="120"/><w:bottom w:w="120"/><w:left w:w="160"/><w:right w:w="160"/></w:tblCellMar>',
                       '  </w:tblPr>',
                       '  <w:tr>']

            for label, val in meta_cells:
                lbl_esc = _escape_xml(label)
                val_esc = _escape_xml(val)
                tbl_xml.append(f"""    <w:tc>
      <w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="F4F7F4"/></w:tcPr>
      <w:p>
        <w:pPr><w:spacing w:after="0"/></w:pPr>
        <w:r><w:rPr><w:b/><w:sz w:val="18"/><w:color w:val="23432B"/></w:rPr><w:t>{lbl_esc}: </w:t></w:r>
        <w:r><w:rPr><w:sz w:val="18"/><w:color w:val="333333"/></w:rPr><w:t>{val_esc}</w:t></w:r>
      </w:p>
    </w:tc>""")
            tbl_xml.append('  </w:tr>')
            tbl_xml.append('</w:tbl>')
            p_elements.append("\n".join(tbl_xml))

            # Spacer
            p_elements.append('<w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>')

            # 4. Ingredients Heading & List
            if recipe.ingredients:
                p_elements.append("""<w:p>
  <w:pPr><w:pStyle w:val="Heading2"/></w:pPr>
  <w:r><w:t>Ingredients</w:t></w:r>
</w:p>""")
                for item in recipe.ingredients:
                    item_esc = _escape_xml(item)
                    p_elements.append(f"""<w:p>
  <w:pPr><w:spacing w:after="60"/><w:ind w:left="280"/></w:pPr>
  <w:r><w:rPr><w:color w:val="2D5A2D"/></w:rPr><w:t>• </w:t></w:r>
  <w:r><w:t>{item_esc}</w:t></w:r>
</w:p>""")

            # Spacer
            p_elements.append('<w:p><w:pPr><w:spacing w:after="120"/></w:pPr></w:p>')

            # 5. Directions Heading & List
            if recipe.directions:
                p_elements.append("""<w:p>
  <w:pPr><w:pStyle w:val="Heading2"/></w:pPr>
  <w:r><w:t>Directions</w:t></w:r>
</w:p>""")
                for step in recipe.directions:
                    step_esc = _escape_xml(step)
                    p_elements.append(f"""<w:p>
  <w:pPr><w:spacing w:after="100"/><w:ind w:left="280"/></w:pPr>
  <w:r><w:t>{step_esc}</w:t></w:r>
</w:p>""")

            # 6. Tags & Source
            meta_notes = []
            if recipe.tags:
                meta_notes.append(f"Tags: {', '.join(recipe.tags)}")
            if recipe.source_notes:
                meta_notes.append(f"Source: {recipe.source_notes}")

            if meta_notes:
                p_elements.append('<w:p><w:pPr><w:spacing w:before="120" w:after="60"/></w:pPr></w:p>')
                for note in meta_notes:
                    note_esc = _escape_xml(note)
                    p_elements.append(f"""<w:p>
  <w:pPr><w:spacing w:after="40"/></w:pPr>
  <w:r><w:rPr><w:i/><w:sz w:val="18"/><w:color w:val="666666"/></w:rPr><w:t>{note_esc}</w:t></w:r>
</w:p>""")

            # 7. Embedded Card Scans
            recipe_imgs: List[Tuple[str, str, int, int]] = []  # (card_id, rel_id, cx, cy)
            for cid in recipe.card_ids:
                if cid in image_map:
                    img_info = image_map[cid]
                    rel_id = img_info[0]
                    cx = img_info[2] if len(img_info) > 2 else MAX_EMU_WIDTH
                    cy = img_info[3] if len(img_info) > 3 else MAX_EMU_HEIGHT
                    recipe_imgs.append((cid, rel_id, cx, cy))

            if recipe_imgs:
                p_elements.append("""<w:p>
  <w:pPr><w:pStyle w:val="Heading3"/></w:pPr>
  <w:r><w:t>Archival Card Scans</w:t></w:r>
</w:p>""")
                for c_num, (cid, rel_id, cx, cy) in enumerate(recipe_imgs, start=1):
                    draw_xml = self._generate_drawing_xml(rel_id, doc_img_counter, cx=cx, cy=cy)
                    doc_img_counter += 1
                    p_elements.append(f"<w:p>{draw_xml}</w:p>")
                    caption_esc = _escape_xml(f"Figure {c_num}: Original Scanned Card ({cid})")
                    p_elements.append(f"""<w:p>
  <w:pPr><w:jc w:val="center"/><w:spacing w:after="160"/></w:pPr>
  <w:r><w:rPr><w:i/><w:sz w:val="18"/><w:color w:val="777777"/></w:rPr><w:t>{caption_esc}</w:t></w:r>
</w:p>""")

        if not p_elements:
            p_elements.append('<w:p><w:r><w:t>No recipes in document.</w:t></w:r></w:p>')

        # Assemble full document.xml
        body_content = "\n".join(p_elements)
        return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
            xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
  <w:body>
{body_content}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
    </w:sectPr>
  </w:body>
</w:document>"""

    def export_to_bytes(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    ) -> bytes:
        """Serializes RecipeSchema models into a standalone OpenXML .docx file bytes."""
        recipe_list = [recipes] if isinstance(recipes, RecipeSchema) else list(recipes)

        media_files: List[Tuple[str, str, bytes]] = []  # (filename, rel_id, bytes)
        image_relationships: List[Tuple[str, str]] = []  # (rel_id, target)
        image_map: Dict[str, Tuple[str, str, int, int]] = {}  # card_id -> (rel_id, target, cx, cy)

        if image_resolver:
            img_idx = 1
            for recipe in recipe_list:
                for cid in recipe.card_ids:
                    if cid in image_map:
                        continue
                    resolved = image_resolver(cid)
                    if resolved is None:
                        continue

                    img_bytes: Optional[bytes] = None
                    ext = "jpg"

                    if isinstance(resolved, (bytes, bytearray)):
                        img_bytes = bytes(resolved)
                    elif isinstance(resolved, (str, Path)):
                        p = Path(resolved)
                        if p.exists() and p.is_file():
                            img_bytes = p.read_bytes()
                            ext = p.suffix.lstrip(".").lower() or "jpg"

                    if img_bytes:
                        filename = f"image_{img_idx}.{ext}"
                        rel_id = f"rIdImg{img_idx}"
                        target = f"media/{filename}"

                        # Calculate proportional EMU dimensions to preserve physical aspect ratio
                        cx = MAX_EMU_WIDTH
                        cy = MAX_EMU_HEIGHT
                        try:
                            with Image.open(io.BytesIO(img_bytes)) as pil_img:
                                w_px, h_px = pil_img.size
                                if w_px > 0 and h_px > 0:
                                    scale = min(MAX_EMU_WIDTH / w_px, MAX_EMU_HEIGHT / h_px)
                                    cx = int(round(w_px * scale))
                                    cy = int(round(h_px * scale))
                        except Exception as exc:
                            logger.debug(f"Could not inspect PIL dimensions for {filename}: {exc}")

                        media_files.append((filename, rel_id, img_bytes))
                        image_relationships.append((rel_id, target))
                        image_map[cid] = (rel_id, target, cx, cy)
                        img_idx += 1

        # Build XML components
        content_types_xml = self._build_content_types_xml(media_files)
        root_rels_xml = self._build_root_rels_xml()
        document_rels_xml = self._build_document_rels_xml(image_relationships)
        styles_xml = self._build_styles_xml()
        document_xml = self._build_document_xml(recipe_list, image_map)

        # Pack into ZIP archive (.docx)
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types_xml)
            zf.writestr("_rels/.rels", root_rels_xml)
            zf.writestr("word/_rels/document.xml.rels", document_rels_xml)
            zf.writestr("word/styles.xml", styles_xml)
            zf.writestr("word/document.xml", document_xml)

            for filename, _, data in media_files:
                zf.writestr(f"word/media/{filename}", data)

        return zip_buffer.getvalue()

    def export_to_file(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        output_path: Union[str, Path],
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    ) -> Path:
        """Writes .docx cookbook to file on disk."""
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        docx_bytes = self.export_to_bytes(recipes, image_resolver=image_resolver)
        path.write_bytes(docx_bytes)
        count = 1 if isinstance(recipes, RecipeSchema) else len(recipes)
        logger.info(f"Exported {count} recipe(s) to DOCX: {path}")
        return path


def export_recipes_to_docx(
    recipes: Union[RecipeSchema, List[RecipeSchema]],
    image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
) -> bytes:
    """Convenience helper to export recipes as .docx binary bytes."""
    return DocxExporter().export_to_bytes(recipes, image_resolver=image_resolver)
