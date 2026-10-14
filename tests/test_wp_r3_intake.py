"""Tests for WP-R3: evals/intake_real.py and evals/INTAKE.md.

Verifies:
- EXIF orientation transposed and applied
- GPS coordinates and sensitive EXIF stripped, DateTimeOriginal preserved and written back
- Images renamed to opaque hex identifiers
- Corpus directory layout evals/corpora/<tier>/<deck>/
- DECKS.json public manifest created/updated without GT leakage
- GT JSON schema rb-gt/1 conformance
- Encryption straight to evals/vault/<tier>.gt.enc (never writing plaintext GT to disk)
- Seamless merging of multiple decks in vault archive
- Dry-run validation mode (prints counts, leaves disk/vault untouched)
- Error handling on missing photos
- CSV transcription format support
- CLI entrypoint invocation
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import pytest
from PIL import ExifTags, Image

import evals.intake_real as intake_real
import evals.vault as vault


def _create_synthetic_photos(
    photos_dir: Path,
    photo_specs: List[Tuple[str, Tuple[int, int], int, str]],
) -> List[Path]:
    """Helper to create dummy camera photos with specific dimensions, EXIF orientation, GPS, and timestamp.

    photo_specs: List of (filename, (width, height), orientation_tag, date_time_original)
    """
    photos_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for fname, (w, h), orientation, timestamp in photo_specs:
        im = Image.new("RGB", (w, h), color=(180, 200, 220))
        exif = im.getexif()

        # Orientation
        if orientation > 1:
            exif[ExifTags.Base.Orientation] = orientation

        # GPS Info IFD (tag 34853)
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
        gps_ifd[1] = "N"
        gps_ifd[2] = (37, 48, 30)
        gps_ifd[3] = "W"
        gps_ifd[4] = (122, 14, 20)

        # Exif sub-IFD (tag 34665) with DateTimeOriginal
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        exif_ifd[ExifTags.Base.DateTimeOriginal] = timestamp
        exif_ifd[ExifTags.Base.LensModel] = "Sensitive Phone Camera Lens 123"

        out_path = photos_dir / fname
        im.save(out_path, format="JPEG", exif=exif)
        created.append(out_path)
    return created


def _setup_vault_keys(tmp_path: Path) -> Tuple[Path, Path]:
    """Create a temporary age identity and recipients file outside repository."""
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)

    ident_path = keys_dir / "test_age.key"
    pubkey, _ = vault.keygen(out_path=ident_path)

    recipients_path = keys_dir / "recipients.txt"
    recipients_path.write_text(f"# Test recipients\n{pubkey}\n", encoding="utf-8")

    return ident_path, recipients_path


def test_exif_orientation_and_gps_stripping(tmp_path: Path):
    """EXIF orientation is applied (pixels transposed), GPS stripped, DateTimeOriginal kept."""
    photos_dir = tmp_path / "photos"
    # Orientation 6 means 90 deg CW rotation: original (200, 100) -> transposed (100, 200)
    _create_synthetic_photos(
        photos_dir,
        [("IMG_0001.JPG", (200, 100), 6, "2024:03:15 10:30:00")],
    )

    dest_file = tmp_path / "processed" / "opaque123.jpg"
    sha256, dto = intake_real.process_image(photos_dir / "IMG_0001.JPG", dest_file)

    assert dest_file.exists()
    assert dto == "2024:03:15 10:30:00"

    with Image.open(dest_file) as processed_im:
        # 1. Orientation transposed: 200x100 -> 100x200
        assert processed_im.size == (100, 200)

        # 2. GPS stripped
        exif = processed_im.getexif()
        assert ExifTags.IFD.GPSInfo not in exif
        assert not bool(exif.get_ifd(ExifTags.IFD.GPSInfo))

        # 3. Sensitive tags stripped
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        assert ExifTags.Base.LensModel not in exif_ifd

        # 4. DateTimeOriginal preserved and written back
        assert exif_ifd.get(ExifTags.Base.DateTimeOriginal) == "2024:03:15 10:30:00"


def test_intake_dry_run_leaves_disk_untouched(tmp_path: Path):
    """Dry run validates inputs and displays counts without writing files or vault archives."""
    photos_dir = tmp_path / "photos"
    _create_synthetic_photos(
        photos_dir,
        [
            ("IMG_01.JPG", (100, 100), 1, "2024:01:01 12:00:00"),
            ("IMG_02.JPG", (100, 100), 1, "2024:01:01 12:01:00"),
        ],
    )

    trans_dir = tmp_path / "transcriptions" / "recipe_01"
    trans_dir.mkdir(parents=True)
    (trans_dir / "faces.txt").write_text("IMG_01.JPG\nIMG_02.JPG\n", encoding="utf-8")
    (trans_dir / "transcription.md").write_text("# Sourdough Bread\n", encoding="utf-8")

    corp_dir = tmp_path / "corpora"
    vault_dir = tmp_path / "vault"

    summary = intake_real.intake_photos(
        photos_dir=photos_dir,
        transcriptions_path=tmp_path / "transcriptions",
        deck_id="d01",
        tier="T4",
        dry_run=True,
        corpora_dir=corp_dir,
        vault_dir=vault_dir,
    )

    assert summary["dry_run"] is True
    assert summary["recipes_count"] == 1
    assert summary["images_count"] == 2
    assert not corp_dir.exists()
    assert not vault_dir.exists()


def test_intake_full_pipeline_and_vault_encryption(tmp_path: Path):
    """Full intake creates opaque files, DECKS.json, and encrypted vault GT adhering to schema rb-gt/1."""
    ident_path, recipients_path = _setup_vault_keys(tmp_path)

    # 1. Prepare photos
    photos_dir = tmp_path / "camera_photos"
    _create_synthetic_photos(
        photos_dir,
        [
            ("IMG_101.JPG", (150, 100), 1, "2024:05:01 09:00:00"),
            ("IMG_102.JPG", (150, 100), 1, "2024:05:01 09:01:00"),
            ("IMG_103.JPG", (120, 120), 1, "2024:05:01 09:05:00"),
        ],
    )

    # 2. Prepare transcriptions (2 recipes: 1 multi-card, 1 single card)
    trans_root = tmp_path / "transcriptions"

    # Recipe 1: 2 faces
    r1_dir = trans_root / "lemon_cake"
    r1_dir.mkdir(parents=True)
    (r1_dir / "faces.txt").write_text("IMG_101.JPG\nIMG_102.JPG\n", encoding="utf-8")
    (r1_dir / "transcription.md").write_text(
        """# Lemon Velvet Cake

Yield: 8 servings
Source: Grandma Betty

## Ingredients
- 2 1/2 cups flour
- 1 cup sugar
- 3 large eggs
- 1/2 tsp salt

## Directions
1. Mix dry ingredients in a large bowl.
2. Fold in lemon zest and eggs.
3. Bake at 350F for 35 minutes.

## Notes
- Best served with fresh raspberries.
""",
        encoding="utf-8",
    )

    # Recipe 2: 1 face
    r2_dir = trans_root / "quick_frosting"
    r2_dir.mkdir(parents=True)
    (r2_dir / "faces.txt").write_text("IMG_103.JPG\n", encoding="utf-8")
    (r2_dir / "transcription.md").write_text(
        """# Quick Frosting

## Ingredients
- 1 cup powdered sugar
- 2 tbsp milk

## Directions
1. Whisk until smooth.
""",
        encoding="utf-8",
    )

    corp_dir = tmp_path / "corpora"
    vault_dir = tmp_path / "vault"

    # 3. Execute intake
    summary = intake_real.intake_photos(
        photos_dir=photos_dir,
        transcriptions_path=trans_root,
        deck_id="d01",
        tier="T4",
        deck_type="mixed",
        dry_run=False,
        corpora_dir=corp_dir,
        vault_dir=vault_dir,
        identity_path=ident_path,
        recipients_path=recipients_path,
    )

    assert summary["dry_run"] is False
    assert summary["recipes_count"] == 2
    assert summary["images_count"] == 3

    # 4. Check corpus images
    deck_images_dir = corp_dir / "t4" / "d01"
    assert deck_images_dir.exists()
    written_images = list(deck_images_dir.glob("*.jpg"))
    assert len(written_images) == 3
    # Opaque names: none of the written files should be named IMG_101.JPG
    written_names = {p.name for p in written_images}
    assert "IMG_101.JPG" not in written_names
    assert "IMG_102.JPG" not in written_names
    assert "IMG_103.JPG" not in written_names

    # 5. Check DECKS.json
    decks_json = corp_dir / "t4" / "DECKS.json"
    assert decks_json.exists()
    decks_meta = json.loads(decks_json.read_text(encoding="utf-8"))
    assert decks_meta == [{"deck_id": "d01", "n_images": 3}]

    # 6. Check Vault archive: plaintext GT is NEVER written to disk
    vault_archive = vault_dir / "t4.gt.enc"
    assert vault_archive.exists()
    # Confirm no plaintext json or txt files exist in vault_dir
    unencrypted_files = [p.name for p in vault_dir.iterdir() if not p.name.endswith(".enc")]
    assert unencrypted_files == [], f"Found unencrypted files in vault: {unencrypted_files}"

    # 7. Decrypt GT and verify schema rb-gt/1
    decrypted_bytes = vault.decrypt_bytes(vault_archive.read_bytes(), identity_path=ident_path)
    gt_data = json.loads(decrypted_bytes.decode("utf-8"))

    assert gt_data["schema"] == "rb-gt/1"
    assert gt_data["tier"] == "T4"
    assert len(gt_data["decks"]) == 1

    deck0 = gt_data["decks"][0]
    assert deck0["deck_id"] == "d01"
    assert deck0["deck_type"] == "mixed"
    assert len(deck0["images"]) == 3
    assert len(deck0["recipes"]) == 2

    # Verify recipe 1 structure
    rec1 = deck0["recipes"][0]
    assert len(rec1["faces"]) == 2
    assert rec1["structured"]["title"] == "Lemon Velvet Cake"
    assert rec1["structured"]["yield"] == "8 servings"
    assert rec1["structured"]["source_note"] == "Grandma Betty"
    assert len(rec1["structured"]["ingredients"]) == 4
    assert rec1["structured"]["ingredients"][0]["quantity"] == "2 1/2"
    assert rec1["structured"]["ingredients"][0]["unit"] == "cups"
    assert rec1["structured"]["ingredients"][0]["item"] == "flour"
    assert len(rec1["structured"]["directions"]) == 3
    assert len(rec1["structured"]["notes"]) == 1
    assert rec1["difficulty"]["faces"] == 2

    # Verify SHA256 hashes match files on disk
    for img_info in deck0["images"]:
        fpath = deck_images_dir / img_info["image_id"]
        assert fpath.exists()
        import hashlib
        assert img_info["sha256"] == hashlib.sha256(fpath.read_bytes()).hexdigest()


def test_merge_multiple_decks_in_vault(tmp_path: Path):
    """Ingesting a second deck merges into the existing encrypted vault archive."""
    ident_path, recipients_path = _setup_vault_keys(tmp_path)
    corp_dir = tmp_path / "corpora"
    vault_dir = tmp_path / "vault"

    # Deck 1
    p_dir1 = tmp_path / "photos_1"
    _create_synthetic_photos(p_dir1, [("A.jpg", (100, 100), 1, "2024:01:01 10:00:00")])
    t_dir1 = tmp_path / "trans_1" / "r1"
    t_dir1.mkdir(parents=True)
    (t_dir1 / "faces.txt").write_text("A.jpg\n", encoding="utf-8")
    (t_dir1 / "transcription.md").write_text("# Recipe One\n", encoding="utf-8")

    intake_real.intake_photos(
        photos_dir=p_dir1,
        transcriptions_path=tmp_path / "trans_1",
        deck_id="d01",
        tier="T4",
        corpora_dir=corp_dir,
        vault_dir=vault_dir,
        identity_path=ident_path,
        recipients_path=recipients_path,
    )

    # Deck 2
    p_dir2 = tmp_path / "photos_2"
    _create_synthetic_photos(p_dir2, [("B.jpg", (100, 100), 1, "2024:01:02 10:00:00")])
    t_dir2 = tmp_path / "trans_2" / "r2"
    t_dir2.mkdir(parents=True)
    (t_dir2 / "faces.txt").write_text("B.jpg\n", encoding="utf-8")
    (t_dir2 / "transcription.md").write_text("# Recipe Two\n", encoding="utf-8")

    intake_real.intake_photos(
        photos_dir=p_dir2,
        transcriptions_path=tmp_path / "trans_2",
        deck_id="d02",
        tier="T4",
        corpora_dir=corp_dir,
        vault_dir=vault_dir,
        identity_path=ident_path,
        recipients_path=recipients_path,
    )

    # Verify DECKS.json has both decks
    decks_json = corp_dir / "t4" / "DECKS.json"
    decks_list = json.loads(decks_json.read_text(encoding="utf-8"))
    deck_ids = [d["deck_id"] for d in decks_list]
    assert "d01" in deck_ids
    assert "d02" in deck_ids

    # Verify vault archive decrypts with both decks
    vault_archive = vault_dir / "t4.gt.enc"
    decrypted = vault.decrypt_bytes(vault_archive.read_bytes(), identity_path=ident_path)
    gt_data = json.loads(decrypted.decode("utf-8"))
    vault_deck_ids = [d["deck_id"] for d in gt_data["decks"]]
    assert "d01" in vault_deck_ids
    assert "d02" in vault_deck_ids


def test_missing_referenced_photo_raises_error(tmp_path: Path):
    """Referencing a photo that does not exist in photos_dir raises FileNotFoundError."""
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()

    trans_dir = tmp_path / "trans" / "r1"
    trans_dir.mkdir(parents=True)
    (trans_dir / "faces.txt").write_text("nonexistent.jpg\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="not found in photos directory"):
        intake_real.intake_photos(
            photos_dir=photos_dir,
            transcriptions_path=tmp_path / "trans",
            deck_id="d01",
            dry_run=True,
            corpora_dir=tmp_path / "corpora",
            vault_dir=tmp_path / "vault",
        )


def test_csv_transcriptions_support(tmp_path: Path):
    """CSV format for transcriptions is supported."""
    ident_path, recipients_path = _setup_vault_keys(tmp_path)
    photos_dir = tmp_path / "photos"
    _create_synthetic_photos(
        photos_dir,
        [
            ("card_a.jpg", (100, 100), 1, "2024:01:01 10:00:00"),
            ("card_b.jpg", (100, 100), 1, "2024:01:01 10:01:00"),
        ],
    )

    csv_file = tmp_path / "transcriptions.csv"
    csv_file.write_text(
        "recipe_id,faces,title,ingredients,directions,notes\n"
        "r001,\"card_a.jpg, card_b.jpg\",\"Berry Cobbler\",\"1 cup berries\n2 tbsp sugar\",\"Mix and bake\",\"Serve warm\"\n",
        encoding="utf-8",
    )

    summary = intake_real.intake_photos(
        photos_dir=photos_dir,
        transcriptions_path=csv_file,
        deck_id="d03",
        tier="T4",
        dry_run=False,
        corpora_dir=tmp_path / "corpora",
        vault_dir=tmp_path / "vault",
        identity_path=ident_path,
        recipients_path=recipients_path,
    )

    assert summary["recipes_count"] == 1
    assert summary["images_count"] == 2


def test_cli_invocation_dry_run(tmp_path: Path, capsys):
    """CLI --dry-run prints counts to stdout and exits with code 0."""
    photos_dir = tmp_path / "photos"
    _create_synthetic_photos(photos_dir, [("IMG_1.jpg", (100, 100), 1, "2024:01:01 10:00:00")])
    trans_dir = tmp_path / "trans" / "r1"
    trans_dir.mkdir(parents=True)
    (trans_dir / "faces.txt").write_text("IMG_1.jpg\n", encoding="utf-8")

    argv = [
        "--photos",
        str(photos_dir),
        "--transcriptions",
        str(tmp_path / "trans"),
        "--deck-id",
        "d99",
        "--dry-run",
    ]

    ret = intake_real.main(argv)
    assert ret == 0
    captured = capsys.readouterr()
    assert "[DRY RUN]" in captured.out
    assert "d99" in captured.out
