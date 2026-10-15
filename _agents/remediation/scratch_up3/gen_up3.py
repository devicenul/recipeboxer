"""WP-UP3 fresh deck generator (hard, realistic, seeded; outputs under $PCD/up3/decks).

Recipes are synthesized procedurally from small vocabularies (no T1-T4 content, no gutenberg text),
rendered with cardforge using ONE stock + ONE writer for homogeneous decks. Capture order is shuffled
and image ids are opaque (seeded hex). Distractor (non-recipe) pages are rendered on the same stock.

manifest.json schema (GT, used only by the dev harness):
  {deck, seed, deck_type, cards:[{card_id, recipe_id, face_index, total_faces, is_distractor}],
   recipes:[{recipe_id, title, ingredients, directions, faces:[card_id...], is_distractor}]}
"""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import ImageDraw

from cardforge.generator import (
    SyntheticCardGenerator,
    apply_ink_fade,
    apply_jpeg_noise,
    apply_rotation_skew,
    create_paper_substrate,
    get_font,
    render_handwritten_lines,
    render_typewritten_lines,
)
from cardforge.models import (
    CardModality,
    CardStock,
    CardWriter,
    DegradationConfig,
    DegradationLevel,
    InkType,
    PaperSubstrate,
    RecipeSchema,
    RulingStyle,
)

PCD = Path(os.environ.get("HOME", "/home/droid")) / ".cache" / "projects" / "recipeboxer-ba754a91"
DECKS_DIR = PCD / "up3" / "decks"

# ----------------------------------------------------------------------------------------------
# Procedural recipe synthesis
# ----------------------------------------------------------------------------------------------
PEOPLE = ["Aunt Ruth", "Grandma Lou", "Mrs. Keller", "Edna", "Mother", "Cousin Fay", "Mrs. Olson",
          "Hazel", "Aunt Bea", "Nana", "Doris", "Mrs. Pruitt", "Irene", "Aunt Vi", "Gladys"]
CATS: Dict[str, Dict[str, Any]] = {
    "cake": {
        "names": ["Spice Cake", "Applesauce Cake", "Chocolate Cake", "Pound Cake", "Date Nut Cake",
                  "Orange Cake", "Coconut Cake", "Jam Cake", "Prune Cake", "Banana Cake", "Sour Cream Cake",
                  "Gingerbread", "Carrot Cake", "Burnt Sugar Cake", "Oatmeal Cake", "Mocha Cake"],
        "core": ["2 cups sifted flour", "1 1/2 cups sugar", "1/2 cup butter", "2 eggs", "1 tsp. soda",
                 "1 cup buttermilk", "1/2 tsp. salt", "1 tsp. vanilla"],
        "extra": ["1 tsp. cinnamon", "1/2 tsp. cloves", "1/2 tsp. nutmeg", "1 cup chopped dates",
                  "1 cup chopped walnuts", "1 cup raisins", "2 squares chocolate", "1 cup applesauce",
                  "grated rind of 1 orange", "1 cup shredded coconut", "1/2 cup strong coffee",
                  "1 cup mashed banana", "1 cup grated carrots", "1/2 cup shortening", "1 cup quick oats"],
        "steps": ["Cream the butter and sugar until light.", "Add the eggs one at a time, beating well.",
                  "Sift the flour with the soda and salt.", "Add dry ingredients alternately with the buttermilk.",
                  "Fold in the {x}.", "Stir in the vanilla.", "Pour into a greased 9 x 13 pan.",
                  "Bake at 350 degrees for 35 to 40 minutes.", "Cool in the pan 10 minutes before turning out.",
                  "Frost with a simple butter icing when cold.", "Dust the {x} with a little flour first so it does not sink."],
    },
    "cookie": {
        "names": ["Molasses Cookies", "Sugar Cookies", "Oatmeal Drops", "Snickerdoodles", "Ice Box Cookies",
                  "Peanut Butter Cookies", "Hermits", "Brownies", "Lemon Bars", "Ginger Snaps", "Date Bars",
                  "Butterscotch Squares", "Rocks", "Thumbprints"],
        "core": ["1 cup shortening", "1 cup brown sugar", "1 egg", "2 1/4 cups flour", "1 tsp. soda",
                 "1/2 tsp. salt"],
        "extra": ["1/2 cup molasses", "2 tsp. ginger", "1 cup peanut butter", "2 cups rolled oats",
                  "1 cup chocolate bits", "1 cup chopped pecans", "juice of 1 lemon", "1 cup chopped dates",
                  "1 tsp. cream of tartar", "2 tbsp. cinnamon sugar", "1/2 cup currants", "1 tsp. vanilla"],
        "steps": ["Cream shortening and sugar.", "Beat in the egg.", "Sift flour, soda and salt together and add.",
                  "Mix in the {x}.", "Chill the dough at least one hour.", "Roll into balls the size of a walnut.",
                  "Roll the balls in the {x}.", "Place 2 inches apart on a greased sheet.",
                  "Bake at 375 degrees 10 to 12 minutes.", "Press flat with a fork dipped in sugar.",
                  "Cut into squares while still warm.", "Store in a tin with waxed paper between layers."],
    },
    "pie": {
        "names": ["Lemon Meringue Pie", "Pumpkin Pie", "Rhubarb Pie", "Pecan Pie", "Sour Cherry Pie",
                  "Custard Pie", "Mincemeat Pie", "Shoofly Pie", "Butterscotch Pie", "Gooseberry Pie",
                  "Sweet Potato Pie", "Chess Pie", "Peach Cobbler", "Apple Crumb Pie"],
        "core": ["1 unbaked 9 inch pie shell", "1 cup sugar", "3 eggs", "2 tbsp. butter", "pinch of salt"],
        "extra": ["2 cups cooked pumpkin", "3 cups cut rhubarb", "1 cup dark corn syrup", "1 1/2 cups pecans",
                  "4 cups pitted sour cherries", "2 cups scalded milk", "1 tsp. nutmeg", "1/2 cup molasses",
                  "3 tbsp. cornstarch", "juice and rind of 2 lemons", "6 tart apples, sliced", "1 cup evaporated milk",
                  "1/2 tsp. ginger", "2 cups mashed sweet potato"],
        "steps": ["Line the pan with pastry and flute the edge.", "Beat the eggs slightly.",
                  "Combine the sugar, salt and {x}.", "Pour into the pie shell.",
                  "Dot with the butter.", "Bake at 425 degrees for 15 minutes.",
                  "Reduce heat to 350 and bake 30 minutes longer or until a knife comes out clean.",
                  "Cook the {x} in a double boiler until thick, stirring constantly.",
                  "Beat the whites stiff with 6 tbsp. sugar for the meringue.",
                  "Spread meringue to the edges and brown lightly.", "Cool before cutting."],
    },
    "bread": {
        "names": ["Oatmeal Bread", "Parker House Rolls", "Cinnamon Rolls", "Corn Bread", "Brown Bread",
                  "Zucchini Bread", "Potato Rolls", "Biscuits", "Date Nut Loaf", "Cranberry Bread",
                  "Sally Lunn", "Spoon Bread", "Muffins", "Popovers"],
        "core": ["1 pkg. dry yeast", "1/4 cup warm water", "1 cup scalded milk", "2 tbsp. sugar",
                 "1 tsp. salt", "3 1/2 cups flour"],
        "extra": ["1 cup rolled oats", "1/2 cup molasses", "1 cup mashed potatoes", "1 cup yellow cornmeal",
                  "2 cups grated zucchini", "1 cup chopped cranberries", "1/4 cup melted butter",
                  "2 tsp. cinnamon", "1 cup chopped nuts", "2 eggs, beaten", "1 tbsp. baking powder"],
        "steps": ["Dissolve yeast in the warm water.", "Cool the milk to lukewarm and add sugar and salt.",
                  "Stir in the {x}.", "Add flour gradually to make a soft dough.",
                  "Knead on a floured board until smooth and elastic.", "Let rise in a warm place until double.",
                  "Punch down and shape into loaves.", "Brush the tops with the {x}.",
                  "Bake at 375 degrees about 45 minutes.", "Turn out on a rack to cool.",
                  "Grease the muffin tins well.", "Fill pans two-thirds full."],
    },
    "main": {
        "names": ["Pot Roast", "Meat Loaf", "Chicken and Dumplings", "Ham Loaf", "Beef Stew",
                  "Tuna Casserole", "Stuffed Peppers", "Pork Chops and Rice", "Chicken Pie", "Swiss Steak",
                  "Salmon Loaf", "Goulash", "Scalloped Potatoes and Ham", "Chili Con Carne", "Hot Dish"],
        "core": ["2 lbs. ground beef", "1 onion, chopped", "1 tsp. salt", "1/4 tsp. pepper"],
        "extra": ["1 cup cracker crumbs", "1 can tomato soup", "1 cup milk", "2 eggs", "1 green pepper, diced",
                  "3 carrots, sliced", "4 potatoes, quartered", "1 cup raw rice", "1 can mushroom soup",
                  "1 can red kidney beans", "2 cups cooked noodles", "1/2 cup grated cheese",
                  "1 stalk celery, chopped", "2 tbsp. flour", "1 bay leaf"],
        "steps": ["Brown the meat in a heavy skillet.", "Add the onion and cook until soft.",
                  "Season with salt and pepper.", "Stir in the {x}.", "Turn into a greased casserole.",
                  "Cover and bake at 350 degrees for 1 hour.", "Add the {x} the last 30 minutes.",
                  "Thicken the gravy with flour mixed in cold water.", "Top with the {x} and return to oven.",
                  "Simmer slowly 2 to 3 hours.", "Serve hot with boiled potatoes."],
    },
    "preserve": {
        "names": ["Bread and Butter Pickles", "Strawberry Jam", "Chow Chow", "Pepper Relish", "Apple Butter",
                  "Grape Jelly", "Watermelon Rind Pickles", "Corn Relish", "Peach Preserves", "Dill Pickles",
                  "Green Tomato Mincemeat", "Pickled Beets"],
        "core": ["4 cups sugar", "2 cups vinegar", "1/4 cup salt"],
        "extra": ["4 qts. sliced cucumbers", "2 qts. hulled berries", "1 head cabbage, chopped", "12 green peppers",
                  "1 tsp. mustard seed", "1 tsp. celery seed", "1 tsp. turmeric", "1 box pectin",
                  "6 large onions", "12 ears of corn", "2 sticks cinnamon", "1 tbsp. whole cloves",
                  "dill heads", "5 lbs. green tomatoes"],
        "steps": ["Wash and prepare the fruit.", "Let stand in salt overnight, then drain.",
                  "Bring the vinegar and sugar to a boil.", "Add the {x} and spices.",
                  "Boil hard 1 minute, stirring constantly.", "Skim off foam.",
                  "Pack in hot sterilized jars.", "Seal at once with paraffin or lids.",
                  "Process in a boiling water bath 10 minutes.", "Tie the {x} in a cheesecloth bag."],
    },
    "salad": {
        "names": ["Lime Jello Salad", "Waldorf Salad", "Potato Salad", "Three Bean Salad", "Cole Slaw",
                  "Frozen Fruit Salad", "Cranberry Salad", "Macaroni Salad", "Seven Layer Salad",
                  "Perfection Salad", "Ambrosia", "Cucumber Salad"],
        "core": ["1 pkg. gelatin", "1 cup boiling water", "1/2 cup mayonnaise"],
        "extra": ["1 cup crushed pineapple", "1 cup cottage cheese", "2 apples, diced", "1/2 cup chopped celery",
                  "6 boiled potatoes", "1 can green beans", "1 can wax beans", "1 small head cabbage",
                  "1 cup miniature marshmallows", "1 cup cranberries, ground", "2 cups cooked macaroni",
                  "2 hard boiled eggs", "1/2 cup sour cream", "1 tbsp. vinegar", "1 tsp. prepared mustard"],
        "steps": ["Dissolve the gelatin in boiling water.", "Chill until partly set.", "Fold in the {x}.",
                  "Pour into a ring mold.", "Chill until firm.", "Combine the {x} lightly.",
                  "Mix the mayonnaise with the vinegar and mustard.", "Toss with the dressing.",
                  "Unmold on lettuce leaves.", "Garnish with paprika.", "Keeps well 2 or 3 days in the icebox."],
    },
    "candy": {
        "names": ["Divinity", "Peanut Brittle", "Fudge", "Pralines", "Taffy", "Caramels", "Penuche",
                  "Popcorn Balls", "Sea Foam", "Rocky Road", "Martha Washington Candy", "Toffee"],
        "core": ["2 cups sugar", "1/2 cup light corn syrup", "1/2 cup water"],
        "extra": ["2 egg whites", "1 cup raw peanuts", "2/3 cup evaporated milk", "1 cup pecan halves",
                  "2 tbsp. butter", "1 tsp. soda", "1 tsp. vanilla", "1 cup brown sugar", "2 qts. popped corn",
                  "1 cup marshmallows", "1 lb. powdered sugar", "6 oz. chocolate bits"],
        "steps": ["Cook sugar, syrup and water to the hard ball stage (250 degrees).",
                  "Beat the egg whites stiff.", "Pour the syrup slowly over the whites, beating constantly.",
                  "Add the {x} and beat until it holds its shape.", "Drop by spoonfuls on waxed paper.",
                  "Stir in the {x} quickly.", "Pour into a buttered pan.", "Mark into squares when cool.",
                  "Test a little in cold water.", "Do not stir after it boils."],
    },
}
YIELDS = ["8 servings", "2 loaves", "4 dozen", "6 servings", "1 pie", "10 pints", "12 rolls", "9 x 13 pan", "3 dozen"]


def synth_recipe(rng: random.Random, cat: Optional[str] = None, size: str = "short",
                 used_titles: Optional[set] = None) -> RecipeSchema:
    cat = cat or rng.choice(list(CATS.keys()))
    spec = CATS[cat]
    for _ in range(50):
        base = rng.choice(spec["names"])
        style = rng.random()
        if style < 0.35:
            title = f"{rng.choice(PEOPLE)}'s {base}"
        elif style < 0.5:
            title = f"{rng.choice(['Old Fashioned', 'Quick', 'Easy', 'Best', 'Company', 'Never Fail', 'Holiday'])} {base}"
        else:
            title = base
        if used_titles is None or title not in used_titles:
            break
    if used_titles is not None:
        used_titles.add(title)
    if size == "short":
        n_ing, n_dir = rng.randint(4, 7), rng.randint(2, 5)
    elif size == "medium":
        n_ing, n_dir = rng.randint(6, 10), rng.randint(5, 8)
    else:
        n_ing, n_dir = rng.randint(9, 14), rng.randint(7, 11)
    core = list(spec["core"])
    extras = rng.sample(spec["extra"], min(len(spec["extra"]), max(0, n_ing - len(core)) + 1))
    ings = (core[: max(2, n_ing - len(extras))] + extras)[:n_ing]
    ext_names = [e.split(" ", 2)[-1].split(",")[0] for e in extras] or ["fruit"]
    steps = []
    pool = list(spec["steps"])
    rng.shuffle(pool)
    first = spec["steps"][0]
    steps.append(first)
    for s in pool:
        if len(steps) >= n_dir:
            break
        if s == first:
            continue
        steps.append(s.replace("{x}", rng.choice(ext_names)))
    while len(steps) < n_dir:
        steps.append(rng.choice(["Mix well.", "Season to taste.", "Let stand 5 minutes.", "Serve warm."]))
    return RecipeSchema(title=title, ingredients=ings, directions=steps,
                        yield_servings=rng.choice(YIELDS) if rng.random() < 0.6 else "",
                        total_time="" )


def near_duplicate(rng: random.Random, rec: RecipeSchema) -> RecipeSchema:
    """Second version of the same dish: same base title family, small ingredient/step edits."""
    ings = list(rec.ingredients)
    if len(ings) > 3:
        i = rng.randrange(len(ings))
        ings[i] = ings[i].replace("1 cup", "3/4 cup").replace("2 cups", "1 1/2 cups").replace("1 tsp.", "2 tsp.")
    if rng.random() < 0.5 and len(ings) > 4:
        ings.pop(rng.randrange(len(ings)))
    dirs = list(rec.directions)
    if len(dirs) > 2 and rng.random() < 0.6:
        j = rng.randrange(1, len(dirs))
        dirs[j] = dirs[j].replace("350", "325").replace("375", "350").replace("1 hour", "1 1/4 hours")
    base = rec.title.split("'s ")[-1]
    title = rng.choice([f"{base} II", f"{rng.choice(PEOPLE)}'s {base}", f"{base} (Better)", f"Another {base}", base])
    return RecipeSchema(title=title, ingredients=ings, directions=dirs, yield_servings=rec.yield_servings)


DISTRACTOR_TEXTS = [
    ["Grocery list", "flour 5 lb", "sugar", "oleo", "coffee", "2 cans milk", "soap flakes", "stamps"],
    ["Phone numbers", "Dr. Hansen  4-2271", "Church office 4-1180", "Mabel  3-5562", "Hardware 4-0093"],
    ["Household hints", "To remove rust stains rub with lemon and salt.", "A little vinegar in the rinse water brightens glass.", "Store woolens with cedar chips."],
    ["Christmas card list 1962", "the Olsons", "Uncle Ted & Marge", "Pastor Lind", "the Kellers", "Fay & Bob"],
    ["Dear Ruth,", "Thank you for the lovely visit.", "The children are well and Bob is busy with the harvest.", "Write soon. Love, Edna"],
    ["Canning record", "July 12 - 14 qts beans", "July 20 - 9 pts jam", "Aug 2 - 18 qts tomatoes", "Aug 9 - pickles 12 pts"],
    ["Club meeting Tues. 2 pm", "bring 2 doz. cookies", "dues 50 cents", "program - Mrs. Pruitt"],
    ["Oven temperatures", "Slow 250 - 325", "Moderate 350 - 375", "Hot 400 - 450", "Very hot 450 - 500"],
    ["Measures", "3 tsp. = 1 tbsp.", "16 tbsp. = 1 cup", "2 cups = 1 pint", "4 cups = 1 quart"],
    ["Garden 1958", "Row 1 beans", "Row 2 sweet corn", "Row 3 tomatoes (12 plants)", "Row 4 squash"],
    ["Notes", "Return casserole dish to Doris", "Pick up pattern at Woolworth", "Mail birthday card Sat."],
    ["Birthdays", "Jan 4 - Fay", "Mar 19 - Bob", "Jun 2 - Nana", "Oct 30 - Ted"],
]


def render_distractor(gen: SyntheticCardGenerator, stock: CardStock, writer: CardWriter, lines: List[str], rng: random.Random):
    img, _ = create_paper_substrate(stock.width, stock.height, stock.substrate, gen.rng, ruling=stock.ruling,
                                    base_color_override=stock.base_color)
    draw = ImageDraw.Draw(img, "RGBA")
    ink = writer.ink_color
    if writer.modality == CardModality.TYPEWRITTEN:
        render_typewritten_lines(draw, lines, 40, 30, stock.width, stock.height, ink, 13, gen.rng, font_style=writer.font_style)
    else:
        render_handwritten_lines(draw, lines, 40, 30, stock.width, stock.height, ink, 14, gen.rng, font_style=writer.font_style)
    img = apply_ink_fade(img, rng.uniform(0.05, 0.25), gen.rng)
    img = apply_rotation_skew(img, rng.uniform(-1.5, 1.5))
    return img


# ----------------------------------------------------------------------------------------------
# Deck assembly
# ----------------------------------------------------------------------------------------------
def pick_faces(rng: random.Random, p_multi: float) -> int:
    if rng.random() >= p_multi:
        return 1
    r = rng.random()
    return 2 if r < 0.6 else (3 if r < 0.85 else 4)


def build_deck(name: str, seed: int, deck_type: str, n_target: int, p_multi: float = 0.35,
               p_distractor: float = 0.0, homogeneous: bool = True, near_dup_frac: float = 0.0,
               degradation: DegradationLevel = DegradationLevel.MILD, modality: CardModality = CardModality.HANDWRITTEN_CURSIVE) -> Path:
    out = DECKS_DIR / name
    if (out / "manifest.json").exists():
        return out
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    gen = SyntheticCardGenerator(seed=seed)

    stocks = [
        CardStock(stock_id="s_cream_ruled", name="Cream ruled", substrate=PaperSubstrate.RULED_INDEX,
                  ruling=RulingStyle.STANDARD_RULED, base_color=(246, 238, 214), aging_level=degradation),
        CardStock(stock_id="s_white_blank", substrate=PaperSubstrate.WHITE_LINEN, ruling=RulingStyle.UNRULED, aging_level=degradation),
        CardStock(stock_id="s_aged", substrate=PaperSubstrate.AGED_YELLOW, ruling=RulingStyle.FAINT_RULED, aging_level=degradation),
        CardStock(stock_id="s_kraft", substrate=PaperSubstrate.KRAFT_BROWN, ruling=RulingStyle.UNRULED, aging_level=degradation),
        CardStock(stock_id="s_grid", substrate=PaperSubstrate.GRID_GRAPH, ruling=RulingStyle.GRID_GRAPH, aging_level=degradation),
    ]
    writers = [
        CardWriter(writer_id="w_cursive_blue", modality=modality, font_style="cursive" if modality == CardModality.HANDWRITTEN_CURSIVE else "typewriter",
                   ink_color=(25, 35, 110), ink_type=InkType.BLUE_BALLPOINT if modality == CardModality.HANDWRITTEN_CURSIVE else InkType.TYPEWRITER_RIBBON),
        CardWriter(writer_id="w_script_black", modality=CardModality.HANDWRITTEN_CURSIVE, font_style="script", ink_color=(20, 20, 20), ink_type=InkType.FOUNTAIN_PEN),
        CardWriter(writer_id="w_typed", modality=CardModality.TYPEWRITTEN, font_style="typewriter", ink_color=(30, 30, 30), ink_type=InkType.TYPEWRITER_RIBBON),
    ]

    used_titles: set = set()
    recipes_out: List[Dict[str, Any]] = []
    cards_out: List[Dict[str, Any]] = []
    images: List[Tuple[str, Any]] = []
    n_cards = 0
    rid_i = 0
    pending_dups: List[RecipeSchema] = []

    def new_id() -> str:
        return "%012x.jpg" % rng.getrandbits(48)

    while n_cards < n_target:
        rid_i += 1
        rid = f"r{rid_i:03d}"
        if p_distractor > 0 and rng.random() < p_distractor:
            stock = stocks[0] if homogeneous else rng.choice(stocks)
            writer = writers[0] if homogeneous else rng.choice(writers)
            lines = rng.choice(DISTRACTOR_TEXTS)
            img = render_distractor(gen, stock, writer, lines, rng)
            cid = new_id()
            images.append((cid, img))
            cards_out.append({"card_id": cid, "recipe_id": rid, "face_index": 0, "total_faces": 1, "is_distractor": True})
            recipes_out.append({"recipe_id": rid, "title": None, "ingredients": [], "directions": [], "faces": [cid],
                                "is_distractor": True, "verbatim": ["\n".join(lines)]})
            n_cards += 1
            continue
        nf = pick_faces(rng, p_multi)
        size = "short" if nf == 1 else ("medium" if nf == 2 else "long")
        if pending_dups and rng.random() < 0.7:
            rec = near_duplicate(rng, pending_dups.pop())
            nf = 1 if len(rec.ingredients) <= 7 and len(rec.directions) <= 5 else max(nf, 2)
        else:
            rec = synth_recipe(rng, size=size, used_titles=used_titles)
            if near_dup_frac > 0 and rng.random() < near_dup_frac:
                pending_dups.append(rec)
        rec.recipe_id = rid
        stock = stocks[0] if homogeneous else rng.choice(stocks)
        writer = writers[0] if homogeneous else rng.choice(writers)
        deg = DegradationConfig.from_level(degradation, gen.rng, include_advanced=True)
        if nf == 1:
            rec.ingredients = rec.ingredients[:7]
            rec.directions = rec.directions[:5]
        face_ids = [new_id() for _ in range(nf)]
        faces = gen.generate_recipe_card_faces(rec, num_faces=nf, stock=stock, writer=writer, degradation=deg, face_ids=face_ids)
        for f in faces:
            images.append((f.card_id, f.image))
            cards_out.append({"card_id": f.card_id, "recipe_id": rid, "face_index": f.face_index, "total_faces": nf,
                              "is_distractor": False})
        recipes_out.append({"recipe_id": rid, "title": rec.title, "ingredients": list(rec.ingredients),
                            "directions": list(rec.directions), "yield": rec.yield_servings or None,
                            "faces": [f.card_id for f in faces], "is_distractor": False,
                            "verbatim": [f.verbatim_text for f in faces]})
        n_cards += nf

    # Shuffle capture order: write images; the manifest card order is shuffled too.
    order = list(range(len(images)))
    rng.shuffle(order)
    for cid, img in images:
        img.convert("RGB").save(out / cid, "JPEG", quality=88)
    cards_shuffled = [cards_out[i] for i in order]
    manifest = {"deck": name, "seed": seed, "deck_type": deck_type, "n_cards": len(cards_out),
                "n_recipes": len(recipes_out), "cards": cards_shuffled, "recipes": recipes_out}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    n_multi = sum(1 for r in recipes_out if len(r["faces"]) > 1)
    print(f"{name}: {len(cards_out)} cards, {len(recipes_out)} recipes, multi={n_multi}, "
          f"distractors={sum(1 for r in recipes_out if r['is_distractor'])}")
    return out


# Train (fit) and held-out (eval) seed sets. NEVER mix.
DECK_SPECS: Dict[str, Dict[str, Any]] = {
    # ---- fit set (seeds 31xx)
    "fit_homo_large": dict(seed=3101, deck_type="large_homogeneous", n_target=140, p_multi=0.38),
    "fit_neardup": dict(seed=3102, deck_type="near_duplicate", n_target=40, p_multi=0.35, near_dup_frac=0.5),
    "fit_standalone": dict(seed=3103, deck_type="standalone_distractor", n_target=40, p_multi=0.12, p_distractor=0.2,
                           degradation=DegradationLevel.MODERATE),
    "fit_mixed": dict(seed=3104, deck_type="mixed", n_target=40, p_multi=0.4, homogeneous=False,
                      degradation=DegradationLevel.MODERATE),
    # ---- held-out set (seeds 32xx)
    "ho_homo_large": dict(seed=3201, deck_type="large_homogeneous", n_target=260, p_multi=0.36),
    "ho_neardup": dict(seed=3202, deck_type="near_duplicate", n_target=40, p_multi=0.35, near_dup_frac=0.5),
    "ho_standalone": dict(seed=3203, deck_type="standalone_distractor", n_target=40, p_multi=0.12, p_distractor=0.2,
                          degradation=DegradationLevel.SEVERE),
    "ho_mixed": dict(seed=3204, deck_type="mixed", n_target=40, p_multi=0.4, homogeneous=False,
                     degradation=DegradationLevel.MODERATE),
}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--decks", default=",".join(DECK_SPECS))
    a = ap.parse_args()
    for d in a.decks.split(","):
        build_deck(d, **DECK_SPECS[d])
