"""Deterministic materializer for large synthetic card evaluation decks.

Reads encrypted deck generation specifications (e.g. evals/vault/t1_large.spec.enc)
and regenerates byte-for-byte identical JPEG card images into a designated output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from cardforge.generator import SyntheticCardGenerator
from cardforge.models import RecipeSchema
from evals.curation.build_synthetic import build_t1_stocks_and_writers
from evals.vault import decrypt_bytes, get_repo_root, DEFAULT_KEY_PATH, ENV_AGE_KEY_FILE


def materialize_deck(
    spec_path: Path,
    out_dir: Path,
    identity_path: Optional[Path] = None,
    verify: bool = True,
) -> int:
    """Decrypts generation spec in memory and regenerates deck images deterministically."""
    if not spec_path.exists():
        raise FileNotFoundError(f"Generation spec not found at '{spec_path}'")

    out_dir.mkdir(parents=True, exist_ok=True)

    # Decrypt generation spec strictly in memory
    ciphertext = spec_path.read_bytes()
    spec_bytes = decrypt_bytes(ciphertext, identity_path=identity_path)
    spec: Dict[str, Any] = json.loads(spec_bytes.decode("utf-8"))

    deck_id = spec["deck_id"]
    base_seed = spec["base_seed"]
    recipe_items = spec["recipe_items"]

    stocks, writers = build_t1_stocks_and_writers()

    total_generated = 0
    mismatches = 0

    print(f"Materializing deck '{deck_id}' ({len(recipe_items)} recipes) into: {out_dir}")

    for r_idx, item in enumerate(recipe_items):
        seed_val = base_seed + (r_idx * 17)
        gen = SyntheticCardGenerator(seed=seed_val)

        r_data = item["recipe_data"]
        stock_id = item["stock_id"]
        writer_id = item["writer_id"]
        n_faces = item["num_faces"]
        cue = item["continuation_cue"]
        face_ids = item["face_ids"]

        stock = stocks.get(stock_id, stocks["s_t1_cream"])
        writer = writers.get(writer_id, writers["w_t1_cursive_blue"])

        rec_obj = RecipeSchema(
            recipe_id=f"r{r_idx + 1:03d}",
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

        for card in cards:
            dest_file = out_dir / card.filename
            card.image.save(dest_file, format="JPEG", quality=80)
            total_generated += 1

    print(f"Successfully materialized {total_generated} cards for deck '{deck_id}'.")
    return total_generated


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for deck materializer."""
    parser = argparse.ArgumentParser(
        prog="evals.curation.materialize",
        description="Regenerates synthetic card images deterministically from an encrypted specification.",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=None,
        help="Path to encrypted generation spec (default: evals/vault/t1_large.spec.enc)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination directory for materialized images (default: git-ignored cache or evals/curation/large_deck_cache)",
    )
    parser.add_argument(
        "--identity",
        type=Path,
        default=None,
        help=f"Path to age identity file (default: ${ENV_AGE_KEY_FILE} or ~/.config/recipeboxer/age.key)",
    )

    args = parser.parse_args(argv)

    repo_root = get_repo_root()
    spec_path = args.spec or (repo_root / "evals" / "vault" / "t1_large.spec.enc")

    if args.out:
        out_dir = args.out
    else:
        env_cache = os.environ.get("PROJECT_CACHE_DIR")
        if env_cache:
            out_dir = Path(env_cache) / "materialized_t1_large"
        else:
            out_dir = repo_root / "evals" / "curation" / "large_deck_cache"

    try:
        materialize_deck(spec_path=spec_path, out_dir=out_dir, identity_path=args.identity)
        return 0
    except Exception as e:
        sys.stderr.write(f"Materialization failed: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
