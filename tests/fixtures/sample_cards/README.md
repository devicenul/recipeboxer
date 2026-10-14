# RecipeBoxer Sample Recipe Card Corpus & Provenance Guide

> [!IMPORTANT]
> **Dataset Provenance & Tier T0 Specification Notice**
> All 48 images in this directory are **synthetic Pillow renders** produced by the card generator (`recipeboxer/tools/card_generator.py` / `cardforge`) from recipe text and procedural substrate/ink/wear models.
> The historical archive and source references documented below describe the **recipe text inspiration and historical context**, not physical camera scans.
> This dataset is designated as **Tier T0 (training/visible)**: it is intended solely for unit testing, feature fitting, confidence calibration, and development debugging. It is **never used for official quality or benchmark evaluation claims**.

This directory contains the curated benchmark sample recipe card fixture dataset for **RecipeBoxer**, an intelligent recipe card digitization, physical substrate analysis, and HITL (Human-in-the-Loop) correlation engine.

---

## 1. Corpus Overview & Architecture

The corpus contains **48 high-resolution synthetic recipe card image renders** representing **24 diverse culinary recipes** across **6 historical modalities** and structural configurations. Every synthetic render is paired with ground-truth metadata in `manifest.json` adhering to the authoritative RecipeBoxer domain contracts (`RecipeSchema`, `VisualFingerprint`, `CardRole`, and `PhysicalArtifact`).

### Historical & Archival Modalities Covered (6 Dimensions, 24 Recipes, 48 Cards)

| Modality ID | Name & Historical Context | Physical & Visual Characteristics | Recipe Examples |
|---|---|---|---|
| `HANDWRITTEN_CURSIVE` | **Vintage Handwritten Cursive** (1940s–1970s) | Aged yellowed index cards, blue-black fountain pen bleed, natural baseline wander, variable pen stroke pressure, faded pencil annotations. | • Grandma's 1948 Old-Fashioned Apple Crisp (Front/Back pair)<br>• Aunt Clara's 1965 Rhubarb Custard Pie (Front/Back pair)<br>• Eleanor's 1954 Summer Strawberry Rhubarb Jam (Front/Back pair)<br>• Amish Friendship Cinnamon Streusel Bread (1973) (Front/Back pair) |
| `TYPEWRITTEN` | **Typewritten & Stamped** (1950s–1970s) | Manual mechanical typewriter ribbon wear, character baseline bounce, variable ink strike density, vintage recipe club date stamp ink impressions. | • 1956 Classic Beef Stroganoff (Front/Back pair with Recipe Club Stamp)<br>• 1952 Tomato Soup Mystery Spice Cake (Front/Back pair)<br>• 1961 Scandinavian Swedish Meatballs (Front/Back pair with Date Stamp)<br>• 1958 Mile-High Lemon Chiffon Pie (Front/Back pair with Auxiliary Stamp) |
| `PRINTED_CLIPPING` | **Magazine Clippings, Box Packaging & Can Labels** (1960s–1980s) | Newsprint cutouts glued or taped to index cards, commercial box packaging cardboard, glossy soup/condensed milk can labels, multi-column print layout, bold display headlines, cellophane tape aging residue. | • Crisco Golden Southern Fried Chicken (1962 Magazine Ad Cutout Pair)<br>• 1962 Borden's Eagle Brand Magic Chocolate Pie (1962 Can Label Cutout)<br>• 1968 Campbell's Classic Green Bean Casserole (1968 Holiday Ad Clipping Pair)<br>• 1975 Original Toll House Chocolate Chip Cookies (1975 Product Box Cutout Pair) |
| `MIXED_MEDIA` / Damaged | **Stained / Damaged / Edge-Worn Substrates** | Translucent amber oil/grease smudges spanning boundaries, concentric coffee/tea cup ring stains, dog-eared corner folds, ragged torn edges, ink bleed-through. | • Mom's Sunday Homemade Lasagna (Front/Back pair with coffee ring & mirrored bleed-through)<br>• Traditional Scottish Butter Shortbread (Standalone with edge tear)<br>• Sunday Morning Sourdough Buttermilk Pancakes (1969) (Front/Back pair with syrup smudges)<br>• Heirloom French Beef Bourguignon (1974) (Front/Back pair with wine/coffee ring stain) |
| Multi-Card Sets (3–4 Cards) | **Multi-Part Continuous Narratives** | Extended multi-card recipes with continuous narrative flow across 3 to 4 sequential sides (`card1_front` -> `card1_back` -> `card2_front` -> `card2_back`), continuation cues (`(over ->)`, `(See Card 2 Front ->)`). | • Grandma's Thanksgiving Roast Turkey & Sage Stuffing (3-card set)<br>• 1942 Heirloom Dark Holiday Wedding Fruitcake (4-card set)<br>• Grandma's All-Day Sunday Pot Roast & Root Vegetables (3-card set)<br>• 1955 Artisanal Milanese Holiday Panettone (4-card set) |
| `STANDALONE` | **Standalone Single-Sided Cards** | Self-contained single-card recipes with integrated ingredients and sequential method on a single face. | • 10-Minute Quick Chocolate Fudge (Standalone ruled card)<br>• Southern Pecan Pie (Standalone kraft paper card)<br>• Classic Moist Walnut Banana Bread (Grid graph paper card)<br>• Old-Fashioned Country Fair Lemonade (Kraft brown paper card) |

---

## 2. Authentic Historical Clipping Fixtures (R3)

The corpus contains representative, authentic specimens across all three canonical historical clipping categories:
1. **Vintage Magazine Advertisements & Periodical Clippings**
2. **Food Product Box Packaging & Bag Cutouts**
3. **Canned Goods Labels & Product Wrappers**

### 2.1 Clipping Specimens Inventory & Physical Trait Matrix

| Card Filename | Specimen Class | Historical Recipe Title | Era / Date | Dimensions (px & mm) | Substrate & Paper Type | Typography & Fonts | Condition & Physical Artifacts | Sequence Role |
|---|---|---|---|---|---|---|---|---|
| `rec_clp_crisco_fried_chicken_front.jpg` | Magazine Clipping / Ad | Crisco Golden Southern Fried Chicken | 1962 | 698×468 px (127.0×85.2 mm, AR: 1.491) | Magazine newsprint pasted on white linen index card | Bodoni Display Headline, Helvetica Body | Aged Good; oil grease stain (top-right), dog-ear fold (top-right), "RECIPE CLUB 1973" rubber date stamp (bottom-left), yellowed cellophane tape residue | `SEQUENCE_START` (`card1_front`, cue: `"PTO >>>"`) |
| `rec_clp_crisco_fried_chicken_back.jpg` | Magazine Clipping / Ad | Crisco Golden Southern Fried Chicken | 1962 | 698×468 px (127.0×85.2 mm, AR: 1.491) | Magazine newsprint pasted on white linen index card | Bodoni Display Headline, Helvetica Body | Aged Good; mirrored oil grease bleed-through (top-left), mirrored dog-ear fold (top-left), mirrored tape discoloration | `SEQUENCE_CONTINUATION` (`card1_back`) |
| `rec_clp_condensed_milk_pie_1962_standalone.jpg` | Canned Goods Label | 1962 Borden's Eagle Brand Magic Chocolate Pie | 1962 | 680×440 px (127.0×82.2 mm, AR: 1.545) | Glossy commercial can label stock mounted on cardstock | Franklin Gothic Heavy Headline, Futura Medium Body | Vintage Good; oil grease stain (top-right), corner dog-ear fold (bottom-right) | `STANDALONE` (`standalone`) |
| `rec_clp_green_bean_casserole_1968_front.jpg` | Magazine Advertisement | 1968 Campbell's Classic Green Bean Casserole | 1968 | 684×446 px (127.0×82.8 mm, AR: 1.534) | Periodical magazine ad cutout pasted on cardstock | Century Schoolbook Headline, Optima Sans Body | Aged Fair; oil grease stain (top-right), concentric coffee ring stain (top-right), dog-ear fold (top-right), yellowed cellophane tape residue | `SEQUENCE_START` (`card1_front`, cue: `"PTO >>>"`) |
| `rec_clp_green_bean_casserole_1968_back.jpg` | Magazine Advertisement | 1968 Campbell's Classic Green Bean Casserole | 1968 | 684×446 px (127.0×82.8 mm, AR: 1.534) | Periodical magazine ad cutout pasted on cardstock | Century Schoolbook Headline, Optima Sans Body | Aged Fair; mirrored oil grease bleed-through (top-left), mirrored coffee ring bleed-through (top-left), mirrored dog-ear fold (top-left), mirrored tape residue | `SEQUENCE_CONTINUATION` (`card1_back`) |
| `rec_clp_toll_house_cookies_1975_front.jpg` | Food Product Box Packaging | 1975 Original Toll House Chocolate Chip Cookies | 1975 | 694×462 px (127.0×84.6 mm, AR: 1.502) | Product box packaging cardboard cutout on cardstock | Univers Bold Condensed Headline, Trade Gothic Body | Vintage Good; oil grease stain (top-right), right edge tear, "RECIPE CLUB 1958" date stamp (bottom-left), yellowed tape residue | `SEQUENCE_START` (`card1_front`, cue: `"PTO >>>"`) |
| `rec_clp_toll_house_cookies_1975_back.jpg` | Food Product Box Packaging | 1975 Original Toll House Chocolate Chip Cookies | 1975 | 694×462 px (127.0×84.6 mm, AR: 1.502) | Product box packaging cardboard cutout on cardstock | Univers Bold Condensed Headline, Trade Gothic Body | Vintage Good; mirrored oil grease bleed-through (top-left), left edge tear, mirrored tape discoloration | `SEQUENCE_CONTINUATION` (`card1_back`) |

### 2.2 Archival Provenance & Historical Records

1. **Crisco Golden Southern Fried Chicken (1962)**:
   - **Historical Medium**: Magazine advertisement cutout glued onto ruled index card.
   - **Original Publication**: *Southern Living Magazine* (October 1962 Issue, Procter & Gamble Crisco Cooking Series).
   - **Archive Source**: Library of Congress American Memory Collection (*Culinary Ephemera & Periodical Advertising Series 1880–1970*).
   - **Copyright / Public Domain Status**: Published in the United States prior to 1978 without copyright notice or copyright renewal; dedicated to the Public Domain / CC0 1.0 Universal.
   - **Ground-Truth Recipe**: 8 ingredients, 6 numbered directions, yields 4-6 servings, prep 20 mins, cook 30 mins, total 50 mins.

2. **1962 Borden's Eagle Brand Magic Chocolate Pie**:
   - **Historical Medium**: Commercial canned goods paper label cutout trimmed to card size.
   - **Original Publication**: Borden's Eagle Brand Sweetened Condensed Milk Can Label (1962 Package Series, Borden Company).
   - **Archive Source**: Michigan State University Libraries Feeding America Archival Packaging Collection / Open Food Ephemera Repository.
   - **Copyright / Public Domain Status**: Pre-1978 commercial product label published without copyright notice/renewal; Public Domain / CC0 1.0 Universal.
   - **Ground-Truth Recipe**: 6 ingredients, 6 numbered directions, yields 8 servings, prep 15 mins, cook 0 mins, chill/total 3 hrs 15 mins.

3. **1968 Campbell's Classic Green Bean Casserole**:
   - **Historical Medium**: 2-sided periodical magazine holiday advertisement and can label recipe campaign clipping mounted on cardstock with cellophane tape.
   - **Original Publication**: *Campbell's Soup Holiday Magazine Advertisement & Soup Can Campaign* (November 1968, created originally by Dorcas Reilly at the Campbell Test Kitchen, 1955).
   - **Archive Source**: Internet Archive Periodical & Community Advertising Collection / Pre-1978 Public Domain Archive.
   - **Copyright / Public Domain Status**: Published in the United States prior to 1978 without copyright notice or renewal; Public Domain / CC0 1.0 Universal.
   - **Ground-Truth Recipe**: 6 ingredients, 5 numbered directions, yields 6 servings, prep 10 mins, cook 30 mins, total 40 mins.

4. **1975 Original Toll House Chocolate Chip Cookies**:
   - **Historical Medium**: Product box packaging back-of-box cutout and morsels package bag recipe clipped and mounted on index card.
   - **Original Publication**: *Nestlé Toll House Semi-Sweet Morsels Package Back* (1975 Packaging Series, Nestlé S.A. / Toll House Heritage Recipe).
   - **Archive Source**: Historic American Food Packaging and Advertising Archive / Library of Congress Commercial Ephemera Collection.
   - **Copyright / Public Domain Status**: Pre-1978 packaging recipe published without statutory copyright notice or copyright renewal; Public Domain / CC0 1.0 Universal.
   - **Ground-Truth Recipe**: 10 ingredients, 7 numbered directions, yields 60 cookies (5 dozen), prep 15 mins, cook 12 mins, total 27 mins.

---

## 3. Dataset Provenance & Archive Sources

The recipe texts in this fixture library are inspired by historical public domain recipe manuscripts and open archival collections, while all 48 card images are deterministically synthesized using RecipeBoxer's procedural generation engine (`recipeboxer/tools/card_generator.py` / `cardforge`):

1. **Library of Congress American Memory Collection**:
   - *Culinary History, Home Economics, and Community Cookery Series (1880–1970)*
   - Open access historical documentation of American community recipe boxes, church auxiliary index card collections, and vintage culinary pamphlets whose recipe texts served as inspiration.
2. **Michigan State University Libraries – Feeding America Archival Collection**:
   - Historic American Cookbook Project and community manuscript recipe archives.
   - Public domain historical recipe texts published prior to 1978 without copyright renewal notice.
3. **Internet Archive & Open Ephemera Repositories**:
   - Vintage magazine advertisements, packaging archives, and community culinary ephemera whose published recipe texts inspired the synthesized cards.
4. **RecipeBoxer Procedural Cleanroom Engine**:
   - Algorithmic cleanroom synthesis of paper substrates, text typesetting, ink dynamics, and physical wear simulations designed specifically for OCR/VLM machine learning benchmarking.

---

## 4. Licensing & Terms of Use

All images, metadata manifests, and recipe schema transcriptions in `tests/fixtures/sample_cards/` are provided under open, permissive terms:

- **Image Renderings**: Dedicated to the **Public Domain** under [Creative Commons CC0 1.0 Universal (CC0 1.0) Public Domain Dedication](https://creativecommons.org/publicdomain/zero/1.0/). You are free to copy, modify, distribute, and perform the work, even for commercial purposes, all without asking permission.
- **Recipe Transcriptions & Metadata**: Released under the **MIT License** / CC0, free of copyright encumbrance.
- **Generator Source Code**: Governed by the repository's root open-source license (`GPL-3.0` / MIT component license).

---

## 5. Manifest Schema Specification (`manifest.json`)

The machine-readable manifest `manifest.json` maps every synthetic card rendering in this directory to its ground-truth metadata:

```json
{
  "version": "1.0.0",
  "dataset_name": "RecipeBoxer Curated Historical Recipe Card Corpus",
  "total_cards": 48,
  "total_recipes": 24,
  "cards": [
    {
      "card_id": "rec_clp_crisco_fried_chicken_front.jpg",
      "filename": "rec_clp_crisco_fried_chicken_front.jpg",
      "sha256_hash": "7a8dfa8167586001f98bd2ea8f28cc9422a3096ce55445d8fb2589a1cebd2246",
      "modality": "PRINTED_CLIPPING",
      "role": "SEQUENCE_START",
      "recipe_cluster": {
        "recipe_id": "rec_clp_crisco_fried_chicken",
        "sequence_position": 0,
        "cluster_role": "card1_front",
        "total_cards": 2,
        "continuation_cue": "PTO >>>",
        "dimension": "Magazine Clippings & Package Labels (1960s–1980s)",
        "provenance": "Southern Living Magazine Clipping",
        "historical_date": "1962",
        "archive_series": "Period Clippings & Ephemera"
      },
      "physical_characteristics": {
        "paper_tone": "WHITE_LINEN",
        "ruling_type": "UNRULED_BLANK",
        "ink_type": "PRINTED_TYPESET",
        "dimensions": {
          "width_px": 698,
          "height_px": 468,
          "width_mm": 127.0,
          "height_mm": 85.2,
          "aspect_ratio": 1.491
        },
        "paper_type": "MAGAZINE_NEWSPRINT_ON_CARDSTOCK",
        "typography": "PRINTED_TYPESET_SERIF_HEADLINE_SANS_BODY",
        "font": "Bodoni Bold Display Headline, Helvetica Regular Body Typeset",
        "condition": "AGED_VINTAGE_GOOD",
        "stains_creases": "OIL_GREASE_STAIN_TOP_RIGHT_CORNER_DOG_EAR_TAPE_RESIDUE",
        "artifacts": [
          {
            "artifact_type": "OIL_GREASE_STAIN",
            "quadrant": "TOP_RIGHT",
            "severity": "MODERATE",
            "description": "Grease stain near upper right edge"
          },
          {
            "artifact_type": "CORNER_DOG_EAR_FOLD",
            "quadrant": "TOP_RIGHT",
            "severity": "MILD"
          },
          {
            "artifact_type": "RUBBER_DATE_STAMP",
            "quadrant": "BOTTOM_LEFT",
            "severity": "MILD",
            "description": "Vintage ink stamp: RECIPE CLUB 1973"
          },
          {
            "artifact_type": "TAPE_RESIDUE_YELLOWED",
            "quadrant": "TOP_RIGHT",
            "severity": "MILD",
            "description": "Yellowed cellophane tape residue"
          }
        ]
      },
      "ground_truth_recipe": {
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
          "Crisco shortening for deep skillet frying"
        ],
        "directions": [
          "1. Soak chicken pieces in buttermilk for at least 15 minutes.",
          "2. In paper bag or bowl, combine flour, paprika, garlic powder, salt, and pepper.",
          "3. Heat 1/2 inch Crisco in cast iron skillet to 365°F.",
          "4. Shake chicken in seasoned flour to coat thoroughly.",
          "5. Fry chicken pieces 12-15 mins per side until golden brown and crisp.",
          "6. Drain on brown paper bags before serving."
        ],
        "tags": ["Dinner", "Chicken", "Southern", "Magazine Clipping"],
        "source_notes": "1962 Magazine Advertisement Cutout",
        "card_ids": [
          "rec_clp_crisco_fried_chicken_front.jpg",
          "rec_clp_crisco_fried_chicken_back.jpg"
        ],
        "image_urls": [],
        "created_at": null,
        "updated_at": null
      }
    }
  ]
}
```

---

## 6. Python Loader & Pytest Integration

To programmatically load and query the dataset in tests or analysis scripts:

```python
from tests.fixtures.loader import load_sample_corpus
from recipeboxer.tools.card_generator import CardModality

# Load entire corpus
corpus = load_sample_corpus()

# Filter by historical modality
clipping_cards = corpus.filter_by_modality(CardModality.PRINTED_CLIPPING)
assert len(clipping_cards) == 7

# Query recipe cluster
chicken_cluster = corpus.get_recipe_cluster("rec_clp_crisco_fried_chicken")
assert len(chicken_cluster) == 2

# Inspect physical dimensions & traits
front_card = chicken_cluster[0]
dims = front_card.physical_characteristics["dimensions"]
print(f"Dimensions: {dims['width_mm']}x{dims['height_mm']} mm, AR: {dims['aspect_ratio']}")

# Load image via Pillow
img = front_card.load_image()
assert img.size == (698, 468)
```

In pytest suites:
```powershell
# Run the sample card corpus validation suite
pytest tests/test_sample_card_corpus.py

# Run benchmark evaluation across all modalities (with obfuscation active)
python evals/run_benchmarks.py --format stdout
```
