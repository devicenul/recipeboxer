"""RecipeBoxer Real Photo Deck Intake Tool (WP-R3).

Automates intake of real user recipe card photos for benchmark tier T4 (or other tiers):
1. Reads raw user photos from a photos directory.
2. Applies EXIF orientation (transposing rotated photos).
3. Strips GPS coordinates and all EXIF privacy metadata, preserving and writing back
   only DateTimeOriginal (capture timestamp) for temporal proximity analysis.
4. Generates opaque, random hexadecimal filenames for image privacy.
5. Writes processed images to evals/corpora/<tier>/<deck_id>/.
6. Updates public deck manifest evals/corpora/<tier>/DECKS.json.
7. Parses transcription files (per-recipe folders with faces.txt and transcription.md,
   or CSV format) and builds ground-truth JSON conforming to schema rb-gt/1.
8. Encrypts ground truth directly into evals/vault/<tier>.gt.enc via evals.vault.encrypt_bytes.
   If an archive already exists, decrypts, merges, and re-encrypts in memory.
   Plaintext ground truth is NEVER written to disk.

Usage
-----
    python -m evals.intake_real --photos /path/to/photos --transcriptions /path/to/transcriptions --deck-id d01 --tier T4

To preview counts without writing any images or touching the vault:
    python -m evals.intake_real --photos /path/to/photos --transcriptions /path/to/transcriptions --deck-id d01 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import ExifTags, Image, ImageOps

import evals.vault as vault

# Regex pattern for ingredient parsing: quantity (integers, decimals, fractions like 1 1/2)
_QTY_PATTERN = re.compile(
    r"^(\d+(?:\s+\d+/\d+|\.\d+|/\d+)?|\d+/\d+)\s*",
    re.IGNORECASE,
)

# Common cooking units
_UNIT_PATTERN = re.compile(
    r"^(cups?|c\.|c|tablespoons?|tbsp\.?|tbs\.?|teaspoons?|tsp\.?|ounces?|oz\.?|"
    r"pounds?|lbs?\.?|grams?|g\.?|kilograms?|kg\.?|milliliters?|ml\.?|liters?|l\.?|"
    r"pinch(?:es)?|dash(?:es)?|cloves?|cans?|packages?|pkgs?\.?|slices?|stalks?|"
    r"bunche?s?|sprigs?|heads?|pieces?)\b\.?\s*",
    re.IGNORECASE,
)


def get_default_repo_root() -> Path:
    """Return repository root path."""
    return Path(__file__).resolve().parent.parent


def extract_date_time_original(im: Image.Image) -> Optional[str]:
    """Extract DateTimeOriginal timestamp from an image's EXIF metadata."""
    try:
        exif = im.getexif()
        if not exif:
            return None

        # Check Exif sub-IFD (tag 34665 = 0x8769)
        try:
            exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            if ExifTags.Base.DateTimeOriginal in exif_ifd:
                val = exif_ifd[ExifTags.Base.DateTimeOriginal]
                if val:
                    return str(val)
        except Exception:
            pass

        # Check direct tag (36867 = DateTimeOriginal)
        if ExifTags.Base.DateTimeOriginal in exif:
            val = exif[ExifTags.Base.DateTimeOriginal]
            if val:
                return str(val)

        if 36867 in exif and exif[36867]:
            return str(exif[36867])

        # Fallback to DateTime (tag 306 = 0x0132)
        if ExifTags.Base.DateTime in exif:
            val = exif[ExifTags.Base.DateTime]
            if val:
                return str(val)
        if 306 in exif and exif[306]:
            return str(exif[306])

    except Exception:
        pass
    return None


def build_clean_exif(date_time_original: Optional[str]) -> Optional[Image.Exif]:
    """Build a clean EXIF object containing ONLY DateTimeOriginal (no GPS or other tags)."""
    if not date_time_original:
        return None
    clean_exif = Image.Exif()
    exif_ifd = clean_exif.get_ifd(ExifTags.IFD.Exif)
    exif_ifd[ExifTags.Base.DateTimeOriginal] = str(date_time_original)
    return clean_exif


def parse_ingredient_line(line: str, section: Optional[str] = None) -> Dict[str, Any]:
    """Parse a single raw ingredient string into structured fields."""
    raw = line.strip().lstrip("-*•").strip()
    qty: Optional[str] = None
    unit: Optional[str] = None
    item = raw

    qty_match = _QTY_PATTERN.match(raw)
    if qty_match:
        qty = qty_match.group(1).strip()
        remainder = raw[qty_match.end() :].strip()
        unit_match = _UNIT_PATTERN.match(remainder)
        if unit_match:
            unit = unit_match.group(1).strip().rstrip(".")
            item = remainder[unit_match.end() :].strip()
        else:
            item = remainder

    return {
        "raw": raw,
        "quantity": qty if qty else None,
        "unit": unit if unit else None,
        "item": item if item else raw,
        "section": section,
    }


def parse_transcription_markdown(content: str) -> Dict[str, Any]:
    """Parse transcription markdown with optional structured sections.

    Supports sections:
    # Title
    ## Ingredients
    ## Directions (or Instructions)
    ## Notes
    ### Face N (for multi-face verbatim splitting)
    """
    lines = content.splitlines()
    title = "Untitled Recipe"
    current_section: Optional[str] = None
    current_ing_section: Optional[str] = None

    ingredients: List[Dict[str, Any]] = []
    directions: List[str] = []
    notes: List[str] = []
    yield_val: Optional[str] = None
    source_val: Optional[str] = None

    face_verbatims: List[str] = []
    current_face_lines: List[str] = []
    has_explicit_faces = False

    for line in lines:
        stripped = line.strip()

        # Check for face delimiters (e.g. '### Face 1', '---', or '<!-- face -->')
        if re.match(r"^###\s+Face\s+\d+", stripped, re.IGNORECASE) or stripped == "---":
            has_explicit_faces = True
            if current_face_lines:
                face_verbatims.append("\n".join(current_face_lines).strip())
                current_face_lines = []
            continue

        current_face_lines.append(line)

        # Check for Title heading (# Title or # Recipe Name)
        if stripped.startswith("# ") and not stripped.startswith("## "):
            header_text = stripped[2:].strip()
            if header_text.lower().startswith("title:"):
                title = header_text[6:].strip()
            elif header_text.lower() != "title":
                title = header_text
            continue

        # Check for section headers (## Section)
        if stripped.startswith("## "):
            sec_name = stripped[3:].strip().lower()
            if "ingredient" in sec_name:
                current_section = "ingredients"
                current_ing_section = None
            elif "direction" in sec_name or "instruction" in sec_name or "step" in sec_name:
                current_section = "directions"
            elif "note" in sec_name:
                current_section = "notes"
            elif "yield" in sec_name or "serving" in sec_name:
                current_section = "yield"
            elif "source" in sec_name:
                current_section = "source"
            else:
                current_section = sec_name
            continue

        # Sub-heading within ingredients (e.g. ### Crust)
        if current_section == "ingredients" and stripped.startswith("### "):
            current_ing_section = stripped[4:].strip()
            continue

        if not stripped:
            continue

        # Yield / Source detection
        if stripped.lower().startswith("yield:") or stripped.lower().startswith("servings:"):
            yield_val = stripped.split(":", 1)[1].strip()
            continue
        if stripped.lower().startswith("source:") or stripped.lower().startswith("source note:"):
            source_val = stripped.split(":", 1)[1].strip()
            continue

        # Accumulate per section
        if current_section == "ingredients":
            ingredients.append(parse_ingredient_line(stripped, section=current_ing_section))
        elif current_section == "directions":
            # Remove leading numbers like '1. ', '2) ' or bullets
            clean_step = re.sub(r"^(\d+[\.\)]\s*|[-*•]\s*)", "", stripped).strip()
            if clean_step:
                directions.append(clean_step)
        elif current_section == "notes":
            clean_note = stripped.lstrip("-*•").strip()
            if clean_note:
                notes.append(clean_note)
        elif current_section == "yield":
            yield_val = stripped
        elif current_section == "source":
            source_val = stripped

    if current_face_lines:
        face_verbatims.append("\n".join(current_face_lines).strip())

    return {
        "title": title,
        "yield": yield_val,
        "source_note": source_val,
        "ingredients": ingredients,
        "directions": directions,
        "notes": notes,
        "face_verbatims": face_verbatims if has_explicit_faces else [],
        "full_text": content.strip(),
    }


def parse_transcriptions_dir(
    transcriptions_dir: Path,
) -> List[Dict[str, Any]]:
    """Parse transcriptions directory containing recipe folders."""
    transcriptions_dir = Path(transcriptions_dir)
    if not transcriptions_dir.exists():
        raise FileNotFoundError(f"Transcriptions directory not found: '{transcriptions_dir}'")

    # Check if transcriptions_dir is itself a single recipe folder
    if (transcriptions_dir / "faces.txt").exists():
        recipe_folders = [transcriptions_dir]
    else:
        recipe_folders = sorted(
            [
                p
                for p in transcriptions_dir.iterdir()
                if p.is_dir() and not p.name.startswith((".", "_"))
            ],
            key=lambda p: p.name,
        )

    if not recipe_folders:
        raise ValueError(f"No recipe folders found in '{transcriptions_dir}'")

    parsed_recipes = []
    for idx, folder in enumerate(recipe_folders, start=1):
        faces_file = folder / "faces.txt"
        if not faces_file.exists():
            raise FileNotFoundError(
                f"Missing faces.txt in recipe folder '{folder.name}' ({folder})"
            )

        # Parse faces list
        faces_content = faces_file.read_text(encoding="utf-8")
        face_images = [
            line.strip()
            for line in faces_content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not face_images:
            raise ValueError(f"No image filenames listed in '{faces_file}'")

        # Parse transcription.md if present
        trans_file = folder / "transcription.md"
        if trans_file.exists():
            md_content = trans_file.read_text(encoding="utf-8")
            parsed_md = parse_transcription_markdown(md_content)
        else:
            # Fallback when transcription.md is absent
            title = folder.name.replace("_", " ").title()
            parsed_md = {
                "title": title,
                "yield": None,
                "source_note": None,
                "ingredients": [],
                "directions": [],
                "notes": [],
                "face_verbatims": [],
                "full_text": "",
            }

        parsed_recipes.append(
            {
                "recipe_id": f"r{idx:03d}",
                "folder_name": folder.name,
                "face_images": face_images,
                "parsed": parsed_md,
            }
        )

    return parsed_recipes


def parse_transcriptions_csv(csv_path: Path) -> List[Dict[str, Any]]:
    """Parse transcriptions from a CSV file."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Transcriptions CSV not found: '{csv_path}'")

    parsed_recipes = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=1):
            raw_faces = row.get("faces") or row.get("images") or ""
            face_images = [p.strip() for p in re.split(r"[,;\s]+", raw_faces) if p.strip()]
            if not face_images:
                continue

            recipe_id = (row.get("recipe_id") or f"r{idx:03d}").strip()
            title = (row.get("title") or f"Recipe {idx}").strip()

            raw_ings = row.get("ingredients") or ""
            ing_lines = [l.strip() for l in re.split(r"[\n;]+", raw_ings) if l.strip()]
            ingredients = [parse_ingredient_line(l) for l in ing_lines]

            raw_dirs = row.get("directions") or row.get("instructions") or ""
            directions = [
                re.sub(r"^(\d+[\.\)]\s*|[-*•]\s*)", "", l).strip()
                for l in re.split(r"[\n;]+", raw_dirs)
                if l.strip()
            ]

            raw_notes = row.get("notes") or ""
            notes = [l.strip().lstrip("-*•").strip() for l in re.split(r"[\n;]+", raw_notes) if l.strip()]

            raw_verbatim = row.get("verbatim") or ""
            face_verbatims = [v.strip() for v in raw_verbatim.split("|") if v.strip()]

            parsed_md = {
                "title": title,
                "yield": row.get("yield") or None,
                "source_note": row.get("source_note") or None,
                "ingredients": ingredients,
                "directions": directions,
                "notes": notes,
                "face_verbatims": face_verbatims,
                "full_text": raw_verbatim,
            }

            parsed_recipes.append(
                {
                    "recipe_id": recipe_id,
                    "folder_name": title,
                    "face_images": face_images,
                    "parsed": parsed_md,
                }
            )

    return parsed_recipes


def process_image(
    src_path: Path,
    dest_path: Path,
) -> Tuple[str, Optional[str]]:
    """Apply EXIF orientation, strip GPS/metadata keeping DateTimeOriginal, save image.

    Returns
    -------
    Tuple[str, Optional[str]]
        (sha256_hash, date_time_original)
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as im:
        date_time_original = extract_date_time_original(im)
        # Apply EXIF orientation
        transposed = ImageOps.exif_transpose(im)
        clean_exif = build_clean_exif(date_time_original)

        # Handle color mode if saving as JPEG
        out_im = transposed
        ext = dest_path.suffix.lower()
        if ext in (".jpg", ".jpeg"):
            if out_im.mode in ("RGBA", "LA", "P"):
                out_im = out_im.convert("RGB")
            elif out_im.mode != "RGB":
                out_im = out_im.convert("RGB")

            if clean_exif is not None:
                out_im.save(dest_path, format="JPEG", exif=clean_exif, quality=95)
            else:
                out_im.save(dest_path, format="JPEG", quality=95)
        else:
            out_im.save(dest_path)

    image_bytes = dest_path.read_bytes()
    sha256 = hashlib.sha256(image_bytes).hexdigest()
    return sha256, date_time_original


def update_decks_json(
    decks_json_path: Path,
    deck_id: str,
    n_images: int,
) -> None:
    """Update or create the public DECKS.json file."""
    decks_json_path.parent.mkdir(parents=True, exist_ok=True)
    deck_list = []
    if decks_json_path.exists():
        try:
            deck_list = json.loads(decks_json_path.read_text(encoding="utf-8"))
        except Exception:
            deck_list = []

    # Update existing entry or append
    updated = False
    for item in deck_list:
        if item.get("deck_id") == deck_id:
            item["n_images"] = n_images
            updated = True
            break
    if not updated:
        deck_list.append({"deck_id": deck_id, "n_images": n_images})

    decks_json_path.write_text(json.dumps(deck_list, indent=2) + "\n", encoding="utf-8")


def intake_photos(
    photos_dir: Union[str, Path],
    transcriptions_path: Union[str, Path],
    deck_id: str = "d01",
    tier: str = "T4",
    deck_type: str = "mixed",
    dry_run: bool = False,
    corpora_dir: Optional[Union[str, Path]] = None,
    vault_dir: Optional[Union[str, Path]] = None,
    identity_path: Optional[Union[str, Path]] = None,
    recipients_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Execute real photos intake pipeline.

    Parameters
    ----------
    photos_dir:
        Directory containing raw source photos.
    transcriptions_path:
        Directory of recipe folders (with faces.txt / transcription.md) or CSV file.
    deck_id:
        Identifier for this deck (e.g. 'd01').
    tier:
        Benchmark tier (e.g. 'T4').
    deck_type:
        Deck classification ('mixed', 'homogeneous', etc.).
    dry_run:
        If True, only validates inputs and returns counts without writing files.
    corpora_dir:
        Root corpora folder (defaults to evals/corpora in repo).
    vault_dir:
        Root vault folder (defaults to evals/vault in repo).
    identity_path:
        Path to age identity file (for decrypting existing GT during merge).
    recipients_path:
        Path to recipients.txt file (for encrypting GT).

    Returns
    -------
    Dict[str, Any]
        Summary dict containing counts and results.
    """
    photos_dir = Path(photos_dir).resolve()
    transcriptions_path = Path(transcriptions_path).resolve()
    repo_root = get_default_repo_root()

    corp_dir = repo_root / "evals" / "corpora" if corpora_dir is None else Path(corpora_dir)
    v_dir = repo_root / "evals" / "vault" if vault_dir is None else Path(vault_dir)

    tier_clean = tier.strip().lower()
    tier_upper = tier.strip().upper()

    # 1. Parse transcriptions
    if transcriptions_path.is_file() and transcriptions_path.suffix.lower() == ".csv":
        recipes = parse_transcriptions_csv(transcriptions_path)
    else:
        recipes = parse_transcriptions_dir(transcriptions_path)

    # 2. Locate and validate all referenced photos
    all_referenced_images: List[str] = []
    photo_map: Dict[str, Path] = {}

    for r in recipes:
        for face_name in r["face_images"]:
            all_referenced_images.append(face_name)
            if face_name in photo_map:
                continue

            # Check direct match
            cand = photos_dir / face_name
            if cand.exists():
                photo_map[face_name] = cand
                continue

            # Case-insensitive search inside photos_dir
            found: Optional[Path] = None
            for p in photos_dir.iterdir():
                if p.name.lower() == face_name.lower():
                    found = p
                    break
            if found:
                photo_map[face_name] = found
            else:
                raise FileNotFoundError(
                    f"Photo '{face_name}' referenced in recipe '{r['recipe_id']}' "
                    f"not found in photos directory '{photos_dir}'"
                )

    total_recipes = len(recipes)
    total_images = len(all_referenced_images)

    # 3. Dry-run early exit
    if dry_run:
        return {
            "dry_run": True,
            "deck_id": deck_id,
            "tier": tier_upper,
            "recipes_count": total_recipes,
            "images_count": total_images,
            "photos_dir": str(photos_dir),
            "transcriptions": str(transcriptions_path),
        }

    # 4. Prepare target directories
    deck_target_dir = corp_dir / tier_clean / deck_id
    deck_target_dir.mkdir(parents=True, exist_ok=True)

    # 5. Process and write images with opaque names
    image_id_map: Dict[str, str] = {}  # orig_name -> opaque_id
    images_meta: List[Dict[str, Any]] = []

    for orig_name, src_path in photo_map.items():
        ext = src_path.suffix.lower() or ".jpg"
        opaque_id = f"{secrets.token_hex(6)}{ext}"
        while (deck_target_dir / opaque_id).exists():
            opaque_id = f"{secrets.token_hex(6)}{ext}"

        dest_file = deck_target_dir / opaque_id
        sha256, _ = process_image(src_path, dest_file)

        image_id_map[orig_name] = opaque_id
        images_meta.append(
            {
                "image_id": opaque_id,
                "sha256": sha256,
                "modality": "MIXED",
                "stock_id": "real",
            }
        )

    # 6. Build Deck recipes structure conforming to contracts §2
    gt_recipes = []
    for r in recipes:
        parsed = r["parsed"]
        mapped_faces = [image_id_map[f] for f in r["face_images"]]

        # Verbatim resolution: per-face verbatim or divide full text
        explicit_verbatims = parsed.get("face_verbatims") or []
        if explicit_verbatims and len(explicit_verbatims) == len(mapped_faces):
            verbatim_list = explicit_verbatims
        elif len(mapped_faces) == 1:
            verbatim_list = [parsed.get("full_text") or ""]
        else:
            full = parsed.get("full_text") or ""
            verbatim_list = [full] + [""] * (len(mapped_faces) - 1)

        gt_recipes.append(
            {
                "recipe_id": r["recipe_id"],
                "faces": mapped_faces,
                "verbatim": verbatim_list,
                "structured": {
                    "title": parsed["title"],
                    "yield": parsed["yield"],
                    "source_note": parsed["source_note"],
                    "ingredients": parsed["ingredients"],
                    "directions": parsed["directions"],
                    "notes": parsed["notes"],
                },
                "difficulty": {
                    "legibility": "good",
                    "faces": len(mapped_faces),
                },
            }
        )

    new_deck = {
        "deck_id": deck_id,
        "deck_type": deck_type,
        "images": images_meta,
        "recipes": gt_recipes,
    }

    # 7. Update public DECKS.json
    decks_json_path = corp_dir / tier_clean / "DECKS.json"
    update_decks_json(decks_json_path, deck_id, len(images_meta))

    # 8. Encrypt GT to evals/vault/<tier>.gt.enc (never write plaintext to disk)
    v_dir.mkdir(parents=True, exist_ok=True)
    vault_file = v_dir / f"{tier_clean}.gt.enc"

    if vault_file.exists():
        # Decrypt, merge deck, and re-encrypt
        existing_ciphertext = vault_file.read_bytes()
        decrypted_bytes = vault.decrypt_bytes(
            existing_ciphertext, identity_path=identity_path
        )
        gt_doc = json.loads(decrypted_bytes.decode("utf-8"))

        # Replace existing deck if present, else append
        deck_updated = False
        for idx, d in enumerate(gt_doc.get("decks", [])):
            if d.get("deck_id") == deck_id:
                gt_doc["decks"][idx] = new_deck
                deck_updated = True
                break
        if not deck_updated:
            gt_doc.setdefault("decks", []).append(new_deck)
        gt_doc["updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    else:
        # Create brand-new GT archive
        gt_doc = {
            "schema": "rb-gt/1",
            "tier": tier_upper,
            "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "generator": {"name": "intake_real", "version": "1.0"},
            "decks": [new_deck],
        }

    plaintext_payload = json.dumps(gt_doc, indent=2).encode("utf-8")
    new_ciphertext = vault.encrypt_bytes(
        plaintext_payload, recipients_path=recipients_path
    )
    vault_file.write_bytes(new_ciphertext)

    return {
        "dry_run": False,
        "deck_id": deck_id,
        "tier": tier_upper,
        "recipes_count": total_recipes,
        "images_count": len(images_meta),
        "corpus_dir": str(deck_target_dir),
        "vault_file": str(vault_file),
        "decks_json": str(decks_json_path),
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for evals.intake_real."""
    parser = argparse.ArgumentParser(
        prog="evals.intake_real",
        description="RecipeBoxer real-photo dataset intake CLI (WP-R3)",
    )
    parser.add_argument(
        "--photos",
        type=Path,
        required=True,
        help="Path to directory containing source photos",
    )
    parser.add_argument(
        "--transcriptions",
        type=Path,
        required=True,
        help="Path to directory of recipe folders (with faces.txt / transcription.md) or CSV",
    )
    parser.add_argument(
        "--deck-id",
        type=str,
        default="d01",
        help="Target deck ID (default: 'd01')",
    )
    parser.add_argument(
        "--tier",
        type=str,
        default="T4",
        help="Benchmark tier (default: 'T4')",
    )
    parser.add_argument(
        "--deck-type",
        type=str,
        default="mixed",
        help="Deck type classification (default: 'mixed')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and display counts without writing images or updating vault",
    )
    parser.add_argument(
        "--corpora-dir",
        type=Path,
        default=None,
        help="Optional custom corpora root directory",
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help="Optional custom vault root directory",
    )
    parser.add_argument(
        "--identity",
        type=Path,
        default=None,
        help="Optional age identity file path (for decrypting during merge)",
    )
    parser.add_argument(
        "--recipients",
        type=Path,
        default=None,
        help="Optional recipients file path (for encrypting ground truth)",
    )

    args = parser.parse_args(argv)

    try:
        res = intake_photos(
            photos_dir=args.photos,
            transcriptions_path=args.transcriptions,
            deck_id=args.deck_id,
            tier=args.tier,
            deck_type=args.deck_type,
            dry_run=args.dry_run,
            corpora_dir=args.corpora_dir,
            vault_dir=args.vault_dir,
            identity_path=args.identity,
            recipients_path=args.recipients,
        )

        if res.get("dry_run"):
            print(f"[DRY RUN] Intake validation successful:")
            print(f"  Tier: {res['tier']}")
            print(f"  Deck ID: {res['deck_id']}")
            print(f"  Recipes found: {res['recipes_count']}")
            print(f"  Images referenced: {res['images_count']}")
            print(f"  Photos source: {res['photos_dir']}")
            print(f"  Transcriptions: {res['transcriptions']}")
            print("  (No files were written to disk or vault.)")
        else:
            print(f"Intake complete successfully:")
            print(f"  Tier: {res['tier']}")
            print(f"  Deck ID: {res['deck_id']}")
            print(f"  Recipes ingested: {res['recipes_count']}")
            print(f"  Images processed: {res['images_count']}")
            print(f"  Images written to: {res['corpus_dir']}")
            print(f"  Public manifest updated: {res['decks_json']}")
            print(f"  Vault encrypted archive: {res['vault_file']}")

    except Exception as e:
        sys.stderr.write(f"evals.intake_real error: {e}\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
