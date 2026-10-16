"""Benchmark tier registry: active tiers, archived (opt-in) tiers and on-disk locations (WP-T3A).

Active tiers (``T0``-``T4``) live at ``evals/corpora/<tier>/`` and ``evals/vault/<tier>.gt.enc``.

Archived tiers are retired corpora kept only so that historical baselines stay re-scorable. They are
never part of a default / all-tier run and must be requested explicitly by name (``--tier T3A``).

* ``T3A`` - the antique set: 25 real 17th-century English manuscript recipes (Folger / Wellcome via
  FromThePage). It was the original ``T3`` tier; the ``honest_v0`` / ``honest_v1`` "T3" numbers refer
  to this set (its encrypted GT and the archived SUT outputs still carry the legacy label ``"T3"``).
  Deactivated because the product targets recipes from the 1900s onward.

``T3`` is now the real-world *modern* (1900s+) tier. Its first set was imported by WP-T3I
(``evals/corpora/t3/``, ``evals/vault/t3.gt.enc``; recipe-text GT still ``pending_draft``). Harness and
scorer raise :class:`TierPendingCurationError` ("T3 corpus pending curation; see evals/INTAKE.md") only
if that corpus or GT is missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Tuple, Union

ACTIVE_TIERS: Tuple[str, ...] = ("T0", "T1", "T2", "T3", "T4")
# Tiers a default / "all tiers" run may iterate over. Archived tiers are deliberately absent.
DEFAULT_TIERS: Tuple[str, ...] = ("T1", "T2", "T3", "T4")

ARCHIVED_TIERS: Dict[str, Dict[str, str]] = {
    "T3A": {
        "corpus": "evals/corpora/archive/t3_antique",
        "vault": "evals/vault/archive/t3a.gt.enc",
        "legacy_label": "T3",
        "description": "archived antique set: 17th-century English manuscript recipes (Folger/Wellcome); "
                       "opt-in only; honest_v0/honest_v1 'T3' numbers refer to this set",
    },
}

# Tiers whose corpus is expected to be imported later; empty -> TierPendingCurationError.
PENDING_CURATION_TIERS: Tuple[str, ...] = ("T3",)


class TierPendingCurationError(FileNotFoundError):
    """Raised when a tier's corpus or GT has not been curated/imported yet (e.g. modern T3)."""

    def __init__(self, tier: str, detail: str = "") -> None:
        self.tier = normalize_tier(tier)
        msg = f"{self.tier} corpus pending curation; see evals/INTAKE.md"
        if self.tier == "T3":
            msg += (" (T3 is now the modern real-world 1900s+ tier; the antique manuscript set used by "
                    "honest_v0/honest_v1 is archived as opt-in tier T3A: use --tier T3A to re-score it)")
        if detail:
            msg += f". {detail}"
        super().__init__(msg)


class UnknownTierError(ValueError):
    """Raised for a tier name that is neither active nor archived."""


def normalize_tier(tier: Union[str, None]) -> str:
    return str(tier or "").strip().upper()


def is_archived(tier: str) -> bool:
    return normalize_tier(tier) in ARCHIVED_TIERS


def is_known(tier: str) -> bool:
    t = normalize_tier(tier)
    return t in ACTIVE_TIERS or t in ARCHIVED_TIERS


def legacy_label(tier: str) -> Optional[str]:
    """Label the archived tier carried when it was active (e.g. ``T3A`` -> ``T3``), else None."""
    entry = ARCHIVED_TIERS.get(normalize_tier(tier))
    return entry["legacy_label"] if entry else None


def corpus_dir_for(tier: str, repo_root: Union[str, Path]) -> Path:
    """Corpus directory for a tier (archived tiers resolve to their archive location)."""
    t = normalize_tier(tier)
    root = Path(repo_root)
    if t in ARCHIVED_TIERS:
        return root / ARCHIVED_TIERS[t]["corpus"]
    lower = root / "evals" / "corpora" / t.lower()
    if lower.exists():
        return lower
    upper = root / "evals" / "corpora" / t
    return upper if upper.exists() else lower


def vault_candidates_for(tier: str, vault_dir: Union[str, Path]) -> Tuple[Path, ...]:
    """Candidate encrypted-GT paths for a tier inside ``vault_dir`` (archived tiers under ``archive/``)."""
    t = normalize_tier(tier)
    v = Path(vault_dir)
    if t in ARCHIVED_TIERS:
        return (v / "archive" / Path(ARCHIVED_TIERS[t]["vault"]).name,)
    return (v / f"{t.lower()}.gt.enc", v / f"{t}.gt.enc")


__all__ = [
    "ACTIVE_TIERS",
    "ARCHIVED_TIERS",
    "DEFAULT_TIERS",
    "PENDING_CURATION_TIERS",
    "TierPendingCurationError",
    "UnknownTierError",
    "corpus_dir_for",
    "is_archived",
    "is_known",
    "legacy_label",
    "normalize_tier",
    "vault_candidates_for",
]
