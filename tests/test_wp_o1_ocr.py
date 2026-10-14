"""Behavioral and unit tests for recipeboxer.vision.ocr (WP-O1 spike).

Tests cover:
- OCRLine data structure
- LocalOCR availability check and fail-fast behavior when missing (INV-AC6)
- LocalOCR read behavior with real/mock engines
- boundary_cues extraction on diverse hand-written string sequences
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from recipeboxer.vision.ocr import LocalOCR, OCRLine, boundary_cues


def test_ocr_line_dataclass() -> None:
    """Verifies OCRLine creation, attributes, and immutability."""
    line = OCRLine(text="Sugar 2 cups", bbox=[[0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [0.0, 5.0]], confidence=0.95)
    assert line.text == "Sugar 2 cups"
    assert len(line.bbox) == 4
    assert line.confidence == 0.95

    # Default values
    line_default = OCRLine(text="Flour")
    assert line_default.text == "Flour"
    assert line_default.bbox == []
    assert line_default.confidence == 1.0


def test_local_ocr_available() -> None:
    """Verifies LocalOCR reports availability correctly based on environment."""
    # When installed in the venv, available() should return True
    is_avail = LocalOCR.available()
    assert isinstance(is_avail, bool)


def test_local_ocr_missing_package_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that LocalOCR gracefully reports unavailable and raises on read when package is missing."""
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)

    assert LocalOCR.available() is False

    ocr = LocalOCR()
    with pytest.raises(RuntimeError, match="Local OCR engine is unavailable"):
        ocr.read("dummy_path.jpg")


def test_local_ocr_read_with_mock_engine() -> None:
    """Verifies LocalOCR translates raw engine outputs into OCRLine objects."""
    mock_engine = MagicMock()
    # RapidOCR output format: (list_of_[box, text, score], elapse_list)
    mock_engine.return_value = (
        [
            [[[10.0, 20.0], [100.0, 20.0], [100.0, 40.0], [10.0, 40.0]], "Preheat oven to 350F", "0.9234"],
            [[[10.0, 50.0], [120.0, 50.0], [120.0, 70.0], [10.0, 70.0]], "1 cup brown sugar", 0.881],
        ],
        [0.1, 0.05, 0.2],
    )

    ocr = LocalOCR(engine=mock_engine)
    lines = ocr.read("dummy_image.jpg")

    assert len(lines) == 2
    assert lines[0].text == "Preheat oven to 350F"
    assert lines[0].bbox[0] == [10.0, 20.0]
    assert pytest.approx(lines[0].confidence, 0.001) == 0.9234
    assert lines[1].text == "1 cup brown sugar"
    assert pytest.approx(lines[1].confidence, 0.001) == 0.881


def test_local_ocr_read_empty_or_none() -> None:
    """Verifies LocalOCR handles images with no text detected."""
    mock_engine = MagicMock()
    mock_engine.return_value = (None, [0.01, 0.01, 0.01])

    ocr = LocalOCR(engine=mock_engine)
    assert ocr.read("blank.jpg") == []


def test_boundary_cues_empty_input() -> None:
    """Verifies boundary_cues on empty sequence returns inactive defaults."""
    res = boundary_cues([])
    assert res["has_continuation_cue"] is False
    assert res["has_continuation_start"] is False
    assert res["continuation_tokens"] == []
    assert res["step_numbers"] == []
    assert res["page_markers"] == []
    assert res["is_cutoff"] is False


def test_boundary_cues_forward_handwritten_strings() -> None:
    """Tests forward continuation cues on representative hand-written strings."""
    # (over ->) cue at bottom
    sample_1 = [
        "Grandma's Apple Crisp",
        "5 apples, peeled",
        "1/2 cup sugar",
        "(over ->)",
    ]
    res1 = boundary_cues(sample_1)
    assert res1["has_continuation_cue"] is True
    assert any("over" in tok for tok in res1["continuation_tokens"])

    # PTO cue
    sample_2 = [
        "Chocolate Fudge",
        "2 cups sugar",
        "PTO >>>",
    ]
    res2 = boundary_cues(sample_2)
    assert res2["has_continuation_cue"] is True
    assert "pto" in res2["continuation_tokens"]

    # Continued on next card
    sample_3 = [
        "Swedish Meatballs",
        "Mix pork and beef",
        "continued on next page",
    ]
    res3 = boundary_cues(sample_3)
    assert res3["has_continuation_cue"] is True


def test_boundary_cues_continuation_headers() -> None:
    """Tests reverse continuation cues / header cues at card top."""
    sample = [
        "Apple Crisp (continued)",
        "4. Cut in butter until coarse crumbs form",
        "5. Bake at 375 for 35 mins",
    ]
    res = boundary_cues(sample)
    assert res["has_continuation_start"] is True
    assert 4 in res["step_numbers"]
    assert 5 in res["step_numbers"]
    assert res["first_step"] == 4


def test_boundary_cues_step_numbering() -> None:
    """Tests extraction of sequential step numbers from recipe instructions."""
    sample = [
        "1. In a large bowl whisk flour and salt.",
        "2. Add cold cubed butter.",
        "3. Mix until dough comes together.",
    ]
    res = boundary_cues(sample)
    assert res["step_numbers"] == [1, 2, 3]
    assert res["first_step"] == 1
    assert res["last_step"] == 3
    assert res["has_continuation_start"] is False


def test_boundary_cues_page_markers_at_edges() -> None:
    """Tests detection of page and card markers at top or bottom boundaries."""
    sample = [
        "Page 2 of 2",
        "Cream butter and sugar until fluffy.",
        "Bake until golden brown.",
        "- 2 -",
    ]
    res = boundary_cues(sample)
    assert 2 in res["page_numbers"]
    assert res["top_page_marker"] == 2
    assert res["bottom_page_marker"] == 2
    assert res["has_continuation_start"] is True


def test_boundary_cues_with_ocr_lines() -> None:
    """Tests boundary_cues when receiving OCRLine instances with 2D bboxes."""
    ocr_lines = [
        OCRLine(text="Page 1", bbox=[[10.0, 5.0], [50.0, 5.0], [50.0, 15.0], [10.0, 15.0]]),
        OCRLine(text="1. Heat oil in skillet", bbox=[[10.0, 40.0], [150.0, 40.0], [150.0, 55.0], [10.0, 55.0]]),
        OCRLine(text="turn over for sauce", bbox=[[10.0, 90.0], [120.0, 90.0], [120.0, 100.0], [10.0, 100.0]]),
    ]
    res = boundary_cues(ocr_lines)
    assert res["top_page_marker"] == 1
    assert res["has_continuation_cue"] is True
    assert "turn over" in res["continuation_tokens"]
    assert res["step_numbers"] == [1]


def test_boundary_cues_cutoff_detection() -> None:
    """Tests trailing cutoff detection when line ends abruptly."""
    sample = [
        "Combine milk and flour,",
        "whisk vigorously with",
    ]
    res = boundary_cues(sample)
    assert res["is_cutoff"] is True

    clean_sample = [
        "Combine milk and flour.",
        "Whisk vigorously.",
    ]
    assert boundary_cues(clean_sample)["is_cutoff"] is False
