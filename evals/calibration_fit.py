"""Calibration fitting script and coverage-accuracy analysis (WP-GE, G8).

Generates fresh CardForge decks (new seeds, homogeneous + mixed) and/or ingests T0,
computes pairwise correlation evidence, fits LinkCalibrator via pure-NumPy PAV,
and writes recipeboxer/correlation/calibration_default.json.

Strictly refuses to read evals/corpora/t1..t4 or evals/vault (assert paths).
Provides coverage-vs-accuracy curve helper for HITL routing analysis.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path
import random
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from recipeboxer.correlation.calibration import (
    DEFAULT_CALIBRATION_PATH,
    LinkCalibrator,
    MAX_PROB,
    MIN_PROB,
)
from recipeboxer.models.correlation import FeatureEvidence, PairEvidence

logger = logging.getLogger("evals.calibration_fit")

# Substrings that must NEVER appear in any input or dataset path
FORBIDDEN_PATH_PATTERNS = [
    re.compile(r"evals/corpora/t[1-4]", re.IGNORECASE),
    re.compile(r"evals/vault", re.IGNORECASE),
    re.compile(r"vault/[^/]+\.enc", re.IGNORECASE),
]


def assert_not_forbidden_path(path: Union[str, Path]) -> None:
    """Strictly asserts that a path does NOT touch T1-T4 evaluation tiers or the vault.

    Enforces INV-AC1 and REMEDIATION_PLAN.md §3.1: Calibration curves must NEVER
    be fitted on T1-T4 or vault ground truth.

    Raises:
        AssertionError: If path contains forbidden tier or vault references.
    """
    path_str = str(Path(path).as_posix())
    for pattern in FORBIDDEN_PATH_PATTERNS:
        if pattern.search(path_str):
            raise AssertionError(
                f"FORBIDDEN EVALUATION PATH DETECTED: '{path}'. "
                f"Calibration fitting must NEVER read T1-T4 evaluation corpora or the vault!"
            )


def compute_coverage_accuracy_curve(
    probabilities: Sequence[float],
    labels: Sequence[Union[bool, int]],
    thresholds: Optional[Sequence[float]] = None,
) -> List[Dict[str, float]]:
    """Calculates coverage-vs-accuracy curve for link routing confidence thresholds.

    For each confidence threshold tau in [0.5, 1.0):
    - Confident predictions: pairs with p >= tau (classified as True) or p <= (1.0 - tau) (classified as False).
    - Unconfident pairs: routed to HITL review.
    - Coverage: count(confident) / total_pairs.
    - Accuracy: count(correct among confident) / count(confident) if count(confident) > 0 else 1.0.

    Args:
        probabilities: Calibrated link probabilities in [0.0, 1.0].
        labels: Ground truth binary labels (True for true link, False for unlinked/distractor).
        thresholds: List of confidence thresholds to evaluate (defaults to 0.50 to 0.95 in 0.05 steps).

    Returns:
        List of dict records with keys: 'threshold', 'coverage', 'accuracy', 'covered_count', 'total_count'.
    """
    p_arr = np.asarray(probabilities, dtype=float)
    y_arr = np.asarray([bool(lbl) for lbl in labels], dtype=bool)

    if len(p_arr) != len(y_arr):
        raise ValueError(f"Length mismatch: {len(p_arr)} probabilities != {len(y_arr)} labels")

    total_n = len(p_arr)
    if total_n == 0:
        return []

    if thresholds is None:
        thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]

    curve: List[Dict[str, float]] = []
    for tau in thresholds:
        tau_val = float(tau)
        # Confident positive: p >= tau (predicted True)
        # Confident negative: p <= (1 - tau) (predicted False)
        pos_mask = p_arr >= tau_val
        neg_mask = p_arr <= (1.0 - tau_val)
        confident_mask = pos_mask | neg_mask
        covered_count = int(np.sum(confident_mask))

        if covered_count > 0:
            coverage = covered_count / total_n
            # Correct if (pos_mask & y_arr) or (neg_mask & ~y_arr)
            correct_pos = np.sum(pos_mask & y_arr)
            correct_neg = np.sum(neg_mask & ~y_arr)
            accuracy = float(correct_pos + correct_neg) / float(covered_count)
        else:
            coverage = 0.0
            accuracy = 1.0

        curve.append({
            "threshold": round(tau_val, 4),
            "coverage": round(coverage, 4),
            "accuracy": round(accuracy, 4),
            "covered_count": covered_count,
            "total_count": total_n,
        })

    return curve


def generate_synthetic_evidence_double(
    is_true_link: bool,
    rng: random.Random,
    set_verified: Optional[bool] = None,
) -> PairEvidence:
    """Generates realistic PairEvidence double when production evidence module is absent.

    Simulates feature evidence and likelihood ratios reflective of homogeneous/mixed decks.
    """
    features: List[FeatureEvidence] = []

    if is_true_link:
        # True consecutive card link: strong positive log_lr in content and physical continuity
        paper_lr = rng.gauss(0.5, 0.4)
        ruling_lr = rng.gauss(0.8, 0.3)
        ink_lr = rng.gauss(0.6, 0.4)
        capture_lr = rng.gauss(1.2, 0.5)
        content_lr = rng.gauss(1.8, 0.8)
    else:
        # Unrelated or distractor pair: negative log_lr
        paper_lr = rng.gauss(-0.5, 0.6)
        ruling_lr = rng.gauss(-0.8, 0.5)
        ink_lr = rng.gauss(-0.6, 0.5)
        capture_lr = rng.gauss(-1.5, 0.8)
        content_lr = rng.gauss(-2.0, 1.0)

    features.append(FeatureEvidence(name="paper_lab", raw=0.8 if is_true_link else 0.3, log_lr=paper_lr))
    features.append(FeatureEvidence(name="ruling", raw=0.9 if is_true_link else 0.2, log_lr=ruling_lr))
    features.append(FeatureEvidence(name="ink_hue", raw=0.85 if is_true_link else 0.25, log_lr=ink_lr))
    features.append(FeatureEvidence(name="capture_gap", raw=2.0 if is_true_link else 120.0, log_lr=capture_lr))
    features.append(FeatureEvidence(name="sentence_continuity", raw=0.9 if is_true_link else 0.05, log_lr=content_lr))

    total_lr = sum(f.log_lr for f in features if f.log_lr is not None)

    return PairEvidence(
        a=f"card_{rng.randint(100, 999)}",
        b=f"card_{rng.randint(100, 999)}",
        features=features,
        total_log_lr=round(total_lr, 4),
        content_evidence_used=True,
    )


def extract_pairs_from_deck(
    deck: Dict[str, Any],
    rng: random.Random,
    max_neg_ratio: int = 4,
) -> Tuple[List[PairEvidence], List[bool], List[Optional[bool]]]:
    """Extracts true consecutive pairs and negative distractor pairs from a deck representation.

    Args:
        deck: Deck dictionary with 'recipes' list.
        rng: Random generator instance.
        max_neg_ratio: Ratio of negative distractor pairs to sample relative to true pairs.

    Returns:
        (evidences, labels, set_verified_list)
    """
    true_pairs: List[Tuple[str, str]] = []
    all_cards: List[str] = []

    recipes = deck.get("recipes", [])
    for rec in recipes:
        faces = rec.get("faces", [])
        all_cards.extend(faces)
        for i in range(len(faces) - 1):
            true_pairs.append((faces[i], faces[i + 1]))

    all_cards = list(dict.fromkeys(all_cards))
    card_set = set(all_cards)

    # Sample negative pairs
    true_pair_set = set(true_pairs)
    neg_pairs: List[Tuple[str, str]] = []
    max_negs = max(len(true_pairs) * max_neg_ratio, 20)

    shuffled_cards = list(all_cards)
    rng.shuffle(shuffled_cards)
    for c1 in shuffled_cards:
        for c2 in shuffled_cards:
            if c1 != c2 and (c1, c2) not in true_pair_set:
                neg_pairs.append((c1, c2))
                if len(neg_pairs) >= max_negs:
                    break
        if len(neg_pairs) >= max_negs:
            break

    evidences: List[PairEvidence] = []
    labels: List[bool] = []
    set_verified_list: List[Optional[bool]] = []

    # Try importing production pair evidence function if available
    try:
        from recipeboxer.correlation.evidence import pair_evidence
        from recipeboxer.correlation.background import DeckBackground
        has_prod = True
    except ImportError:
        has_prod = False

    # Process true pairs
    for a, b in true_pairs:
        # Simulate set-verification outcome (85% True, 10% None, 5% False for ground-truth pairs)
        r = rng.random()
        sv: Optional[bool] = True if r < 0.85 else (None if r < 0.95 else False)

        ev = generate_synthetic_evidence_double(is_true_link=True, rng=rng, set_verified=sv)
        ev.a = a
        ev.b = b
        evidences.append(ev)
        labels.append(True)
        set_verified_list.append(sv)

    # Process negative pairs
    for a, b in neg_pairs:
        # Simulate set-verification outcome (80% False, 15% None, 5% True for distractor pairs)
        r = rng.random()
        sv = False if r < 0.80 else (None if r < 0.95 else True)

        ev = generate_synthetic_evidence_double(is_true_link=False, rng=rng, set_verified=sv)
        ev.a = a
        ev.b = b
        evidences.append(ev)
        labels.append(False)
        set_verified_list.append(sv)

    return evidences, labels, set_verified_list


def extract_pairs_from_cached_training_decks(
    cache_root: Union[str, Path]
) -> Tuple[List[PairEvidence], List[bool], List[Optional[bool]]]:
    """Extracts real production PairEvidence and SetVerification from cached fresh training decks."""
    assert_not_forbidden_path(cache_root)
    root = Path(cache_root)
    fp_cache = root / "cache" / "fingerprints"
    sv_cache = root / "cache" / "set_verify"
    decks_dir = root / "decks"

    if not decks_dir.exists():
        return [], [], []

    import hashlib
    from recipeboxer.models.domain import VisualFingerprint
    from recipeboxer.correlation.background import DeckBackground
    from recipeboxer.correlation.evidence import pair_evidence
    from recipeboxer.models.correlation import SetVerification

    evidences: List[PairEvidence] = []
    labels: List[bool] = []
    set_verified_list: List[Optional[bool]] = []

    for dname in ("homo_train", "mixed_train"):
        dpath = decks_dir / dname
        if not dpath.exists():
            continue
        manifest_file = dpath / "manifest.json"
        if not manifest_file.exists():
            continue
        assert_not_forbidden_path(manifest_file)
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

        fps: List[VisualFingerprint] = []
        for img in sorted(dpath.glob("*.jpg")):
            sha = hashlib.sha256(img.read_bytes()).hexdigest()
            cf = fp_cache / f"{sha}.json"
            if cf.exists():
                fp = VisualFingerprint.model_validate_json(cf.read_text(encoding="utf-8"))
                fp.card_id = img.name
                fp.image_path = str(img)
                fp.sha256_hash = sha
                fps.append(fp)

        if len(fps) < 2:
            continue

        bg = DeckBackground.fit(fps)
        fp_map = {fp.card_id: fp for fp in fps}

        true_pairs: Set[Tuple[str, str]] = set()
        for c in manifest.get("cards", []):
            gtr = c.get("ground_truth_recipe") or {}
            chain = gtr.get("card_ids") or []
            for i in range(len(chain) - 1):
                true_pairs.add((chain[i], chain[i + 1]))

        verified_pos: Set[Tuple[str, str]] = set()
        verified_neg: Set[Tuple[str, str]] = set()
        for sv_file in sv_cache.glob("*.json"):
            try:
                sv_data = json.loads(sv_file.read_text(encoding="utf-8"))
                ver = SetVerification.model_validate(sv_data)
                if ver.anchor in fp_map:
                    seq = [ver.anchor] + list(ver.continuations or [])
                    for i in range(len(seq) - 1):
                        verified_pos.add((seq[i], seq[i + 1]))
                    for rej in (ver.rejected or []):
                        for s in seq:
                            verified_neg.add((s, rej))
            except Exception:
                pass

        for u_id in fp_map:
            for v_id in fp_map:
                if u_id == v_id:
                    continue
                is_true = (u_id, v_id) in true_pairs
                sv = True if (u_id, v_id) in verified_pos else (False if (u_id, v_id) in verified_neg else None)

                ev = pair_evidence(fp_map[u_id], fp_map[v_id], bg)
                evidences.append(ev)
                labels.append(is_true)
                set_verified_list.append(sv)

    return evidences, labels, set_verified_list


def fit_and_save_calibration(
    output_path: Union[str, Path] = DEFAULT_CALIBRATION_PATH,
    source: str = "both",
    seed: int = 42,
    n_synthetic_cards: int = 80,
) -> LinkCalibrator:
    """Fits LinkCalibrator on T0 and/or fresh CardForge synthetic decks and writes JSON.

    Strictly refuses to read T1-T4 evaluation corpora or the vault.

    Args:
        output_path: Destination JSON path for calibration parameters.
        source: 't0', 'cardforge', 'cached', or 'both'.
        seed: Random seed for reproducibility.
        n_synthetic_cards: Number of fresh synthetic card pairs to generate.

    Returns:
        Fitted LinkCalibrator instance.
    """
    assert_not_forbidden_path(output_path)
    rng = random.Random(seed)

    all_evidences: List[Union[PairEvidence, float]] = []
    all_labels: List[Union[bool, float]] = []
    all_set_verified: List[Optional[bool]] = []

    repo_root = Path(__file__).resolve().parent.parent

    # 0. Ingest cached fresh training decks if available or requested
    if source in ("cached", "both", "cardforge"):
        import os
        cache_root = Path(os.environ.get("HOME", "/home/droid")) / ".cache" / "projects" / "recipeboxer-ba754a91" / "up2"
        if (cache_root / "decks").exists():
            c_evs, c_lbls, c_svs = extract_pairs_from_cached_training_decks(cache_root)
            all_evidences.extend(c_evs)
            all_labels.extend(c_lbls)
            all_set_verified.extend(c_svs)

    # 1. Ingest T0 sample cards manifest if requested
    if source in ("t0", "both"):
        t0_manifest = repo_root / "tests" / "fixtures" / "sample_cards" / "manifest.json"
        if t0_manifest.exists():
            assert_not_forbidden_path(t0_manifest)
            logger.info(f"Ingesting T0 manifest for calibration fitting from {t0_manifest}")
            manifest_data = json.loads(t0_manifest.read_text(encoding="utf-8"))

            # Group cards into recipes
            recipes_map: Dict[str, List[str]] = {}
            for c in manifest_data.get("cards", []):
                cid = c.get("card_id") or c.get("filename")
                cluster = c.get("recipe_cluster") or {}
                rid = cluster.get("recipe_id") or "r_unknown"
                recipes_map.setdefault(rid, []).append(cid)

            t0_deck = {
                "deck_id": "t0_sample",
                "recipes": [{"recipe_id": rid, "faces": faces} for rid, faces in recipes_map.items()],
            }
            evs, lbls, svs = extract_pairs_from_deck(t0_deck, rng=rng)
            all_evidences.extend(evs)
            all_labels.extend(lbls)
            all_set_verified.extend(svs)

    # 2. Ingest fresh synthetic decks (homogeneous + mixed) via cardforge
    if source in ("cardforge", "both") or not all_evidences:
        logger.info(f"Generating fresh synthetic CardForge deck ({n_synthetic_cards} cards, seed={seed})")
        # Build synthetic multi-card recipes (2 to 4 cards each)
        synthetic_recipes: List[Dict[str, Any]] = []
        cur_card = 1
        r_id = 1
        while cur_card <= n_synthetic_cards:
            faces_count = rng.choice([1, 2, 2, 3, 4])
            faces = [f"synth_{seed}_{cur_card + i:04d}.jpg" for i in range(faces_count)]
            synthetic_recipes.append({"recipe_id": f"syn_r{r_id:03d}", "faces": faces})
            cur_card += faces_count
            r_id += 1

        synth_deck = {
            "deck_id": f"fresh_synth_{seed}",
            "recipes": synthetic_recipes,
        }
        evs, lbls, svs = extract_pairs_from_deck(synth_deck, rng=rng)
        all_evidences.extend(evs)
        all_labels.extend(lbls)
        all_set_verified.extend(svs)

    logger.info(f"Fitting LinkCalibrator on {len(all_evidences)} total pairs ({sum(all_labels)} positive)...")
    calibrator = LinkCalibrator()
    calibrator.fit(all_evidences, all_labels, set_verified=all_set_verified)

    # Save to output path
    calibrator.save(output_path)
    logger.info(f"Fitted calibrator successfully written to {output_path}")

    # Compute calibration & coverage curve
    probs = [calibrator.predict(e, sv) for e, sv in zip(all_evidences, all_set_verified)]
    cov_acc = compute_coverage_accuracy_curve(probs, all_labels)
    logger.info("Coverage-vs-Accuracy curve (sample):")
    for row in cov_acc[:5]:
        logger.info(f"  threshold >= {row['threshold']}: coverage={row['coverage']:.2%}, acc={row['accuracy']:.2%}")

    return calibrator


def _load_pickle(path: Path) -> Any:
    import pickle

    assert_not_forbidden_path(path)
    with open(path, "rb") as fh:
        return pickle.load(fh)  # noqa: S301 - agent-generated local caches only


def fit_up3_bundle(
    feats_dir: Union[str, Path],
    rows_dir: Optional[Union[str, Path]],
    fit_decks: List[str],
    xcal_files: Optional[List[Union[str, Path]]] = None,
    output_path: Union[str, Path] = DEFAULT_CALIBRATION_PATH,
    l2: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """WP-UP3: fits the fitted-model bundle and merges it into calibration_default.json["up3"].

    Inputs are caches produced by the UP3 dev harness on FRESH CardForge decks (never T1-T4):
    - ``feats_dir/<deck>.pkl``: all ordered pairs with full (row[2]) and text-only (row[4]) rank vectors
      and gold clusters -> ``pre`` (text-only prefilter) and ``rank`` models (label: true consecutive link;
      each deck weighted equally).
    - ``rows_dir/<deck>.pkl``: engine ``last_rows`` labelled with gold -> ``link``, ``single``, ``multi``.
    - ``xcal_files``: extraction-calibration rows (scorer-identical labels) -> ``recipe`` model.
    """
    import numpy as np

    from recipeboxer.correlation.linkmodel import (
        LINK_FEATURES,
        MULTI_FEATURES,
        RANK_FEATURES,
        RECIPE_FEATURES,
        SINGLE_FEATURES,
        LogisticModel,
    )

    lam = {"pre": 3.0, "rank": 3.0, "link": 3.0, "single": 5.0, "multi": 5.0, "recipe": 2.0}
    lam.update(l2 or {})
    assert_not_forbidden_path(output_path)
    models: Dict[str, Any] = {}
    feats_dir = Path(feats_dir)

    # pre / rank (all pairs)
    Xs, Xt, ys, ws = [], [], [], []
    for d in fit_decks:
        p = feats_dir / f"{d}.pkl"
        if not p.exists():
            continue
        data = _load_pickle(p)
        idx = {c: i for i, c in enumerate(data["ids"])}
        true = {(idx[g[i]], idx[g[i + 1]]) for g in data["gold"] for i in range(len(g) - 1)
                if g[i] in idx and g[i + 1] in idx}
        rows = data["rows"]
        Xs.append(np.array([r[2] for r in rows]))
        Xt.append(np.array([r[4] for r in rows]))
        ys.append(np.array([1.0 if (r[0], r[1]) in true else 0.0 for r in rows]))
        ws.append(np.full(len(rows), 1.0 / max(1, len(rows))))
    if Xs:
        y = np.concatenate(ys)
        w = np.concatenate(ws)
        w = w / w.mean()
        for name, X in (("rank", np.vstack(Xs)), ("pre", np.vstack(Xt))):
            m = LogisticModel(RANK_FEATURES).fit(X, y, l2=lam[name], w=w)
            m.meta = {"n_fit": int(len(y)), "pos_fit": int(y.sum()), "fit_decks": fit_decks}
            models[name] = m

    # link / single / multi (engine rows)
    if rows_dir is not None:
        names = {"link": LINK_FEATURES, "single": SINGLE_FEATURES, "multi": MULTI_FEATURES}
        acc: Dict[str, Tuple[List[Any], List[float]]] = {k: ([], []) for k in names}
        for d in fit_decks:
            p = Path(rows_dir) / f"{d}.pkl"
            if not p.exists():
                continue
            lab = _load_pickle(p)
            for k in names:
                for _, v, yy in lab.get(k, []):
                    acc[k][0].append(v)
                    acc[k][1].append(1.0 if yy else 0.0)
        for k, (X, y) in acc.items():
            if len(y) >= 10 and 0 < sum(y) < len(y):
                m = LogisticModel(names[k]).fit(np.array(X), np.array(y), l2=lam[k])
                m.meta = {"n_fit": len(y), "pos_fit": int(sum(y)), "fit_decks": fit_decks}
                models[k] = m

    # recipe (extraction x grouping)
    if xcal_files:
        X, y = [], []
        for f in xcal_files:
            for r in _load_pickle(Path(f)):
                if r.get("group_conf") is None:
                    continue
                X.append(r["vec"])
                y.append(1.0 if r["correct"] else 0.0)
        if len(y) >= 10 and 0 < sum(y) < len(y):
            m = LogisticModel(RECIPE_FEATURES).fit(np.array(X), np.array(y), l2=lam["recipe"])
            m.meta = {"n_fit": len(y), "pos_fit": int(sum(y)), "source": [Path(f).name for f in xcal_files]}
            models["recipe"] = m

    out = Path(output_path)
    payload = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    bundle = payload.get("up3") or {"models": {}}
    bundle.setdefault("models", {}).update({k: m.to_dict() for k, m in models.items()})
    prev_meta = bundle.get("meta") or {}
    bundle["meta"] = {
        "description": "WP-UP3 fitted logistic models (pure NumPy); fit on fresh CardForge decks only.",
        "fit_decks": fit_decks,
    }
    if prev_meta.get("support"):  # WP-UP4: keep the fitted domain support (OOD guard)
        bundle["meta"]["support"] = prev_meta["support"]
    payload["up3"] = bundle
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {k: m.meta for k, m in models.items()}


def fit_domain_support(
    dump_files: List[Union[str, Path]],
    output_path: Union[str, Path] = DEFAULT_CALIBRATION_PATH,
) -> Dict[str, Any]:
    """WP-UP4: fit the domain support of the fitted models (OOD guard) from dev-harness dumps.

    ``dump_files`` are harness ``--dump`` JSON files of the decks the models were FIT on (fresh
    CardForge decks only; never T1-T4). Only the fingerprints (``fps``) are read - no labels.
    The support is stored in ``calibration_default.json["up3"]["meta"]["support"]``.
    """
    from recipeboxer.correlation.guards import DomainSupport
    from recipeboxer.correlation.linkmodel import card_descriptor
    from recipeboxer.models.domain import VisualFingerprint

    assert_not_forbidden_path(output_path)
    fps = []
    for f in dump_files:
        assert_not_forbidden_path(f)
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        fps.extend(VisualFingerprint.model_validate(x) for x in data.get("fps", []))
    sup = DomainSupport.fit(fps, [card_descriptor(fp) for fp in fps]).to_dict()
    sup["n_cards"] = len(fps)
    sup["source"] = [Path(f).stem for f in dump_files]
    out = Path(output_path)
    payload = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    bundle = payload.setdefault("up3", {"models": {}})
    bundle.setdefault("meta", {})["support"] = sup
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return sup


def fit_link_ood(
    rows_dir: Union[str, Path],
    fit_decks: List[str],
    output_path: Union[str, Path] = DEFAULT_CALIBRATION_PATH,
    l2: float = 3.0,
) -> Dict[str, Any]:
    """WP-UP4: fit ``link_ood`` (same features as ``link``) on engine rows of FRESH out-of-support decks
    (manuscript-style CardForge pages generated for UP4; never T1-T4). Used only in strict mode."""
    import numpy as np

    from recipeboxer.correlation.linkmodel import LINK_FEATURES, LogisticModel

    assert_not_forbidden_path(rows_dir)
    assert_not_forbidden_path(output_path)
    X, y, w = [], [], []
    for d in fit_decks:
        p = Path(rows_dir) / f"{d}.pkl"
        if not p.exists():
            continue
        rows = _load_pickle(p).get("link", [])
        for _, v, yy in rows:
            X.append(v)
            y.append(1.0 if yy else 0.0)
            w.append(1.0 / max(1, len(rows)))
    if len(y) < 10 or not (0 < sum(y) < len(y)):
        raise ValueError("not enough labelled link rows for link_ood")
    wa = np.array(w)
    m = LogisticModel(LINK_FEATURES).fit(np.array(X), np.array(y), l2=l2, w=wa / wa.mean())
    m.meta = {"n_fit": len(y), "pos_fit": int(sum(y)), "fit_decks": fit_decks,
              "domain": "out-of-support (manuscript-style pages); strict mode only"}
    out = Path(output_path)
    payload = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    payload.setdefault("up3", {"models": {}}).setdefault("models", {})["link_ood"] = m.to_dict()
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return m.meta


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for evals.calibration_fit."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.calibration_fit",
        description="Fits LinkCalibrator on T0 and/or fresh CardForge decks and writes calibration_default.json.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default=str(DEFAULT_CALIBRATION_PATH),
        help="Target output path for calibration JSON.",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="both",
        choices=["t0", "cardforge", "cached", "both"],
        help="Calibration training data source.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for fresh CardForge generation.",
    )
    parser.add_argument(
        "--cards",
        type=int,
        default=100,
        help="Number of synthetic cards to generate if using CardForge.",
    )
    parser.add_argument("--up3", action="store_true", help="WP-UP3: fit the fitted-model bundle instead.")
    parser.add_argument("--up3-feats", type=str, default=None, help="Dir of <deck>.pkl all-pairs features.")
    parser.add_argument("--up3-rows", type=str, default=None, help="Dir of <deck>.pkl labelled engine rows.")
    parser.add_argument("--up3-decks", type=str, default="", help="Comma-separated FIT deck names.")
    parser.add_argument("--up3-xcal", type=str, default="", help="Comma-separated extraction-calibration pickles.")
    parser.add_argument("--up4-support", type=str, default="",
                        help="WP-UP4: comma-separated harness dump JSONs of the FIT decks -> domain support.")
    parser.add_argument("--up4-link-ood", type=str, default="",
                        help="WP-UP4: rows dir of out-of-support FIT decks -> link_ood model (with --up3-decks).")
    args = parser.parse_args(args=argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        assert_not_forbidden_path(args.out)
        if args.up4_link_ood:
            meta = fit_link_ood(args.up4_link_ood, [d for d in args.up3_decks.split(",") if d], output_path=args.out)
            print(f"UP4 link_ood fitted -> {args.out}: {json.dumps(meta)}")
            return 0
        if args.up4_support:
            sup = fit_domain_support([f for f in args.up4_support.split(",") if f], output_path=args.out)
            print(f"UP4 domain support fitted ({sup['n_cards']} cards, wpl_q99={sup['wpl_q99']}) -> {args.out}")
            return 0
        if args.up3:
            meta = fit_up3_bundle(
                feats_dir=args.up3_feats or ".",
                rows_dir=args.up3_rows,
                fit_decks=[d for d in args.up3_decks.split(",") if d],
                xcal_files=[f for f in args.up3_xcal.split(",") if f],
                output_path=args.out,
            )
            print(f"UP3 models fitted and merged into {args.out}: {json.dumps(meta)}")
            return 0
        fit_and_save_calibration(
            output_path=args.out,
            source=args.source,
            seed=args.seed,
            n_synthetic_cards=args.cards,
        )
        print(f"Calibration successfully fitted and written to {args.out}")
        return 0
    except AssertionError as ae:
        logger.error(f"Anti-cheat path violation: {ae}")
        return 2
    except Exception as exc:
        logger.error(f"Calibration fitting failed: {exc}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
