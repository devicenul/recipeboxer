"""Dataset loader utilities and pytest fixtures for RecipeBoxer sample card fixtures."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pytest
from PIL import Image

from recipeboxer.models.domain import CardRole
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.tools.card_generator import CardModality


# Default path locations
FIXTURES_ROOT = Path(__file__).resolve().parent
SAMPLE_CARDS_DIR = FIXTURES_ROOT / "sample_cards"
MANIFEST_PATH = SAMPLE_CARDS_DIR / "manifest.json"


def get_sample_card_dir() -> Path:
    """Returns absolute path to the curated sample recipe cards fixture directory."""
    return SAMPLE_CARDS_DIR


def get_manifest_path() -> Path:
    """Returns absolute path to the ground-truth metadata manifest."""
    return MANIFEST_PATH


@dataclass
class CardEntry:
    """Represents a single recipe card scan with ground-truth metadata."""
    card_id: str
    filename: str
    file_path: Path
    sha256_hash: Optional[str]
    modality: CardModality
    role: CardRole
    recipe_cluster: Dict[str, Any]
    physical_characteristics: Dict[str, Any]
    ground_truth_recipe: RecipeSchema

    def load_image(self) -> Image.Image:
        """Loads and returns the decoded Pillow Image for this card scan."""
        if not self.file_path.exists():
            raise FileNotFoundError(f"Card image not found at: {self.file_path}")
        with Image.open(self.file_path) as im:
            return im.copy()

    def compute_sha256(self) -> str:
        """Calculates SHA-256 hex digest of the raw image file on disk."""
        if not self.file_path.exists():
            raise FileNotFoundError(f"Card image not found at: {self.file_path}")
        return hashlib.sha256(self.file_path.read_bytes()).hexdigest()


class SampleCardCorpus:
    """Encapsulates the curated sample recipe card library with querying and filtering helpers."""

    def __init__(
        self,
        corpus_dir: Optional[Union[str, Path]] = None,
        manifest_path: Optional[Union[str, Path]] = None,
    ):
        self.corpus_dir = Path(corpus_dir or SAMPLE_CARDS_DIR).resolve()
        self.manifest_path = Path(manifest_path or (self.corpus_dir / "manifest.json")).resolve()

        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Sample cards manifest not found at: {self.manifest_path}")

        raw_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.manifest: Dict[str, Any] = raw_manifest

        self.cards: List[CardEntry] = []
        self._cards_by_id: Dict[str, CardEntry] = {}
        self._cards_by_recipe: Dict[str, List[CardEntry]] = {}

        for card_data in raw_manifest.get("cards", []):
            cid = card_data["card_id"]
            fname = card_data["filename"]
            fpath = self.corpus_dir / fname

            modality_raw = card_data.get("modality", "")
            try:
                modality = CardModality(modality_raw)
            except ValueError:
                modality = CardModality(str(modality_raw).upper())

            role_raw = card_data.get("role", "")
            try:
                role = CardRole(role_raw)
            except ValueError:
                role = CardRole(str(role_raw).upper())

            rec_raw = card_data.get("ground_truth_recipe", {})
            try:
                gt_recipe = RecipeSchema.model_validate(rec_raw)
            except Exception:
                gt_recipe = RecipeSchema.model_construct(**rec_raw) if isinstance(rec_raw, dict) else RecipeSchema(title="", ingredients=[], directions=[])

            entry = CardEntry(
                card_id=cid,
                filename=fname,
                file_path=fpath,
                sha256_hash=card_data.get("sha256_hash"),
                modality=modality,
                role=role,
                recipe_cluster=card_data.get("recipe_cluster", {}),
                physical_characteristics=card_data.get("physical_characteristics", {}),
                ground_truth_recipe=gt_recipe,
            )
            self.cards.append(entry)

            # Register multi-format lookup keys for card_id and filename
            for key_variant in (
                cid,
                fname,
                Path(fname).stem,
                Path(cid).stem,
                cid.lower(),
                fname.lower(),
                Path(fname).stem.lower(),
                Path(cid).stem.lower(),
                cid.replace("-", "_"),
                cid.replace("_", "-"),
                cid.lower().replace("-", "_"),
                cid.lower().replace("_", "-"),
                Path(fname).stem.lower().replace("-", "_"),
                Path(fname).stem.lower().replace("_", "-"),
                Path(cid).stem.lower().replace("-", "_"),
                Path(cid).stem.lower().replace("_", "-"),
            ):
                self._cards_by_id[key_variant] = entry

            rec_id = entry.recipe_cluster.get("recipe_id", "")
            if rec_id:
                if rec_id not in self._cards_by_recipe:
                    self._cards_by_recipe[rec_id] = []
                self._cards_by_recipe[rec_id].append(entry)

        # Unique list of recipes
        seen_rec_ids = set()
        self.recipes: List[RecipeSchema] = []
        for card in self.cards:
            rid = card.ground_truth_recipe.recipe_id
            if rid not in seen_rec_ids:
                seen_rec_ids.add(rid)
                self.recipes.append(card.ground_truth_recipe)

    def __len__(self) -> int:
        return len(self.cards)

    def __getitem__(self, index: int) -> CardEntry:
        return self.cards[index]

    def __iter__(self):
        return iter(self.cards)

    def get_by_id(self, card_id: str) -> Optional[CardEntry]:
        """Retrieves card entry by unique card_id, filename, or stem (case and delimiter-insensitive)."""
        if not card_id:
            return None
        card_str = str(card_id).strip()
        stem = Path(card_str).stem
        norm_underscore = card_str.lower().replace("-", "_").replace(" ", "_")
        norm_hyphen = card_str.lower().replace("_", "-").replace(" ", "-")
        stem_norm_underscore = stem.lower().replace("-", "_").replace(" ", "_")
        stem_norm_hyphen = stem.lower().replace("_", "-").replace(" ", "-")

        return (
            self._cards_by_id.get(card_str)
            or self._cards_by_id.get(card_str.lower())
            or self._cards_by_id.get(norm_underscore)
            or self._cards_by_id.get(norm_hyphen)
            or self._cards_by_id.get(f"{card_str}.jpg")
            or self._cards_by_id.get(f"{card_str.lower()}.jpg")
            or self._cards_by_id.get(f"{norm_underscore}.jpg")
            or self._cards_by_id.get(f"{norm_hyphen}.jpg")
            or self._cards_by_id.get(f"{card_str}.png")
            or self._cards_by_id.get(f"{card_str.lower()}.png")
            or self._cards_by_id.get(f"{norm_underscore}.png")
            or self._cards_by_id.get(f"{norm_hyphen}.png")
            or self._cards_by_id.get(stem)
            or self._cards_by_id.get(stem.lower())
            or self._cards_by_id.get(stem_norm_underscore)
            or self._cards_by_id.get(stem_norm_hyphen)
        )

    def filter_by_modality(self, modality: Union[str, CardModality]) -> List[CardEntry]:
        """Filters cards matching a specific modality (case and delimiter-insensitive)."""
        mod_str = modality.value if isinstance(modality, CardModality) else str(modality)
        mod_norm = mod_str.upper().strip().replace("-", "_").replace(" ", "_")
        return [c for c in self.cards if c.modality.value.upper().replace("-", "_").replace(" ", "_") == mod_norm]

    def filter_by_role(self, role: Union[str, CardRole]) -> List[CardEntry]:
        """Filters cards matching a specific role (case and delimiter-insensitive)."""
        role_str = role.value if isinstance(role, CardRole) else str(role)
        role_norm = role_str.upper().strip().replace("-", "_").replace(" ", "_")
        return [c for c in self.cards if c.role.value.upper().replace("-", "_").replace(" ", "_") == role_norm]

    def get_recipe_cluster(self, recipe_id: str) -> List[CardEntry]:
        """Returns all card entries belonging to a given recipe cluster in sequence order (case and delimiter-insensitive)."""
        if not recipe_id:
            return []
        rec_id_str = str(recipe_id).strip()
        cluster = self._cards_by_recipe.get(rec_id_str)
        if cluster is None:
            rec_norm = rec_id_str.lower().replace("-", "_").replace(" ", "_")
            for k, v in self._cards_by_recipe.items():
                if k.lower().replace("-", "_").replace(" ", "_") == rec_norm:
                    cluster = v
                    break
        cluster = cluster or []
        return sorted(cluster, key=lambda c: c.recipe_cluster.get("sequence_position", 0))

    def get_pairs(self) -> List[Tuple[CardEntry, CardEntry]]:
        """Returns all two-sided front/back card pairs."""
        pairs: List[Tuple[CardEntry, CardEntry]] = []
        for rec_id, cards in self._cards_by_recipe.items():
            if len(cards) == 2:
                sorted_cards = sorted(cards, key=lambda c: c.recipe_cluster.get("sequence_position", 0))
                if sorted_cards[0].role == CardRole.FRONT_ONLY and sorted_cards[1].role == CardRole.BACK_ONLY:
                    pairs.append((sorted_cards[0], sorted_cards[1]))
        return pairs

    def get_multi_card_sets(self) -> Dict[str, List[CardEntry]]:
        """Returns multi-card recipes containing 3 or more cards."""
        return {
            rec_id: sorted(cards, key=lambda c: c.recipe_cluster.get("sequence_position", 0))
            for rec_id, cards in self._cards_by_recipe.items()
            if len(cards) >= 3
        }

    def get_standalones(self) -> List[CardEntry]:
        """Returns all single-sided standalone recipe cards."""
        return [c for c in self.cards if c.role == CardRole.STANDALONE]

    def load_image(self, card: Union[str, CardEntry]) -> Image.Image:
        """Loads and returns decoded Pillow image for a card entry or card ID."""
        if isinstance(card, str):
            entry = self.get_by_id(card)
            if not entry:
                raise KeyError(f"Card '{card}' not found in corpus.")
            return entry.load_image()
        return card.load_image()

    def validate_integrity(self) -> Dict[str, Any]:
        """Validates that all cards exist on disk, decode properly, and conform to schemas."""
        missing_files = []
        corrupted_files = []
        hash_mismatches = []
        invalid_recipes = []

        for card in self.cards:
            if not card.file_path.exists():
                missing_files.append(card.filename)
                continue

            # Test Pillow decodability
            try:
                img = card.load_image()
                if img.width <= 0 or img.height <= 0:
                    corrupted_files.append(card.filename)
            except Exception as e:
                corrupted_files.append(f"{card.filename}: {e}")

            # Test SHA256 integrity if present
            if card.sha256_hash:
                actual_hash = card.compute_sha256()
                if actual_hash != card.sha256_hash:
                    hash_mismatches.append(card.filename)

            # Test RecipeSchema validation
            try:
                if not card.ground_truth_recipe.title or not card.ground_truth_recipe.title.strip():
                    raise ValueError("Recipe title cannot be empty.")
                RecipeSchema.model_validate(card.ground_truth_recipe.model_dump())
            except Exception as e:
                invalid_recipes.append(f"{card.ground_truth_recipe.recipe_id}: {e}")

        return {
            "total_cards": len(self.cards),
            "missing_files": missing_files,
            "corrupted_files": corrupted_files,
            "hash_mismatches": hash_mismatches,
            "invalid_recipes": invalid_recipes,
            "is_valid": len(missing_files) == 0 and len(corrupted_files) == 0 and len(hash_mismatches) == 0 and len(invalid_recipes) == 0,
        }


def load_sample_corpus(directory: Optional[Union[str, Path]] = None) -> SampleCardCorpus:
    """Factory helper to construct and return a SampleCardCorpus instance."""
    return SampleCardCorpus(corpus_dir=directory)


# ============================================================================
# Pytest Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def sample_card_corpus() -> SampleCardCorpus:
    """Session-scoped fixture providing access to the curated sample recipe card corpus."""
    return load_sample_corpus()


@pytest.fixture(scope="session")
def ground_truth_manifest(sample_card_corpus: SampleCardCorpus) -> Dict[str, Any]:
    """Session-scoped fixture returning the raw parsed manifest dictionary."""
    return sample_card_corpus.manifest


@pytest.fixture(scope="session")
def sample_card_pairs(sample_card_corpus: SampleCardCorpus) -> List[Tuple[CardEntry, CardEntry]]:
    """Returns list of curated front/back card pairs."""
    return sample_card_corpus.get_pairs()


@pytest.fixture(scope="session")
def sample_card_standalones(sample_card_corpus: SampleCardCorpus) -> List[CardEntry]:
    """Returns list of standalone single-sided card entries."""
    return sample_card_corpus.get_standalones()


@pytest.fixture(scope="session")
def sample_card_multi_sets(sample_card_corpus: SampleCardCorpus) -> Dict[str, List[CardEntry]]:
    """Returns dict of multi-card recipe clusters (>= 3 cards)."""
    return sample_card_corpus.get_multi_card_sets()
