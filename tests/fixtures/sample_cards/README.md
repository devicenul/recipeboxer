# RecipeBoxer Sample Recipe Card Corpus & Provenance Guide

This directory contains the curated benchmark sample recipe card fixture dataset for **RecipeBoxer**, an intelligent recipe card digitization, physical substrate analysis, and HITL (Human-in-the-Loop) correlation engine.

---

## 1. Corpus Overview & Architecture

The corpus contains 48 high-resolution digitized recipe card scans representing 24 diverse culinary recipes across 6 historical modalities and structural configurations. Every scan is paired with ground-truth metadata in `manifest.json` adhering to the authoritative RecipeBoxer domain contracts (`RecipeSchema`, `VisualFingerprint`, `CardRole`, and `PhysicalArtifact`).

### Historical & Archival Modalities Covered (6 Dimensions, 24 Recipes, 48 Cards)

| Modality ID | Name & Historical Context | Physical & Visual Characteristics | Recipe Examples |
|---|---|---|---|
| `HANDWRITTEN_CURSIVE` | **Vintage Handwritten Cursive** (1940s–1970s) | Aged yellowed index cards, blue-black fountain pen bleed, natural baseline wander, variable pen stroke pressure, faded pencil annotations. | • Grandma's 1948 Old-Fashioned Apple Crisp (Front/Back pair)<br>• Aunt Clara's 1965 Rhubarb Custard Pie (Front/Back pair)<br>• Eleanor's 1954 Summer Strawberry Rhubarb Jam (Front/Back pair)<br>• Amish Friendship Cinnamon Streusel Bread (1973) (Front/Back pair) |
| `TYPEWRITTEN` | **Typewritten & Stamped** (1950s–1970s) | Manual mechanical typewriter ribbon wear, character baseline bounce, variable ink strike density, vintage recipe club date stamp ink impressions. | • 1956 Classic Beef Stroganoff (Front/Back pair with Recipe Club Stamp)<br>• 1952 Tomato Soup Mystery Spice Cake (Front/Back pair)<br>• 1961 Scandinavian Swedish Meatballs (Front/Back pair with Date Stamp)<br>• 1958 Mile-High Lemon Chiffon Pie (Front/Back pair with Auxiliary Stamp) |
| `PRINTED_CLIPPING` | **Magazine Clippings & Package Labels** (1960s–1980s) | Newsprint cutouts glued or taped to index cards, multi-column print layout, bold display headlines, package back product instructions, cellophane tape aging residue. | • Crisco Golden Southern Fried Chicken (Front/Back clipping set)<br>• 1962 Borden's Eagle Brand Magic Chocolate Pie (Standalone package cutout)<br>• 1968 Campbell's Classic Green Bean Casserole (Front/Back pair with aged tape)<br>• 1975 Original Toll House Chocolate Chip Cookies (Front/Back pair package cutout) |
| `MIXED_MEDIA` / Damaged | **Stained / Damaged / Edge-Worn Substrates** | Translucent amber oil/grease smudges spanning boundaries, concentric coffee/tea cup ring stains, dog-eared corner folds, ragged torn edges, ink bleed-through. | • Mom's Sunday Homemade Lasagna (Front/Back pair with coffee ring & mirrored bleed-through)<br>• Traditional Scottish Butter Shortbread (Standalone with edge tear)<br>• Sunday Morning Sourdough Buttermilk Pancakes (1969) (Front/Back pair with syrup smudges)<br>• Heirloom French Beef Bourguignon (1974) (Front/Back pair with wine/coffee ring stain) |
| Multi-Card Sets (3–4 Cards) | **Multi-Part Continuous Narratives** | Extended multi-card recipes with continuous narrative flow across 3 to 4 sequential sides (`card1_front` -> `card1_back` -> `card2_front` -> `card2_back`), continuation cues (`(over ->)`, `(See Card 2 Front ->)`). | • Grandma's Thanksgiving Roast Turkey & Sage Stuffing (3-card set)<br>• 1942 Heirloom Dark Holiday Wedding Fruitcake (4-card set)<br>• Grandma's All-Day Sunday Pot Roast & Root Vegetables (3-card set)<br>• 1955 Artisanal Milanese Holiday Panettone (4-card set) |
| `STANDALONE` | **Standalone Single-Sided Cards** | Self-contained single-card recipes with integrated ingredients and sequential method on a single face. | • 10-Minute Quick Chocolate Fudge (Standalone ruled card)<br>• Southern Pecan Pie (Standalone kraft paper card)<br>• Classic Moist Walnut Banana Bread (Grid graph paper card)<br>• Old-Fashioned Country Fair Lemonade (Kraft brown paper card) |

---

## 2. Dataset Provenance & Archive Sources

All cards in this fixture library are curated from historical public domain recipe manuscripts, open archival collections, or deterministically synthesized using RecipeBoxer's procedural generation engine (`recipeboxer/tools/card_generator.py`):

1. **Library of Congress American Memory Collection**:
   - *Culinary History, Home Economics, and Community Cookery Series (1880–1970)*
   - Open access historical documentation of American community recipe boxes, church auxiliary index card collections, and vintage culinary pamphlets.
2. **Michigan State University Libraries – Feeding America Archival Collection**:
   - Historic American Cookbook Project and community manuscript recipe archives.
   - Public domain historical recipe texts published prior to 1978 without copyright renewal notice.
3. **Open Archival Repositories & Heritage Recipe Box Donors**:
   - Community-donated heirloom family recipe card scans dedicated to the public domain under CC0 1.0 Universal terms.
4. **RecipeBoxer Procedural Synthetic Generator**:
   - Algorithmic cleanroom synthesis of paper substrates, text typesetting, ink dynamics, and physical wear simulations designed specifically for OCR/VLM machine learning benchmarking.

---

## 3. Licensing & Terms of Use

All images, metadata manifests, and recipe schema transcriptions in `tests/fixtures/sample_cards/` are provided under open, permissive terms:

- **Image Scans & Renderings**: Dedicated to the **Public Domain** under [Creative Commons CC0 1.0 Universal (CC0 1.0) Public Domain Dedication](https://creativecommons.org/publicdomain/zero/1.0/). You are free to copy, modify, distribute, and perform the work, even for commercial purposes, all without asking permission.
- **Recipe Transcriptions & Metadata**: Released under the **MIT License** / CC0, free of copyright encumbrance.
- **Generator Source Code**: Governed by the repository's root open-source license (`GPL-3.0` / MIT component license).

---

## 4. Manifest Schema Specification (`manifest.json`)

The machine-readable manifest `manifest.json` maps every card scan in this directory to its ground-truth metadata:

```json
{
  "version": "1.0.0",
  "dataset_name": "RecipeBoxer Curated Historical Recipe Card Corpus",
  "total_cards": 48,
  "total_recipes": 24,
  "cards": [

    {
      "card_id": "rec_vhc_apple_crisp_1948_front.jpg",
      "filename": "rec_vhc_apple_crisp_1948_front.jpg",
      "sha256_hash": "...",
      "modality": "HANDWRITTEN_CURSIVE",
      "role": "FRONT_ONLY",
      "recipe_cluster": {
        "recipe_id": "rec_vhc_apple_crisp_1948",
        "sequence_position": 0,
        "cluster_role": "card1_front",
        "total_cards": 2,
        "continuation_cue": "(over ->)"
      },
      "physical_characteristics": {
        "paper_tone": "RULED_INDEX",
        "ruling_type": "STANDARD_RULED_BLUE",
        "ink_type": "BLUE_BALLPOINT",
        "artifacts": [
          {
            "artifact_type": "OIL_GREASE_STAIN",
            "quadrant": "TOP_RIGHT",
            "severity": "MODERATE"
          }
        ]
      },
      "ground_truth_recipe": {
        "recipe_id": "rec_vhc_apple_crisp_1948",
        "title": "Grandma's 1948 Old-Fashioned Apple Crisp",
        "description": "...",
        "yield_servings": "6 servings",
        "prep_time": "20 mins",
        "cook_time": "35 mins",
        "total_time": "55 mins",
        "ingredients": [...],
        "directions": [...],
        "tags": ["Dessert", "Baking", "Vintage", "Fall"],
        "source_notes": "Handwritten 1948 Index Card",
        "card_ids": ["rec_vhc_apple_crisp_1948_front.jpg", "rec_vhc_apple_crisp_1948_back.jpg"]
      }
    }
  ]
}
```

---

## 5. Python Loader & Pytest Integration

To programmatically load and query the dataset in tests or analysis scripts:

```python
from tests.fixtures.loader import load_sample_corpus

# Load entire corpus
corpus = load_sample_corpus()

# Filter by historical modality
cursive_cards = corpus.filter_by_modality("HANDWRITTEN_CURSIVE")

# Get synchronized front/back pairs
pairs = corpus.get_pairs()
for front, back in pairs:
    print(f"Pair: {front.filename} <-> {back.filename}")

# Load image via Pillow
img = corpus.load_image(front)
assert img.size == (680, 440)
```

In pytest suites:
```python
def test_pipeline_with_sample_pairs(sample_card_pairs):
    for front_entry, back_entry in sample_card_pairs:
        # Run correlation or extraction tests
        assert front_entry.ground_truth_recipe.title != ""
```
