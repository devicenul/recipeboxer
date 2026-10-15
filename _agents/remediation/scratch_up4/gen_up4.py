"""WP-UP4 hard 'manuscript-style' deck generator (fresh seeds; outputs under $PCD/up4/decks).

Simulates photographed pages of early-modern household receipt books (NOT cards):
- prose receipts ("To make a Tansey. Take ... "), no ingredient lists, headings inline or in the margin,
- dense period-style script fonts on aged paper, long portrait pages,
- several 'books' per deck (different paper tint / hand) or one book,
- mostly one receipt per page, some pages ending mid-receipt with the text continuing on the next page
  (and the next receipt starting on the same page), non-recipe distractor pages (accounts, index, notes,
  pen trials), and same-dish-different-receipt pairs ("To make a Cake" twice).
Gold partition = chains of pages connected by text flow (manifest.true_links lists the flow edges).
Vocabulary is procedural; no T1-T4 content.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFilter

from cardforge.generator import apply_ink_fade, apply_rotation_skew, create_paper_substrate, get_font
from cardforge.models import PaperSubstrate, RulingStyle

PCD = Path(os.environ.get("HOME", "/home/droid")) / ".cache" / "projects" / "recipeboxer-ba754a91"
DECKS_DIR = PCD / "up4" / "decks"

DISHES = [
    "a Tansey", "a Sack Posset", "Pancakes", "a Cake", "Shrewsbury Cakes", "a Seed Cake", "Jumbals", "Macaroons",
    "a Hare Pie", "a Venison Pasty", "Mince Pies", "a Florentine", "Collops", "a Fricassee of Chickens",
    "Marchpane", "Quince Marmalade", "Conserve of Roses", "Syrup of Violets", "Cowslip Wine", "Mead",
    "Metheglin", "Gooseberry Wine", "a Hasty Pudding", "a Rice Pudding", "a Sagoe Pudding", "Black Puddings",
    "a Cheesecake", "Wafers", "Gingerbread", "Biskets", "Orange Chips", "Candied Angelica", "Pickled Cucumbers",
    "Pickled Walnuts", "Potted Beef", "a Calf's Foot Jelly", "Hartshorn Jelly", "Lemon Cream", "a Syllabub",
    "Snow Cream", "a Plague Water", "a Surfeit Water", "a Salve for a Burn", "a Cordial Water",
]
OPENERS = ["To make {d}", "To make {d} the best way", "How to make {d}", "{D}", "Another way to make {d}",
           "To make {d} my Lady's way", "For {d}", "A Receipt for {d}"]
TAKES = [
    "Take a pound of fine flower and a pound of sugar beaten and searced",
    "Take a quart of cream and the yolks of eight eggs well beaten",
    "Take two quarts of new milk and set it on the fire",
    "Take three pounds of the best butter and wash it in rose water",
    "Take a peck of {f} and pick them clean",
    "Take a leg of veal and cut it in thin slices",
    "Take the whites of six eggs and beat them to a froth",
    "Take a gallon of spring water and boil it an hour",
    "Take half a pound of almonds blanched and beaten small",
    "Take a good handful of {h} and shred it very small",
]
MIDS = [
    "then put in a little nutmeg grated and some mace",
    "season it with salt and pepper and a little cloves beaten",
    "stir it continually till it be thick then take it off the fire",
    "put in a spoonful of rose water and as much sack",
    "let it stand all night covered close",
    "strain it through a hair sieve into a clean pan",
    "then boil it softly till it come to a candy height",
    "add a pint of ale yeast and set it before the fire to rise",
    "butter your dish well and lay in a sheet of paste",
    "then put it into a slow oven and let it bake an hour",
    "skim it as often as any scum doth rise",
    "and when it is cold put it up in glasses",
    "beat it in a stone mortar the space of an hour",
    "then lay it upon papers and dry it in a stove",
    "put in the {f} and let them boil a walm or two",
    "and squeeze in the juice of two lemons",
    "and stop it close with a cork and keep it for use",
]
ENDS = ["and so serve it to the table", "and it will keep all the year", "this is very good",
        "probatum est", "and so use it", "and it is done", "serve it hot with sugar strewed over it"]
FRUITS = ["quinces", "gooseberries", "cowslips", "damsons", "currants", "cherries", "walnuts", "violets"]
HERBS = ["sage", "parsley", "thyme", "tansy", "marjoram", "rosemary", "balm", "mint"]

DISTRACTORS = [
    ["Laid out for the house", "Item for candles  0 - 3 - 4", "Item for soap  0 - 1 - 6", "Item to the carrier  0 - 2 - 0",
     "Item for sugar  0 - 5 - 2", "Item for wages  1 - 10 - 0", "Sum  2 - 2 - 0"],
    ["The Table", "Almond cakes .... 3", "Biskets .... 5", "Cordial water .... 8", "Hare pie .... 11",
     "Jelly of hartshorn .... 14", "Marmalade .... 17", "Pudding .... 21"],
    ["Mary Hartley her book", "Anno Domini 1672", "", "God give her grace", "therein to look"],
    ["Received of Mr. Coles", "the sum of four pounds", "for half a year's rent", "due at Michaelmas last", "by me  J. W."],
    ["pen pen pen  my pen is bad", "ink and paper", "A B C D E F G", "Honour thy father and thy mother"],
    ["Linen sent to wash", "6 smocks", "4 shifts", "12 napkins", "2 table cloths", "3 aprons", "pillow bears 4"],
    ["Dear Sister", "I have sent you by the bearer", "the receipt you desired", "and pray let me hear", "how you do. Yours"],
    ["Memorandum", "the brown cow calved the 3rd of May", "sowed the barley field", "paid the smith"],
]

BOOKS = [  # paper tint, ink, font, size
    ((236, 220, 186), (58, 40, 25), "script", 25),
    ((228, 214, 178), (40, 30, 30), "caveat", 27),
    ((242, 230, 200), (75, 52, 30), "script", 23),
    ((222, 205, 168), (30, 25, 20), "caveat", 25),
    ((240, 226, 196), (90, 60, 40), "serif_italic", 21),
]


def receipt(rng: random.Random, dish: str) -> Tuple[str, str]:
    op = rng.choice(OPENERS)
    title = op.format(d=dish, D=dish[0].upper() + dish[1:])
    parts = [rng.choice(TAKES)]
    for _ in range(rng.randint(3, 9)):
        parts.append(rng.choice(MIDS))
    if rng.random() < 0.7:
        parts.append(rng.choice(ENDS))
    body = ", ".join(parts).replace("{f}", rng.choice(FRUITS)).replace("{h}", rng.choice(HERBS)) + "."
    return title, body


def wrap(text: str, font, width: int) -> List[str]:
    out, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if font.getlength(t) <= width:
            cur = t
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def render_page(rng: random.Random, book, items: List[Tuple[str, str]], W: int, H: int, margin_titles: bool) -> Image.Image:
    """items: list of ('title'|'body'|'plain', text) rendered top to bottom."""
    tint, ink, font_name, size = book
    sub_rng = random.Random(rng.random())
    img, _ = create_paper_substrate(W, H, PaperSubstrate.AGED_YELLOW, sub_rng, ruling=RulingStyle.UNRULED,
                                    base_color_override=tuple(max(0, c + rng.randint(-6, 6)) for c in tint))
    img = img.convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    font = get_font(font_name, size=size)
    tfont = get_font(font_name, size=int(size * 1.15))
    lh = int(size * 1.35)
    left = int(W * (0.24 if margin_titles else 0.09))
    right = W - int(W * 0.07)
    y = int(H * 0.06)
    for kind, text in items:
        if kind == "title":
            if margin_titles:
                for k, ln in enumerate(wrap(text, font, int(W * 0.15))[:4]):
                    d.text((int(W * 0.04), y + k * lh), ln, fill=(*ink, 235), font=font)
            else:
                ln = text if tfont.getlength(text) < right - left else text[:40]
                d.text((left + int((right - left - tfont.getlength(ln)) / 2), y), ln, fill=(*ink, 240), font=tfont)
                y += int(lh * 1.3)
            continue
        for ln in wrap(text, font, right - left):
            if y > H - int(H * 0.06):
                break
            d.text((left + rng.randint(-3, 3), y + rng.randint(-1, 1)), ln, fill=(*ink, rng.randint(200, 250)), font=font)
            y += lh
        y += int(lh * 0.4)
    img = img.convert("RGB")
    img = apply_ink_fade(img, rng.uniform(0.05, 0.3), sub_rng)
    if rng.random() < 0.5:
        img = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.3, 0.9)))
    img = apply_rotation_skew(img, rng.uniform(-2.0, 2.0))
    return img


def lines_capacity(book, W: int, H: int) -> int:
    size = book[3]
    return int((H * 0.88) / (size * 1.35))


def build_deck(name: str, seed: int, n_target: int, n_books: int, p_flow: float, p_distractor: float,
               p_same_dish: float, W: int = 900, H: int = 1300) -> Path:
    out = DECKS_DIR / name
    if (out / "manifest.json").exists():
        return out
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    books = rng.sample(BOOKS, n_books)
    pages: List[Dict[str, Any]] = []  # {id, img, book}
    links: List[Tuple[str, str]] = []
    distractor_ids: set = set()
    multi_ids: set = set()
    used = []

    def new_id() -> str:
        return "%012x.jpg" % rng.getrandbits(48)

    while len(pages) < n_target:
        book = rng.choice(books)
        bi = BOOKS.index(book)
        margin = (bi % 2 == 1)
        if rng.random() < p_distractor:
            lines = rng.choice(DISTRACTORS)
            items = [("plain", ln) for ln in lines if ln]
            pid = new_id()
            pages.append({"id": pid, "img": render_page(rng, book, items, W, H, False), "book": bi})
            distractor_ids.add(pid)
            continue
        if used and rng.random() < p_same_dish:
            dish = rng.choice(used)
        else:
            dish = rng.choice(DISHES)
        used.append(dish)
        title, body = receipt(rng, dish)
        font = get_font(book[2], size=book[3])
        left_frac = 0.24 if margin else 0.09
        width = int(W * (1 - left_frac - 0.07))
        wl = wrap(body, font, width)
        cap = lines_capacity(book, W, H) - 2
        if rng.random() < p_flow and len(pages) + 2 <= n_target:
            # Flowing run: receipt A starts on page 1 and is cut mid-sentence; page 2 carries the rest of A
            # and (often) the start of receipt B, which may itself continue on page 3.
            title2, body2 = receipt(rng, rng.choice(DISHES))
            wl2 = wrap(body2, font, width)
            # make A long enough to overflow page 1
            while len(wl) <= cap:
                _, more = receipt(rng, dish)
                body = body[:-1] + ", " + more[0].lower() + more[1:]
                wl = wrap(body, font, width)
            p1_lines, rest = wl[:cap], wl[cap:]
            p1 = render_page(rng, book, [("title", title), ("body", " ".join(p1_lines))], W, H, margin)
            id1 = new_id()
            items2 = [("body", " ".join(rest))]
            three = rng.random() < 0.35 and len(pages) + 3 <= n_target
            room = cap - len(rest) - 2
            if three:
                while len(wl2) <= room + 2:
                    _, more = receipt(rng, rng.choice(DISHES))
                    body2 = body2[:-1] + ", " + more[0].lower() + more[1:]
                    wl2 = wrap(body2, font, width)
                items2 += [("title", title2), ("body", " ".join(wl2[:max(1, room)]))]
            elif rng.random() < 0.6 and room > 4:
                items2 += [("title", title2), ("body", " ".join(wl2[:room]))]  # complete second receipt
            p2 = render_page(rng, book, items2, W, H, margin)
            id2 = new_id()
            pages += [{"id": id1, "img": p1, "book": bi}, {"id": id2, "img": p2, "book": bi}]
            links.append((id1, id2))
            if len(items2) > 1:
                multi_ids.add(id2)
            if three:
                p3 = render_page(rng, book, [("body", " ".join(wl2[max(1, room):]))], W, H, margin)
                id3 = new_id()
                pages.append({"id": id3, "img": p3, "book": bi})
                links.append((id2, id3))
            continue
        # single receipt page (truncate to capacity so it is complete); sometimes two short receipts on one page
        items = [("title", title), ("body", " ".join(wl[: cap - 1]))]
        pid = new_id()
        if len(wl) < cap // 2 and rng.random() < 0.5:
            t3, b3 = receipt(rng, rng.choice(DISHES))
            items += [("title", t3), ("body", " ".join(wrap(b3, font, width)[: cap - len(wl) - 3]))]
            multi_ids.add(pid)
        pages.append({"id": pid, "img": render_page(rng, book, items, W, H, margin), "book": bi})

    for p in pages:
        p["img"].save(out / p["id"], "JPEG", quality=85)
    succ = {a: b for a, b in links}
    has_pred = {b for _, b in links}
    recipes = []
    for p in pages:
        if p["id"] in has_pred:
            continue
        chain = [p["id"]]
        while chain[-1] in succ:
            chain.append(succ[chain[-1]])
        recipes.append({"recipe_id": f"r{len(recipes) + 1:03d}", "faces": chain,
                        "is_distractor": p["id"] in distractor_ids})
    cards = [{"card_id": p["id"], "is_distractor": p["id"] in distractor_ids, "multi_recipe_page": p["id"] in multi_ids}
             for p in pages]
    rng.shuffle(cards)
    man = {"deck": name, "seed": seed, "deck_type": "ms_style", "n_cards": len(pages), "cards": cards,
           "recipes": recipes, "true_links": [list(e) for e in links]}
    (out / "manifest.json").write_text(json.dumps(man, indent=1), encoding="utf-8")
    print(f"{name}: {len(pages)} pages, {len(recipes)} clusters, links={len(links)}, distractors={len(distractor_ids)}")
    return out


DECK_SPECS: Dict[str, Dict[str, Any]] = {
    # fit (41xx)
    "fit_ms_mixed": dict(seed=4101, n_target=30, n_books=4, p_flow=0.15, p_distractor=0.15, p_same_dish=0.25),
    "fit_ms_book": dict(seed=4102, n_target=30, n_books=1, p_flow=0.3, p_distractor=0.1, p_same_dish=0.3),
    "fit_ms_flow1": dict(seed=4103, n_target=36, n_books=3, p_flow=0.45, p_distractor=0.1, p_same_dish=0.3),
    "fit_ms_flow2": dict(seed=4104, n_target=36, n_books=1, p_flow=0.45, p_distractor=0.1, p_same_dish=0.3),
    # held-out (42xx)
    "ho_ms_mixed": dict(seed=4201, n_target=30, n_books=4, p_flow=0.12, p_distractor=0.15, p_same_dish=0.25),
    "ho_ms_book": dict(seed=4202, n_target=30, n_books=1, p_flow=0.3, p_distractor=0.1, p_same_dish=0.3),
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--decks", default=",".join(DECK_SPECS))
    a = ap.parse_args()
    for dn in a.decks.split(","):
        build_deck(dn, **DECK_SPECS[dn])
