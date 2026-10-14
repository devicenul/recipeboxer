"""Curates synthetic recipe card evaluation corpora T1 and T2.

Enforces Wave 3 interface contracts and procedural secrecy:
- Recipe text is fetched at runtime from Project Gutenberg public-domain cookbooks (1880-1922)
  into a local cache outside the workspace ($PROJECT_CACHE_DIR).
- Recipe source text is NEVER written to workspace files in plaintext.
- Ground truth JSON is assembled strictly in memory and encrypted directly
  into evals/vault/<tier>.gt.enc using multi-recipient age encryption.
- T1 images are saved to evals/corpora/t1/ (opaque names, total size <= 40 MB).
- Large 300+ card deck spec is encrypted to evals/vault/t1_large.spec.enc
  for on-demand deterministic materialization.
- T2 images are tarred and encrypted to evals/vault/t2.images.tar.enc.
- Public evals/corpora/t1/DECKS.json publishes only deck_id and n_images.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import random
import re
import secrets
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

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
from cardforge.generator import SyntheticCardGenerator
from evals.vault import encrypt_bytes, load_recipients, get_repo_root

# Unit pattern for recipe ingredient parser
UNIT_PATTERN = r'(?:cups?|c\.|tablespoons?|tbsp\.?|tbs\.?|teaspoons?|tsp\.?|pounds?|lbs?\.?|oz\.?|ounces?|pints?|pt\.?|quarts?|qt\.?|gallons?|gal\.?|packages?|pkgs?\.?|pinch|pinches|dash|dashes|slices?|cloves?|cans?|bottles?|sticks?|heaping tbsp\.?)'
ING_RE = re.compile(
    rf'^\s*(?P<qty>[\d¼½¾⅓⅔⅛⅜⅝⅞]+(?:[\s/–\d¼½¾⅓⅔⅛⅜⅝⅞]*)?)?\s*(?P<unit>{UNIT_PATTERN})?\s*(?:of\s+)?(?P<item>.+?)\s*$',
    re.IGNORECASE,
)


def get_cache_dir() -> Path:
    """Returns project cache directory outside workspace."""
    env_cache = os.environ.get("PROJECT_CACHE_DIR")
    if env_cache:
        target = Path(env_cache) / "gutenberg_cache"
    else:
        repo_root = get_repo_root()
        digest = hashlib.sha1(str(repo_root.resolve()).encode("utf-8")).hexdigest()[:8]
        target = Path.home() / ".cache" / "projects" / f"{repo_root.name}-{digest}" / "gutenberg_cache"
    target.mkdir(parents=True, exist_ok=True)
    return target


def ensure_gutenberg_book(book_id: int, cache_dir: Path) -> Path:
    """Downloads Project Gutenberg plain-text cookbook if not already cached outside workspace."""
    target = cache_dir / f"pg{book_id}.txt"
    if not target.exists():
        url = f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt"
        req = urllib.request.Request(url, headers={"User-Agent": "RecipeBoxer-Curator/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        target.write_bytes(data)
    return target


def parse_ingredient_line(line: str) -> Dict[str, Any]:
    """Parses a raw ingredient string into structured quantity, unit, item."""
    clean = line.strip().rstrip(".,;")
    m = ING_RE.match(clean)
    if m:
        qty = m.group("qty").strip() if m.group("qty") else None
        unit = m.group("unit").strip() if m.group("unit") else None
        item = m.group("item").strip() if m.group("item") else clean
        return {"raw": clean, "quantity": qty, "unit": unit, "item": item, "section": None}
    return {"raw": clean, "quantity": None, "unit": None, "item": clean, "section": None}


def parse_recipes_from_text(
    text: str,
    source_note: str,
    max_recipes: int = 250,
) -> List[Dict[str, Any]]:
    """Parses historical recipes and their ingredients/directions from Gutenberg plain text."""
    # Strip Gutenberg preface and license blocks
    start_m = re.search(r'\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK[^\*]*\*\*\*', text)
    if start_m:
        text = text[start_m.end():]
    end_m = re.search(r'\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK', text)
    if end_m:
        text = text[:end_m.start()]

    qty_re = re.compile(rf'^\s*(?:[\d¼½¾⅓⅔⅛⅜⅝⅞]+(?:[\s/–\d¼½¾⅓⅔⅛⅜⅝⅞]*)?)\s+{UNIT_PATTERN}\b', re.IGNORECASE)
    fraction_re = re.compile(r'^\s*[\d¼½¾⅓⅔⅛⅜⅝⅞/]+\s+[A-Za-z]')

    blocks = text.split('\n\n')
    recipes = []
    i = 0
    while i < len(blocks) - 1 and len(recipes) < max_recipes:
        b = blocks[i].strip()
        lines = [l.strip() for l in b.splitlines() if l.strip()]
        if not lines:
            i += 1
            continue

        cand_title = lines[0]
        # Candidate title filter
        if (len(lines) == 1 and 3 <= len(cand_title) <= 50 and
            not cand_title.endswith(':') and not cand_title.startswith('CHAPTER') and
            not cand_title.startswith('INDEX') and not cand_title.startswith('PART ') and
            not cand_title.startswith('TABLE') and not cand_title.startswith('RULES') and
            not cand_title.startswith('FOOD') and not cand_title.startswith('NOTE')):

            next_b = blocks[i+1].strip()
            next_lines = [l.strip() for l in next_b.splitlines() if l.strip()]
            ings = [l for l in next_lines if qty_re.match(l) or fraction_re.match(l)]

            if 3 <= len(ings) <= 12:
                dirs = []
                j = i + 2
                while j < min(i + 5, len(blocks)):
                    dir_b = blocks[j].strip()
                    if not dir_b:
                        j += 1
                        continue
                    if len(dir_b.splitlines()) == 1 and dir_b.isupper():
                        break
                    dir_lines = [l.strip() for l in dir_b.splitlines() if l.strip()]
                    if sum(1 for l in dir_lines if qty_re.match(l)) > 2:
                        break
                    dirs.append(' '.join(dir_lines))
                    j += 1

                if dirs:
                    t_clean = cand_title.rstrip('.')
                    if t_clean.isupper():
                        t_clean = t_clean.title()

                    step_list = []
                    for d in dirs:
                        s_parts = [s.strip() + '.' for s in re.split(r'\.\s+', d) if len(s.strip()) > 8]
                        if s_parts:
                            step_list.extend(s_parts)
                        else:
                            step_list.append(d)

                    recipes.append({
                        "title": t_clean,
                        "ingredients": ings,
                        "structured_ingredients": [parse_ingredient_line(l) for l in ings],
                        "directions": step_list[:8],
                        "yield": "4 to 6 servings",
                        "source_note": source_note,
                        "notes": [],
                    })
                    i = j - 1
        i += 1
    return recipes


def build_t1_stocks_and_writers() -> Tuple[Dict[str, CardStock], Dict[str, CardWriter]]:
    """Creates stationery stocks and writers for Tier 1."""
    stocks = {
        "s_t1_cream": CardStock(
            stock_id="s_t1_cream",
            name="Cream Standard Ruled",
            substrate=PaperSubstrate.RULED_INDEX,
            ruling=RulingStyle.STANDARD_RULED,
            base_color=(248, 244, 230),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MILD,
        ),
        "s_t1_yellow": CardStock(
            stock_id="s_t1_yellow",
            name="Aged Yellow Ruled",
            substrate=PaperSubstrate.AGED_YELLOW,
            ruling=RulingStyle.STANDARD_RULED,
            base_color=(245, 236, 210),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MODERATE,
        ),
        "s_t1_linen": CardStock(
            stock_id="s_t1_linen",
            name="White Linen Unruled",
            substrate=PaperSubstrate.WHITE_LINEN,
            ruling=RulingStyle.UNRULED,
            base_color=(252, 250, 246),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MILD,
        ),
        "s_t1_kraft": CardStock(
            stock_id="s_t1_kraft",
            name="Kraft Wide Ruled",
            substrate=PaperSubstrate.KRAFT_BROWN,
            ruling=RulingStyle.WIDE_RULED,
            base_color=(218, 195, 162),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MODERATE,
        ),
        "s_t1_narrow": CardStock(
            stock_id="s_t1_narrow",
            name="Narrow Ruled Index",
            substrate=PaperSubstrate.RULED_INDEX,
            ruling=RulingStyle.NARROW_RULED,
            base_color=(246, 244, 236),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MILD,
        ),
        "s_t1_grid": CardStock(
            stock_id="s_t1_grid",
            name="Graph Grid Card",
            substrate=PaperSubstrate.GRID_GRAPH,
            ruling=RulingStyle.GRID_GRAPH,
            base_color=(248, 250, 246),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MILD,
        ),
        "s_t1_double": CardStock(
            stock_id="s_t1_double",
            name="Double Header Ruled",
            substrate=PaperSubstrate.RULED_INDEX,
            ruling=RulingStyle.DOUBLE_HEADER,
            base_color=(250, 246, 238),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MODERATE,
        ),
    }

    writers = {
        "w_t1_cursive_blue": CardWriter(
            writer_id="w_t1_cursive_blue",
            name="Primary Cook (Blue Cursive)",
            modality=CardModality.HANDWRITTEN_CURSIVE,
            font_style="caveat",
            ink_color=(22, 45, 130),
            ink_type=InkType.BLUE_BALLPOINT,
            continuation_cue_style="over",
        ),
        "w_t1_cursive_sepia": CardWriter(
            writer_id="w_t1_cursive_sepia",
            name="Grandmother (Sepia Script)",
            modality=CardModality.HANDWRITTEN_CURSIVE,
            font_style="marck_script",
            ink_color=(68, 40, 25),
            ink_type=InkType.FOUNTAIN_PEN,
            continuation_cue_style="over",
        ),
        "w_t1_typewriter": CardWriter(
            writer_id="w_t1_typewriter",
            name="Manual Typewriter",
            modality=CardModality.TYPEWRITTEN,
            font_style="special_elite",
            ink_color=(28, 28, 30),
            ink_type=InkType.TYPEWRITER_RIBBON,
            continuation_cue_style="pto",
        ),
        "w_t1_clipping": CardWriter(
            writer_id="w_t1_clipping",
            name="Pasted Newsprint Clipping",
            modality=CardModality.PRINTED_CLIPPING,
            font_style="print",
            ink_color=(20, 20, 20),
            ink_type=InkType.PRINTED_TYPESET,
        ),
        "w_t1_pencil": CardWriter(
            writer_id="w_t1_pencil",
            name="Quick Notes (Pencil)",
            modality=CardModality.HANDWRITTEN_CURSIVE,
            font_style="caveat",
            ink_color=(65, 65, 68),
            ink_type=InkType.PENCIL_GRAPHITE,
        ),
        "w_t1_mixed": CardWriter(
            writer_id="w_t1_mixed",
            name="Annotated Typed Card",
            modality=CardModality.MIXED_MEDIA,
            font_style="special_elite",
            ink_color=(30, 30, 30),
            ink_type=InkType.TYPEWRITER_RIBBON,
        ),
    }

    return stocks, writers


def build_t2_stocks_and_writers() -> Tuple[Dict[str, CardStock], Dict[str, CardWriter]]:
    """Creates distinct stationery stocks and writers for Tier 2 (held-out)."""
    stocks = {
        "s_t2_ivory": CardStock(
            stock_id="s_t2_ivory",
            name="Ivory Cardstock",
            substrate=PaperSubstrate.RULED_INDEX,
            ruling=RulingStyle.STANDARD_RULED,
            base_color=(250, 247, 238),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MILD,
        ),
        "s_t2_parchment": CardStock(
            stock_id="s_t2_parchment",
            name="Faint Ruled Parchment",
            substrate=PaperSubstrate.AGED_YELLOW,
            ruling=RulingStyle.FAINT_RULED,
            base_color=(242, 232, 210),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MODERATE,
        ),
        "s_t2_blue_card": CardStock(
            stock_id="s_t2_blue_card",
            name="Pale Blue Index Card",
            substrate=PaperSubstrate.RULED_INDEX,
            ruling=RulingStyle.BLUE_NO_HEADER,
            base_color=(234, 242, 248),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MILD,
        ),
        "s_t2_canary": CardStock(
            stock_id="s_t2_canary",
            name="Canary Wide Ruled",
            substrate=PaperSubstrate.AGED_YELLOW,
            ruling=RulingStyle.WIDE_RULED,
            base_color=(252, 248, 205),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MODERATE,
        ),
        "s_t2_kraft": CardStock(
            stock_id="s_t2_kraft",
            name="Heavy Kraft Narrow",
            substrate=PaperSubstrate.KRAFT_BROWN,
            ruling=RulingStyle.NARROW_RULED,
            base_color=(212, 190, 155),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MODERATE,
        ),
        "s_t2_green_ledger": CardStock(
            stock_id="s_t2_green_ledger",
            name="Linen Green Ledger",
            substrate=PaperSubstrate.WHITE_LINEN,
            ruling=RulingStyle.GREEN_LEDGER,
            base_color=(244, 248, 242),
            width=1020,
            height=660,
            aging_level=DegradationLevel.MILD,
        ),
    }

    writers = {
        "w_t2_fountain_black": CardWriter(
            writer_id="w_t2_fountain_black",
            name="Estate Hand (Black Fountain Pen)",
            modality=CardModality.HANDWRITTEN_CURSIVE,
            font_style="marck_script",
            ink_color=(25, 25, 28),
            ink_type=InkType.FOUNTAIN_PEN,
        ),
        "w_t2_fountain_blue": CardWriter(
            writer_id="w_t2_fountain_blue",
            name="Estate Hand (Blue Fountain Pen)",
            modality=CardModality.HANDWRITTEN_CURSIVE,
            font_style="caveat",
            ink_color=(20, 35, 140),
            ink_type=InkType.FOUNTAIN_PEN,
        ),
        "w_t2_typewriter": CardWriter(
            writer_id="w_t2_typewriter",
            name="Vintage Elite Typewriter",
            modality=CardModality.TYPEWRITTEN,
            font_style="special_elite",
            ink_color=(25, 25, 25),
            ink_type=InkType.TYPEWRITER_RIBBON,
        ),
        "w_t2_ballpoint": CardWriter(
            writer_id="w_t2_ballpoint",
            name="Modern Ballpoint Hand",
            modality=CardModality.HANDWRITTEN_CURSIVE,
            font_style="caveat",
            ink_color=(30, 60, 160),
            ink_type=InkType.BLUE_BALLPOINT,
        ),
        "w_t2_clipping": CardWriter(
            writer_id="w_t2_clipping",
            name="Newspaper Clipping Insert",
            modality=CardModality.PRINTED_CLIPPING,
            font_style="print",
            ink_color=(20, 20, 20),
            ink_type=InkType.PRINTED_TYPESET,
        ),
    }

    return stocks, writers


def generate_deck_faces(
    recipes_data: List[Dict[str, Any]],
    deck_id: str,
    deck_type: str,
    stock_mapping: List[CardStock],
    writer_mapping: List[CardWriter],
    face_counts: List[int],
    cue_flags: List[Optional[bool]],
    base_seed: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Tuple[str, Image.Image]]]:
    """Generates cards, images, and ground-truth metadata for a single deck."""
    rng = random.Random(base_seed)
    images_meta: List[Dict[str, Any]] = []
    recipes_meta: List[Dict[str, Any]] = []
    rendered_images: List[Tuple[str, Image.Image]] = []

    for r_idx, r_data in enumerate(recipes_data):
        seed_val = base_seed + (r_idx * 17)
        gen = SyntheticCardGenerator(seed=seed_val)
        stock = stock_mapping[r_idx % len(stock_mapping)]
        writer = writer_mapping[r_idx % len(writer_mapping)]
        n_faces = face_counts[r_idx % len(face_counts)]
        cue = cue_flags[r_idx % len(cue_flags)]

        # Unique opaque image filenames (deterministic from base seed)
        face_ids = [f"{rng.getrandbits(48):012x}.jpg" for _ in range(n_faces)]
        rec_id = f"r{r_idx + 1:03d}"

        rec_obj = RecipeSchema(
            recipe_id=rec_id,
            title=r_data["title"],
            yield_servings=r_data.get("yield", "4 servings"),
            total_time="45 mins",
            ingredients=r_data["ingredients"],
            directions=r_data["directions"],
            source_notes=r_data.get("source_note", ""),
            card_ids=face_ids,
        )

        cards = gen.generate_recipe_card_faces(
            recipe=rec_obj,
            num_faces=n_faces,
            stock=stock,
            writer=writer,
            face_ids=face_ids,
            continuation_cue=cue,
        )

        verbatims = []
        for c_idx, card in enumerate(cards):
            # Encode image to JPEG buffer in memory to compute deterministic SHA-256
            buf = io.BytesIO()
            card.image.save(buf, format="JPEG", quality=80)
            img_bytes = buf.getvalue()
            sha256_hex = hashlib.sha256(img_bytes).hexdigest()

            # Canonical modality for contracts schema
            modality_str = str(card.modality.value)
            if "CURSIVE" in modality_str or "HANDWRITTEN" in modality_str:
                can_mod = "HANDWRITTEN_CURSIVE"
            elif "TYPEWRITTEN" in modality_str or "TYPEWRITER" in modality_str:
                can_mod = "TYPEWRITTEN"
            elif "CLIPPING" in modality_str:
                can_mod = "CLIPPING"
            else:
                can_mod = "MIXED"

            images_meta.append({
                "image_id": card.filename,
                "sha256": sha256_hex,
                "modality": can_mod,
                "stock_id": card.stock_id,
            })
            verbatims.append(card.verbatim_text or "")
            rendered_images.append((card.filename, card.image))

        legibility = "good" if n_faces <= 2 else ("fair" if n_faces <= 3 else "poor")

        recipes_meta.append({
            "recipe_id": rec_id,
            "faces": face_ids,
            "verbatim": verbatims,
            "structured": {
                "title": r_data["title"],
                "yield": r_data.get("yield", None),
                "source_note": r_data.get("source_note", None),
                "ingredients": r_data.get("structured_ingredients", []),
                "directions": r_data.get("directions", []),
                "notes": r_data.get("notes", []),
            },
            "difficulty": {
                "legibility": legibility,
                "faces": n_faces,
            },
        })

    deck_gt = {
        "deck_id": deck_id,
        "deck_type": deck_type,
        "images": images_meta,
        "recipes": recipes_meta,
    }

    return deck_gt, images_meta, rendered_images


def build_t1(
    cache_dir: Path,
    corpora_dir: Path,
    vault_dir: Path,
    base_seed: int = 50000,
) -> Dict[str, Any]:
    """Generates Tier 1 dev-eval corpus, public images, and encrypted GT."""
    t1_dir = corpora_dir / "t1"
    t1_dir.mkdir(parents=True, exist_ok=True)

    # 1. Ingest recipes from T1 Project Gutenberg sources
    fannie_path = ensure_gutenberg_book(65061, cache_dir)
    bradley_path = ensure_gutenberg_book(10582, cache_dir)

    all_t1_recipes = parse_recipes_from_text(
        fannie_path.read_text(encoding="utf-8", errors="ignore"),
        source_note="The Boston Cooking-School Cook Book (1896)",
        max_recipes=220,
    )
    bradley_recipes = parse_recipes_from_text(
        bradley_path.read_text(encoding="utf-8", errors="ignore"),
        source_note="For Luncheon and Supper Guests (1922)",
        max_recipes=50,
    )
    all_t1_recipes.extend(bradley_recipes)

    stocks, writers = build_t1_stocks_and_writers()

    decks_gt = []
    decks_public_info = []

    # Deck d01: Homogeneous-box deck (>=70% same stock, same writer)
    # 12 recipes: 10 on primary stock/writer, 2 distractor
    d01_recipes = all_t1_recipes[:12]
    d01_stocks = [stocks["s_t1_cream"]] * 10 + [stocks["s_t1_yellow"], stocks["s_t1_linen"]]
    d01_writers = [writers["w_t1_cursive_blue"]] * 10 + [writers["w_t1_typewriter"], writers["w_t1_pencil"]]
    d01_faces = [1, 2, 2, 1, 2, 3, 2, 1, 2, 3, 2, 1]  # 4 standalones, 6 pairs, 2 3-face
    d01_cues = [None, True, False, None, True, True, False, None, True, False, True, None]

    d01_gt, d01_imgs, d01_rendered = generate_deck_faces(
        d01_recipes, "d01", "homogeneous", d01_stocks, d01_writers, d01_faces, d01_cues, base_seed + 100
    )
    decks_gt.append(d01_gt)
    decks_public_info.append({"deck_id": "d01", "n_images": len(d01_imgs)})

    # Save d01 images
    d01_path = t1_dir / "d01"
    if d01_path.exists():
        shutil.rmtree(d01_path)
    d01_path.mkdir(parents=True, exist_ok=True)
    for fname, img in d01_rendered:
        img.save(d01_path / fname, format="JPEG", quality=80)

    # Deck d02: Mixed deck (high diversity of substrates and hands)
    # 12 recipes
    d02_recipes = all_t1_recipes[12:24]
    d02_stocks = [
        stocks["s_t1_kraft"], stocks["s_t1_linen"], stocks["s_t1_yellow"],
        stocks["s_t1_narrow"], stocks["s_t1_grid"], stocks["s_t1_double"],
    ] * 2
    d02_writers = [
        writers["w_t1_typewriter"], writers["w_t1_cursive_sepia"], writers["w_t1_clipping"],
        writers["w_t1_pencil"], writers["w_t1_mixed"], writers["w_t1_cursive_blue"],
    ] * 2
    d02_faces = [1, 2, 3, 2, 1, 4, 2, 3, 2, 1, 3, 2]  # 3 standalones, 5 pairs, 3 3-face, 1 4-face
    d02_cues = [None, True, True, False, None, True, True, False, True, None, False, True]

    d02_gt, d02_imgs, d02_rendered = generate_deck_faces(
        d02_recipes, "d02", "mixed", d02_stocks, d02_writers, d02_faces, d02_cues, base_seed + 200
    )
    decks_gt.append(d02_gt)
    decks_public_info.append({"deck_id": "d02", "n_images": len(d02_imgs)})

    # Save d02 images
    d02_path = t1_dir / "d02"
    if d02_path.exists():
        shutil.rmtree(d02_path)
    d02_path.mkdir(parents=True, exist_ok=True)
    for fname, img in d02_rendered:
        img.save(d02_path / fname, format="JPEG", quality=80)

    # Deck d03: Near-duplicate recipes (variants of same dish)
    # 10 recipes
    d03_recipes = all_t1_recipes[24:34]
    # Inject near-duplicate variant naming for pairs
    if len(d03_recipes) >= 6:
        d03_recipes[0]["title"] = f"{d03_recipes[0]['title']} I"
        d03_recipes[1]["title"] = f"{d03_recipes[0]['title'][:-2]} II"
        d03_recipes[2]["title"] = f"{d03_recipes[2]['title']} I"
        d03_recipes[3]["title"] = f"{d03_recipes[2]['title'][:-2]} II"
        d03_recipes[4]["title"] = f"{d03_recipes[4]['title']} (Old-Style)"
        d03_recipes[5]["title"] = f"{d03_recipes[4]['title'].split(' (')[0]} (Modern)"

    d03_stocks = [stocks["s_t1_cream"], stocks["s_t1_narrow"], stocks["s_t1_yellow"]] * 4
    d03_writers = [writers["w_t1_cursive_blue"], writers["w_t1_cursive_sepia"], writers["w_t1_typewriter"]] * 4
    d03_faces = [1, 2, 2, 1, 2, 3, 2, 3, 2, 1]  # 3 standalones, 5 pairs, 2 3-face
    d03_cues = [None, True, False, None, True, True, False, True, False, None]

    d03_gt, d03_imgs, d03_rendered = generate_deck_faces(
        d03_recipes, "d03", "near_duplicate", d03_stocks, d03_writers, d03_faces, d03_cues, base_seed + 300
    )
    decks_gt.append(d03_gt)
    decks_public_info.append({"deck_id": "d03", "n_images": len(d03_imgs)})

    # Save d03 images
    d03_path = t1_dir / "d03"
    if d03_path.exists():
        shutil.rmtree(d03_path)
    d03_path.mkdir(parents=True, exist_ok=True)
    for fname, img in d03_rendered:
        img.save(d03_path / fname, format="JPEG", quality=80)

    # Deck d04: Large 300+ card deck
    # 145 recipes (319 cards)
    d04_faces = [1] * 30 + [2] * 75 + [3] * 25 + [4] * 11 + [5] * 4
    n_d04_recipes = len(d04_faces)
    d04_recipes_raw = all_t1_recipes[34 : 34 + n_d04_recipes]
    while len(d04_recipes_raw) < n_d04_recipes:
        d04_recipes_raw.extend(all_t1_recipes[: n_d04_recipes - len(d04_recipes_raw)])

    d04_stock_dom = stocks["s_t1_cream"]
    d04_writer_dom = writers["w_t1_cursive_blue"]
    d04_stocks = []
    d04_writers = []
    for k in range(len(d04_recipes_raw)):
        if k % 7 == 0:
            d04_stocks.append(stocks["s_t1_yellow"])
            d04_writers.append(writers["w_t1_typewriter"])
        elif k % 11 == 0:
            d04_stocks.append(stocks["s_t1_linen"])
            d04_writers.append(writers["w_t1_pencil"])
        else:
            d04_stocks.append(d04_stock_dom)
            d04_writers.append(d04_writer_dom)

    d04_cues = [(k % 3 != 0) for k in range(len(d04_recipes_raw))]

    d04_gt, d04_imgs, d04_rendered = generate_deck_faces(
        d04_recipes_raw, "d04", "large", d04_stocks, d04_writers, d04_faces, d04_cues, base_seed + 1000
    )
    decks_gt.append(d04_gt)
    decks_public_info.append({"deck_id": "d04", "n_images": len(d04_imgs)})

    # Save d04 images (committed for untrusted CI and public runs)
    d04_path = t1_dir / "d04"
    if d04_path.exists():
        shutil.rmtree(d04_path)
    d04_path.mkdir(parents=True, exist_ok=True)
    for fname, img in d04_rendered:
        img.save(d04_path / fname, format="JPEG", quality=80)

    # Encrypt d04 generation specification for deterministic on-demand materialization
    d04_spec = {
        "deck_id": "d04",
        "base_seed": base_seed + 1000,
        "n_images": len(d04_imgs),
        "recipe_items": [
            {
                "recipe_data": d04_recipes_raw[idx],
                "stock_id": d04_stocks[idx].stock_id,
                "writer_id": d04_writers[idx].writer_id,
                "num_faces": d04_faces[idx],
                "continuation_cue": d04_cues[idx],
                "face_ids": d04_gt["recipes"][idx]["faces"],
            }
            for idx in range(len(d04_recipes_raw))
        ],
    }
    spec_json = json.dumps(d04_spec, indent=2)
    spec_enc = encrypt_bytes(spec_json)
    (vault_dir / "t1_large.spec.enc").write_bytes(spec_enc)

    # 2. Write PUBLIC DECKS.json (no deck_type, no GT)
    (t1_dir / "DECKS.json").write_text(json.dumps(decks_public_info, indent=2), encoding="utf-8")

    # 3. Assemble full Ground Truth JSON and encrypt straight into vault
    t1_gt = {
        "schema": "rb-gt/1",
        "tier": "T1",
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "generator": {"name": "cardforge", "version": "0.1.0"},
        "decks": decks_gt,
    }
    t1_gt_json = json.dumps(t1_gt, indent=2)
    t1_gt_enc = encrypt_bytes(t1_gt_json)
    (vault_dir / "t1.gt.enc").write_bytes(t1_gt_enc)

    return t1_gt


def build_t2(
    cache_dir: Path,
    vault_dir: Path,
    base_seed: int = 70000,
) -> Dict[str, Any]:
    """Generates Tier 2 held-out corpus and writes encrypted images tar + encrypted GT."""
    # 1. Ingest recipes from T2 Project Gutenberg sources (completely distinct from T1)
    p15464 = ensure_gutenberg_book(15464, cache_dir)
    p13177 = ensure_gutenberg_book(13177, cache_dir)
    p22114 = ensure_gutenberg_book(22114, cache_dir)

    all_t2_recipes = parse_recipes_from_text(
        p15464.read_text(encoding="utf-8", errors="ignore"),
        source_note="Foods That Will Win The War (1918)",
        max_recipes=100,
    )
    r13177 = parse_recipes_from_text(
        p13177.read_text(encoding="utf-8", errors="ignore"),
        source_note="Chocolate and Cocoa Recipes (1909)",
        max_recipes=60,
    )
    all_t2_recipes.extend(r13177)

    stocks, writers = build_t2_stocks_and_writers()
    decks_gt = []
    tar_buf = io.BytesIO()

    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        # Deck d01: Homogeneous deck (held out)
        d01_recipes = all_t2_recipes[:12]
        d01_stocks = [stocks["s_t2_ivory"]] * 10 + [stocks["s_t2_parchment"], stocks["s_t2_blue_card"]]
        d01_writers = [writers["w_t2_fountain_black"]] * 10 + [writers["w_t2_typewriter"], writers["w_t2_ballpoint"]]
        d01_faces = [1, 2, 2, 3, 1, 2, 2, 3, 1, 2, 2, 1]
        d01_cues = [None, True, False, True, None, True, False, True, None, True, False, None]

        d01_gt, d01_imgs, d01_rendered = generate_deck_faces(
            d01_recipes, "d01", "homogeneous", d01_stocks, d01_writers, d01_faces, d01_cues, base_seed + 100
        )
        decks_gt.append(d01_gt)
        for fname, img in d01_rendered:
            img_io = io.BytesIO()
            img.save(img_io, format="JPEG", quality=80)
            data = img_io.getvalue()
            ti = tarfile.TarInfo(name=f"d01/{fname}")
            ti.size = len(data)
            ti.mtime = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            tar.addfile(ti, io.BytesIO(data))

        # Deck d02: Mixed deck (held out)
        d02_recipes = all_t2_recipes[12:24]
        d02_stocks = [
            stocks["s_t2_parchment"], stocks["s_t2_blue_card"], stocks["s_t2_canary"],
            stocks["s_t2_kraft"], stocks["s_t2_green_ledger"], stocks["s_t2_ivory"],
        ] * 2
        d02_writers = [
            writers["w_t2_fountain_blue"], writers["w_t2_typewriter"], writers["w_t2_ballpoint"],
            writers["w_t2_clipping"], writers["w_t2_fountain_black"], writers["w_t2_fountain_blue"],
        ] * 2
        d02_faces = [1, 2, 3, 2, 4, 1, 2, 3, 2, 4, 1, 2]
        d02_cues = [None, True, False, True, True, None, True, False, True, True, None, True]

        d02_gt, d02_imgs, d02_rendered = generate_deck_faces(
            d02_recipes, "d02", "mixed", d02_stocks, d02_writers, d02_faces, d02_cues, base_seed + 200
        )
        decks_gt.append(d02_gt)
        for fname, img in d02_rendered:
            img_io = io.BytesIO()
            img.save(img_io, format="JPEG", quality=80)
            data = img_io.getvalue()
            ti = tarfile.TarInfo(name=f"d02/{fname}")
            ti.size = len(data)
            ti.mtime = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            tar.addfile(ti, io.BytesIO(data))

        # Deck d03: Near-duplicate recipes (held out)
        d03_recipes = all_t2_recipes[24:34]
        if len(d03_recipes) >= 6:
            d03_recipes[0]["title"] = f"{d03_recipes[0]['title']} (War-Time Version)"
            d03_recipes[1]["title"] = f"{d03_recipes[0]['title'].split(' (')[0]} (Peace-Time Standard)"
            d03_recipes[2]["title"] = f"{d03_recipes[2]['title']} I"
            d03_recipes[3]["title"] = f"{d03_recipes[2]['title'][:-2]} II"
            d03_recipes[4]["title"] = f"Rich {d03_recipes[4]['title']}"
            d03_recipes[5]["title"] = f"Plain {d03_recipes[4]['title'].replace('Rich ', '')}"

        d03_stocks = [stocks["s_t2_ivory"], stocks["s_t2_canary"], stocks["s_t2_parchment"]] * 4
        d03_writers = [writers["w_t2_fountain_black"], writers["w_t2_ballpoint"], writers["w_t2_typewriter"]] * 4
        d03_faces = [1, 2, 2, 3, 2, 3, 2, 4, 2, 1]
        d03_cues = [None, True, False, True, True, False, True, False, True, None]

        d03_gt, d03_imgs, d03_rendered = generate_deck_faces(
            d03_recipes, "d03", "near_duplicate", d03_stocks, d03_writers, d03_faces, d03_cues, base_seed + 300
        )
        decks_gt.append(d03_gt)
        for fname, img in d03_rendered:
            img_io = io.BytesIO()
            img.save(img_io, format="JPEG", quality=80)
            data = img_io.getvalue()
            ti = tarfile.TarInfo(name=f"d03/{fname}")
            ti.size = len(data)
            ti.mtime = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            tar.addfile(ti, io.BytesIO(data))

    # 2. Encrypt images tar archive
    tar_bytes = tar_buf.getvalue()
    tar_enc = encrypt_bytes(tar_bytes)
    (vault_dir / "t2.images.tar.enc").write_bytes(tar_enc)

    # 3. Encrypt GT JSON
    t2_gt = {
        "schema": "rb-gt/1",
        "tier": "T2",
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "generator": {"name": "cardforge", "version": "0.1.0"},
        "decks": decks_gt,
    }
    t2_gt_json = json.dumps(t2_gt, indent=2)
    t2_gt_enc = encrypt_bytes(t2_gt_json)
    (vault_dir / "t2.gt.enc").write_bytes(t2_gt_enc)

    return t2_gt


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for build_synthetic."""
    parser = argparse.ArgumentParser(
        prog="evals.curation.build_synthetic",
        description="Builds synthetic evaluation corpora T1 and T2 per Wave 3 contracts.",
    )
    parser.add_argument(
        "--tier",
        choices=["T1", "T2", "ALL"],
        default="ALL",
        help="Target corpus tier to build (default: ALL)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="External Project Gutenberg cache directory (defaults to $PROJECT_CACHE_DIR)",
    )
    parser.add_argument(
        "--corpora-dir",
        type=Path,
        default=None,
        help="Corpora destination directory (default: evals/corpora)",
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Vault destination directory (default: evals/vault)",
    )
    parser.add_argument(
        "--t1-seed",
        type=int,
        default=50000,
        help="Base random seed for T1 (default: 50000)",
    )
    parser.add_argument(
        "--t2-seed",
        type=int,
        default=70000,
        help="Base random seed for T2 (default: 70000)",
    )

    args = parser.parse_args(argv)

    repo_root = get_repo_root()
    cache_dir = args.cache_dir or get_cache_dir()
    corpora_dir = args.corpora_dir or (repo_root / "evals" / "corpora")
    vault_dir = args.vault_dir or (repo_root / "evals" / "vault")

    corpora_dir.mkdir(parents=True, exist_ok=True)
    vault_dir.mkdir(parents=True, exist_ok=True)

    print(f"RecipeBoxer Eval Curation: building synthetic corpora (Tier={args.tier})")
    print(f"Source cache: {cache_dir}")
    print(f"Vault output: {vault_dir}")

    if args.tier in ("T1", "ALL"):
        print("Building Tier 1 (dev-eval) corpus and ground truth...")
        t1_gt = build_t1(cache_dir, corpora_dir, vault_dir, base_seed=args.t1_seed)
        t1_decks = t1_gt["decks"]
        total_t1_recipes = sum(len(d["recipes"]) for d in t1_decks)
        total_t1_images = sum(len(d["images"]) for d in t1_decks)
        print(f"Tier 1 built successfully: {len(t1_decks)} decks, {total_t1_recipes} recipes, {total_t1_images} card images.")

    if args.tier in ("T2", "ALL"):
        print("Building Tier 2 (held-out) corpus and ground truth...")
        t2_gt = build_t2(cache_dir, vault_dir, base_seed=args.t2_seed)
        t2_decks = t2_gt["decks"]
        total_t2_recipes = sum(len(d["recipes"]) for d in t2_decks)
        total_t2_images = sum(len(d["images"]) for d in t2_decks)
        print(f"Tier 2 built successfully: {len(t2_decks)} decks, {total_t2_recipes} recipes, {total_t2_images} card images.")

    print("Curation build complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
