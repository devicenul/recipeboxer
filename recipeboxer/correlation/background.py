"""Collection-relative background distribution and log-likelihood ratio engine (G10).

Implements WAVE 5 Contracts §1.2 and REMEDIATION_PLAN.md §9 / §9.1:
1. DeckBackground: Models the background distribution of pairwise features across
   random pairs of THIS deck (sampled deterministically up to M pairs).
2. Parametric same-recipe priors: Documented defaults for P(raw | same), refit by
   calibration_fit later.
3. Log likelihood ratio:
   log_lr = log P(raw | same) / P(raw | background)
   via smoothed histograms / KDE.
4. Homogeneous collection constraint: In a box where all cards share stock,
   stock match provides ~0 log_lr (mutual information is zero). A rare stock pair
   in the deck yields high positive log_lr.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np

from recipeboxer.models.correlation import FeatureEvidence
from recipeboxer.models.domain import VisualFingerprint


@dataclass
class FeaturePrior:
    """Parametric distribution prior for same-recipe pairs."""

    name: str
    domain_min: float
    domain_max: float
    num_bins: int = 20
    prior_type: str = "folded_normal"  # folded_normal | exponential | beta | discrete | linear_ramp
    prior_params: Dict[str, float] = field(default_factory=dict)
    var_threshold: float = 1e-4

    def bin_edges(self) -> np.ndarray:
        return np.linspace(self.domain_min, self.domain_max, self.num_bins + 1)

    def compute_prior_bin_probs(self) -> np.ndarray:
        """Computes discrete probability mass per bin under the same-recipe prior."""
        edges = self.bin_edges()
        centers = 0.5 * (edges[:-1] + edges[1:])
        widths = edges[1:] - edges[:-1]

        if self.prior_type == "folded_normal":
            # For distance metrics where 0 is ideal match (e.g. paper_lab, aspect_ratio, ink_hue)
            sigma = self.prior_params.get("sigma", 1.5)
            # Folded normal density: sqrt(2/pi)/sigma * exp(-x^2 / (2*sigma^2))
            densities = np.exp(-0.5 * (centers / sigma) ** 2)
        elif self.prior_type == "exponential":
            # For similarity metrics in [0, 1] where 1 is ideal match (e.g. ruling)
            decay = self.prior_params.get("decay", 5.0)
            densities = np.exp(decay * (centers - self.domain_max))
        elif self.prior_type == "beta":
            # Beta distribution favoring high values
            a = self.prior_params.get("alpha", 4.0)
            b = self.prior_params.get("beta", 1.5)
            # Normalized to [0, 1]
            u = np.clip((centers - self.domain_min) / max(1e-6, self.domain_max - self.domain_min), 1e-4, 1.0 - 1e-4)
            densities = (u ** (a - 1.0)) * ((1.0 - u) ** (b - 1.0))
        elif self.prior_type == "discrete":
            # Discrete values (e.g. sort_adjacency: idx_b - idx_a)
            weights = self.prior_params.get("weights", {})
            densities = np.zeros(len(centers), dtype=np.float64)
            for i, c in enumerate(centers):
                val = int(round(c))
                densities[i] = weights.get(str(val), weights.get(val, 0.01))
        elif self.prior_type == "linear_ramp":
            # Linear ramp increasing from domain_min to domain_max
            densities = np.clip((centers - self.domain_min) / max(1e-6, self.domain_max - self.domain_min), 0.05, 1.0)
        else:
            densities = np.ones(len(centers), dtype=np.float64)

        masses = densities * widths
        total = np.sum(masses)
        if total <= 0:
            return np.full(len(centers), 1.0 / len(centers))
        return masses / total


# Documented default parametric same-recipe priors (refit by calibration_fit later)
DEFAULT_PRIORS: Dict[str, FeaturePrior] = {
    "paper_lab": FeaturePrior(
        name="paper_lab",
        domain_min=0.0,
        domain_max=50.0,
        num_bins=25,
        prior_type="folded_normal",
        prior_params={"sigma": 1.5},  # Same-recipe cards typically have Delta E <= 2.0
        var_threshold=0.05,
    ),
    "ruling": FeaturePrior(
        name="ruling",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="exponential",
        prior_params={"decay": 6.0},  # Strong preference for ruling match ~ 1.0
        var_threshold=0.01,
    ),
    "ink_hue": FeaturePrior(
        name="ink_hue",
        domain_min=0.0,
        domain_max=180.0,
        num_bins=18,
        prior_type="folded_normal",
        prior_params={"sigma": 15.0},  # Angular hue difference <= 15 deg
        var_threshold=5.0,
    ),
    "aspect_ratio": FeaturePrior(
        name="aspect_ratio",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="folded_normal",
        prior_params={"sigma": 0.04},
        var_threshold=0.005,
    ),
    "capture_gap": FeaturePrior(
        name="capture_gap",
        domain_min=-600.0,
        domain_max=1800.0,
        num_bins=24,
        prior_type="linear_ramp",
        prior_params={},
        var_threshold=1.0,
    ),
    "sort_adjacency": FeaturePrior(
        name="sort_adjacency",
        domain_min=-5.0,
        domain_max=5.0,
        num_bins=11,
        prior_type="discrete",
        # Immediate successor (b = a + 1) has 75% probability; b = a + 2 has 12%
        prior_params={"weights": {"1": 0.75, "2": 0.12, "3": 0.03, "-1": 0.02, "0": 0.01}},
        var_threshold=0.01,
    ),
    "header_score": FeaturePrior(
        name="header_score",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="beta",
        prior_params={"alpha": 4.0, "beta": 1.5},
        var_threshold=0.01,
    ),
    "show_through": FeaturePrior(
        name="show_through",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="beta",
        prior_params={"alpha": 3.0, "beta": 1.5},
        var_threshold=0.01,
    ),
    "mirrored_wear": FeaturePrior(
        name="mirrored_wear",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="beta",
        prior_params={"alpha": 3.0, "beta": 1.5},
        var_threshold=0.01,
    ),
    "outline": FeaturePrior(
        name="outline",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="beta",
        prior_params={"alpha": 3.0, "beta": 1.5},
        var_threshold=0.01,
    ),
    "content_xref": FeaturePrior(
        name="content_xref",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="beta",
        prior_params={"alpha": 3.5, "beta": 1.5},
        var_threshold=0.01,
    ),
    "sentence_continuity": FeaturePrior(
        name="sentence_continuity",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="beta",
        prior_params={"alpha": 4.0, "beta": 1.5},
        var_threshold=0.01,
    ),
    "step_continuity": FeaturePrior(
        name="step_continuity",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="beta",
        prior_params={"alpha": 4.0, "beta": 1.5},
        var_threshold=0.01,
    ),
    "title_position": FeaturePrior(
        name="title_position",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="beta",
        prior_params={"alpha": 3.0, "beta": 1.5},
        var_threshold=0.01,
    ),
    "embedding_sim": FeaturePrior(
        name="embedding_sim",
        domain_min=-1.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="beta",
        prior_params={"alpha": 4.0, "beta": 1.5},
        var_threshold=0.01,
    ),
    "ocr_cue": FeaturePrior(
        name="ocr_cue",
        domain_min=0.0,
        domain_max=1.0,
        num_bins=20,
        prior_type="beta",
        prior_params={"alpha": 3.5, "beta": 1.5},
        var_threshold=0.01,
    ),
}


@dataclass
class BinnedDistribution:
    """Empirical histogram distribution with Laplace smoothing for background pairs."""

    edges: np.ndarray
    bin_probs: np.ndarray
    sample_count: int
    mean: float
    variance: float
    std: float

    @classmethod
    def from_samples(cls, samples: List[float], prior: FeaturePrior, alpha: float = 0.5) -> "BinnedDistribution":
        edges = prior.bin_edges()
        num_bins = prior.num_bins

        if not samples:
            uniform = np.full(num_bins, 1.0 / num_bins, dtype=np.float64)
            return cls(
                edges=edges,
                bin_probs=uniform,
                sample_count=0,
                mean=0.5 * (prior.domain_min + prior.domain_max),
                variance=1.0,
                std=1.0,
            )

        arr = np.array(samples, dtype=np.float64)
        mean_val = float(np.mean(arr))
        var_val = float(np.var(arr)) if len(arr) > 1 else 0.0
        std_val = math.sqrt(var_val)

        # Histogram bin counts
        counts, _ = np.histogram(arr, bins=edges)

        # Laplace (additive) smoothing
        smoothed_counts = counts.astype(np.float64) + alpha
        bin_probs = smoothed_counts / np.sum(smoothed_counts)

        # Moving average smoothing across adjacent bins to prevent sharp steps
        if num_bins >= 3:
            kernel = np.array([0.15, 0.70, 0.15])
            padded = np.pad(bin_probs, (1, 1), mode="edge")
            smoothed = np.convolve(padded, kernel, mode="valid")
            bin_probs = smoothed / np.sum(smoothed)

        return cls(
            edges=edges,
            bin_probs=bin_probs,
            sample_count=len(samples),
            mean=mean_val,
            variance=var_val,
            std=std_val,
        )

    def get_bin_index(self, val: float) -> int:
        idx = int(np.digitize(val, self.edges) - 1)
        return max(0, min(len(self.bin_probs) - 1, idx))


class DeckBackground:
    """Collection-relative background model for a specific deck (G10)."""

    def __init__(
        self,
        distributions: Optional[Dict[str, BinnedDistribution]] = None,
        priors: Optional[Dict[str, FeaturePrior]] = None,
        card_substrates: Optional[List[Dict[str, Any]]] = None,
        deck_size: int = 0,
    ) -> None:
        self.distributions = distributions or {}
        self.priors = priors or dict(DEFAULT_PRIORS)
        self.card_substrates = card_substrates or []
        self.deck_size = deck_size

    @classmethod
    def fit(
        cls,
        fps: List[VisualFingerprint],
        raw_pair_fn: Optional[Callable[[VisualFingerprint, VisualFingerprint], List[FeatureEvidence]]] = None,
        *,
        max_pairs: int = 500,
        seed: int = 42,
        priors: Optional[Dict[str, FeaturePrior]] = None,
    ) -> "DeckBackground":
        """Fits background distributions over random pairs of THIS deck.

        Args:
            fps: List of VisualFingerprints in the deck.
            raw_pair_fn: Function (a, b) -> list[FeatureEvidence] computing raw pairwise features.
                         If None, imports compute_raw_features from recipeboxer.correlation.evidence.
            max_pairs: Maximum number of pairs to sample deterministically.
            seed: Deterministic PRNG seed.
            priors: Optional custom FeaturePrior overrides.

        Returns:
            Fitted DeckBackground instance.
        """
        active_priors = dict(DEFAULT_PRIORS)
        if priors:
            active_priors.update(priors)

        n = len(fps)
        if n < 2:
            return cls(distributions={}, priors=active_priors, card_substrates=[], deck_size=n)

        # Resolve raw_pair_fn lazily if not provided
        if raw_pair_fn is None:
            from recipeboxer.correlation.evidence import compute_raw_features
            raw_pair_fn = compute_raw_features

        # Record substrate profiles for each card for collection-relative rarity
        substrates: List[Dict[str, Any]] = []
        for fp in fps:
            paper_lab = None
            if fp.pixel and fp.pixel.paper_lab:
                paper_lab = fp.pixel.paper_lab
            elif fp.paper_color and hasattr(fp.paper_color, "lab_l"):
                paper_lab = (float(fp.paper_color.lab_l), float(fp.paper_color.lab_a), float(fp.paper_color.lab_b))

            ruling_kind = None
            ruling_spacing = None
            if fp.pixel:
                ruling_kind = fp.pixel.ruling_kind
                ruling_spacing = fp.pixel.ruling_spacing_px
            elif fp.ruling:
                ruling_kind = getattr(fp.ruling, "ruling_type", None)
                ruling_spacing = getattr(fp.ruling, "line_spacing_px", None)

            ink_hues = fp.pixel.ink_hues if fp.pixel else []

            substrates.append({
                "card_id": fp.card_id,
                "paper_lab": paper_lab,
                "ruling_kind": ruling_kind,
                "ruling_spacing": ruling_spacing,
                "ink_hues": ink_hues,
                "aspect_ratio": fp.pixel.aspect_ratio if fp.pixel else None,
            })

        # Canonical pairs enumeration
        all_pairs: List[Tuple[int, int]] = []
        for i in range(n):
            for j in range(n):
                if i != j:
                    all_pairs.append((i, j))

        total_pairs = len(all_pairs)
        if total_pairs <= max_pairs:
            sampled_pairs = all_pairs
        else:
            rng = random.Random(seed)
            sampled_indices = rng.sample(range(total_pairs), max_pairs)
            sampled_indices.sort()
            sampled_pairs = [all_pairs[idx] for idx in sampled_indices]

        # Collect raw values per feature across sampled pairs
        raw_values: Dict[str, List[float]] = {name: [] for name in active_priors}

        for i, j in sampled_pairs:
            feats = raw_pair_fn(fps[i], fps[j])
            for feat in feats:
                if feat.raw is not None and feat.name in raw_values:
                    raw_values[feat.name].append(float(feat.raw))

        # Build empirical smoothed distributions
        dists: Dict[str, BinnedDistribution] = {}
        for name, prior in active_priors.items():
            samples = raw_values.get(name, [])
            dists[name] = BinnedDistribution.from_samples(samples, prior)

        return cls(distributions=dists, priors=active_priors, card_substrates=substrates, deck_size=n)

    def _compute_substrate_rarity(
        self,
        name: str,
        fp_a: Any,
        fp_b: Any,
    ) -> Optional[float]:
        """Computes collection-relative background probability for matching card A's substrate.

        In a homogeneous deck where 95% of cards are cream index cards:
            P(random card matches card A) ~ 0.95 -> stock match log_lr ~ 0.
        In a deck where only 2 cards are kraft paper:
            P(random card matches kraft card A) ~ 2/N -> rare stock pair log_lr is high!
        """
        if not self.card_substrates or self.deck_size < 2:
            return None

        if name == "paper_lab":
            lab_a = None
            if hasattr(fp_a, "pixel") and fp_a.pixel and fp_a.pixel.paper_lab:
                lab_a = fp_a.pixel.paper_lab
            elif hasattr(fp_a, "paper_color") and fp_a.paper_color and hasattr(fp_a.paper_color, "lab_l"):
                lab_a = (float(fp_a.paper_color.lab_l), float(fp_a.paper_color.lab_a), float(fp_a.paper_color.lab_b))

            if lab_a is None:
                return None

            # Count cards in deck matching paper LAB within Delta E <= 4.0
            matches = 0
            for card in self.card_substrates:
                c_lab = card.get("paper_lab")
                if c_lab:
                    de = math.sqrt(sum((x - y) ** 2 for x, y in zip(lab_a, c_lab)))
                    if de <= 4.0:
                        matches += 1

            fraction = max(1.0 / self.deck_size, matches / self.deck_size)
            return fraction

        if name == "ruling":
            kind_a = getattr(getattr(fp_a, "pixel", None), "ruling_kind", None)
            if kind_a is None:
                kind_a = getattr(getattr(fp_a, "ruling", None), "ruling_type", None)
            if kind_a is None:
                return None

            matches = 0
            for card in self.card_substrates:
                if card.get("ruling_kind") == kind_a:
                    matches += 1
            fraction = max(1.0 / self.deck_size, matches / self.deck_size)
            return fraction

        if name == "ink_hue":
            hues_a = getattr(getattr(fp_a, "pixel", None), "ink_hues", None) or []
            if not hues_a:
                return None
            ha = hues_a[0]
            matches = 0
            for card in self.card_substrates:
                c_hues = card.get("ink_hues") or []
                for hb in c_hues:
                    circ = min(abs(ha - hb) % 360.0, 360.0 - (abs(ha - hb) % 360.0))
                    if circ <= 20.0:
                        matches += 1
                        break
            fraction = max(1.0 / self.deck_size, matches / self.deck_size)
            return fraction

        return None

    def log_lr(
        self,
        name: str,
        raw: Optional[float],
        *,
        fp_a: Any = None,
        fp_b: Any = None,
    ) -> Optional[float]:
        """Calculates the log-likelihood ratio log P(raw | same) / P(raw | background).

        Args:
            name: Feature name (e.g. "paper_lab", "ruling", "header_score").
            raw: Raw feature measurement / similarity (None if not computable).
            fp_a: Optional preceding card fingerprint (for collection-relative substrate rarity).
            fp_b: Optional succeeding card fingerprint.

        Returns:
            log_lr float, or None if not computable.
        """
        if raw is None:
            return None

        prior = self.priors.get(name)
        dist = self.distributions.get(name)
        if prior is None or dist is None:
            return None

        # Homogeneous deck check: if the feature has near-zero variance across random pairs
        # in this deck, then any matching pair carries almost zero discriminative evidence.
        if dist.variance < prior.var_threshold:
            # If the raw value is close to the deck mean, mutual information is ~ 0
            if abs(raw - dist.mean) <= max(2.0 * dist.std, 0.05):
                return 0.0

        # Substrate collection-relative rarity check (paper_lab, ruling, ink_hue)
        # In a homogeneous deck, common stock match -> rarity_fraction ~ 0.90 -> log_lr ~ 0
        # For a rare stock pair (e.g. 2 kraft cards in a white deck) -> rarity_fraction ~ 0.10 -> log_lr is high!
        if fp_a is not None and fp_b is not None and name in ("paper_lab", "ruling", "ink_hue"):
            rarity_fraction = self._compute_substrate_rarity(name, fp_a, fp_b)
            if rarity_fraction is not None:
                # Is this pair a match?
                # For paper_lab: match if raw (Delta E) <= 3.0
                # For ruling: match if raw >= 0.85
                # For ink_hue: match if raw <= 20.0
                is_match = False
                if name == "paper_lab" and raw <= 3.0:
                    is_match = True
                elif name == "ruling" and raw >= 0.85:
                    is_match = True
                elif name == "ink_hue" and raw <= 20.0:
                    is_match = True

                if is_match:
                    # P(match | same) ~ 0.90
                    # P(match | random card in this deck) = rarity_fraction
                    p_same = 0.90
                    p_bg = max(0.01, rarity_fraction)
                    llr = math.log(p_same / p_bg)
                    # If common stock in a homogeneous deck (e.g. fraction >= 0.80), llr is dampened near 0
                    if rarity_fraction >= 0.80:
                        llr = max(-0.15, min(0.15, llr * 0.1))
                    return round(llr, 4)

        # Standard binned likelihood ratio calculation
        bin_idx = dist.get_bin_index(raw)
        prior_probs = prior.compute_prior_bin_probs()

        p_same = float(prior_probs[bin_idx])
        p_bg = float(dist.bin_probs[bin_idx])

        # Guard against zero probabilities
        p_same = max(1e-6, p_same)
        p_bg = max(1e-6, p_bg)

        llr = math.log(p_same / p_bg)

        # Apply shrinkage if deck background variance is very low
        if dist.variance < prior.var_threshold * 10.0:
            scale = dist.variance / (prior.var_threshold * 10.0)
            llr *= scale

        return round(llr, 4)
