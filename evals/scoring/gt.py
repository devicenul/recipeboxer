"""Ground truth decryption and oracle plan generation (WP-E4).

Handles:
- Loading and age decryption of evaluation ground-truth vaults (evals.vault.decrypt_bytes).
- Automatic generation of T0 ground truth from tests/fixtures/sample_cards/manifest.json.
- Construction of oracle grouping plans with randomized face orders and recipe set orders.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Union

logger = logging.getLogger("evals.scoring.gt")


def get_repo_root() -> Path:
    """Returns absolute path to the repository root."""
    return Path(__file__).resolve().parent.parent.parent


def build_t0_ground_truth(manifest_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Constructs rb-gt/1 conforming ground-truth dictionary from T0 sample cards manifest."""
    repo_root = get_repo_root()
    path = Path(manifest_path or (repo_root / "tests" / "fixtures" / "sample_cards" / "manifest.json")).resolve()

    if not path.exists():
        raise FileNotFoundError(f"T0 manifest file not found at {path}")

    raw_manifest = json.loads(path.read_text(encoding="utf-8"))

    images_list: List[Dict[str, Any]] = []
    recipes_by_cluster: Dict[str, List[Dict[str, Any]]] = {}

    for card in raw_manifest.get("cards", []):
        fname = card.get("filename") or card.get("card_id")
        sha = card.get("sha256_hash")
        mod = card.get("modality", "HANDWRITTEN_CURSIVE")

        images_list.append({
            "image_id": fname,
            "sha256": sha,
            "modality": mod,
            "stock_id": "s0",
        })

        cluster = card.get("recipe_cluster") or {}
        rid = cluster.get("recipe_id")
        if not rid:
            gt_rec = card.get("ground_truth_recipe") or {}
            rid = gt_rec.get("recipe_id") or fname

        if rid not in recipes_by_cluster:
            recipes_by_cluster[rid] = []
        recipes_by_cluster[rid].append(card)

    recipes_gt: List[Dict[str, Any]] = []
    for rid, cluster_cards in sorted(recipes_by_cluster.items(), key=lambda x: x[0]):
        sorted_cards = sorted(
            cluster_cards,
            key=lambda c: (c.get("recipe_cluster") or {}).get("sequence_position", 0),
        )
        faces = [c.get("filename") or c.get("card_id") for c in sorted_cards]

        # Extract structured recipe from first card containing ground_truth_recipe
        first_gt: Dict[str, Any] = {}
        for c in sorted_cards:
            if c.get("ground_truth_recipe"):
                first_gt = c["ground_truth_recipe"]
                break

        ingredients_raw = first_gt.get("ingredients", [])
        structured_ingredients: List[Dict[str, Any]] = []
        for ing in ingredients_raw:
            if isinstance(ing, dict):
                structured_ingredients.append(ing)
            else:
                structured_ingredients.append({
                    "raw": str(ing),
                    "quantity": "",
                    "unit": "",
                    "item": str(ing),
                    "section": None,
                })

        directions_raw = first_gt.get("directions", [])
        directions_list = [str(d) for d in directions_raw]

        structured = {
            "title": first_gt.get("title", "Untitled Recipe"),
            "yield": first_gt.get("yield_servings"),
            "source_note": first_gt.get("source_notes"),
            "ingredients": structured_ingredients,
            "directions": directions_list,
            "notes": first_gt.get("tags", []),
        }

        verbatim = [f"Card face transcription for {f}" for f in faces]

        recipes_gt.append({
            "recipe_id": rid,
            "faces": faces,
            "verbatim": verbatim,
            "structured": structured,
            "difficulty": {
                "legibility": "good",
                "faces": len(faces),
            },
        })

    deck = {
        "deck_id": "d01",
        "deck_type": "mixed",
        "images": images_list,
        "recipes": recipes_gt,
    }

    return {
        "schema": "rb-gt/1",
        "tier": "T0",
        "created": "2026-10-14T00:00:00Z",
        "generator": {"name": "sample_cards", "version": "1.0"},
        "decks": [deck],
    }


def load_ground_truth(
    tier: str,
    vault_dir: Optional[Union[str, Path]] = None,
    identity_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Loads ground-truth JSON for the given evaluation tier.

    T0 is parsed from tests/fixtures/sample_cards/manifest.json.
    T1-T4 are decrypted from evals/vault/<tier>.gt.enc using age identity.
    """
    norm_tier = str(tier).strip().upper()
    repo_root = get_repo_root()

    if norm_tier == "T0":
        return build_t0_ground_truth()

    # T1..T4 from vault
    v_dir = Path(vault_dir or (repo_root / "evals" / "vault")).resolve()

    enc_candidates = [
        v_dir / f"{tier.lower()}.gt.enc",
        v_dir / f"{tier.upper()}.gt.enc",
    ]
    enc_path: Optional[Path] = None
    for c in enc_candidates:
        if c.exists() and c.is_file():
            enc_path = c
            break

    if not enc_path:
        raise FileNotFoundError(
            f"Encrypted ground-truth vault file not found for tier {tier} in {v_dir}. "
            f"Expected one of: {[p.name for p in enc_candidates]}"
        )

    # Decrypt via evals.vault
    from evals.vault import decrypt_bytes

    ciphertext = enc_path.read_bytes()
    plaintext = decrypt_bytes(ciphertext, identity_path=identity_path)
    gt_data = json.loads(plaintext.decode("utf-8"))
    return gt_data


def create_oracle_plan(
    gt_data: Dict[str, Any],
    deck_filter: Optional[Set[str]] = None,
    seed: int = 42,
) -> Dict[str, Any]:
    """Creates an oracle grouping plan with sets in random order and faces shuffled."""
    rng = random.Random(seed)
    deck_plans: List[Dict[str, Any]] = []

    for deck in gt_data.get("decks", []):
        deck_id = deck.get("deck_id", "d01")
        if deck_filter and deck_id not in deck_filter:
            continue

        raw_sets: List[List[str]] = []
        for r in deck.get("recipes", []):
            faces = list(r.get("faces", []))
            rng.shuffle(faces)
            raw_sets.append(faces)

        rng.shuffle(raw_sets)
        deck_plans.append({
            "deck_id": deck_id,
            "sets": raw_sets,
        })

    return {"decks": deck_plans}
