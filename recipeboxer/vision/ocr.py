"""Optional complementary local OCR engine and text boundary cue detector.

Role & Invariants (INV-AC6, REMEDIATION_PLAN §8.4):
Local OCR is EVIDENCE ONLY, never a fallback.
1. Cheap text cues for grouping ('cont', 'over', 'continued', page-number-like
   tokens at top/bottom, step numbers) before spending LLM calls.
2. Independent check on numbers, units, and printed text during grounding (WP-X6).
3. Local OCR is NEVER used when unavailable; it NEVER substitutes for multimodal
   model output or bypasses LLM key requirements.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OCRLine:
    """A single recognized line of text with bounding box and confidence."""

    text: str
    bbox: List[List[float]] = field(default_factory=list)
    confidence: float = 1.0


class LocalOCR:
    """Optional complementary local OCR engine using RapidOCR (ONNX).

    Evidence only, never a fallback (INV-AC6).
    """

    def __init__(self, engine: Any = None) -> None:
        self._engine = engine

    @classmethod
    def available(cls) -> bool:
        """Checks if the local OCR engine is installed and functional."""
        import sys

        if sys.modules.get("rapidocr_onnxruntime") is None and "rapidocr_onnxruntime" in sys.modules:
            return False
        try:
            import importlib

            importlib.import_module("rapidocr_onnxruntime")
            return True
        except Exception:
            return False

    def read(self, image: Any) -> List[OCRLine]:
        """Runs local OCR on the given image.

        Args:
            image: Image as file path (str/Path), PIL Image, numpy array, or bytes.

        Returns:
            List[OCRLine]: Detected text lines with bounding polygon coords and scores.

        Raises:
            RuntimeError: If local OCR is unavailable (INV-AC6).
        """
        if not self.available() and self._engine is None:
            raise RuntimeError(
                "Local OCR engine is unavailable (rapidocr-onnxruntime not installed or available). "
                "Per INV-AC6, local OCR is evidence only and cannot run when unavailable."
            )

        if self._engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore

                self._engine = RapidOCR()
            except Exception as init_err:
                raise RuntimeError(f"Failed to initialize RapidOCR engine: {init_err}") from init_err

        input_data = image
        try:
            if hasattr(image, "convert") and hasattr(image, "size"):
                import numpy as np  # type: ignore

                input_data = np.array(image.convert("RGB"))
            elif isinstance(image, Path):
                input_data = str(image)
        except Exception as prep_err:
            logger.debug("Image conversion error: %s", prep_err)
            input_data = image

        try:
            raw_result, _ = self._engine(input_data)
        except Exception as ocr_err:
            logger.warning("RapidOCR execution error: %s", ocr_err)
            return []

        if not raw_result:
            return []

        lines: List[OCRLine] = []
        for raw_item in raw_result:
            try:
                coords = [[float(pt[0]), float(pt[1])] for pt in raw_item[0]]
                recognized_text = str(raw_item[1]).strip()
                score_val = float(raw_item[2])
                lines.append(OCRLine(text=recognized_text, bbox=coords, confidence=score_val))
            except Exception as parse_err:
                logger.debug("Failed parsing OCR line %s: %s", raw_item, parse_err)
                continue

        return lines


# Forward continuation cue phrases (directional or turn-over indicators)
_FORWARD_CUE_PHRASES = (
    "turn over",
    "see back",
    "p.t.o.",
    "p.t.o",
    "pto",
    "continued on",
    "cont'd on",
    "cont. on",
    "see next",
    "seenext",
    "(see",
    "see card",
    "seecard",
    "(over",
    "over ->",
    "over >",
    "over >>",
    "over >>>",
    "->",
    ">>>",
)

# Culinary words indicating 'over' is a food prep preposition rather than a card turn instruction
_CULINARY_OVER_CONTEXT = (
    "heat",
    "flame",
    "burner",
    "water",
    "boil",
    "simmer",
    "pour",
    "spread",
    "drizzle",
    "sprinkle",
    "layer",
    "top",
    "skillet",
    "pan",
    "pot",
    "bowl",
    "surface",
    "ice",
    "serve",
    "crust",
    "batter",
    "dough",
    "apples",
    "potatoes",
    "vegetables",
)

# Header / continuation start markers
_REVERSE_CUE_PHRASES = (
    "continued from",
    "cont'd from",
    "instructions continued",
    "directions continued",
    "recipe continued",
    "directionscontinued",
    "instructionscontinued",
)

# Step number pattern at line start: e.g. "1.", "Step 2:", "3)", or OCR artifacts "l," / "I."
_STEP_NUM_PATTERN = re.compile(r"^\s*(?:step\s*)?([1-9]|1[0-9]|l|I)[.):,\s]", re.IGNORECASE)

# Page number pattern: e.g. "Page 2", "Page 2 of 2", "Pg. 2"
_PAGE_TOKEN_PATTERN = re.compile(r"\b(?:page|pg\.?)\s*(\d{1,2})\b", re.IGNORECASE)

# Page X of Y pattern: e.g. "2 of 2", "1 / 2"
_PAGE_X_OF_Y_PATTERN = re.compile(r"\b(\d{1,2})\s*(?:of|/)\s*(\d{1,2})\b", re.IGNORECASE)

# Standalone bracketed or hyphenated numbers: e.g. "- 2 -", "(2)"
_STANDALONE_PAGE_PATTERN = re.compile(r"^\s*[-–—(]?\s*(\d{1,2})\s*[-–—)]?\s*$")


def boundary_cues(lines: Sequence[Union[str, OCRLine]]) -> Dict[str, Any]:
    """Extracts generic boundary cues from OCR or transcribed text lines.

    Identifies forward continuation cues ('cont', 'over', 'continued', 'pto'),
    continuation headers, page-like numbers at card top/bottom, and sequential
    step numbers.

    This function operates strictly on the text provided; it is generic across
    recipes and contains zero corpus-specific logic.

    Args:
        lines: Sequence of raw text strings or OCRLine objects.

    Returns:
        dict: Extracted boundary cues and structural signals.
    """
    if not lines:
        return {
            "has_continuation_cue": False,
            "has_continuation_start": False,
            "continuation_tokens": [],
            "page_numbers": [],
            "page_markers": [],
            "top_page_marker": None,
            "bottom_page_marker": None,
            "step_numbers": [],
            "first_step": None,
            "last_step": None,
            "is_cutoff": False,
            "cues": [],
        }

    # Extract text and optional vertical positions
    line_entries: List[Dict[str, Any]] = []
    max_y = 0.0

    for idx, item in enumerate(lines):
        if isinstance(item, str):
            text_str = item.strip()
            line_entries.append({"text": text_str, "y_mid": None, "index": idx})
        else:
            text_str = getattr(item, "text", str(item)).strip()
            bbox = getattr(item, "bbox", [])
            y_mid = None
            if bbox and len(bbox) >= 2:
                y_coords = [float(pt[1]) for pt in bbox if len(pt) >= 2]
                if y_coords:
                    y_mid = sum(y_coords) / len(y_coords)
                    if max(y_coords) > max_y:
                        max_y = max(y_coords)
            line_entries.append({"text": text_str, "y_mid": y_mid, "index": idx})

    total_lines = len(line_entries)
    continuation_tokens: List[str] = []
    continuation_headers: List[str] = []
    step_numbers: List[int] = []
    page_markers: List[Dict[str, Any]] = []
    all_cues: List[str] = []

    for entry in line_entries:
        line_text = entry["text"]
        if not line_text:
            continue

        line_lower = line_text.lower()
        words = line_lower.split()

        # Classify vertical position: top, bottom, or middle
        y_val = entry["y_mid"]
        if y_val is not None and max_y > 0.0:
            rel_y = y_val / max_y
            is_top = rel_y <= 0.28
            is_bottom = rel_y >= 0.72
        else:
            rel_idx = entry["index"] / float(max(1, total_lines - 1))
            is_top = entry["index"] <= 1 or rel_idx <= 0.25
            is_bottom = entry["index"] >= total_lines - 2 or rel_idx >= 0.75

        # 1. Forward continuation cues ('over', 'cont', 'pto', etc.)
        matched_forward = False
        for phrase in _FORWARD_CUE_PHRASES:
            if phrase in line_lower:
                continuation_tokens.append(phrase)
                all_cues.append(f"forward_cue:{phrase}")
                matched_forward = True
                break

        if not matched_forward:
            # Word-level checks for standalone tokens
            if "pto" in words:
                continuation_tokens.append("pto")
                all_cues.append("forward_cue:pto")
            elif "over" in words:
                # Disambiguate culinary preposition (e.g. "over medium heat", "serve over ice")
                is_culinary = any(cw in words for cw in _CULINARY_OVER_CONTEXT)
                if not is_culinary and is_bottom and len(words) <= 3:
                    continuation_tokens.append("over")
                    all_cues.append("forward_cue:over")
            elif any(w in ("cont", "cont.", "cont'd", "continued") for w in words):
                # Distinguish between forward cue (at bottom/end) and continuation header (at top)
                if is_bottom or not is_top:
                    continuation_tokens.append("cont")
                    all_cues.append("forward_cue:cont")
                else:
                    continuation_headers.append("cont")
                    all_cues.append("continuation_header:cont")

        # 2. Reverse / start continuation cues ('continued from', header 'cont', etc.)
        matched_header = False
        for phrase in _REVERSE_CUE_PHRASES:
            if phrase in line_lower:
                continuation_headers.append(phrase)
                all_cues.append(f"continuation_header:{phrase}")
                matched_header = True
                break

        if not matched_header and is_top:
            if "continued" in line_lower or "cont'd" in line_lower:
                continuation_headers.append("continued")
                all_cues.append("continuation_header:continued")

        # 3. Step numbering
        step_match = _STEP_NUM_PATTERN.match(line_text)
        if step_match:
            try:
                raw_grp = step_match.group(1).lower()
                if raw_grp in ("l", "i"):
                    num = 1
                else:
                    num = int(raw_grp)
                step_numbers.append(num)
            except ValueError:
                pass

        # 4. Page / card numbering cues at top or bottom boundaries
        if is_top or is_bottom:
            pos_label = "top" if is_top else "bottom"

            # Check "Page X" / "Pg X"
            for p_match in _PAGE_TOKEN_PATTERN.finditer(line_text):
                try:
                    p_num = int(p_match.group(1))
                    page_markers.append({"number": p_num, "token": p_match.group(0), "position": pos_label})
                    all_cues.append(f"page_marker:{pos_label}:{p_num}")
                except ValueError:
                    pass

            # Check "X of Y"
            for xy_match in _PAGE_X_OF_Y_PATTERN.finditer(line_text):
                try:
                    cur_num = int(xy_match.group(1))
                    total_num = int(xy_match.group(2))
                    page_markers.append(
                        {
                            "number": cur_num,
                            "total": total_num,
                            "token": xy_match.group(0),
                            "position": pos_label,
                        }
                    )
                    all_cues.append(f"page_marker:{pos_label}:{cur_num}_of_{total_num}")
                except ValueError:
                    pass

            # Standalone digits at edge (e.g. "- 2 -")
            if len(words) <= 3:
                standalone_match = _STANDALONE_PAGE_PATTERN.match(line_text)
                if standalone_match:
                    try:
                        s_num = int(standalone_match.group(1))
                        # Only treat as page number if small (1..9)
                        if 1 <= s_num <= 9:
                            page_markers.append(
                                {"number": s_num, "token": line_text, "position": pos_label}
                            )
                            all_cues.append(f"standalone_marker:{pos_label}:{s_num}")
                    except ValueError:
                        pass

    # Inspect last line for cutoff punctuation (e.g. dangling comma, hyphen, conjunction)
    is_cutoff = False
    if line_entries:
        last_text = line_entries[-1]["text"].rstrip()
        if last_text:
            trailing_char = last_text[-1]
            last_words = last_text.lower().split()
            trailing_word = last_words[-1] if last_words else ""
            if trailing_char in (",", "-", "–", "—", ";", ":"):
                is_cutoff = True
            elif trailing_word in ("and", "with", "or", "to", "in", "into", "until", "for"):
                is_cutoff = True

    top_marker = next((m["number"] for m in page_markers if m["position"] == "top"), None)
    bottom_marker = next((m["number"] for m in page_markers if m["position"] == "bottom"), None)
    all_page_nums = [m["number"] for m in page_markers]

    has_continuation_cue = bool(continuation_tokens)
    has_continuation_start = bool(continuation_headers) or (
        bool(step_numbers) and step_numbers[0] > 1
    ) or (top_marker is not None and top_marker > 1)

    return {
        "has_continuation_cue": has_continuation_cue,
        "has_continuation_start": has_continuation_start,
        "continuation_tokens": continuation_tokens,
        "page_numbers": all_page_nums,
        "page_markers": page_markers,
        "top_page_marker": top_marker,
        "bottom_page_marker": bottom_marker,
        "step_numbers": step_numbers,
        "first_step": step_numbers[0] if step_numbers else None,
        "last_step": step_numbers[-1] if step_numbers else None,
        "is_cutoff": is_cutoff,
        "cues": all_cues,
    }
