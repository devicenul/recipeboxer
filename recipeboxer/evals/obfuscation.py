"""Evaluation Filename Obfuscation and Anti-Cheating Architecture for RecipeBoxer.

Sanitizes recipe card scans and metadata to opaque identifiers (e.g. `card_<sha256[:8]>.jpg` or UUID4),
strips embedded EXIF metadata, and maintains a strict bidirectional mapping between obfuscated identifiers
and ground-truth cards so evaluation metrics (correlation pairing F1, transcription CER/WER, schema conformity)
evaluate accurately against ground truth without exposing lexical hints (recipe titles, dates, card roles)
to multimodal models.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from PIL import Image

from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import CardRole
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.tools.card_generator import CardModality

try:
    from tests.fixtures.loader import CardEntry, SampleCardCorpus, get_manifest_path, get_sample_card_dir
except ImportError:
    CardEntry = Any  # type: ignore[misc,assignment]
    SampleCardCorpus = Any  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


# ============================================================================
# 1. OBFUSCATION CONFIGURATION & MODELS
# ============================================================================

class ObfuscationStrategy(str, Enum):
    """Naming strategies for generating opaque card identifiers."""
    DETERMINISTIC_SHA256 = "DETERMINISTIC_SHA256"
    UUID4 = "UUID4"
    SEQUENTIAL = "SEQUENTIAL"


@dataclass
class ObfuscatedCard:
    """Represents a card scan sanitized for anti-cheating model evaluation."""
    original_card_id: str
    obfuscated_id: str
    file_path: Path
    original_file_path: Path
    sha256_hash: str
    modality: CardModality
    role: CardRole
    recipe_cluster: Dict[str, Any] = field(default_factory=dict)
    physical_characteristics: Dict[str, Any] = field(default_factory=dict)
    ground_truth_recipe: Optional[RecipeSchema] = None

    def load_image(self) -> Image.Image:
        """Loads decoded Pillow RGB image from the sanitized file path."""
        with Image.open(self.file_path) as im:
            return im.copy()


def sanitize_image_exif(source_path: Union[str, Path], destination_path: Union[str, Path]) -> None:
    """Copies an image to destination while stripping all EXIF metadata tags.

    Args:
        source_path: Path to original image file.
        destination_path: Path where sanitized image will be saved.
    """
    src = Path(source_path).resolve()
    dst = Path(destination_path).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src) as im:
        # Decode pixels into a fresh image to guarantee zero EXIF / metadata carryover
        im_format = im.format or "JPEG"
        if im_format.upper() not in ("JPEG", "JPG", "PNG", "WEBP"):
            im_format = "JPEG"

        # Create clean copy without any exif dict or info metadata
        clean_im = Image.new(im.mode, im.size)
        clean_im.putdata(list(im.getdata()))

        # Save with empty exif bytes
        save_kwargs: Dict[str, Any] = {}
        if im_format.upper() in ("JPEG", "JPG"):
            save_kwargs["quality"] = 95
            save_kwargs["optimize"] = False
        clean_im.save(dst, format=im_format, **save_kwargs)


_ACTIVE_OBFUSCATION_REGISTRY: Dict[str, str] = {}


def resolve_active_obfuscation_id(identifier: Union[str, Path]) -> str:
    """Resolves an obfuscated identifier or path to original ground-truth card_id if active.

    Args:
        identifier: Obfuscated ID or file path (e.g. 'card_a7b8c9d0.jpg' or Path).

    Returns:
        Original ground-truth card_id if registered in active corpus, or filename as fallback.
    """
    if not identifier:
        return ""
    clean = Path(str(identifier).strip()).name
    full_str = str(identifier).strip()
    return (
        _ACTIVE_OBFUSCATION_REGISTRY.get(full_str)
        or _ACTIVE_OBFUSCATION_REGISTRY.get(clean)
        or _ACTIVE_OBFUSCATION_REGISTRY.get(Path(clean).stem)
        or clean
    )


# ============================================================================
# 2. OBFUSCATED CARD CORPUS & CONTEXT MANAGER
# ============================================================================

class ObfuscatedCardCorpus:

    """Sanitizes card scans to opaque identifiers and maintains bidirectional ground-truth mapping.

    Implements Python context manager (`with ObfuscatedCardCorpus(...) as corpus:`) to automatically
    manage temporary sanitized image directories and clean them up on exit.
    """

    def __init__(
        self,
        cards_or_dir: Union[str, Path, List[Any], SampleCardCorpus],
        target_dir: Optional[Union[str, Path]] = None,
        strategy: ObfuscationStrategy = ObfuscationStrategy.DETERMINISTIC_SHA256,
        prefix: str = "card_",
    ) -> None:
        """Initializes ObfuscatedCardCorpus.

        Args:
            cards_or_dir: Path to scan directory, List of CardEntry instances, or SampleCardCorpus.
            target_dir: Optional destination directory for sanitized scans (created as tempdir if None).
            strategy: Opaque naming strategy (DETERMINISTIC_SHA256, UUID4, SEQUENTIAL).
            prefix: Prefix for opaque identifiers (default: 'card_').
        """
        self.strategy = strategy
        self.prefix = prefix
        self._custom_target_dir = Path(target_dir).resolve() if target_dir else None
        self._temp_dir: Optional[tempfile.TemporaryDirectory[str]] = None

        if self._custom_target_dir:
            self.target_dir = self._custom_target_dir
            self.target_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="recipeboxer_obf_")
            self.target_dir = Path(self._temp_dir.name)

        # Bidirectional mapping tables
        self._id_to_obfuscated: Dict[str, str] = {}
        self._obfuscated_to_id: Dict[str, str] = {}
        self._original_to_path: Dict[str, Path] = {}
        self._obfuscated_to_path: Dict[str, Path] = {}
        self._obfuscated_cards: List[ObfuscatedCard] = []

        # Ingest and sanitize input cards
        self._process_input_cards(cards_or_dir)

    def _process_input_cards(self, cards_or_dir: Union[str, Path, List[Any], SampleCardCorpus]) -> None:
        """Inspects input types and produces sanitized ObfuscatedCard entries."""
        raw_cards: List[Any] = []

        if isinstance(cards_or_dir, (str, Path)):
            p = Path(cards_or_dir).resolve()
            if p.is_dir():
                manifest_file = p / "manifest.json"
                if manifest_file.exists():
                    try:
                        raw_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                        for card_dict in raw_data.get("cards", []):
                            fname = card_dict.get("filename", "")
                            fpath = p / fname
                            raw_cards.append({
                                "card_id": card_dict.get("card_id", fname),
                                "filename": fname,
                                "file_path": fpath,
                                "sha256_hash": card_dict.get("sha256_hash"),
                                "modality": card_dict.get("modality", "HANDWRITTEN_CURSIVE"),
                                "role": card_dict.get("role", "SEQUENCE_START"),
                                "recipe_cluster": card_dict.get("recipe_cluster", {}),
                                "physical_characteristics": card_dict.get("physical_characteristics", {}),
                                "ground_truth_recipe": card_dict.get("ground_truth_recipe"),
                            })
                    except Exception as e:
                        logger.warning(f"Failed to parse manifest in {p}: {e}")

                if not raw_cards:
                    # Scan directory for images
                    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
                        for img_f in p.glob(ext):
                            raw_cards.append({
                                "card_id": img_f.name,
                                "filename": img_f.name,
                                "file_path": img_f,
                                "sha256_hash": None,
                                "modality": "HANDWRITTEN_CURSIVE",
                                "role": "SEQUENCE_START",
                                "recipe_cluster": {},
                                "physical_characteristics": {},
                                "ground_truth_recipe": None,
                            })
            elif p.is_file():
                raw_cards.append({
                    "card_id": p.name,
                    "filename": p.name,
                    "file_path": p,
                    "sha256_hash": None,
                    "modality": "HANDWRITTEN_CURSIVE",
                    "role": "SEQUENCE_START",
                    "recipe_cluster": {},
                    "physical_characteristics": {},
                    "ground_truth_recipe": None,
                })
        elif hasattr(cards_or_dir, "cards"):
            raw_cards = list(getattr(cards_or_dir, "cards"))
        elif isinstance(cards_or_dir, (list, tuple)):
            raw_cards = list(cards_or_dir)

        # Generate obfuscated cards
        for idx, item in enumerate(raw_cards):
            orig_id, orig_path, orig_sha, mod, role, cluster, phys, gt = self._extract_card_info(item, idx)
            ext = orig_path.suffix if orig_path and orig_path.suffix else ".jpg"

            # Compute opaque identifier
            obf_id = self._generate_opaque_id(orig_id, orig_path, orig_sha, idx, ext)

            # Sanitize image file
            target_image_path = self.target_dir / obf_id
            if orig_path and orig_path.exists() and orig_path.is_file():
                sanitize_image_exif(orig_path, target_image_path)
            else:
                # Create a blank fallback image if source does not exist
                fallback_im = Image.new("RGB", (600, 400), color=(245, 240, 225))
                fallback_im.save(target_image_path, format="JPEG")

            # Calculate SHA256 of sanitized image
            actual_sha = hashlib.sha256(target_image_path.read_bytes()).hexdigest()

            # Record bidirectional mappings
            self._id_to_obfuscated[orig_id] = obf_id
            # Also record stem and lowercase variations for robust resolution
            self._id_to_obfuscated[Path(orig_id).stem] = Path(obf_id).stem
            self._obfuscated_to_id[obf_id] = orig_id
            self._obfuscated_to_id[Path(obf_id).stem] = orig_id

            if orig_path:
                self._original_to_path[orig_id] = target_image_path
                self._obfuscated_to_path[obf_id] = target_image_path

            obf_card = ObfuscatedCard(
                original_card_id=orig_id,
                obfuscated_id=obf_id,
                file_path=target_image_path,
                original_file_path=orig_path,
                sha256_hash=orig_sha or actual_sha,
                modality=mod,
                role=role,
                recipe_cluster=cluster,
                physical_characteristics=phys,
                ground_truth_recipe=gt,
            )
            self._obfuscated_cards.append(obf_card)

    def _extract_card_info(self, item: Any, idx: int) -> Tuple[
        str, Path, Optional[str], CardModality, CardRole, Dict[str, Any], Dict[str, Any], Optional[RecipeSchema]
    ]:
        """Normalizes diverse card input types into standard attribute tuple."""
        if hasattr(item, "card_id") and hasattr(item, "file_path"):
            cid = str(item.card_id)
            fpath = Path(item.file_path)
            sha = getattr(item, "sha256_hash", None)
            mod = CardModality.normalize(getattr(item, "modality", CardModality.HANDWRITTEN_CURSIVE))
            role_val = getattr(item, "role", CardRole.SEQUENCE_START)
            role = CardRole(role_val) if not isinstance(role_val, CardRole) else role_val
            cluster = getattr(item, "recipe_cluster", {}) or {}
            phys = getattr(item, "physical_characteristics", {}) or {}
            gt = getattr(item, "ground_truth_recipe", None)
            return cid, fpath, sha, mod, role, cluster, phys, gt

        if isinstance(item, dict):
            cid = str(item.get("card_id") or item.get("filename") or f"card_{idx:03d}")
            fpath = Path(item.get("file_path") or item.get("filename") or "")
            sha = item.get("sha256_hash")
            mod = CardModality.normalize(item.get("modality", CardModality.HANDWRITTEN_CURSIVE))
            role_val = item.get("role", CardRole.SEQUENCE_START)
            role = CardRole(role_val) if not isinstance(role_val, CardRole) else role_val
            cluster = item.get("recipe_cluster", {}) or {}
            phys = item.get("physical_characteristics", {}) or {}
            gt = item.get("ground_truth_recipe")
            if gt and not isinstance(gt, RecipeSchema):
                gt = RecipeSchema.model_validate(gt)
            return cid, fpath, sha, mod, role, cluster, phys, gt

        if isinstance(item, (str, Path)):
            p = Path(item)
            return p.name, p, None, CardModality.HANDWRITTEN_CURSIVE, CardRole.SEQUENCE_START, {}, {}, None

        # Fallback
        c_name = f"card_{idx:03d}.jpg"
        return c_name, Path(c_name), None, CardModality.HANDWRITTEN_CURSIVE, CardRole.SEQUENCE_START, {}, {}, None

    def _generate_opaque_id(
        self,
        orig_id: str,
        orig_path: Path,
        orig_sha: Optional[str],
        idx: int,
        ext: str,
    ) -> str:
        """Generates an opaque identifier that contains zero lexical hints."""
        if self.strategy == ObfuscationStrategy.DETERMINISTIC_SHA256:
            # Hash original identifier and path to produce deterministic opaque ID
            seed = f"{orig_id}_{orig_path.name if orig_path else ''}_{orig_sha or ''}"
            digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
            return f"{self.prefix}{digest}{ext}"
        elif self.strategy == ObfuscationStrategy.UUID4:
            u = uuid.uuid4().hex[:8]
            return f"{self.prefix}{u}{ext}"
        elif self.strategy == ObfuscationStrategy.SEQUENTIAL:
            return f"{self.prefix}{idx + 1:04d}{ext}"
        else:
            digest = hashlib.sha256(orig_id.encode("utf-8")).hexdigest()[:8]
            return f"{self.prefix}{digest}{ext}"

    # ------------------------------------------------------------------------
    # Public Resolution & Mapping APIs
    # ------------------------------------------------------------------------

    @property
    def obfuscated_cards(self) -> List[ObfuscatedCard]:
        """Returns the list of sanitized ObfuscatedCard instances."""
        return list(self._obfuscated_cards)

    def obfuscate_id(self, original_id: str) -> str:
        """Translates an original card ID/filename to its opaque obfuscated ID.

        Args:
            original_id: Original card identifier (e.g. 'rec_vhc_peach_cobbler_1931_front.jpg').

        Returns:
            Opaque obfuscated identifier (e.g. 'card_a7b8c9d0.jpg').
        """
        clean = str(original_id).strip()
        if clean in self._id_to_obfuscated:
            return self._id_to_obfuscated[clean]
        stem = Path(clean).stem
        if stem in self._id_to_obfuscated:
            return self._id_to_obfuscated[stem]
        # Return fallback deterministic opaque ID if not registered
        h = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]
        return f"{self.prefix}{h}.jpg"

    def resolve_id(self, obfuscated_id: str) -> str:
        """Translates an opaque obfuscated card ID back to its original ground-truth card ID.

        Args:
            obfuscated_id: Opaque identifier (e.g. 'card_a7b8c9d0.jpg').

        Returns:
            Original ground-truth identifier (e.g. 'rec_vhc_peach_cobbler_1931_front.jpg').
        """
        clean = str(obfuscated_id).strip()
        if clean in self._obfuscated_to_id:
            return self._obfuscated_to_id[clean]
        stem = Path(clean).stem
        if stem in self._obfuscated_to_id:
            return self._obfuscated_to_id[stem]
        return clean

    def get_obfuscated_path(self, original_id: str) -> Path:
        """Returns the filesystem Path to the sanitized, EXIF-stripped image for an original ID.

        Args:
            original_id: Original ground-truth card ID.

        Returns:
            Path to sanitized image in target_dir.
        """
        clean = str(original_id).strip()
        if clean in self._original_to_path:
            return self._original_to_path[clean]
        obf_id = self.obfuscate_id(clean)
        if obf_id in self._obfuscated_to_path:
            return self._obfuscated_to_path[obf_id]
        return self.target_dir / obf_id

    def get_original_path(self, obfuscated_id: str) -> Path:
        """Returns the original filesystem path for an obfuscated card ID."""
        orig_id = self.resolve_id(obfuscated_id)
        for card in self._obfuscated_cards:
            if card.original_card_id == orig_id:
                return card.original_file_path
        return self.target_dir / obfuscated_id

    def map_proposals_to_ground_truth(self, proposals: Sequence[Any]) -> List[CorrelationProposal]:
        """Maps predicted correlation proposals containing opaque IDs back to original ground-truth IDs.

        Preserves all transition scores, confidence, and explanations while replacing
        `ordered_card_ids` with resolved ground-truth identifiers.

        Args:
            proposals: List of CorrelationProposal instances or tuples.

        Returns:
            List of CorrelationProposal instances with resolved original card IDs.
        """
        mapped: List[CorrelationProposal] = []

        for p in proposals:
            if isinstance(p, CorrelationProposal):
                resolved_ids = [self.resolve_id(cid) for cid in p.ordered_card_ids]
                # Re-validate with resolved IDs
                p_copy = p.model_copy(deep=True)
                p_copy.ordered_card_ids = resolved_ids
                mapped.append(p_copy)
            elif isinstance(p, (tuple, list)):
                resolved_ids = [self.resolve_id(cid) for cid in p if cid]
                if resolved_ids:
                    prop = CorrelationProposal(
                        proposal_id=f"prop_{uuid.uuid4().hex[:8]}",
                        ordered_card_ids=resolved_ids,
                        part_count=len(resolved_ids),
                        confidence=1.0,
                        is_standalone=(len(resolved_ids) == 1),
                    )
                    mapped.append(prop)
            elif isinstance(p, dict):
                p_dict = copy.deepcopy(p)
                if "ordered_card_ids" in p_dict:
                    p_dict["ordered_card_ids"] = [self.resolve_id(cid) for cid in p_dict["ordered_card_ids"]]
                if "front_card_id" in p_dict and p_dict["front_card_id"]:
                    p_dict["front_card_id"] = self.resolve_id(p_dict["front_card_id"])
                if "back_card_id" in p_dict and p_dict["back_card_id"]:
                    p_dict["back_card_id"] = self.resolve_id(p_dict["back_card_id"])
                mapped.append(CorrelationProposal.model_validate(p_dict))

        return mapped

    def map_correlation_result(self, result: CorrelationResult) -> CorrelationResult:
        """Translates an entire CorrelationResult containing opaque IDs back to ground truth."""
        mapped_proposals = self.map_proposals_to_ground_truth(result.proposals)
        mapped_unmatched = [self.resolve_id(cid) for cid in getattr(result, "unmatched_card_ids", [])]
        return CorrelationResult(
            proposals=mapped_proposals,
            unmatched_card_ids=mapped_unmatched,
        )

    # ------------------------------------------------------------------------
    # Context Manager Protocol
    # ------------------------------------------------------------------------


    def __enter__(self) -> ObfuscatedCardCorpus:
        for obf_id, orig_id in self._obfuscated_to_id.items():
            _ACTIVE_OBFUSCATION_REGISTRY[obf_id] = orig_id
        for orig_id, obf_path in self._original_to_path.items():
            _ACTIVE_OBFUSCATION_REGISTRY[str(obf_path)] = orig_id
            _ACTIVE_OBFUSCATION_REGISTRY[obf_path.name] = orig_id
            _ACTIVE_OBFUSCATION_REGISTRY[obf_path.stem] = orig_id
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Cleans up temporary sanitized files and directories."""
        for obf_id in list(self._obfuscated_to_id.keys()):
            _ACTIVE_OBFUSCATION_REGISTRY.pop(obf_id, None)
        for obf_path in list(self._obfuscated_to_path.values()):
            _ACTIVE_OBFUSCATION_REGISTRY.pop(str(obf_path), None)
            _ACTIVE_OBFUSCATION_REGISTRY.pop(obf_path.name, None)
            _ACTIVE_OBFUSCATION_REGISTRY.pop(obf_path.stem, None)

        if self._temp_dir is not None:
            try:
                self._temp_dir.cleanup()
                self._temp_dir = None
            except Exception as e:
                logger.debug(f"Temporary obfuscation directory cleanup failed: {e}")


# Backward-compatibility alias
EvaluationObfuscator = ObfuscatedCardCorpus

__all__ = [
    "ObfuscationStrategy",
    "ObfuscatedCard",
    "ObfuscatedCardCorpus",
    "EvaluationObfuscator",
    "resolve_active_obfuscation_id",
    "sanitize_image_exif",
]

