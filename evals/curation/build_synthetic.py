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
import copy
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
from cardforge.generator import SyntheticCardGenerator, adapt_text_for_font, MissingGlyphError
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


DISTINCTIVE_INGREDIENTS = {
    "apple", "apples", "banana", "beef", "berry", "berries", "cabbage", "carrot", "carrots",
    "cheese", "chicken", "chocolate", "cocoa", "coffee", "corn", "crab", "currant", "currants",
    "date", "dates", "duck", "egg", "eggs", "fig", "figs", "fish", "ginger", "grape", "grapes",
    "ham", "hominy", "lamb", "lemon", "lemons", "lobster", "mushroom", "mushrooms", "mutton",
    "nut", "nuts", "oat", "oats", "oatmeal", "onion", "onions", "orange", "oranges", "oyster",
    "oysters", "pea", "peas", "peach", "peaches", "peanut", "peanuts", "pear", "pears", "pecan",
    "pecans", "pork", "potato", "potatoes", "prune", "prunes", "pumpkin", "raisin", "raisins",
    "raspberry", "rice", "salmon", "shrimp", "spinach", "squash", "strawberry", "strawberries",
    "tea", "tomato", "tomatoes", "turkey", "veal", "walnut", "walnuts"
}


def check_title_body_consistency(
    title: str,
    ingredients: List[str],
    directions: List[str],
) -> Tuple[bool, str]:
    """Audits title/body consistency so recipes never mislabel dishes or ingredients."""
    full_text = (" ".join(ingredients) + " " + " ".join(directions)).lower()
    title_words = re.findall(r"[a-zA-Z]+", title.lower())

    # 1. Distinctive ingredient check
    for w in title_words:
        if w in DISTINCTIVE_INGREDIENTS:
            stem = w[:-1] if w.endswith("s") else w
            if stem not in full_text:
                return (
                    False,
                    f"Distinctive ingredient '{w}' from title '{title}' not found in ingredients or directions.",
                )

    # 2. General title relevance check: at least one substantial title word must appear in body
    stop_words = {
        "recipe", "recipes", "style", "with", "from", "first", "second", "modern", "standard",
        "version", "part", "warm", "cold", "rich", "plain", "best", "good", "quick",
        "easy", "homemade", "simple", "traditional", "baked", "boiled", "fried",
        "roast", "roasted", "steamed", "fresh", "sweet", "sour", "card", "back", "front",
    }
    substantive = [w for w in title_words if len(w) >= 4 and w not in stop_words]
    if substantive:
        stems = [w[:-1] if w.endswith("s") else w for w in substantive]
        if not any(s in full_text for s in stems):
            return (
                False,
                f"No substantive title words from '{title}' found in ingredients or directions.",
            )

    return True, ""


def make_near_duplicate_variant(
    base_recipe: Dict[str, Any],
    variant_title: str,
    variant_type: str = "numeric",
    seed: int = 42,
) -> Dict[str, Any]:
    """Synthesizes a genuine near-duplicate recipe variant with matching title and body."""
    rng = random.Random(seed)
    v = copy.deepcopy(base_recipe)
    v["title"] = variant_title
    ings = list(v.get("ingredients", []))
    dirs = list(v.get("directions", []))

    new_ings = []
    modified_ing = False
    for ig in ings:
        if not modified_ing and any(k in ig.lower() for k in ("sugar", "butter", "milk", "water", "flour")):
            if "1/3" in ig or "⅓" in ig:
                new_ings.append(ig.replace("1/3", "1/2").replace("⅓", "½"))
                modified_ing = True
                continue
            elif "1/4" in ig or "¼" in ig:
                new_ings.append(ig.replace("1/4", "1/3").replace("¼", "⅓"))
                modified_ing = True
                continue
            elif "1/2" in ig or "½" in ig:
                new_ings.append(ig.replace("1/2", "3/4").replace("½", "¾"))
                modified_ing = True
                continue
            elif "1 cup" in ig.lower():
                new_ings.append(ig.replace("1 cup", "1¼ cups").replace("1 Cup", "1¼ Cups"))
                modified_ing = True
                continue
        new_ings.append(ig)

    # Add or tweak subtle flavoring
    if variant_type in ("numeric", "modern", "rich"):
        if not any("vanilla" in ig.lower() or "nutmeg" in ig.lower() or "cinnamon" in ig.lower() for ig in ings):
            new_ings.append("½ teaspoon vanilla extract" if rng.random() < 0.5 else "¼ teaspoon grated nutmeg")
    elif variant_type == "peacetime":
        new_ings = [ig.replace("corn syrup", "sugar").replace("shortening", "butter") for ig in new_ings]

    v["ingredients"] = new_ings
    v["structured_ingredients"] = [parse_ingredient_line(ig) for ig in new_ings]

    # Adjust cooking time slightly
    new_dirs = []
    for d in dirs:
        if "minutes" in d.lower():
            new_dirs.append(re.sub(r'(\d+)\s+minutes', lambda m: f"{int(m.group(1)) + 5} minutes", d))
        else:
            new_dirs.append(d)
    v["directions"] = new_dirs

    # Assert consistency of generated variant
    ok, err = check_title_body_consistency(v["title"], v["ingredients"], v["directions"])
    if not ok:
        raise ValueError(f"Generated near-duplicate variant failed consistency: {err}")

    return v


def is_candidate_recipe_title(cand_title: str) -> bool:
    """Filters candidate title strings to reject captions, metadata, and non-recipes."""
    if not (3 <= len(cand_title) <= 50):
        return False
    clean = cand_title.strip()
    if clean.startswith("[") or clean.endswith("]"):
        return False
    if "Illustration" in clean or "Page " in clean or "_Page" in clean or "Fig." in clean:
        return False
    if clean.endswith(":") or clean.endswith(";") or clean.endswith(","):
        return False
    upper_bad = (
        "CHAPTER", "INDEX", "PART ", "TABLE", "RULES", "FOOD", "NOTE",
        "CONTENTS", "PREFACE", "MENUS", "BILL OF FARE", "INTRODUCTION"
    )
    if any(clean.upper().startswith(p) for p in upper_bad):
        return False
    lower_bad = (
        "ration", "require", "stand until", "let stand", "digestive", "diet",
        "illustration", "servings", "calories", "nutritive"
    )
    if any(p in clean.lower() for p in lower_bad):
        return False
    return True


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
        if len(lines) == 1 and is_candidate_recipe_title(cand_title):
            next_b = blocks[i+1].strip()
            next_lines = [l.strip() for l in next_b.splitlines() if l.strip()]
            ings = [l for l in next_lines if qty_re.match(l) or fraction_re.match(l)]

            if 3 <= len(ings) <= 12:
                dirs = []
                j = i + 2
                while j < min(i + 6, len(blocks)):
                    dir_b = blocks[j].strip()
                    if not dir_b:
                        j += 1
                        continue
                    dir_lines = [l.strip() for l in dir_b.splitlines() if l.strip()]
                    # Stop if this block is the candidate title of the NEXT recipe
                    if len(dir_lines) == 1 and is_candidate_recipe_title(dir_lines[0]):
                        if j + 1 < len(blocks):
                            next_next = [l.strip() for l in blocks[j+1].splitlines() if l.strip()]
                            if sum(1 for l in next_next if qty_re.match(l) or fraction_re.match(l)) >= 3:
                                break
                    if sum(1 for l in dir_lines if qty_re.match(l)) > 2:
                        break
                    # Clean out illustration captions and page notes
                    clean_lines = [
                        l for l in dir_lines
                        if not l.startswith("[") and "Illustration" not in l and "_Page" not in l
                    ]
                    if clean_lines:
                        dirs.append(' '.join(clean_lines))
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

                    # Validate title/body consistency
                    ok, _ = check_title_body_consistency(t_clean, ings, step_list)
                    if ok:
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
        # 1. Assert title/body consistency
        ok, reason = check_title_body_consistency(
            r_data["title"], r_data["ingredients"], r_data["directions"]
        )
        if not ok:
            raise ValueError(f"Deck '{deck_id}' recipe '{r_data['title']}' failed consistency: {reason}")

        seed_val = base_seed + (r_idx * 17)
        gen = SyntheticCardGenerator(seed=seed_val)
        stock = stock_mapping[r_idx % len(stock_mapping)]
        writer = writer_mapping[r_idx % len(writer_mapping)]
        n_faces = face_counts[r_idx % len(face_counts)]
        cue = cue_flags[r_idx % len(cue_flags)]

        # Unique opaque image filenames (deterministic from base seed)
        face_ids = [f"{rng.getrandbits(48):012x}.jpg" for _ in range(n_faces)]
        rec_id = f"r{r_idx + 1:03d}"

        # 2. Adapt text for writer font so rendered text and GT never silently drop characters
        font_style = writer.font_style
        adapted_title = adapt_text_for_font(r_data["title"], font_style)
        adapted_ings = [adapt_text_for_font(ig, font_style) for ig in r_data["ingredients"]]
        adapted_dirs = [adapt_text_for_font(d, font_style) for d in r_data["directions"]]
        adapted_structured_ings = [parse_ingredient_line(ig) for ig in adapted_ings]

        rec_obj = RecipeSchema(
            recipe_id=rec_id,
            title=adapted_title,
            yield_servings=r_data.get("yield", "4 servings"),
            total_time="45 mins",
            ingredients=adapted_ings,
            directions=adapted_dirs,
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
                "title": adapted_title,
                "yield": r_data.get("yield", None),
                "source_note": r_data.get("source_note", None),
                "ingredients": adapted_structured_ings,
                "directions": adapted_dirs,
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
    base_seed: int = 55000,
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

    # Deck d03: Near-duplicate recipes (genuine variants of same dish)
    # 10 recipes
    d03_pool = all_t1_recipes[24:34]
    d03_recipes = []
    # Pair 1: Dish variant I & II
    rec0 = copy.deepcopy(d03_pool[0])
    rec0["title"] = f"{rec0['title']} I"
    rec1 = make_near_duplicate_variant(d03_pool[0], f"{d03_pool[0]['title']} II", "numeric", seed=base_seed + 301)
    d03_recipes.extend([rec0, rec1])

    # Pair 2: Dish variant I & II
    rec2 = copy.deepcopy(d03_pool[1])
    rec2["title"] = f"{rec2['title']} I"
    rec3 = make_near_duplicate_variant(d03_pool[1], f"{d03_pool[1]['title']} II", "numeric", seed=base_seed + 302)
    d03_recipes.extend([rec2, rec3])

    # Pair 3: Dish (Old-Style) & (Modern)
    rec4 = copy.deepcopy(d03_pool[2])
    rec4["title"] = f"{rec4['title']} (Old-Style)"
    rec5 = make_near_duplicate_variant(d03_pool[2], f"{d03_pool[2]['title']} (Modern)", "modern", seed=base_seed + 303)
    d03_recipes.extend([rec4, rec5])

    # Remaining 4 recipes
    for rem in d03_pool[3:7]:
        d03_recipes.append(copy.deepcopy(rem))

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
                "recipe_data": {
                    "title": d04_gt["recipes"][idx]["structured"]["title"],
                    "yield": d04_gt["recipes"][idx]["structured"]["yield"],
                    "ingredients": [ig["raw"] for ig in d04_gt["recipes"][idx]["structured"]["ingredients"]],
                    "directions": d04_gt["recipes"][idx]["structured"]["directions"],
                    "source_note": d04_gt["recipes"][idx]["structured"]["source_note"],
                },
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
    base_seed: int = 75000,
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

        # Deck d03: Near-duplicate recipes (genuine variants of same dish)
        d03_pool = all_t2_recipes[24:34]
        d03_recipes = []
        # Pair 1: War-Time Version vs Peace-Time Standard
        rec0 = copy.deepcopy(d03_pool[0])
        rec0["title"] = f"{rec0['title']} (War-Time Version)"
        rec1 = make_near_duplicate_variant(d03_pool[0], f"{d03_pool[0]['title']} (Peace-Time Standard)", "peacetime", seed=base_seed + 301)
        d03_recipes.extend([rec0, rec1])

        # Pair 2: Variant I & II
        rec2 = copy.deepcopy(d03_pool[1])
        rec2["title"] = f"{rec2['title']} I"
        rec3 = make_near_duplicate_variant(d03_pool[1], f"{d03_pool[1]['title']} II", "numeric", seed=base_seed + 302)
        d03_recipes.extend([rec2, rec3])

        # Pair 3: Rich vs Plain
        rec4 = copy.deepcopy(d03_pool[2])
        rec4["title"] = f"Rich {d03_pool[2]['title']}"
        rec5 = make_near_duplicate_variant(d03_pool[2], f"Plain {d03_pool[2]['title']}", "plain", seed=base_seed + 303)
        d03_recipes.extend([rec4, rec5])

        for rem in d03_pool[3:7]:
            d03_recipes.append(copy.deepcopy(rem))

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
        default=55000,
        help="Base random seed for T1 (default: 55000)",
    )
    parser.add_argument(
        "--t2-seed",
        type=int,
        default=75000,
        help="Base random seed for T2 (default: 75000)",
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
