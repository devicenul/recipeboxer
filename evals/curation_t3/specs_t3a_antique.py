"""Frozen candidate specs of the ARCHIVED antique tier T3A (WP-T3A).

25 real 17th-century English manuscript recipes (Folger Shakespeare Library / Wellcome Collection,
transcribed on FromThePage). This was the original T3 corpus; it is now archived as the opt-in tier
T3A (images: evals/corpora/archive/t3_antique/, GT: evals/vault/archive/t3a.gt.enc) because the product
targets recipes from the 1900s onward. Kept only for provenance / reproducibility; use with
``python -m evals.curation_t3.fetch --preset t3a_antique --tier T3A ...``.
"""

from __future__ import annotations

from typing import Any, Dict, List

T3A_ANTIQUE_SPECS: List[Dict[str, Any]] = [
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
