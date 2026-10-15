"""Script to generate train and test cardforge decks for UP2."""

from __future__ import annotations

import json
import os
import random
from pathlib import Path
from typing import Any, Dict, List

from cardforge.generator import SyntheticCardGenerator
from cardforge.models import (
    CardModality,
    CardRole,
    DegradationConfig,
    DegradationLevel,
    GeneratedCard,
    PaperSubstrate,
    RecipeSchema,
)
from cardforge.templates_t0 import ARCHIVAL_RECIPE_TEMPLATES


def get_template_map() -> Dict[str, Dict[str, Any]]:
    return {t["recipe_id"]: dict(t) for t in ARCHIVAL_RECIPE_TEMPLATES}


def generate_deck(
    deck_name: str,
    output_dir: Path,
    recipe_ids: List[str],
    seed: int,
    homogeneous_substrate: PaperSubstrate | None = None,
    homogeneous_modality: CardModality | None = None,
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generator = SyntheticCardGenerator(seed=seed)
    rng = random.Random(seed)
    template_map = get_template_map()

    manifest_entries: List[Dict[str, Any]] = []
    all_cards: List[GeneratedCard] = []

    for rid in recipe_ids:
        t_data = dict(template_map[rid])
        t_data.pop("dimension", None)
        t_data.pop("provenance", None)
        t_data.pop("historical_date", None)
        t_data.pop("archive_series", None)

        rec = RecipeSchema(**t_data)

        # Modality and substrate selection
        m = homogeneous_modality or (
            CardModality.HANDWRITTEN_CURSIVE if ("vhc" in rid or "apple" in rid or "rhubarb" in rid or "turkey" in rid or "fruitcake" in rid or "jam" in rid or "amish" in rid or "pot_roast" in rid or "panettone" in rid or "banana" in rid or "fudge" in rid or "pecan" in rid)
            else CardModality.TYPEWRITTEN if ("typ" in rid or "lemonade" in rid)
            else CardModality.PRINTED_CLIPPING if "clp" in rid
            else CardModality.MIXED_MEDIA
        )
        s = homogeneous_substrate or (
            PaperSubstrate.GRID_GRAPH if "banana" in rid
            else PaperSubstrate.KRAFT_BROWN if ("pecan" in rid or "lemonade" in rid)
            else PaperSubstrate.WHITE_LINEN if ("clp" in rid or "shortbread" in rid or "lemon_chiffon" in rid)
            else PaperSubstrate.AGED_YELLOW if ("typ" in rid or "fruitcake" in rid or "amish" in rid or "bourguignon" in rid or "panettone" in rid)
            else PaperSubstrate.RULED_INDEX
        )

        deg = DegradationConfig.from_level(DegradationLevel.MODERATE, rng)

        if "fruitcake" in rid or "panettone" in rid:
            cards = generator.generate_multi_card_set(rec, num_cards=4, modality=m, substrate=s, degradation=deg)
        elif "mcs" in rid or "turkey" in rid or "pot_roast" in rid:
            cards = generator.generate_multi_card_set(rec, num_cards=3, modality=m, substrate=s, degradation=deg)
        elif "sta" in rid or "shortbread" in rid or "condensed_milk" in rid or "banana" in rid or "lemonade" in rid:
            cards = [generator.generate_standalone_card(rec, modality=m, substrate=s, degradation=deg)]
        else:
            cards = list(generator.generate_card_pair(rec, modality=m, substrate=s, degradation=deg))

        for card in cards:
            fname = f"{Path(card.filename).stem}.jpg"
            card.filename = fname
            card.card_id = fname
            img_path = output_dir / fname
            card.image.save(img_path, "JPEG", quality=92)
            all_cards.append(card)

            raw_bytes = img_path.read_bytes()
            import hashlib
            sha = hashlib.sha256(raw_bytes).hexdigest()

            manifest_entries.append({
                "card_id": fname,
                "filename": fname,
                "sha256_hash": sha,
                "modality": card.modality.value,
                "role": card.role.value,
                "recipe_cluster": {
                    "recipe_id": rid,
                    "title": rec.title,
                },
                "ground_truth_recipe": rec.model_dump(),
            })

    manifest = {
        "version": "1.0.0",
        "deck_name": deck_name,
        "seed": seed,
        "total_cards": len(manifest_entries),
        "total_recipes": len(set(e["recipe_cluster"]["recipe_id"] for e in manifest_entries)),
        "cards": manifest_entries,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Generated {deck_name}: {len(manifest_entries)} cards across {manifest['total_recipes']} recipes into {output_dir}")
    return manifest
