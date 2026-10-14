# CardForge

Synthetic recipe card generator used by RecipeBoxer evals; intended to become its own repository; must never be imported by the recipeboxer app.

## Overview

CardForge is a procedural generation engine for creating realistic synthetic historical recipe cards. It produces high-resolution card scans across multiple visual modalities and paper substrates, with configurable physical degradation artifacts (such as coffee rings, grease smudges, ink fading, tears, tape residue, and dog-ear folds).

## Key Features

- **Modalities**:
  - `HANDWRITTEN_CURSIVE`: Vintage handwritten script on ruled index cards with ink bleed.
  - `TYPEWRITTEN`: Manual typewriter strike marks, character bounce, and ribbon wear.
  - `PRINTED_CLIPPING`: Glued newsprint and magazine clippings with multi-column layouts.
  - `MIXED_MEDIA`: Composite cards featuring handwritten corrections or stains.
- **Substrates & Ruling Variants**:
  - Paper tones: Aged yellow, white linen, ruled index, grid graph, kraft brown, blank, cream, canary.
  - Rulings: `STANDARD_RULED`, `WIDE_RULED`, `NARROW_RULED`, `BLUE_NO_HEADER`, `DOUBLE_HEADER`, `FAINT_RULED`, `GREEN_LEDGER`, `GRID_GRAPH`, `DOT_GRID`, `UNRULED`.
- **Stationery & Author Concepts**:
  - `CardStock`: Physical substrate, dimensions, ruling line variant, paper tone, base aging profile.
  - `CardWriter`: Writer personality, font style (Caveat, Special Elite, Marck Script, DejaVu), ink color and type, continuation cue habits.
- **Physical Artifacts & Degradations**:
  - Coffee/tea rings, oil smudges, edge tears, corner dog-ears, date stamps, cellophane tape residue.
  - Creases (fold lines), perspective warp (off-axis camera framing), specular glare, JPEG compression noise, and mirrored show-through / ghost bleed-through.
- **Narrative Continuity & Multi-Face Sets**:
  - Supports 1 to 5 faces (standalones, front/back pairs, and 3–5 face sequences).
  - Configurable continuation cues (present or absent).
  - Exact verbatim tracking per face.
- **T0 Archival Templates**: 24 historical recipe templates spanning 6 categories.
- **Deterministic**: Fully deterministic given a random seed.

## Usage

```bash
# Generate the standard T0 benchmark corpus into tests/fixtures/sample_cards
python -m cardforge

# Generate custom count into a target directory
python -m cardforge -o /tmp/cards -n 5 --seed 42 --modality HANDWRITTEN_CURSIVE
```
