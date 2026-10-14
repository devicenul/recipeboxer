"""Extracted visual pixel features and capture metadata models (WP-G1, WP-G2, WP-G3)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple
from pydantic import BaseModel, Field


class PixelFeatures(BaseModel):
    """Low-level pixel features extracted from physical card scan (WP-G2)."""

    paper_lab: Tuple[float, float, float]
    paper_lab_std: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    ruling_spacing_px: Optional[float] = None
    ruling_angle_deg: Optional[float] = None
    ruling_kind: Optional[str] = None  # none|lined|grid|double
    ink_hues: List[float] = Field(default_factory=list)
    aspect_ratio: float = 1.0
    long_edge_px: int = 0
    deskew_angle_deg: float = 0.0
    crop_box: Optional[Tuple[int, int, int, int]] = None
    stain_mask_ref: Optional[str] = None  # key into an in-memory/session cache, not a file path


class CaptureMeta(BaseModel):
    """Capture metadata extracted from card scan file or EXIF (WP-G3)."""

    taken_at: Optional[datetime] = None
    subsec: Optional[int] = None
    device: Optional[str] = None
    source: str = "none"  # "exif"|"mtime"|"none"
    natural_sort_index: int = 0
