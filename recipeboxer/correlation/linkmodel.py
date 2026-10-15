"""Fitted link / cluster probability models (WP-UP3).

Replaces hand-set evidence sums as the *decision* score with small pure-NumPy logistic models whose
coefficients are FITTED on fresh synthetic decks (evals/calibration_fit.py --up3) and shipped in
``calibration_default.json`` under ``"up3"``:

- ``rank``  : P(A -> B | pair evidence), no set-verification input. Used to rank screening candidates
              (which ones the SetVerifier sees) and for links that were never verified.
- ``link``  : P(A -> B | pair evidence, SetVerifier outcome, deck context). Calibrated link probability
              fed to the path-cover optimizer.
- ``multi`` : P(multi-card proposal is exactly one complete recipe | chain features).
- ``single``: P(single-card proposal is truly a standalone item | evidence: title present, complete
              ending, no candidate link above threshold, ...). This is the fitted standalone
              confidence - NOT a hard-coded prior (INV-AC4).

Every feature is generic (structure, boundary text, physical pairing, collection-relative LRs); no
filenames, image hashes or corpus-specific strings are used.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from recipeboxer.models.correlation import PairEvidence
from recipeboxer.models.domain import VisualFingerprint

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "calibration_default.json"

_LLR_FEATURES = (
    "paper_lab", "ruling", "ink_hue", "aspect_ratio", "show_through", "mirrored_wear", "outline",
    "header_score", "content_xref", "sentence_continuity", "step_continuity", "title_position",
    "marker_evidence",
)
_WORD = re.compile(r"[a-z0-9]+")
_TITLE_STOP = {
    "the", "and", "with", "for", "of", "a", "an", "my", "our", "old", "best", "easy", "quick", "s",
    "cont", "continued", "card", "back", "front", "page", "part", "finishing", "directions",
    "ingredients", "from", "over", "see", "turn", "to", "ii", "recipe",
}
_FWD_CUES = ("over", "pto", "turn", "see back", "see card", "cont", "continued", "->", "next", ">>")
_CONT_HDR = ("cont", "continued", "card 2", "card 3", "page 2", "part 2", "back", "finishing")


# ----------------------------------------------------------------------------------------------
# Per-card descriptors (cheap, fingerprint-only)
# ----------------------------------------------------------------------------------------------
def _norm_words(text: str) -> List[str]:
    return _WORD.findall((text or "").lower())


def _title_tokens(fp: VisualFingerprint) -> set:
    return {w for w in _norm_words(fp.visible_title or "") if len(w) > 2 and w not in _TITLE_STOP}


def _head_lines(fp: VisualFingerprint) -> List[str]:
    out = list(fp.first_lines or [])[:2]
    bt = getattr(fp, "boundary_text", None)
    if bt is not None and getattr(bt, "first_line", None):
        out.append(bt.first_line)
    return [ln for ln in out if ln]


def _tail_lines(fp: VisualFingerprint) -> List[str]:
    out = list(fp.last_lines or [])[-2:]
    bt = getattr(fp, "boundary_text", None)
    if bt is not None and getattr(bt, "last_line", None):
        out.append(bt.last_line)
    return [ln for ln in out if ln]


def card_descriptor(fp: VisualFingerprint) -> Dict[str, float]:
    """Cheap per-card structural descriptor used by link and cluster models."""
    from recipeboxer.correlation.content import completeness_prior

    pri = completeness_prior(fp)
    sec = {s.strip().lower() for s in (fp.sections_detected or [])}
    has_title = 1.0 if (fp.visible_title and fp.visible_title.strip()) else 0.0
    has_ing = 1.0 if (fp.has_ingredients is True or "ingredients" in sec) else 0.0
    has_dir = 1.0 if (fp.has_directions is True or "directions" in sec) else 0.0
    markers = " | ".join(m.lower() for m in (fp.printed_markers or []) if m)
    tail = " | ".join(t.lower() for t in _tail_lines(fp))
    head = " | ".join(h.lower() for h in _head_lines(fp))
    fwd = 1.0 if any(c in markers for c in _FWD_CUES) or any(c in tail for c in ("(over", "over ->", "pto", "see card", "turn to", "cont'd", ">>>")) else 0.0
    conth = 1.0 if (not has_title and (any(c in head for c in _CONT_HDR) or any(c in markers for c in _CONT_HDR))) else 0.0
    role = str(getattr(fp.role, "value", fp.role) or "").upper()
    return {
        "has_title": has_title,
        "has_ing": has_ing,
        "has_dir": has_dir,
        "ing_only": 1.0 if (has_ing and not has_dir) else 0.0,
        "dir_only": 1.0 if (has_dir and not has_ing) else 0.0,
        "no_recipe_sections": 1.0 if (not has_ing and not has_dir) else 0.0,
        "back": 1.0 if fp.looks_like_back is True else 0.0,
        "cut_end": 1.0 if fp.text_cut_off_end is True else 0.0,
        "cut_start": 1.0 if fp.text_cut_off_start is True else 0.0,
        "fwd_cue": fwd,
        "cont_header": conth,
        "role_start": 1.0 if "START" in role else 0.0,
        "role_cont": 1.0 if ("CONTINUATION" in role or "BACK" in role) else 0.0,
        "role_standalone": 1.0 if "STANDALONE" in role else 0.0,
        "role_unknown": 1.0 if "UNKNOWN" in role else 0.0,
        "role_conf": float(getattr(fp, "role_confidence", 0.5) or 0.5),
        "expects_cont": float(pri.get("expects_continuation", 0.5)),
        "is_complete": float(pri.get("is_complete", 0.5)),
        "is_start": float(pri.get("is_start", 0.5)),
        "has_yield": 1.0 if ("yield" in sec or "yield" in tail) else 0.0,
    }


def _title_header_overlap(fa: VisualFingerprint, fb: VisualFingerprint) -> float:
    """Fraction of A's distinctive title words that re-appear in B's header/markers (e.g. 'X cont.')."""
    ta = _title_tokens(fa)
    if not ta:
        return 0.0
    hb = set()
    for ln in _head_lines(fb) + list(fb.printed_markers or []) + ([fb.visible_title] if fb.visible_title else []):
        hb |= set(_norm_words(ln))
    return len(ta & hb) / max(1, len(ta))


def _titles_conflict(fa: VisualFingerprint, fb: VisualFingerprint) -> float:
    """1.0 when both faces carry distinct full titles (two different recipe starts)."""
    if not (fa.visible_title and fb.visible_title):
        return 0.0
    ta, tb = _title_tokens(fa), _title_tokens(fb)
    if not ta or not tb:
        return 0.0
    if any(c in (fb.visible_title or "").lower() for c in ("cont", "card 2", "page 2", "part 2")):
        return 0.0
    return 1.0 if len(ta & tb) / max(1, min(len(ta), len(tb))) < 0.5 else 0.0


# ----------------------------------------------------------------------------------------------
# Pair features
# ----------------------------------------------------------------------------------------------
RANK_FEATURES: Tuple[str, ...] = tuple(
    [f"llr_{n}" for n in _LLR_FEATURES]
    + [
        "a_ing_only", "a_fwd_cue", "a_cut_end", "a_expects_cont", "a_is_complete", "a_has_title",
        "a_back", "a_role_standalone", "a_no_recipe",
        "b_has_title", "b_back", "b_dir_only", "b_cont_header", "b_cut_start", "b_is_start",
        "b_role_cont", "b_role_standalone", "b_no_recipe", "b_has_ing",
        "title_hdr_overlap", "titles_conflict", "ing_to_dir", "fwd_and_cont", "b_has_yield",
    ]
)
LINK_FEATURES: Tuple[str, ...] = (
    "rank_logit", "sv_pos_direct", "sv_pos_chain", "sv_neg", "sv_seen", "sv_conf_pos", "rescue_pos",
    "n_pos_into_b", "n_pos_out_a", "log_n",
)


def link_feature_vector(rank_logit: float, *, pos_direct: bool, pos_chain: bool, neg: bool, seen: bool,
                        conf: Optional[float], rescue: bool, n_into_b: int, n_out_a: int, n_cards: int) -> np.ndarray:
    """Features for P(A->B is a true consecutive link | evidence, SetVerifier outcome, deck context)."""
    pos = pos_direct or pos_chain
    return np.asarray([
        max(-12.0, min(12.0, float(rank_logit))),
        1.0 if pos_direct else 0.0,
        1.0 if (pos_chain and not pos_direct) else 0.0,
        1.0 if (neg and not pos) else 0.0,
        1.0 if seen else 0.0,
        float(conf) if (pos and conf is not None) else 0.0,
        1.0 if (rescue and pos) else 0.0,
        float(max(0, n_into_b - 1)) if pos else 0.0,
        float(max(0, n_out_a - 1)) if pos else 0.0,
        math.log(max(2, n_cards)),
    ], dtype=float)


def _llr(ev: PairEvidence, name: str) -> float:
    for f in ev.features:
        if f.name == name and f.log_lr is not None:
            return float(max(-3.0, min(3.0, f.log_lr)))
    return 0.0


def rank_feature_vector(ev: PairEvidence, da: Dict[str, float], db: Dict[str, float],
                        fa: VisualFingerprint, fb: VisualFingerprint) -> np.ndarray:
    v = [_llr(ev, n) for n in _LLR_FEATURES]
    v += [
        da["ing_only"], da["fwd_cue"], da["cut_end"], da["expects_cont"], da["is_complete"], da["has_title"],
        da["back"], da["role_standalone"], da["no_recipe_sections"],
        db["has_title"], db["back"], db["dir_only"], db["cont_header"], db["cut_start"], db["is_start"],
        db["role_cont"], db["role_standalone"], db["no_recipe_sections"], db["has_ing"],
        _title_header_overlap(fa, fb), _titles_conflict(fa, fb),
        da["ing_only"] * db["dir_only"], da["fwd_cue"] * db["cont_header"], db["has_yield"],
    ]
    return np.asarray(v, dtype=float)


# ----------------------------------------------------------------------------------------------
# Logistic model
# ----------------------------------------------------------------------------------------------
def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-z))


class LogisticModel:
    """L2-regularised logistic regression (Newton/IRLS), standardised features, pure NumPy."""

    def __init__(self, names: Sequence[str], coef: Optional[Sequence[float]] = None, intercept: float = 0.0,
                 mean: Optional[Sequence[float]] = None, scale: Optional[Sequence[float]] = None,
                 temperature: float = 1.0, meta: Optional[Dict[str, Any]] = None) -> None:
        self.names = list(names)
        d = len(self.names)
        self.coef = np.asarray(coef if coef is not None else np.zeros(d), dtype=float)
        self.intercept = float(intercept)
        self.mean = np.asarray(mean if mean is not None else np.zeros(d), dtype=float)
        self.scale = np.asarray(scale if scale is not None else np.ones(d), dtype=float)
        self.temperature = float(temperature)
        self.meta = dict(meta or {})

    @property
    def fitted(self) -> bool:
        return bool(np.any(self.coef != 0.0) or self.intercept != 0.0)

    def fit(self, X: np.ndarray, y: np.ndarray, l2: float = 1.0, w: Optional[np.ndarray] = None,
            iters: int = 50) -> "LogisticModel":
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        n, d = X.shape
        self.mean = X.mean(axis=0)
        sd = X.std(axis=0)
        self.scale = np.where(sd < 1e-6, 1.0, sd)
        Z = (X - self.mean) / self.scale
        Zb = np.hstack([Z, np.ones((n, 1))])
        sw = np.ones(n) if w is None else np.asarray(w, dtype=float)
        beta = np.zeros(d + 1)
        reg = np.full(d + 1, l2)
        reg[-1] = 1e-6
        for _ in range(iters):
            p = _sigmoid(Zb @ beta)
            g = Zb.T @ (sw * (p - y)) + reg * beta
            W = sw * p * (1 - p) + 1e-9
            H = (Zb * W[:, None]).T @ Zb + np.diag(reg)
            step = np.linalg.solve(H, g)
            beta -= step
            if np.max(np.abs(step)) < 1e-7:
                break
        self.coef = beta[:-1]
        self.intercept = float(beta[-1])
        self.temperature = 1.0
        return self

    def logit(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        return ((X - self.mean) / self.scale) @ self.coef + self.intercept

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return _sigmoid(self.logit(X) / max(1e-3, self.temperature))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "names": self.names,
            "coef": [round(float(c), 6) for c in self.coef],
            "intercept": round(self.intercept, 6),
            "mean": [round(float(c), 6) for c in self.mean],
            "scale": [round(float(c), 6) for c in self.scale],
            "temperature": round(self.temperature, 6),
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LogisticModel":
        return cls(d["names"], d.get("coef"), d.get("intercept", 0.0), d.get("mean"), d.get("scale"),
                   d.get("temperature", 1.0), d.get("meta"))


# ----------------------------------------------------------------------------------------------
# Cluster (proposal) features
# ----------------------------------------------------------------------------------------------
SINGLE_FEATURES: Tuple[str, ...] = (
    "has_title", "has_ing", "has_dir", "ing_only", "dir_only", "no_recipe_sections", "back", "cut_end",
    "cut_start", "fwd_cue", "cont_header", "role_start", "role_cont", "role_standalone", "role_unknown",
    "role_conf", "expects_cont", "is_complete", "is_start", "has_yield",
    "max_out_link", "max_in_link", "max_out_rank", "max_in_rank", "n_out_above_half", "sv_any_continuation",
    "log_n",
)
MULTI_FEATURES: Tuple[str, ...] = (
    "n_links", "min_link", "mean_link", "min_link_logit", "all_sv_pos", "frac_sv_pos", "head_has_title",
    "head_is_start", "head_ing_only", "tail_expects_cont", "tail_fwd_cue", "tail_has_yield", "tail_is_complete",
    "max_tail_out_other", "max_head_in_other", "n_titles", "mid_has_title", "log_n",
)


def _logit(p: float) -> float:
    p = min(1 - 1e-4, max(1e-4, float(p)))
    return math.log(p / (1 - p))


def single_feature_vector(desc: Dict[str, float], ctx: Dict[str, float]) -> np.ndarray:
    v = [desc[k] for k in SINGLE_FEATURES[:20]]
    v += [ctx.get(k, 0.0) for k in SINGLE_FEATURES[20:]]
    return np.asarray(v, dtype=float)


def multi_feature_vector(chain_desc: List[Dict[str, float]], link_ps: List[float], sv_pos: List[bool],
                         ctx: Dict[str, float]) -> np.ndarray:
    head, tail = chain_desc[0], chain_desc[-1]
    n_titles = sum(d["has_title"] for d in chain_desc)
    mid_title = sum(d["has_title"] for d in chain_desc[1:])
    v = [
        float(len(link_ps)), min(link_ps), float(np.mean(link_ps)), _logit(min(link_ps)),
        1.0 if all(sv_pos) else 0.0, float(np.mean(sv_pos)) if sv_pos else 0.0, head["has_title"],
        head["is_start"], head["ing_only"], tail["expects_cont"], tail["fwd_cue"], tail["has_yield"], tail["is_complete"],
        ctx.get("max_tail_out_other", 0.0), ctx.get("max_head_in_other", 0.0), float(n_titles), float(mid_title),
        ctx.get("log_n", 0.0),
    ]
    return np.asarray(v, dtype=float)


# ----------------------------------------------------------------------------------------------
# Recipe-level (scored) confidence: P(extracted correctly AND grouped correctly)
# ----------------------------------------------------------------------------------------------
RECIPE_FEATURES: Tuple[str, ...] = (
    "group_logit", "group_conf_missing", "is_multi", "n_cards", "log_n_ing", "log_n_dir", "no_ing", "no_dir",
    "title_inferred", "n_warn", "n_warn_grounding", "n_warn_disagree", "n_warn_lowconf", "mean_line_conf",
    "min_line_conf",
)


def recipe_feature_vector(group_conf: Optional[float], recipe: Any, n_cards: int) -> np.ndarray:
    """Generic extraction-quality signals of a structured recipe + its grouping confidence."""
    ings, steps, confs = [], [], []
    for sec in (getattr(recipe, "sections", None) or []):
        for ing in (getattr(sec, "ingredients", None) or []):
            ings.append(ing)
            c = getattr(ing, "confidence", None)
            if isinstance(c, (int, float)):
                confs.append(float(c))
        for st in (getattr(sec, "steps", None) or []):
            steps.append(st)
            c = getattr(st, "confidence", None)
            if isinstance(c, (int, float)):
                confs.append(float(c))
    codes = [str(getattr(w, "code", "") or "").upper() for w in (getattr(recipe, "warnings", None) or [])]
    return np.asarray([
        _logit(group_conf) if group_conf is not None else 0.0,
        0.0 if group_conf is not None else 1.0,
        1.0 if n_cards > 1 else 0.0,
        float(n_cards),
        math.log1p(len(ings)),
        math.log1p(len(steps)),
        1.0 if not ings else 0.0,
        1.0 if not steps else 0.0,
        1.0 if getattr(recipe, "title_is_inferred", False) else 0.0,
        float(len(codes)),
        float(sum(1 for c in codes if "GROUND" in c)),
        float(sum(1 for c in codes if "DISAGREE" in c or "CONSISTEN" in c)),
        float(sum(1 for c in codes if "LOW_CONF" in c or "ILLEGIBLE" in c)),
        float(np.mean(confs)) if confs else 0.5,
        float(min(confs)) if confs else 0.5,
    ], dtype=float)


def recipe_confidence(group_conf: Optional[float], recipe: Any, n_cards: int,
                      models: Optional["Up3Models"] = None) -> Optional[float]:
    """Scored recipe confidence (``recipes[].confidence`` in headless output).

    Semantics: probability that this predicted recipe is BOTH grouped correctly and extracted correctly
    (the scorer's definition: content_sim >= 0.5 AND ingredient_f1 >= 0.7 on the matched gold recipe).
    Uses the fitted ``recipe`` model when available; falls back to the grouping confidence (an upper
    bound) when no recipe model is fitted; None when there is no evidence at all.
    """
    m = (models or default_models()).get("recipe")
    if m is None or group_conf is None:
        return group_conf
    return round(float(m.predict_proba(recipe_feature_vector(group_conf, recipe, n_cards))[0]), 4)


# ----------------------------------------------------------------------------------------------
# Bundle loading
# ----------------------------------------------------------------------------------------------
class Up3Models:
    """Container for the fitted UP3 models; missing/unfitted models fall back to None."""

    def __init__(self, models: Optional[Dict[str, LogisticModel]] = None, meta: Optional[Dict[str, Any]] = None):
        self.models = dict(models or {})
        self.meta = dict(meta or {})
        self.load_status: Dict[str, Any] = {"loaded": bool(self.models), "path": None, "error": None,
                                            "failed_models": []}

    def provenance(self) -> Dict[str, Any]:
        """Run-provenance record (RT3-08): which fitted models are active and whether loading failed."""
        return dict(self.load_status, active_models=sorted(k for k in self.models if self.get(k) is not None))

    def get(self, name: str) -> Optional[LogisticModel]:
        m = self.models.get(name)
        return m if (m is not None and m.fitted) else None

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Up3Models":
        p = Path(path) if path else DEFAULT_MODEL_PATH
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            # RT3-08: never silent -- production falls back to the legacy (unfitted) path.
            logger.error("UP3 model bundle %s could not be loaded (%s: %s); falling back to legacy grouping "
                         "and recipe confidences", p, type(exc).__name__, exc)
            out = cls()
            out.load_status = {"loaded": False, "path": p.name, "error": f"{type(exc).__name__}: {exc}",
                               "failed_models": []}
            return out
        up3 = data.get("up3") or {}
        models = {}
        failed: List[str] = []
        for k, v in (up3.get("models") or {}).items():
            try:
                models[k] = LogisticModel.from_dict(v)
            except Exception as exc:
                logger.error("UP3 model %r in %s could not be loaded (%s: %s); it is disabled", k, p,
                             type(exc).__name__, exc)
                failed.append(k)
        out = cls(models, up3.get("meta") or {})
        out.load_status = {"loaded": bool(models), "path": p.name, "error": None if models else "no models in bundle",
                           "failed_models": failed}
        if not models:
            logger.error("UP3 model bundle %s contains no loadable models; legacy path in use", p)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {"meta": self.meta, "models": {k: m.to_dict() for k, m in self.models.items()}}


_CACHED: Optional[Up3Models] = None


def default_models() -> Up3Models:
    global _CACHED
    if _CACHED is None:
        _CACHED = Up3Models.load()
    return _CACHED


def reset_default_models() -> None:
    global _CACHED
    _CACHED = None
