"""Ground-Truth Spotcheck Tool for T3 Curation.

Decrypts evals/vault/t3.gt.enc in memory using an authorized age identity
and displays a representative ~20% random sample in the terminal for human review.

Enforces Invariants:
- INV-AC1: Operates outside production code; requires an identity outside the workspace.
- Memory-only: Decryption occurs strictly in-memory; no plaintext GT touches disk.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from evals import vault


def spotcheck_vault(
    vault_file: Path,
    identity_path: Optional[Path] = None,
    sample_rate: float = 0.2,
    seed: Optional[int] = 42,
    show_all: bool = False,
) -> None:
    """Decrypt GT in memory and print a spot-check sample to stdout."""
    resolved_vault = Path(vault_file).expanduser().resolve()
    if not resolved_vault.exists():
        raise FileNotFoundError(f"Vault archive not found at '{resolved_vault}'")

    ciphertext = resolved_vault.read_bytes()
    plaintext = vault.decrypt_bytes(ciphertext, identity_path=identity_path)
    gt_data: Dict[str, Any] = json.loads(plaintext.decode("utf-8"))

    schema = gt_data.get("schema", "unknown")
    tier = gt_data.get("tier", "unknown")
    created = gt_data.get("created", "unknown")
    decks: List[Dict[str, Any]] = gt_data.get("decks", [])

    print("=" * 72)
    print(f"RECIPEBOXER GROUND-TRUTH SPOTCHECK: TIER {tier} ({schema})")
    print(f"Archive: {resolved_vault.name} | Created: {created}")
    print("=" * 72)

    total_images = sum(len(d.get("images", [])) for d in decks)
    all_recipes: List[Tuple[str, Dict[str, Any]]] = []
    for d in decks:
        deck_id = d.get("deck_id", "unknown")
        for r in d.get("recipes", []):
            all_recipes.append((deck_id, r))

    total_recipes = len(all_recipes)
    if total_recipes == 0:
        print("No recipes found in archive.")
        return

    if show_all:
        sample = all_recipes
        print(f"Showing ALL {len(sample)} recipes across {len(decks)} deck(s):")
    else:
        sample_size = max(1, int(round(total_recipes * sample_rate)))
        rng = random.Random(seed) if seed is not None else random.Random()
        sample = rng.sample(all_recipes, sample_size)
        print(
            f"Showing random {len(sample)}/{total_recipes} recipe sample "
            f"({len(sample)/total_recipes*100:.1f}%, seed={seed}):"
        )
    print("-" * 72)

    for idx, (deck_id, recipe) in enumerate(sample, start=1):
        rid = recipe.get("recipe_id", "unknown")
        faces = recipe.get("faces", [])
        diff = recipe.get("difficulty", {})
        status = recipe.get("structured_status", "pending")
        verbatim_list = recipe.get("verbatim", [])
        excluded = recipe.get("excluded_reason")

        print(f"\n[{idx}/{len(sample)}] Recipe ID: {rid} (Deck: {deck_id})")
        print(f"  Faces ({len(faces)}): {', '.join(faces)}")
        print(f"  Legibility: {diff.get('legibility', 'unknown')} | Status: {status}")
        if excluded:
            print(f"  Excluded Reason: {excluded}")

        for f_idx, (face_id, v_text) in enumerate(zip(faces, verbatim_list), start=1):
            print(f"  --- Face {f_idx} [{face_id}] Verbatim ({len(v_text.splitlines())} lines) ---")
            for line in v_text.splitlines()[:6]:
                print(f"    | {line}")
            if len(v_text.splitlines()) > 6:
                print(f"    | ... ({len(v_text.splitlines()) - 6} more lines)")

        if recipe.get("structured") is not None:
            print(f"  Structured data: {json.dumps(recipe['structured'], indent=4)}")
        else:
            print("  Structured data: [pending text-only draft]")

    print("\n" + "=" * 72)
    print("SUMMARY")
    print(f"  Total Decks:    {len(decks)}")
    print(f"  Total Images:   {total_images}")
    print(f"  Total Recipes:  {total_recipes}")
    print(f"  Sampled:        {len(sample)} ({len(sample)/total_recipes*100:.1f}%)")
    print("=" * 72)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Spotcheck encrypted ground-truth vault in memory")
    parser.add_argument(
        "--vault-file",
        type=Path,
        default=vault.get_repo_root() / "evals" / "vault" / "t3.gt.enc",
        help="Path to encrypted vault file (default: evals/vault/t3.gt.enc)",
    )
    parser.add_argument(
        "--identity",
        type=Path,
        default=None,
        help="Path to age identity file (default: ~/.config/recipeboxer/age.key)",
    )
    parser.add_argument(
        "--sample-rate",
        type=float,
        default=0.2,
        help="Fraction of recipes to sample (default: 0.2)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for repeatable sampling (default: 42)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Display all recipes instead of a random sample",
    )
    args = parser.parse_args(argv)

    try:
        spotcheck_vault(
            vault_file=args.vault_file,
            identity_path=args.identity,
            sample_rate=args.sample_rate,
            seed=args.seed,
            show_all=args.all,
        )
        return 0
    except Exception as e:
        sys.stderr.write(f"spotcheck error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
