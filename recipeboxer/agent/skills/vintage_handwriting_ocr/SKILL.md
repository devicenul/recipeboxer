---
name: vintage-handwriting-ocr
description: "Specialized OCR and transcription skill for 19th and 20th-century American cursive handwriting, archaic culinary units and abbreviations, and fraction normalization."
---

# Vintage Cursive Handwriting OCR & Culinary Paleography Skill

## Overview

The `vintage-handwriting-ocr` skill equips RecipeBoxer agents with forensic domain expertise in transcribing 19th and 20th-century American cursive handwriting (including Spencerian and Palmer scripts), decoding historical culinary shorthand, resolving ambiguous letterforms, and standardizing archaic measurements.

---

## 1. Paleographic Letterform Disambiguation Rules

Vintage American cursive handwriting often features historical ligatures and ambiguous stroke forms. Apply the following forensic disambiguation rules:

- **'T' vs 'F' / 't' vs 'f'**: Capital 'T' lacks a crossbar; capital 'F' has a central tick or crossbar. Lowercase 't' vs 'f': 'f' extends below the baseline with a descending loop.
- **'T' vs 't' in Culinary Shorthand**:
  - Capital **`T`**, **`T.`**, **`Tb.`**, or **`Tbs.`** = **Tablespoon (tbsp)**.
  - Lowercase **`t`**, **`t.`**, **`tsp.`**, or **`ts.`** = **Teaspoon (tsp)**.
  - If case is ambiguous, evaluate context: salt/spices/extracts are almost always teaspoons (`1 tsp cinnamon`, `1/2 tsp salt`); flour/sugar/oil/liquids in small quantities are often tablespoons (`2 tbsp flour for thickening`).
- **'S' vs 'L' / 'G' vs 'Y'**: Spencerian initial 'S' resembles a tall loop with a hook; Palmer capital 'G' can resemble 'S' or 'Y'.
- **'r' vs 'v' / 'n' vs 'u'**: Flat-topped cursive 'r' vs peaked 'v'. Inverted minims ('n' vs 'u') must be resolved by English orthography and ingredient lexicon.
- **'ss' as Long-S**: 19th-century cursive sometimes renders double-s ('ss') as a long-s ligature resembling 'fs' or 'β' (e.g. "Molafses" $\rightarrow$ "Molasses").

---

## 2. Archaic Culinary Units & Vocabulary Dictionary

Historical American recipe boxes frequently use archaic terms that require systematic normalization:

| Historical Term / Abbreviation | Modern Culinary Standard | Typical Volume / Weight Equivalent |
|---|---|---|
| **oleo** / **oleomargarine** | butter or margarine | 1:1 butter substitute |
| **saleratus** | baking soda | potassium / sodium bicarbonate |
| **sweet milk** | whole milk | standard fresh milk (distinguished from sour milk / buttermilk) |
| **sour milk** | buttermilk / clabbered milk | 1 cup milk + 1 tbsp vinegar or lemon juice |
| **butter size of an egg** | ~1/4 cup butter | 4 tablespoons / 2 oz / 57g |
| **butter size of a walnut** | ~2 tablespoons butter | 1 oz / 28g |
| **butter size of a hazelnut / pea** | ~1 teaspoon butter | 1/6 oz / ~5g |
| **butter size of a hickory nut** | ~1.5 tablespoons butter | ~0.75 oz / ~21g |
| **gill** / **jill** | 1/2 cup | 4 fluid ounces / 118 ml |
| **peck** | 2 dry gallons | 8 dry quarts / 16 dry pints |
| **teacup** | ~3/4 cup | 6 fluid ounces / 177 ml |
| **wineglass** | ~1/4 cup | 2 fluid ounces / 60 ml |
| **tumbler** | 1 cup | 8 fluid ounces / 237 ml |
| **drachm** / **dram** | 1/8 ounce | ~3.7 grams / ~3/4 tsp |
| **scruple** | 1/24 ounce | ~1.3 grams / ~1/4 tsp |
| **pinch** / **dash** | 1/16 to 1/8 tsp | a pinch |
| **quick oven** | 375°F - 400°F | moderately hot oven |
| **moderate oven** | 350°F | standard baking temperature |
| **slow oven** | 250°F - 300°F | low temperature braising/baking |

---

## 3. Fraction Normalization Standard

Normalize all fractions into standard ASCII representations without Unicode corruption:

- **Single Fractions**: `½` $\rightarrow$ `1/2`, `¼` $\rightarrow$ `1/4`, `¾` $\rightarrow$ `3/4`, `⅓` $\rightarrow$ `1/3`, `⅔` $\rightarrow$ `2/3`, `⅛` $\rightarrow$ `1/8`, `⅜` $\rightarrow$ `3/8`, `⅝` $\rightarrow$ `5/8`, `⅞` $\rightarrow$ `7/8`.
- **Mixed Numbers**: `1½` $\rightarrow$ `1 1/2`, `2¾` $\rightarrow$ `2 3/4`, `3 1/3` $\rightarrow$ `3 1/3`.
- **Compound Quantities**: Convert "one and a half cups" to `"1 1/2 cups"`.

---

## 4. Transcription & Direction Sequencing Rules

1. Preserve the natural ingredient quantities, units, ingredient names, and preparatory directions.
2. If instructions contain step-by-step paragraphs without explicit numbering, separate into logical numbered sequences starting from `1. `, `2. `, etc.
3. Preserve marginalia attribution (e.g. "From Aunt Clara, 1934") in `source_notes`.
