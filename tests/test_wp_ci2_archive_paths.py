"""WP-CI2: portable (OS-independent) ZipSlip validation for .recipeboxer archives."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from recipeboxer.storage.archive import (
    _portable_relative_member_path,
    extract_recipeboxer_archive,
    validate_archive_manifest,
)


@pytest.mark.parametrize(
    "name",
    [
        "..\\evil.txt",
        "a\\..\\..\\evil.txt",
        "C:evil.txt",
        "c:/evil.txt",
        "Z:\\x\\y.txt",
        "\\\\server\\share\\evil.txt",
        "//server/share/evil.txt",
        "assets/scans/x.jpg:stream",
        "a/\x00b",
        "",
        "/",
        "..",
    ],
)
def test_rejected_names(name: str) -> None:
    with pytest.raises(ValueError, match="ZipSlip path traversal attempt"):
        _portable_relative_member_path(name)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("manifest.json", "manifest.json"),
        ("assets\\scans\\abc.jpg", "assets/scans/abc.jpg"),
        ("/assets/scans/abc.jpg", "assets/scans/abc.jpg"),
        ("\\evil.txt", "evil.txt"),
        ("./a/./b/../c.txt", "a/c.txt"),
        ("assets/", "assets"),
    ],
)
def test_accepted_names_are_normalized(name: str, expected: str) -> None:
    assert _portable_relative_member_path(name) == expected


def test_manifest_asset_archive_path_traversal_rejected() -> None:
    manifest = {
        "format_version": "1.0.0",
        "project_id": "p",
        "project_name": "n",
        "assets": [
            {
                "sha256_hash": "0" * 64,
                "filename": "x.jpg",
                "archive_path": "..\\..\\outside.jpg",
            }
        ],
    }
    with pytest.raises(ValueError, match="ZipSlip"):
        validate_archive_manifest(manifest)


def test_backslash_entry_extracted_inside_target(tmp_path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": "1.0.0", "project_id": "p", "project_name": "n"}))
        zf.writestr("assets\\scans\\a.txt", b"ok")
    target = tmp_path / "out"
    extract_recipeboxer_archive(buf.getvalue(), target, verify_hashes=False)
    assert (target / "assets" / "scans" / "a.txt").read_bytes() == b"ok"


def test_sibling_prefix_directory_not_confused(tmp_path) -> None:
    """'out' must not accept writes into 'out2' (old str.startswith check was prefix-unsafe)."""
    (tmp_path / "out2").mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": "1.0.0", "project_id": "p", "project_name": "n"}))
        zf.writestr("../out2/evil.txt", b"x")
    with pytest.raises(ValueError):
        extract_recipeboxer_archive(buf.getvalue(), tmp_path / "out", verify_hashes=False)
    assert not (tmp_path / "out2" / "evil.txt").exists()
