"""T3 Real-Web Corpus Fetch & Curation Tooling.

Downloads public-domain and CC-licensed recipe documents from archival sources,
normalizes image resolution and orientation, strips metadata, and builds
ground-truth records in-memory, encrypting directly into the evaluation vault.

Enforces Invariants:
- SECRECY: Verbatim transcription text and recipe titles never touch disk in
  plaintext within the workspace; encrypted directly into evals/vault/t3.gt.enc.
- OPAQUE IDENTIFIERS: All images receive random hex identifiers (secrets.token_hex(6)).
- IMAGE LIMITS: Long side <= 2000px, EXIF orientation applied, metadata stripped,
  total deck size <= 30 MB.
- ROBUSTNESS: Persistent caching under $PROJECT_CACHE_DIR outside the workspace,
  retry with exponential backoff on timeouts/WAF limits via httpx.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from PIL import Image, ImageOps

from evals import vault

HTTP_HEADERS = {
    "User-Agent": "curl/8.5.0",
    "Accept": "*/*",
}


CANDIDATE_SPECS: List[Dict[str, Any]] = [
    # 1. Lady Ann Fanshawe MS 7113: folios 4r and 4v form a 2-page sequential continuation pair
    {
        "recipe_ref": "fanshawe_f04_pair",
        "doc_type": "manuscript page (pair)",
        "stock_id": "stock_fol_ms7113",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "fanshawe_f04r",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/fanshawe-lady-ann-1625-1680/display/1680793",
                "attribution_desc": "Folger Shakespeare Library MS 7113, folio 4r (digitized via Wellcome Collection b19612503_MS7113_0005)",
                "modality": "HANDWRITTEN_CURSIVE",
            },
            {
                "face_ref": "fanshawe_f04v",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/fanshawe-lady-ann-1625-1680/display/1680794",
                "attribution_desc": "Folger Shakespeare Library MS 7113, folio 4v (digitized via Wellcome Collection b19612503_MS7113_0006)",
                "modality": "HANDWRITTEN_CURSIVE",
            },
        ],
    },
    # 2. Lady Ann Fanshawe MS 7113: folio 5r
    {
        "recipe_ref": "fanshawe_f05r",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms7113",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "fanshawe_f05r",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/fanshawe-lady-ann-1625-1680/display/1680795",
                "attribution_desc": "Folger Shakespeare Library MS 7113, folio 5r (digitized via Wellcome Collection b19612503_MS7113_0007)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    # 3. MS 9317: English cookery notebook page
    {
        "recipe_ref": "ms9317_p08",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms9317",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "ms9317_p08",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/ms-9317/display/33044308",
                "attribution_desc": "Folger Shakespeare Library MS 9317, leaf 8 (digitized via Wellcome Collection b30745239)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    # 4-7. Lady Ayscough MS 1026: 4 pages
    {
        "recipe_ref": "ayscough_p02",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms1026",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "ayscough_p02",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/ayscough-lady/display/1780356",
                "attribution_desc": "Folger Shakespeare Library MS 1026, folio 2 (digitized via Wellcome Collection b19680326)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "ayscough_p03",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms1026",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "ayscough_p03",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/ayscough-lady/display/1780357",
                "attribution_desc": "Folger Shakespeare Library MS 1026, folio 3 (digitized via Wellcome Collection b19680326)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "ayscough_p04",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms1026",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "ayscough_p04",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/ayscough-lady/display/1780358",
                "attribution_desc": "Folger Shakespeare Library MS 1026, folio 4 (digitized via Wellcome Collection b19680326)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "ayscough_p05",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms1026",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "ayscough_p05",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/ayscough-lady/display/1780359",
                "attribution_desc": "Folger Shakespeare Library MS 1026, folio 5 (digitized via Wellcome Collection b19680326)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    # 8-11. Anne Brumwich MS 160: 4 pages
    {
        "recipe_ref": "brumwich_p02",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms160",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "brumwich_p02",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/brumwich-anne-others/display/25084222",
                "attribution_desc": "Folger Shakespeare Library MS 160, folio 2 (digitized via Wellcome Collection b1968132x)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "brumwich_p03",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms160",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "brumwich_p03",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/brumwich-anne-others/display/25084223",
                "attribution_desc": "Folger Shakespeare Library MS 160, folio 3 (digitized via Wellcome Collection b1968132x)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "brumwich_p04",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms160",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "brumwich_p04",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/brumwich-anne-others/display/25084224",
                "attribution_desc": "Folger Shakespeare Library MS 160, folio 4 (digitized via Wellcome Collection b1968132x)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "brumwich_p05",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms160",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "brumwich_p05",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/brumwich-anne-others/display/25084225",
                "attribution_desc": "Folger Shakespeare Library MS 160, folio 5 (digitized via Wellcome Collection b1968132x)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    # 12-13. Elizabeth Bulkeley MS 169: 2 pages
    {
        "recipe_ref": "bulkeley_p02",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms169",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "bulkeley_p02",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/bulkeley-elizabeth/display/32696396",
                "attribution_desc": "Folger Shakespeare Library MS 169, folio 2 (digitized via Wellcome Collection b19681719)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "bulkeley_p05",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms169",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "bulkeley_p05",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/bulkeley-elizabeth/display/32696399",
                "attribution_desc": "Folger Shakespeare Library MS 169, folio 5 (digitized via Wellcome Collection b19681719)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    # 14-16. Coley Family MS 1711: 3 pages
    {
        "recipe_ref": "coley_p02",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms1711",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "coley_p02",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/coley-family/display/32492432",
                "attribution_desc": "Folger Shakespeare Library MS 1711, folio 2 (digitized via Wellcome Collection b19682542)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "coley_p03",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms1711",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "coley_p03",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/coley-family/display/32492433",
                "attribution_desc": "Folger Shakespeare Library MS 1711, folio 3 (digitized via Wellcome Collection b19682542)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "coley_p05",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms1711",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "coley_p05",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/coley-family/display/32492435",
                "attribution_desc": "Folger Shakespeare Library MS 1711, folio 5 (digitized via Wellcome Collection b19682542)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    # 17. Cookery MS 1810: page 3
    {
        "recipe_ref": "cookery_p03",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms1810",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "cookery_p03",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/cookery-books-18th-cent/display/25083681",
                "attribution_desc": "Folger Shakespeare Library MS 1810, page 3 (digitized via Wellcome Collection b19682852)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    # 18-19. English Recipe Book MS 6956: 2 pages
    {
        "recipe_ref": "erb_p02",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms6956",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "erb_p02",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/english-recipe-book-18th-century/display/1653219",
                "attribution_desc": "Folger Shakespeare Library MS 6956, folio 2 (digitized via Wellcome Collection b19608937)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "erb_p04",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms6956",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "erb_p04",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/english-recipe-book-18th-century/display/1653221",
                "attribution_desc": "Folger Shakespeare Library MS 6956, folio 4 (digitized via Wellcome Collection b19608937)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    # 20-21. Mary Hawker MS 9304: 2 pages
    {
        "recipe_ref": "hawker_p04",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms9304",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "hawker_p04",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/mary-hawker-s-recipe-book/display/32549615",
                "attribution_desc": "Folger Shakespeare Library MS 9304, leaf 4 (digitized via Wellcome Collection b3074465x)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "hawker_p05",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms9304",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "hawker_p05",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/mary-hawker-s-recipe-book/display/32549616",
                "attribution_desc": "Folger Shakespeare Library MS 9304, leaf 5 (digitized via Wellcome Collection b3074465x)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    # 22. Receipt Book MS 4054: page 5
    {
        "recipe_ref": "receipt_p05",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms4054",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "good",
        "faces": [
            {
                "face_ref": "receipt_p05",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/receipt-book-17th-18th-century/display/33703500",
                "attribution_desc": "Folger Shakespeare Library MS 4054, folio 5 (digitized via Wellcome Collection b19339240)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    # 23-25. Jane Baber MS 108: 3 pages
    {
        "recipe_ref": "baber_p02",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms108",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "baber_p02",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/baber-jane/display/36176003",
                "attribution_desc": "Folger Shakespeare Library MS 108, folio 2 (digitized via Wellcome Collection b1968101x)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "baber_p03",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms108",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "baber_p03",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/baber-jane/display/36176004",
                "attribution_desc": "Folger Shakespeare Library MS 108, folio 3 (digitized via Wellcome Collection b1968101x)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
    {
        "recipe_ref": "baber_p04",
        "doc_type": "manuscript page",
        "stock_id": "stock_fol_ms108",
        "institution": "Folger Shakespeare Library / Wellcome Collection",
        "license": "Public Domain Mark (PD)",
        "legibility": "fair",
        "faces": [
            {
                "face_ref": "baber_p04",
                "display_url": "https://fromthepage.com/folger/early-modern-recipe-books/baber-jane/display/36176005",
                "attribution_desc": "Folger Shakespeare Library MS 108, folio 4 (digitized via Wellcome Collection b1968101x)",
                "modality": "HANDWRITTEN_CURSIVE",
            }
        ],
    },
]


def clean_html_transcription(raw_html: str) -> str:
    """Extract clean verbatim transcription lines from HTML."""
    text = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def fetch_bytes_with_retry(
    client: httpx.Client,
    url: str,
    max_retries: int = 4,
    base_delay: float = 1.5,
) -> bytes:
    """Fetch URL bytes with exponential backoff retry via httpx."""
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.content
            if resp.status_code == 429:
                time.sleep(base_delay * (2**attempt))
                continue
            resp.raise_for_status()
        except Exception as e:
            last_err = e
            time.sleep(base_delay * (1.8**attempt))
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts: {last_err}")


def process_image(img_bytes: bytes) -> Tuple[bytes, Tuple[int, int], str]:
    """Normalize image: EXIF orientation, max 2000px, strip metadata, compute sha256."""
    with Image.open(io.BytesIO(img_bytes)) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > 2000:
            im.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
        out_buf = io.BytesIO()
        im.save(out_buf, format="JPEG", quality=85, optimize=True)
        final_bytes = out_buf.getvalue()
        sha = hashlib.sha256(final_bytes).hexdigest()
        return final_bytes, im.size, sha


def fetch_t3_corpus(
    corpus_dir: Path,
    vault_file: Path,
    cache_dir: Path,
    recipients_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute T3 curation pipeline and encrypt GT in memory."""
    # Ensure intermediate cache directory outside the workspace
    resolved_cache = Path(cache_dir).expanduser().resolve()
    resolved_cache.mkdir(parents=True, exist_ok=True)
    raw_cache = resolved_cache / "raw"
    raw_cache.mkdir(parents=True, exist_ok=True)

    # Deck output path: evals/corpora/t3/d01
    deck_dir = corpus_dir / "d01"
    deck_dir.mkdir(parents=True, exist_ok=True)
    for existing_img in deck_dir.glob("*.jpg"):
        existing_img.unlink()

    gt_images: List[Dict[str, Any]] = []
    gt_recipes: List[Dict[str, Any]] = []
    attribution_rows: List[Dict[str, str]] = []

    total_images = 0
    total_recipes = 0
    total_bytes = 0

    print(f"Starting T3 curation across {len(CANDIDATE_SPECS)} candidate recipe definitions...")

    with httpx.Client(timeout=35.0, headers=HTTP_HEADERS, follow_redirects=True) as client:
        for r_idx, spec in enumerate(CANDIDATE_SPECS, start=1):
            recipe_id = f"r{r_idx:03d}"
            recipe_faces: List[str] = []
            recipe_verbatim: List[str] = []

            print(f"[{r_idx}/{len(CANDIDATE_SPECS)}] Curating {spec['recipe_ref']} ({len(spec['faces'])} face(s))...")

            for f_idx, face_spec in enumerate(spec["faces"], start=1):
                f_ref = face_spec["face_ref"]
                disp_url = face_spec["display_url"]

                # 1. Fetch display HTML (using persistent cache if present)
                html_cache_path = raw_cache / f"{f_ref}.html"
                if html_cache_path.exists():
                    page_html = html_cache_path.read_text(encoding="utf-8")
                else:
                    page_html = fetch_bytes_with_retry(client, disp_url).decode("utf-8")
                    html_cache_path.write_text(page_html, encoding="utf-8")

                # 2. Extract IIIF tileSource and transcription preview
                tile_match = re.search(r'tileSources:\s*\["([^"]+)"\]', page_html)
                preview_match = re.search(r'<div class="page-preview[^"]*"[^>]*>(.*?)</div>', page_html, re.DOTALL)

                if not tile_match:
                    raise ValueError(f"Tile source missing on {disp_url}")
                if not preview_match:
                    raise ValueError(f"Transcription preview missing on {disp_url}")

                trans_text = clean_html_transcription(preview_match.group(1))
                if not trans_text:
                    raise ValueError(f"Extracted empty transcription for {disp_url}")

                # 3. Construct IIIF image stream URL (capped at 2000px long side)
                tile_src = tile_match.group(1)
                base_iiif = tile_src[:-len("/info.json")] if tile_src.endswith("/info.json") else tile_src
                iiif_img_url = f"{base_iiif}/full/!2000,2000/0/default.jpg"

                # 4. Fetch raw image bytes (using cache if present)
                img_cache_path = raw_cache / f"{f_ref}.jpg"
                if img_cache_path.exists():
                    raw_img_bytes = img_cache_path.read_bytes()
                else:
                    raw_img_bytes = fetch_bytes_with_retry(client, iiif_img_url)
                    img_cache_path.write_bytes(raw_img_bytes)

                # 5. Process image (EXIF orientation, metadata stripping, JPEG compression)
                processed_bytes, (img_w, img_h), sha256_hash = process_image(raw_img_bytes)

                # 6. Assign opaque random image ID (secrets.token_hex(6) + .jpg)
                opaque_id = f"{secrets.token_hex(6)}.jpg"
                img_dest = deck_dir / opaque_id
                img_dest.write_bytes(processed_bytes)

                total_images += 1
                total_bytes += len(processed_bytes)

                gt_images.append({
                    "image_id": opaque_id,
                    "sha256": sha256_hash,
                    "modality": face_spec["modality"],
                    "stock_id": spec["stock_id"],
                })

                recipe_faces.append(opaque_id)
                recipe_verbatim.append(trans_text)

                attribution_rows.append({
                    "image_id": opaque_id,
                    "institution": spec["institution"],
                    "item_url": disp_url,
                    "license": spec["license"],
                    "attribution": face_spec["attribution_desc"],
                })

                time.sleep(0.3)  # Polite pause between items

            total_recipes += 1
            gt_recipes.append({
                "recipe_id": recipe_id,
                "faces": recipe_faces,
                "verbatim": recipe_verbatim,
                "structured": None,
                "structured_status": "pending_text_only_draft",
                "difficulty": {
                    "legibility": spec["legibility"],
                    "faces": len(recipe_faces),
                },
                "excluded_reason": None,
            })

    # Validate image size ceiling (<= 30 MB)
    total_mb = total_bytes / (1024 * 1024)
    print(f"Deck d01 curation completed: {total_images} images, {total_recipes} recipes, {total_mb:.2f} MB")
    if total_mb > 30.0:
        raise ValueError(f"Corpus size ({total_mb:.2f} MB) exceeds maximum 30 MB contract requirement.")

    # Build GT JSON structure in memory
    gt_payload: Dict[str, Any] = {
        "schema": "rb-gt/1",
        "tier": "T3",
        "created": datetime.now(timezone.utc).isoformat(),
        "generator": {
            "name": "evals.curation_t3.fetch",
            "version": "1.0.0",
        },
        "decks": [
            {
                "deck_id": "d01",
                "deck_type": "real_distractor",
                "images": gt_images,
                "recipes": gt_recipes,
            }
        ],
    }

    # Encrypt GT in memory and write directly to evals/vault/t3.gt.enc
    gt_bytes = json.dumps(gt_payload, indent=2).encode("utf-8")
    enc_bytes = vault.encrypt_bytes(gt_bytes, recipients_path=recipients_path)
    vault_file.parent.mkdir(parents=True, exist_ok=True)
    vault_file.write_bytes(enc_bytes)
    print(f"Encrypted GT written to {vault_file} ({len(enc_bytes)} bytes)")

    # Write PUBLIC deck descriptor (NO deck_type, NO GT)
    decks_json = corpus_dir / "DECKS.json"
    decks_json.write_text(
        json.dumps([{"deck_id": "d01", "n_images": total_images}], indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Public deck registry written to {decks_json}")

    # Write ATTRIBUTION.md (neutral identifiers, no secret recipe titles/text)
    attr_path = corpus_dir.parent / "real_web" / "ATTRIBUTION.md"
    attr_path.parent.mkdir(parents=True, exist_ok=True)

    attr_lines = [
        "# T3 Real-Web Corpus Attribution & Provenance",
        "",
        "This evaluation corpus contains real-world digitized manuscripts and documents",
        "from cultural heritage institutions, curated under permissive Open Access licenses.",
        "In accordance with evaluation secrecy and licensing compliance:",
        "- All assets are identified solely by opaque identifiers.",
        "- Specific recipe titles and verbatim texts are strictly excluded from repository metadata.",
        "- Rights and provenance are recorded per institution below.",
        "",
        "| Opaque Image ID | Source Institution | Item URL | Rights Statement & License | Attribution Line |",
        "|---|---|---|---|---|",
    ]

    for row in attribution_rows:
        attr_lines.append(
            f"| `{row['image_id']}` | {row['institution']} | [{row['institution']}]({row['item_url']}) | "
            f"{row['license']} | {row['attribution']} |"
        )

    attr_lines.append("")
    attr_path.write_text("\n".join(attr_lines), encoding="utf-8")
    print(f"Attribution registry written to {attr_path}")

    return {
        "total_images": total_images,
        "total_recipes": total_recipes,
        "total_mb": total_mb,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and curate T3 real-web corpus")
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=vault.get_repo_root() / "evals" / "corpora" / "t3",
        help="Destination directory for T3 corpus (default: evals/corpora/t3)",
    )
    parser.add_argument(
        "--vault-file",
        type=Path,
        default=vault.get_repo_root() / "evals" / "vault" / "t3.gt.enc",
        help="Destination path for encrypted GT vault file (default: evals/vault/t3.gt.enc)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("~/.cache/recipeboxer/t3_cache").expanduser(),
        help="Directory outside workspace for intermediate assets",
    )
    parser.add_argument(
        "--recipients",
        type=Path,
        default=None,
        help="Path to recipients.txt (default: evals/vault/recipients.txt)",
    )
    args = parser.parse_args(argv)

    try:
        fetch_t3_corpus(
            corpus_dir=args.corpus_dir,
            vault_file=args.vault_file,
            cache_dir=args.cache_dir,
            recipients_path=args.recipients,
        )
        return 0
    except Exception as e:
        sys.stderr.write(f"curation_t3.fetch error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
