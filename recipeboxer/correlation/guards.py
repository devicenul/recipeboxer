"""WP-UP4 grouping guards: out-of-distribution detection and hard link constraints.

The UP3 fitted ranking/link models were fitted on CardForge index-card decks. On out-of-distribution
collections (e.g. photographed manuscript pages: dense prose, long pages, dangling page-break text,
non-recipe leaves) their probabilities are not trustworthy, and the eval showed false merges there.
This module provides generic, content-free guards used by :class:`GroupingEngine`:

1. :class:`DomainSupport` - a compact description of the fitted support (boundary-line text density
   quantile and the observed structural descriptor patterns of the fit cards). ``assess`` returns the
   fraction of a deck's cards outside that support; a deck above ``OOD_FRAC`` runs in *strict* mode.
2. Strict mode (engine): a link may only be formed when the SetVerifier positively confirmed it with
   at least one validated verbatim quote, no contradicted quote and confidence >= ``STRICT_MIN_SV_CONF``;
   orphan rescue is disabled (it offers title-less fragments - in OOD decks typically pages whose
   predecessor is simply not in the batch - to arbitrary predecessors).
3. Always-on hard constraints: non-recipe (distractor) faces are never linked; a strong physical
   mismatch (paper colour LLR <= ``PHYS_CONFLICT_LLR``) is never linked; a face that visibly starts a new
   recipe at its top (heading as its first line, no cut-off start, no continuation header and no shared
   title words with the predecessor) can only be linked with *strong* SetVerifier evidence.

No filenames, image hashes, capture order or corpus-specific strings are used.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from recipeboxer.models.domain import VisualFingerprint

PATTERN_KEYS: Tuple[str, ...] = (
    "has_title", "has_ing", "has_dir", "no_recipe_sections", "back", "cut_end", "cut_start", "fwd_cue",
    "cont_header", "role_start", "role_cont", "role_standalone", "role_unknown",
)
OOD_FRAC = 0.20               # deck is out of support when >= this fraction of its cards is
MIN_PATTERN_COUNT = 2         # a descriptor pattern seen fewer times than this in the fit data is unseen
STRICT_MIN_SV_CONF = 0.60
STRONG_MIN_SV_CONF = 0.80
PHYS_CONFLICT_LLR = -3.0
GATE_CAP = 0.02               # probability assigned to gated (forbidden) links

_WORD = re.compile(r"[a-z0-9]+")
_CONT_MARKERS = ("cont", "continued", "card 2", "card 3", "page 2", "part 2", "side 2", "back", "finishing")
_HEAD_STOP = {
    "the", "and", "with", "for", "of", "a", "an", "to", "make", "how", "receipt", "recipe", "another", "way",
    "my", "our", "best", "old", "easy", "quick", "s", "in", "on", "or", "it", "very", "good",
}


def _words(text: Optional[str]) -> List[str]:
    return _WORD.findall((text or "").lower())


def boundary_words_per_line(fp: VisualFingerprint) -> float:
    """Mean word count of the face's transcribed boundary lines (text density proxy)."""
    lines = [ln for ln in list(fp.first_lines or []) + list(fp.last_lines or []) if ln and ln.strip()]
    if not lines:
        return 0.0
    return sum(len(ln.split()) for ln in lines) / len(lines)


def descriptor_pattern(desc: Dict[str, float]) -> str:
    return "".join("1" if float(desc.get(k, 0.0)) >= 0.5 else "0" for k in PATTERN_KEYS)


class DomainSupport:
    """Fitted support of the UP3/UP4 link models (stored in ``calibration_default.json`` meta)."""

    def __init__(self, wpl_q99: Optional[float] = None, patterns: Optional[Dict[str, int]] = None,
                 ood_frac: float = OOD_FRAC) -> None:
        self.wpl_q99 = wpl_q99
        self.patterns = dict(patterns or {})
        self.ood_frac = float(ood_frac)

    @property
    def available(self) -> bool:
        return self.wpl_q99 is not None and bool(self.patterns)

    @classmethod
    def from_meta(cls, meta: Optional[Dict[str, Any]]) -> "DomainSupport":
        sup = (meta or {}).get("support") or {}
        return cls(sup.get("wpl_q99"), sup.get("patterns"), sup.get("ood_frac", OOD_FRAC))

    @classmethod
    def fit(cls, fps: Sequence[VisualFingerprint], descs: Sequence[Dict[str, float]]) -> "DomainSupport":
        vals = sorted(boundary_words_per_line(fp) for fp in fps)
        q = vals[min(len(vals) - 1, int(math.ceil(0.99 * len(vals))) - 1)] if vals else None
        pats: Dict[str, int] = {}
        for d in descs:
            k = descriptor_pattern(d)
            pats[k] = pats.get(k, 0) + 1
        return cls(round(float(q), 4) if q is not None else None, pats)

    def to_dict(self) -> Dict[str, Any]:
        return {"wpl_q99": self.wpl_q99, "patterns": self.patterns, "ood_frac": self.ood_frac,
                "pattern_keys": list(PATTERN_KEYS)}

    def assess(self, fps: Sequence[VisualFingerprint], descs: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
        """Fraction of cards outside the fitted support and the resulting strict-mode decision."""
        if not self.available or not fps:
            return {"available": False, "ood": False, "frac_out": 0.0}
        n_dense = n_unseen = n_out = 0
        for fp in fps:
            dense = boundary_words_per_line(fp) > float(self.wpl_q99)
            d = descs.get(fp.card_id) or {}
            unseen = self.patterns.get(descriptor_pattern(d), 0) < MIN_PATTERN_COUNT
            n_dense += dense
            n_unseen += unseen
            n_out += (dense or unseen)
        n = len(fps)
        frac = n_out / n
        return {"available": True, "ood": frac >= self.ood_frac, "frac_out": round(frac, 4),
                "frac_dense": round(n_dense / n, 4), "frac_unseen_pattern": round(n_unseen / n, 4)}


# ----------------------------------------------------------------------------------------------
# Hard constraints
# ----------------------------------------------------------------------------------------------
def is_nonrecipe(desc: Dict[str, float], fp: VisualFingerprint) -> bool:
    """A face with no recipe sections and no sign of being a fragment (distractor / non-recipe leaf)."""
    if float(desc.get("no_recipe_sections", 0.0)) < 0.5:
        return False
    if fp.text_cut_off_start is True or fp.text_cut_off_end is True:
        return False
    if float(desc.get("cont_header", 0.0)) >= 0.5 or float(desc.get("fwd_cue", 0.0)) >= 0.5:
        return False
    return True


def _head_tokens(text: Optional[str]) -> set:
    return {w for w in _words(text) if len(w) > 2 and w not in _HEAD_STOP}


def starts_new_recipe(fa: VisualFingerprint, fb: VisualFingerprint, db: Dict[str, float]) -> bool:
    """True when B visibly begins a NEW recipe at its top that is not a continuation of A's recipe."""
    title = (fb.visible_title or "").strip()
    if not title or fb.text_cut_off_start is True or float(db.get("cont_header", 0.0)) >= 0.5:
        return False
    low = title.lower()
    if any(m in low for m in _CONT_MARKERS):
        return False
    first = list(fb.first_lines or [])[:1]
    bt = getattr(fb, "boundary_text", None)
    if bt is not None and getattr(bt, "first_line", None):
        first.append(bt.first_line)
    head = " ".join(first).lower()
    if any(m in head for m in _CONT_MARKERS):
        return False
    t_tokens = _head_tokens(title)
    if not t_tokens:
        return False
    head_words = set(_words(head))
    if len(t_tokens & head_words) / len(t_tokens) < 0.5:
        return False  # the heading is not at the top of the face (e.g. tail of A, then a new heading)
    a_tokens = _head_tokens(fa.visible_title) | {w for ln in (fa.printed_markers or []) for w in _head_tokens(ln)}
    if a_tokens and t_tokens <= a_tokens:
        return False  # B repeats A's title (e.g. "X (cont.)" without marker)
    return True


def physical_conflict(ev: Any) -> bool:
    """Strong paper-colour mismatch between the two faces (different leaves / stocks)."""
    for f in getattr(ev, "features", None) or []:
        if f.name == "paper_lab" and f.log_lr is not None and float(f.log_lr) <= PHYS_CONFLICT_LLR:
            return True
    return False


def sv_quality(ver: Any) -> Tuple[int, int, float]:
    """(n validated quotes, n contradicted quotes, confidence) of a SetVerification."""
    vq = len(getattr(ver, "verified_quotes", None) or [])
    uq = len(getattr(ver, "unverified_quotes", None) or [])
    c = getattr(ver, "confidence", None)
    return vq, uq, float(c) if c is not None else 0.0


def sv_positive_ok(quality: Optional[Tuple[int, int, float]], min_conf: float) -> bool:
    if quality is None:
        return False
    vq, uq, c = quality
    return vq >= 1 and uq == 0 and c >= min_conf


__all__ = [
    "DomainSupport", "boundary_words_per_line", "descriptor_pattern", "is_nonrecipe", "starts_new_recipe",
    "physical_conflict", "sv_quality", "sv_positive_ok", "OOD_FRAC", "GATE_CAP", "STRICT_MIN_SV_CONF",
    "STRONG_MIN_SV_CONF", "PHYS_CONFLICT_LLR",
]
