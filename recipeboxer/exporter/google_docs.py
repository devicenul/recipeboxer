"""Google Docs Archival Exporter for RecipeBoxer.

Generates structured Google Docs API batchUpdate requests, page-break layouts,
metadata tables, bulleted ingredients, numbered directions, tags, embedded card scans,
and downloadable JSON and HTML interchange archives.
"""

from __future__ import annotations

import base64
import html
import io
import json
import logging
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Union

import httpx

from recipeboxer.exporter.curation import CuratedCookbook, CuratedSection, GroupingPreset
from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger(__name__)


class GoogleDocsExporter:
    """Serializes RecipeSchema collections into Google Docs batch update requests, JSON and HTML archives."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self.encoding = encoding

    def build_batch_update_requests(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
        include_page_breaks: bool = True,
        curation: Optional[CuratedCookbook] = None,
    ) -> List[Dict[str, Any]]:
        """Constructs ordered Google Docs API batchUpdate requests for the given recipes.

        Args:
            recipes: Single RecipeSchema or list of RecipeSchema models.
            image_resolver: Optional callable that resolves a card_id to a file path, URI, or bytes.
            include_page_breaks: Whether to insert page breaks between recipes.
            curation: Optional CuratedCookbook model specifying grouping, TOC, and section dividers.

        Returns:
            List of Google Docs API request dicts (insertText, updateTextStyle,
            updateParagraphStyle, insertPageBreak, insertInlineImage, etc.).
        """
        if curation:
            recipe_list = curation.all_recipes
        else:
            recipe_list = [recipes] if isinstance(recipes, RecipeSchema) else list(recipes)
        requests: List[Dict[str, Any]] = []

        # We construct sequentially with relative indexing for Google Docs API batchUpdate
        # In Google Docs API, batchUpdate executes requests in sequence.
        # We model requests using standard Google Docs API v1 schema.
        current_index = 1

        # 1. Table of Contents batchUpdate requests
        if curation and curation.include_toc and curation.total_recipe_count > 0:
            toc_text = f"{curation.toc_title or 'Table of Contents'}\n"
            start_pos = current_index
            end_pos = start_pos + len(toc_text)
            requests.append({"insertText": {"location": {"index": start_pos}, "text": toc_text}})
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": start_pos, "endIndex": end_pos},
                    "paragraphStyle": {"namedStyleType": "HEADING_1", "alignment": "CENTER"},
                    "fields": "namedStyleType,alignment",
                }
            })
            requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": start_pos, "endIndex": end_pos - 1},
                    "textStyle": {
                        "bold": True,
                        "fontSize": {"magnitude": 22, "unit": "PT"},
                        "foregroundColor": {"color": {"rgbColor": {"red": 0.137, "green": 0.263, "blue": 0.169}}},
                    },
                    "fields": "bold,fontSize,foregroundColor",
                }
            })
            current_index = end_pos

            ornament_text = "✦  ✦  ✦\n\n"
            o_start = current_index
            o_end = o_start + len(ornament_text)
            requests.append({"insertText": {"location": {"index": o_start}, "text": ornament_text}})
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": o_start, "endIndex": o_end},
                    "paragraphStyle": {"alignment": "CENTER"},
                    "fields": "alignment",
                }
            })
            requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": o_start, "endIndex": o_end - 1},
                    "textStyle": {
                        "fontSize": {"magnitude": 12, "unit": "PT"},
                        "foregroundColor": {"color": {"rgbColor": {"red": 0.176, "green": 0.353, "blue": 0.176}}},
                    },
                    "fields": "fontSize,foregroundColor",
                }
            })
            current_index = o_end

            show_headers = len(curation.sections) > 1 or curation.grouping_preset != GroupingPreset.NONE
            for sec in curation.sections:
                if show_headers:
                    sec_t = f"{sec.title}\n"
                    s_start = current_index
                    s_end = s_start + len(sec_t)
                    requests.append({"insertText": {"location": {"index": s_start}, "text": sec_t}})
                    requests.append({
                        "updateParagraphStyle": {
                            "range": {"startIndex": s_start, "endIndex": s_end},
                            "paragraphStyle": {"namedStyleType": "HEADING_2"},
                            "fields": "namedStyleType",
                        }
                    })
                    requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": s_start, "endIndex": s_end - 1},
                            "textStyle": {
                                "bold": True,
                                "fontSize": {"magnitude": 13, "unit": "PT"},
                                "foregroundColor": {"color": {"rgbColor": {"red": 0.137, "green": 0.263, "blue": 0.169}}},
                            },
                            "fields": "bold,fontSize,foregroundColor",
                        }
                    })
                    current_index = s_end

                for rec in sec.recipes:
                    rec_entry = f"•  {rec.title or 'Untitled Recipe'}\n"
                    r_start = current_index
                    r_end = r_start + len(rec_entry)
                    requests.append({"insertText": {"location": {"index": r_start}, "text": rec_entry}})
                    requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": r_start, "endIndex": r_end - 1},
                            "textStyle": {"fontSize": {"magnitude": 11, "unit": "PT"}},
                            "fields": "fontSize",
                        }
                    })
                    current_index = r_end

                sp_text = "\n"
                requests.append({"insertText": {"location": {"index": current_index}, "text": sp_text}})
                current_index += len(sp_text)

            if include_page_breaks:
                requests.append({"insertPageBreak": {"location": {"index": current_index}}})
                current_index += 1

        # 2. Section Dividers and Recipe Content
        sections_to_render = curation.sections if curation and curation.sections else [
            CuratedSection(section_id="all", title="All Recipes", recipes=recipe_list)
        ]

        for s_idx, section in enumerate(sections_to_render):
            show_divider = bool(
                curation
                and curation.include_section_dividers
                and (len(curation.sections) > 1 or curation.grouping_preset != GroupingPreset.NONE)
            )

            if show_divider:
                div_title = f"{section.title}\n"
                dt_start = current_index
                dt_end = dt_start + len(div_title)
                requests.append({"insertText": {"location": {"index": dt_start}, "text": div_title}})
                requests.append({
                    "updateParagraphStyle": {
                        "range": {"startIndex": dt_start, "endIndex": dt_end},
                        "paragraphStyle": {"namedStyleType": "HEADING_1", "alignment": "CENTER"},
                        "fields": "namedStyleType,alignment",
                    }
                })
                requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": dt_start, "endIndex": dt_end - 1},
                        "textStyle": {
                            "bold": True,
                            "fontSize": {"magnitude": 26, "unit": "PT"},
                            "foregroundColor": {"color": {"rgbColor": {"red": 0.137, "green": 0.263, "blue": 0.169}}},
                        },
                        "fields": "bold,fontSize,foregroundColor",
                    }
                })
                current_index = dt_end

                div_ornament = "✦  ✦  ✦\n"
                do_start = current_index
                do_end = do_start + len(div_ornament)
                requests.append({"insertText": {"location": {"index": do_start}, "text": div_ornament}})
                requests.append({
                    "updateParagraphStyle": {
                        "range": {"startIndex": do_start, "endIndex": do_end},
                        "paragraphStyle": {"alignment": "CENTER"},
                        "fields": "alignment",
                    }
                })
                requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": do_start, "endIndex": do_end - 1},
                        "textStyle": {
                            "fontSize": {"magnitude": 14, "unit": "PT"},
                            "foregroundColor": {"color": {"rgbColor": {"red": 0.176, "green": 0.353, "blue": 0.176}}},
                        },
                        "fields": "fontSize,foregroundColor",
                    }
                })
                current_index = do_end

                if section.subtitle:
                    div_sub = f"{section.subtitle}\n"
                    ds_start = current_index
                    ds_end = ds_start + len(div_sub)
                    requests.append({"insertText": {"location": {"index": ds_start}, "text": div_sub}})
                    requests.append({
                        "updateParagraphStyle": {
                            "range": {"startIndex": ds_start, "endIndex": ds_end},
                            "paragraphStyle": {"alignment": "CENTER"},
                            "fields": "alignment",
                        }
                    })
                    requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": ds_start, "endIndex": ds_end - 1},
                            "textStyle": {
                                "italic": True,
                                "fontSize": {"magnitude": 12, "unit": "PT"},
                                "foregroundColor": {"color": {"rgbColor": {"red": 0.4, "green": 0.4, "blue": 0.4}}},
                            },
                            "fields": "italic,fontSize,foregroundColor",
                        }
                    })
                    current_index = ds_end

                if include_page_breaks:
                    requests.append({"insertPageBreak": {"location": {"index": current_index}}})
                    current_index += 1

            for r_idx, recipe in enumerate(section.recipes):
                if (current_index > 1 or (s_idx > 0 or r_idx > 0)) and include_page_breaks and not show_divider and r_idx > 0:
                    requests.append({
                        "insertPageBreak": {
                            "location": {"index": current_index},
                        }
                    })
                    current_index += 1
                elif show_divider and r_idx > 0 and include_page_breaks:
                    requests.append({
                        "insertPageBreak": {
                            "location": {"index": current_index},
                        }
                    })
                    current_index += 1

                # 1. Recipe Title (HEADING_1)
                title_text = f"{recipe.title}\n"
                start_pos = current_index
                end_pos = start_pos + len(title_text)
                requests.append({
                    "insertText": {
                        "location": {"index": start_pos},
                        "text": title_text,
                    }
                })
                requests.append({
                    "updateParagraphStyle": {
                        "range": {"startIndex": start_pos, "endIndex": end_pos},
                        "paragraphStyle": {"namedStyleType": "HEADING_1"},
                        "fields": "namedStyleType",
                    }
                })
                requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": start_pos, "endIndex": end_pos - 1},
                        "textStyle": {
                            "bold": True,
                            "fontSize": {"magnitude": 20, "unit": "PT"},
                            "foregroundColor": {
                                "color": {"rgbColor": {"red": 0.15, "green": 0.25, "blue": 0.15}}
                            },
                        },
                        "fields": "bold,fontSize,foregroundColor",
                    }
                })
                current_index = end_pos

                # 2. Description (if present)
                if recipe.description:
                    desc_text = f"{recipe.description}\n\n"
                    d_start = current_index
                    d_end = d_start + len(desc_text)
                    requests.append({
                        "insertText": {
                            "location": {"index": d_start},
                            "text": desc_text,
                        }
                    })
                    requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": d_start, "endIndex": d_end - 1},
                            "textStyle": {
                                "italic": True,
                                "fontSize": {"magnitude": 11, "unit": "PT"},
                                "foregroundColor": {
                                    "color": {"rgbColor": {"red": 0.35, "green": 0.35, "blue": 0.35}}
                                },
                            },
                            "fields": "italic,fontSize,foregroundColor",
                        }
                    })
                    current_index = d_end

                # 3. Metadata Table (Prep, Cook, Total Time, Yield)
                meta_items = []
                if recipe.yield_servings:
                    meta_items.append(f"Yield: {recipe.yield_servings}")
                if recipe.prep_time:
                    meta_items.append(f"Prep: {recipe.prep_time}")
                if recipe.cook_time:
                    meta_items.append(f"Cook: {recipe.cook_time}")
                if recipe.total_time:
                    meta_items.append(f"Total: {recipe.total_time}")

                if meta_items:
                    meta_header = " | ".join(meta_items) + "\n\n"
                    m_start = current_index
                    m_end = m_start + len(meta_header)
                    requests.append({
                        "insertText": {
                            "location": {"index": m_start},
                            "text": meta_header,
                        }
                    })
                    requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": m_start, "endIndex": m_end - 1},
                            "textStyle": {
                                "bold": True,
                                "fontSize": {"magnitude": 10, "unit": "PT"},
                                "foregroundColor": {
                                    "color": {"rgbColor": {"red": 0.2, "green": 0.4, "blue": 0.3}}
                                },
                            },
                            "fields": "bold,fontSize,foregroundColor",
                        }
                    })
                    current_index = m_end

                # 4. Ingredients Section (HEADING_2 + dynamic bullets)
                if recipe.ingredients:
                    ing_heading = "Ingredients\n"
                    ih_start = current_index
                    ih_end = ih_start + len(ing_heading)
                    requests.append({
                        "insertText": {
                            "location": {"index": ih_start},
                            "text": ing_heading,
                        }
                    })
                    requests.append({
                        "updateParagraphStyle": {
                            "range": {"startIndex": ih_start, "endIndex": ih_end},
                            "paragraphStyle": {"namedStyleType": "HEADING_2"},
                            "fields": "namedStyleType",
                        }
                    })
                    current_index = ih_end

                    ing_block = "".join(f"{item}\n" for item in recipe.ingredients)
                    ib_start = current_index
                    ib_end = ib_start + len(ing_block)
                    requests.append({
                        "insertText": {
                            "location": {"index": ib_start},
                            "text": ing_block,
                        }
                    })
                    requests.append({
                        "createParagraphBullets": {
                            "range": {"startIndex": ib_start, "endIndex": ib_end},
                            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                        }
                    })
                    current_index = ib_end

                    # Spacer newline
                    requests.append({
                        "insertText": {
                            "location": {"index": current_index},
                            "text": "\n",
                        }
                    })
                    current_index += 1

                # 5. Directions Section (HEADING_2 + numbered instructions)
                if recipe.directions:
                    dir_heading = "Directions\n"
                    dh_start = current_index
                    dh_end = dh_start + len(dir_heading)
                    requests.append({
                        "insertText": {
                            "location": {"index": dh_start},
                            "text": dir_heading,
                        }
                    })
                    requests.append({
                        "updateParagraphStyle": {
                            "range": {"startIndex": dh_start, "endIndex": dh_end},
                            "paragraphStyle": {"namedStyleType": "HEADING_2"},
                            "fields": "namedStyleType",
                        }
                    })
                    current_index = dh_end

                    cleaned_dir_steps = [
                        re.sub(r"^(?:Step\s+)?\d+[\.\)\-:]\s*", "", s, flags=re.IGNORECASE).strip()
                        for s in recipe.directions
                    ]
                    dir_block = "".join(f"{step}\n" for step in cleaned_dir_steps)
                    db_start = current_index
                    db_end = db_start + len(dir_block)
                    requests.append({
                        "insertText": {
                            "location": {"index": db_start},
                            "text": dir_block,
                        }
                    })
                    requests.append({
                        "createParagraphBullets": {
                            "range": {"startIndex": db_start, "endIndex": db_end},
                            "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN",
                        }
                    })
                    current_index = db_end

                    # Spacer newline
                    requests.append({
                        "insertText": {
                            "location": {"index": current_index},
                            "text": "\n",
                        }
                    })
                    current_index += 1

                # 6. Tags & Source Notes
                extra_lines = []
                if recipe.tags:
                    extra_lines.append(f"Tags: {', '.join(recipe.tags)}")
                if recipe.source_notes:
                    extra_lines.append(f"Source: {recipe.source_notes}")

                if extra_lines:
                    notes_text = "\n".join(extra_lines) + "\n\n"
                    n_start = current_index
                    n_end = n_start + len(notes_text)
                    requests.append({
                        "insertText": {
                            "location": {"index": n_start},
                            "text": notes_text,
                        }
                    })
                    requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": n_start, "endIndex": n_end - 1},
                            "textStyle": {
                                "italic": True,
                                "fontSize": {"magnitude": 9, "unit": "PT"},
                                "foregroundColor": {
                                    "color": {"rgbColor": {"red": 0.45, "green": 0.45, "blue": 0.45}}
                                },
                            },
                            "fields": "italic,fontSize,foregroundColor",
                        }
                    })
                    current_index = n_end

                # 7. Embedded Card Scans (front, back, attachments)
                card_images: List[str] = []
                if image_resolver:
                    for cid in recipe.card_ids:
                        resolved = image_resolver(cid)
                        if resolved:
                            card_images.append(str(resolved))
                if not card_images and recipe.image_urls:
                    card_images.extend(recipe.image_urls)

                if card_images:
                    scans_heading = "Archival Card Scans\n"
                    sh_start = current_index
                    sh_end = sh_start + len(scans_heading)
                    requests.append({
                        "insertText": {
                            "location": {"index": sh_start},
                            "text": scans_heading,
                        }
                    })
                    requests.append({
                        "updateParagraphStyle": {
                            "range": {"startIndex": sh_start, "endIndex": sh_end},
                            "paragraphStyle": {"namedStyleType": "HEADING_3"},
                            "fields": "namedStyleType",
                        }
                    })
                    current_index = sh_end

                    for img_uri in card_images:
                        requests.append({
                            "insertInlineImage": {
                                "location": {"index": current_index},
                                "uri": img_uri,
                                "objectSize": {
                                    "height": {"magnitude": 250, "unit": "PT"},
                                    "width": {"magnitude": 350, "unit": "PT"},
                                },
                            }
                        })
                        current_index += 1
                        requests.append({
                            "insertText": {
                                "location": {"index": current_index},
                                "text": "\n",
                            }
                        })
                        current_index += 1

        return requests

    def export_to_json_dict(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        title: str = "RecipeBoxer Archival Cookbook",
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
        curation: Optional[CuratedCookbook] = None,
    ) -> Dict[str, Any]:
        """Exports Google Docs interchange document dictionary containing metadata and batch requests."""
        if curation:
            recipe_list = curation.all_recipes
        else:
            recipe_list = [recipes] if isinstance(recipes, RecipeSchema) else list(recipes)
        batch_requests = self.build_batch_update_requests(
            recipes=recipe_list,
            image_resolver=image_resolver,
            curation=curation,
        )

        return {
            "title": title,
            "documentId": f"recipeboxer_doc_{len(recipe_list)}_recipes",
            "revisionId": "1",
            "recipe_count": len(recipe_list),
            "requests": batch_requests,
            "recipes": [r.model_dump() for r in recipe_list],
        }

    def export_to_json_string(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        title: str = "RecipeBoxer Archival Cookbook",
        indent: int = 2,
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
        curation: Optional[CuratedCookbook] = None,
    ) -> str:
        """Serializes Google Docs archive into JSON formatted string."""
        data = self.export_to_json_dict(recipes, title=title, image_resolver=image_resolver, curation=curation)
        return json.dumps(data, indent=indent, ensure_ascii=False)

    def export_to_html(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        title: str = "RecipeBoxer Archival Cookbook",
        embed_images_base64: bool = True,
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
        curation: Optional[CuratedCookbook] = None,
    ) -> str:
        """Generates self-contained Google Docs compatible HTML interchange archive with TOC and dividers."""
        if curation:
            recipe_list = curation.all_recipes
        else:
            recipe_list = [recipes] if isinstance(recipes, RecipeSchema) else list(recipes)

        html_out = io.StringIO()
        html_out.write("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n")
        html_out.write("<meta charset=\"utf-8\">\n")
        html_out.write(f"<title>{html.escape(title)}</title>\n")
        html_out.write("""<style>
body {
    font-family: 'Merriweather', Georgia, serif;
    line-height: 1.6;
    color: #1a1a1a;
    background-color: #faf8f5;
    margin: 0;
    padding: 24px;
}
.cookbook-container {
    max-width: 800px;
    margin: 0 auto;
    background: #ffffff;
    padding: 48px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.08);
    border-radius: 8px;
}
.table-of-contents {
    margin-bottom: 48px;
    padding: 32px 0 36px 0;
    border-bottom: 2px solid #23432b;
}
.toc-title {
    font-size: 26px;
    color: #23432b;
    text-align: center;
    margin-bottom: 4px;
}
.toc-ornament {
    text-align: center;
    color: #2d5a2d;
    font-size: 14px;
    margin-bottom: 24px;
}
.toc-section-title {
    font-size: 16px;
    color: #23432b;
    margin-top: 16px;
    margin-bottom: 8px;
    font-family: sans-serif;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.toc-recipe-list {
    list-style-type: none;
    padding-left: 0;
    margin: 0 0 16px 0;
}
.toc-recipe-list li {
    padding: 4px 0;
    border-bottom: 1px dotted #ccc;
    display: flex;
    justify-content: space-between;
}
.toc-recipe-list a {
    color: #23432b;
    text-decoration: none;
    font-weight: 500;
}
.toc-recipe-list a:hover {
    text-decoration: underline;
}
.section-divider {
    text-align: center;
    padding: 64px 24px;
    margin-bottom: 48px;
    background: #f4f7f4;
    border: 2px solid #23432b;
    border-radius: 8px;
}
.section-divider-title {
    font-size: 30px;
    color: #23432b;
    margin: 0 0 12px 0;
}
.section-divider-ornament {
    color: #2d5a2d;
    font-size: 16px;
    margin-bottom: 12px;
}
.section-divider-subtitle {
    font-style: italic;
    color: #555555;
    font-size: 15px;
    margin: 0;
}
.recipe-card {
    margin-bottom: 48px;
    padding-bottom: 36px;
    border-bottom: 2px dashed #e2dcd5;
}
.recipe-card:last-child {
    border-bottom: none;
    margin-bottom: 0;
}
.recipe-page-break {
    page-break-before: always;
    break-before: page;
}
h1.recipe-title {
    font-size: 26px;
    color: #23432b;
    margin-top: 0;
    margin-bottom: 8px;
    border-bottom: 2px solid #23432b;
    padding-bottom: 6px;
}
p.recipe-desc {
    font-style: italic;
    color: #555555;
    margin-top: 4px;
    margin-bottom: 16px;
}
.meta-table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 20px;
    background: #f4f7f4;
    border: 1px solid #dbe5db;
    border-radius: 4px;
}
.meta-table td {
    padding: 8px 12px;
    font-size: 13px;
    color: #2b4c2b;
}
.meta-table strong {
    color: #163016;
}
h2.section-heading {
    font-size: 18px;
    color: #23432b;
    margin-top: 20px;
    margin-bottom: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-family: sans-serif;
}
ul.ingredient-list {
    margin: 0 0 20px 0;
    padding-left: 24px;
}
ul.ingredient-list li {
    margin-bottom: 6px;
    font-size: 15px;
}
ol.direction-list {
    margin: 0 0 20px 0;
    padding-left: 24px;
}
ol.direction-list li {
    margin-bottom: 10px;
    font-size: 15px;
    line-height: 1.5;
}
.tags-source {
    font-size: 12px;
    color: #777777;
    margin-top: 16px;
    font-style: italic;
}
.tag-badge {
    display: inline-block;
    background: #eef2ee;
    color: #2d5a2d;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    margin-right: 4px;
    font-style: normal;
}
.scans-container {
    margin-top: 24px;
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
}
.scan-wrapper {
    flex: 1 1 calc(50% - 16px);
    max-width: 100%;
    border: 1px solid #e0d9cf;
    border-radius: 6px;
    overflow: hidden;
    background: #fdfbf7;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    text-align: center;
    padding: 8px;
}
.scan-wrapper img {
    max-width: 100%;
    height: auto;
    border-radius: 4px;
}
.scan-caption {
    font-size: 11px;
    color: #666;
    margin-top: 6px;
    font-family: sans-serif;
}
@media print {
    body { background: none; padding: 0; }
    .cookbook-container { box-shadow: none; padding: 0; }
    .recipe-page-break { page-break-before: always; break-before: page; }
}
</style>
</head>
<body>
<div class="cookbook-container">
""")

        # 1. Table of Contents
        if curation and curation.include_toc and curation.total_recipe_count > 0:
            toc_title_str = curation.toc_title or "Table of Contents"
            html_out.write('<div class="table-of-contents recipe-page-break">\n')
            html_out.write(f'<h1 class="toc-title">{html.escape(toc_title_str)}</h1>\n')
            html_out.write('<div class="toc-ornament">✦  ✦  ✦</div>\n')

            show_sec_headers = len(curation.sections) > 1 or curation.grouping_preset != GroupingPreset.NONE
            for sec in curation.sections:
                if show_sec_headers:
                    html_out.write(f'<h2 class="toc-section-title">{html.escape(sec.title)}</h2>\n')
                html_out.write('<ul class="toc-recipe-list">\n')
                for rec in sec.recipes:
                    rec_t = rec.title or "Untitled Recipe"
                    html_out.write(f'  <li><a href="#recipe-{html.escape(rec.recipe_id)}">{html.escape(rec_t)}</a></li>\n')
                html_out.write('</ul>\n')
            html_out.write('</div>\n')

        # 2. Sections and Recipe Cards
        sections_to_render = curation.sections if curation and curation.sections else [
            CuratedSection(section_id="all", title="All Recipes", recipes=recipe_list)
        ]

        rendered_recipe_count = 0
        for s_idx, section in enumerate(sections_to_render):
            show_divider = bool(
                curation
                and curation.include_section_dividers
                and (len(curation.sections) > 1 or curation.grouping_preset != GroupingPreset.NONE)
            )

            if show_divider:
                html_out.write('<div class="section-divider recipe-page-break">\n')
                html_out.write(f'<h1 class="section-divider-title">{html.escape(section.title)}</h1>\n')
                html_out.write('<div class="section-divider-ornament">✦  ✦  ✦</div>\n')
                if section.subtitle:
                    html_out.write(f'<p class="section-divider-subtitle">{html.escape(section.subtitle)}</p>\n')
                html_out.write('</div>\n')

            for r_idx, recipe in enumerate(section.recipes):
                rendered_recipe_count += 1
                page_break_cls = " recipe-page-break" if (rendered_recipe_count > 1 or (curation and curation.include_toc) or show_divider) else ""
                recipe_anchor_id = f' id="recipe-{html.escape(recipe.recipe_id)}"'
                html_out.write(f'<div class="recipe-card{page_break_cls}"{recipe_anchor_id}>\n')
                html_out.write(f'<h1 class="recipe-title">{html.escape(recipe.title)}</h1>\n')

                if recipe.description:
                    html_out.write(f'<p class="recipe-desc">{html.escape(recipe.description)}</p>\n')

                # Metadata table
                html_out.write('<table class="meta-table"><tr>\n')
                html_out.write(f'<td><strong>Yield:</strong> {html.escape(recipe.yield_servings or "—")}</td>\n')
                html_out.write(f'<td><strong>Prep Time:</strong> {html.escape(recipe.prep_time or "—")}</td>\n')
                html_out.write(f'<td><strong>Cook Time:</strong> {html.escape(recipe.cook_time or "—")}</td>\n')
                html_out.write(f'<td><strong>Total Time:</strong> {html.escape(recipe.total_time or "—")}</td>\n')
                html_out.write('</tr></table>\n')

                # Ingredients
                if recipe.ingredients:
                    html_out.write('<h2 class="section-heading">Ingredients</h2>\n')
                    html_out.write('<ul class="ingredient-list">\n')
                    for item in recipe.ingredients:
                        html_out.write(f'  <li>{html.escape(item)}</li>\n')
                    html_out.write('</ul>\n')

                # Directions
                if recipe.directions:
                    html_out.write('<h2 class="section-heading">Directions</h2>\n')
                    html_out.write('<ol class="direction-list">\n')
                    for step in recipe.directions:
                        cleaned_step = re.sub(r"^(?:Step\s+)?\d+[\.\)\-:]\s*", "", step, flags=re.IGNORECASE).strip()
                        html_out.write(f'  <li>{html.escape(cleaned_step)}</li>\n')
                    html_out.write('</ol>\n')

                # Tags & Source
                html_out.write('<div class="tags-source">\n')
                if recipe.tags:
                    html_out.write('  <div>Tags: ')
                    for tag in recipe.tags:
                        html_out.write(f'<span class="tag-badge">{html.escape(tag)}</span>')
                    html_out.write('</div>\n')
                if recipe.source_notes:
                    html_out.write(f'  <div style="margin-top:4px;">Source: {html.escape(recipe.source_notes)}</div>\n')
                html_out.write('</div>\n')

                # Card Scans
                resolved_images: List[tuple[str, str]] = []
                if image_resolver:
                    for c_idx, cid in enumerate(recipe.card_ids, start=1):
                        res = image_resolver(cid)
                        if res is not None:
                            if isinstance(res, (bytes, bytearray)):
                                b64 = base64.b64encode(res).decode("ascii")
                                src = f"data:image/jpeg;base64,{b64}"
                                resolved_images.append((src, f"Card Scan #{c_idx} ({cid})"))
                            elif isinstance(res, (str, Path)):
                                p = Path(res)
                                if p.exists() and embed_images_base64:
                                    data = p.read_bytes()
                                    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                                    b64 = base64.b64encode(data).decode("ascii")
                                    src = f"data:{mime};base64,{b64}"
                                    resolved_images.append((src, f"Card Scan #{c_idx} ({p.name})"))
                                else:
                                    resolved_images.append((str(res), f"Card Scan #{c_idx}"))
                if not resolved_images and recipe.image_urls:
                    for c_idx, url in enumerate(recipe.image_urls, start=1):
                        resolved_images.append((url, f"Card Scan #{c_idx}"))

                if resolved_images:
                    html_out.write('<h2 class="section-heading">Archival Card Scans</h2>\n')
                    html_out.write('<div class="scans-container">\n')
                    for src, caption in resolved_images:
                        html_out.write('  <div class="scan-wrapper">\n')
                        html_out.write(f'    <img src="{html.escape(src)}" alt="{html.escape(caption)}" loading="lazy" />\n')
                        html_out.write(f'    <div class="scan-caption">{html.escape(caption)}</div>\n')
                        html_out.write('  </div>\n')
                    html_out.write('</div>\n')

                html_out.write('</div>\n')  # end recipe-card

        html_out.write("</div>\n</body>\n</html>\n")
        return html_out.getvalue()

    def export_to_file(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        output_path: Union[str, Path],
        as_html: bool = False,
        title: str = "RecipeBoxer Archival Cookbook",
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
        curation: Optional[CuratedCookbook] = None,
    ) -> Path:
        """Writes Google Docs archive to a JSON or HTML file on disk."""
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        if as_html or path.suffix.lower() in (".html", ".htm"):
            content = self.export_to_html(recipes, title=title, image_resolver=image_resolver, curation=curation)
        else:
            content = self.export_to_json_string(recipes, title=title, image_resolver=image_resolver, curation=curation)

        path.write_text(content, encoding=self.encoding)
        count = curation.total_recipe_count if curation else (1 if isinstance(recipes, RecipeSchema) else len(recipes))
        logger.info(f"Exported {count} recipe(s) to Google Docs archive: {path}")
        return path

    def export_to_bytes(
        self,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        as_html: bool = False,
        title: str = "RecipeBoxer Archival Cookbook",
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
        curation: Optional[CuratedCookbook] = None,
    ) -> bytes:
        """Serializes Google Docs archive into UTF-8 encoded bytes."""
        if as_html:
            return self.export_to_html(recipes, title=title, image_resolver=image_resolver, curation=curation).encode(self.encoding)
        return self.export_to_json_string(recipes, title=title, image_resolver=image_resolver, curation=curation).encode(self.encoding)

    def create_google_document(
        self,
        access_token: str,
        recipes: Union[RecipeSchema, List[RecipeSchema]],
        title: str = "RecipeBoxer Archival Cookbook",
        image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
        drive_folder_name: str = "RecipeBoxer Scans",
        http_client: Optional[httpx.Client] = None,
        curation: Optional[CuratedCookbook] = None,
    ) -> Dict[str, Any]:
        """Creates an authentic Google Doc in the user's account using Google Docs & Drive APIs.

        1. Uploads card scan images to the user's Google Drive folder.
        2. Creates a new Google Document.
        3. Applies structured batchUpdate requests (typography, tables, lists, inline images).
        4. Returns the created document ID and direct URL.
        """
        if not access_token or not access_token.strip():
            raise ValueError("Google OAuth access token is required")

        if curation:
            recipe_list = curation.all_recipes
        else:
            recipe_list = [recipes] if isinstance(recipes, RecipeSchema) else list(recipes)

        if not recipe_list:
            raise ValueError("No recipes provided for Google Docs export")

        clean_token = re.sub(r"^Bearer\s+", "", access_token.strip(), flags=re.IGNORECASE).strip().strip('"\'')
        if not clean_token:
            raise ValueError("Google OAuth access token is required")
        headers = {
            "Authorization": f"Bearer {clean_token}",
            "Accept": "application/json",
        }

        client_provided = http_client is not None
        client = http_client or httpx.Client(timeout=45.0)

        try:
            # Step 1: Create or find target folder in Google Drive
            folder_id: Optional[str] = None
            try:
                search_resp = client.get(
                    "https://www.googleapis.com/drive/v3/files",
                    headers=headers,
                    params={
                        "q": f"name='{drive_folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                        "fields": "files(id, name)",
                    },
                )
                if search_resp.status_code == 200:
                    files = search_resp.json().get("files", [])
                    if files:
                        folder_id = files[0]["id"]
                elif search_resp.status_code == 401:
                    raise ValueError("Invalid or expired Google OAuth access token (401 Unauthorized)")

                if not folder_id and search_resp.status_code in (200, 404):
                    create_folder_resp = client.post(
                        "https://www.googleapis.com/drive/v3/files",
                        headers=headers,
                        json={
                            "name": drive_folder_name,
                            "mimeType": "application/vnd.google-apps.folder",
                        },
                    )
                    if create_folder_resp.status_code in (200, 201):
                        folder_id = create_folder_resp.json().get("id")
                    elif create_folder_resp.status_code == 401:
                        raise ValueError("Invalid or expired Google OAuth access token (401 Unauthorized)")
            except ValueError:
                raise
            except Exception as e:
                logger.warning(f"Could not initialize Google Drive folder '{drive_folder_name}': {e}")

            # Step 2: Upload Card Scan Images to Google Drive
            drive_image_map: Dict[str, str] = {}
            for recipe in recipe_list:
                for cid in recipe.card_ids:
                    if cid in drive_image_map:
                        continue
                    resolved = image_resolver(cid) if image_resolver else None
                    if resolved is None:
                        continue

                    # If already a public web URL, use directly
                    if isinstance(resolved, str) and (resolved.startswith("http://") or resolved.startswith("https://")):
                        drive_image_map[cid] = resolved
                        continue

                    raw_bytes: Optional[bytes] = None
                    file_ext = "jpg"
                    mime_type = "image/jpeg"

                    if isinstance(resolved, (bytes, bytearray)):
                        raw_bytes = bytes(resolved)
                    elif isinstance(resolved, (str, Path)):
                        p = Path(resolved)
                        if p.exists() and p.is_file():
                            raw_bytes = p.read_bytes()
                            file_ext = p.suffix.lstrip(".").lower() or "jpg"
                            mime_type = "image/png" if file_ext == "png" else "image/jpeg"

                    if not raw_bytes:
                        continue

                    file_name = f"scan_{cid}.{file_ext}"
                    try:
                        # Multipart upload to Google Drive
                        metadata: Dict[str, Any] = {"name": file_name}
                        if folder_id:
                            metadata["parents"] = [folder_id]

                        boundary = "-------RecipeBoxerBoundary" + re.sub(r"[^a-zA-Z0-9]", "", str(cid))[:12]
                        body_parts = (
                            f"--{boundary}\r\n"
                            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                            f"{json.dumps(metadata)}\r\n"
                            f"--{boundary}\r\n"
                            f"Content-Type: {mime_type}\r\n\r\n"
                        ).encode("utf-8") + raw_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

                        upload_headers = {
                            "Authorization": f"Bearer {clean_token}",
                            "Content-Type": f"multipart/related; boundary={boundary}",
                            "Accept": "application/json",
                        }

                        upload_resp = client.post(
                            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webContentLink,thumbnailLink",
                            headers=upload_headers,
                            content=body_parts,
                        )

                        if upload_resp.status_code in (200, 201):
                            up_data = upload_resp.json()
                            file_id = up_data.get("id")
                            if file_id:
                                # Make file readable so Docs service can embed it
                                try:
                                    client.post(
                                        f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
                                        headers=headers,
                                        json={"role": "reader", "type": "anyone"},
                                    )
                                except Exception:
                                    pass

                                img_link = up_data.get("webContentLink") or f"https://drive.google.com/uc?export=view&id={file_id}"
                                drive_image_map[cid] = img_link
                        elif upload_resp.status_code == 401:
                            raise ValueError("Invalid or expired Google OAuth access token (401 Unauthorized)")
                        else:
                            logger.warning(f"Failed to upload card {cid} to Google Drive: {upload_resp.status_code} {upload_resp.text}")
                    except ValueError:
                        raise
                    except Exception as exc:
                        logger.warning(f"Error uploading image {cid} to Google Drive: {exc}")

            # Step 3: Create Google Document
            doc_create_resp = client.post(
                "https://docs.googleapis.com/v1/documents",
                headers=headers,
                json={"title": title},
            )
            if doc_create_resp.status_code == 401:
                raise ValueError("Invalid or expired Google OAuth access token (401 Unauthorized)")
            if doc_create_resp.status_code not in (200, 201):
                raise ValueError(f"Failed to create Google Doc: HTTP {doc_create_resp.status_code} - {doc_create_resp.text}")

            doc_data = doc_create_resp.json()
            document_id = doc_data.get("documentId")
            if not document_id:
                raise ValueError("Google Docs API did not return a valid documentId")

            # Step 4: Build Batch Update Requests
            def drive_image_resolver(cid: str) -> Optional[Union[str, Path, bytes]]:
                if cid in drive_image_map:
                    return drive_image_map[cid]
                if image_resolver:
                    res = image_resolver(cid)
                    if isinstance(res, str) and (res.startswith("http://") or res.startswith("https://")):
                        return res
                return None

            batch_requests = self.build_batch_update_requests(
                recipes=recipe_list,
                image_resolver=drive_image_resolver,
                include_page_breaks=True,
                curation=curation,
            )

            # Step 5: Execute Batch Update Requests
            if batch_requests:
                batch_resp = client.post(
                    f"https://docs.googleapis.com/v1/documents/{document_id}:batchUpdate",
                    headers=headers,
                    json={"requests": batch_requests},
                )
                if batch_resp.status_code == 401:
                    raise ValueError("Invalid or expired Google OAuth access token (401 Unauthorized)")
                if batch_resp.status_code not in (200, 201):
                    logger.warning(f"Google Docs batchUpdate returned HTTP {batch_resp.status_code}: {batch_resp.text}")

            doc_url = f"https://docs.google.com/document/d/{document_id}/edit"
            logger.info(f"Successfully created Google Doc '{title}' ({document_id}) with {len(recipe_list)} recipe(s)")

            return {
                "document_id": document_id,
                "url": doc_url,
                "title": title,
                "recipe_count": len(recipe_list),
            }

        finally:
            if not client_provided:
                client.close()


def export_recipes_to_google_docs_batch_requests(
    recipes: Union[RecipeSchema, List[RecipeSchema]],
    image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    curation: Optional[CuratedCookbook] = None,
) -> List[Dict[str, Any]]:
    """Convenience helper to generate Google Docs API batchUpdate requests."""
    return GoogleDocsExporter().build_batch_update_requests(recipes, image_resolver=image_resolver, curation=curation)


def export_recipes_to_google_docs_json(
    recipes: Union[RecipeSchema, List[RecipeSchema]],
    title: str = "RecipeBoxer Archival Cookbook",
    image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    curation: Optional[CuratedCookbook] = None,
) -> str:
    """Convenience helper to export Google Docs JSON archive string."""
    return GoogleDocsExporter().export_to_json_string(recipes, title=title, image_resolver=image_resolver, curation=curation)


def export_recipes_to_google_docs_html(
    recipes: Union[RecipeSchema, List[RecipeSchema]],
    title: str = "RecipeBoxer Archival Cookbook",
    image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    curation: Optional[CuratedCookbook] = None,
) -> str:
    """Convenience helper to export Google Docs HTML archive string."""
    return GoogleDocsExporter().export_to_html(recipes, title=title, image_resolver=image_resolver, curation=curation)


def create_google_docs_archival_document(
    access_token: str,
    recipes: Union[RecipeSchema, List[RecipeSchema]],
    title: str = "RecipeBoxer Archival Cookbook",
    image_resolver: Optional[Callable[[str], Optional[Union[str, Path, bytes]]]] = None,
    drive_folder_name: str = "RecipeBoxer Scans",
    http_client: Optional[httpx.Client] = None,
    curation: Optional[CuratedCookbook] = None,
) -> Dict[str, Any]:
    """Convenience helper to create a real Google Doc in the user's account."""
    return GoogleDocsExporter().create_google_document(
        access_token=access_token,
        recipes=recipes,
        title=title,
        image_resolver=image_resolver,
        drive_folder_name=drive_folder_name,
        http_client=http_client,
        curation=curation,
    )


