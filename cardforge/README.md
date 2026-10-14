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
- **Substrates**: Aged yellow, white linen, ruled index, grid graph, kraft brown, and blank.
- **Physical Artifacts**: Coffee/tea rings, oil smudges, edge tears, corner dog-ears, date stamps, and cellophane tape residue.
- **Narrative Continuity**: Supports front/back card pairs and multi-card sets (3–4 cards) with narrative continuation cues.
- **T0 Archival Templates**: 24 historical recipe templates spanning 6 categories.
- **Deterministic**: Fully deterministic given a random seed.

## Usage

```bash
# Generate the standard T0 benchmark corpus into tests/fixtures/sample_cards
python -m cardforge

# Generate custom count into a target directory
python -m cardforge -o /tmp/cards -n 5 --seed 42 --modality HANDWRITTEN_CURSIVE
```
