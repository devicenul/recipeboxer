"""Cost engine, price table, usage tracking, and budget enforcement for RecipeBoxer (WP-B1).

Implements:
- PriceTable: loaded from config/pricing.toml with per-model input/output/thinking rates,
  context-tier thresholds, and strict unpriced model warnings (never 0 silently).
- CostLedger: UsageLedger implementation tracking tokens, latency, per-call USD, and aggregates.
- BudgetGuard: Pre-call enforcement checking running spend against spend_cap_usd and max_calls.
- BudgetExceeded: Typed exception mapped to FailureReason.BUDGET_EXCEEDED.
- estimate_run_cost: Pre-run cost estimator following REMEDIATION_PLAN.md §7.1.
"""

from __future__ import annotations

import importlib.resources
import json
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from recipeboxer.ai.client import LLMStage, UsageLedger, UsageRecord, UsageTokens
from recipeboxer.config import DEFAULT_FLASH_MODEL, QUALITY_PROFILES, get_settings
from recipeboxer.models.failures import BudgetExceeded, BudgetUnpriced, FailureReason, StageUnavailableError

logger = logging.getLogger(__name__)


@dataclass
class ModelPrice:
    """Pricing configuration for an individual model in USD per Million Tokens (MTok)."""

    model: str
    input_per_mtok: float
    output_per_mtok: float
    thinking_per_mtok: float = 0.0
    context_tier_threshold_tokens: Optional[int] = None
    input_per_mtok_high_tier: Optional[float] = None
    output_per_mtok_high_tier: Optional[float] = None
    verified_on: str = "UNVERIFIED"
    source_url: str = ""


class PriceTable:
    """Model pricing lookup table loaded from user-editable TOML configuration (WP-B1)."""

    def __init__(
        self,
        prices: Optional[Dict[str, ModelPrice]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.prices: Dict[str, ModelPrice] = dict(prices or {})
        self.meta: Dict[str, Any] = dict(meta or {})

    @classmethod
    def load(cls, path: Optional[Union[Path, str]] = None) -> "PriceTable":
        """Loads and parses pricing configuration from TOML file or package data.

        Args:
            path: Optional custom path to pricing TOML file. Defaults to package data pricing.toml.

        Returns:
            Configured PriceTable instance.
        """
        raw_bytes: Optional[bytes] = None
        source_desc: str = ""

        # 1. Custom path explicitly supplied
        if path is not None:
            p = Path(path).resolve()
            if p.exists() and p.is_file():
                try:
                    raw_bytes = p.read_bytes()
                    source_desc = str(p)
                except Exception as exc:
                    logger.error(f"Failed to read pricing TOML file '{p}': {exc}.")
            elif str(path) in ("config/pricing.toml", "pricing.toml") or Path(path).name == "pricing.toml":
                # Tolerant fallback to packaged default if legacy repo-relative path is requested
                logger.info(f"Explicit path '{path}' not found on disk, checking package data.")
            else:
                logger.warning(f"Pricing configuration file not found at '{p}'. PriceTable initialized empty.")
                return cls(prices={}, meta={})

        # 2. Check settings override if no explicit path was provided or resolved
        if raw_bytes is None:
            try:
                settings = get_settings()
                user_path = getattr(settings, "pricing_path", None)
                if user_path is not None:
                    p = Path(user_path).resolve()
                    if p.exists() and p.is_file():
                        raw_bytes = p.read_bytes()
                        source_desc = str(p)
            except Exception as exc:
                logger.debug(f"Could not load pricing_path from settings: {exc}")

        # 3. Default to package data (recipeboxer/data/pricing.toml) via importlib.resources
        if raw_bytes is None:
            try:
                traversable = importlib.resources.files("recipeboxer.data").joinpath("pricing.toml")
                raw_bytes = traversable.read_bytes()
                source_desc = "recipeboxer.data/pricing.toml"
            except Exception as exc:
                logger.error(f"Failed to load package data pricing.toml: {exc}. PriceTable initialized empty.")
                return cls(prices={}, meta={})

        try:
            data = tomllib.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            logger.error(f"Failed to parse pricing TOML ({source_desc}): {exc}. PriceTable initialized empty.")
            return cls(prices={}, meta={})

        meta = dict(data.get("meta", {}))
        prices: Dict[str, ModelPrice] = {}

        for key, val in data.items():
            if key == "meta" or not isinstance(val, dict):
                continue

            model_id = str(key).strip()
            # Normalize model key
            norm_key = model_id[len("models/"):] if model_id.startswith("models/") else model_id
            norm_key = norm_key.lower()

            try:
                mp = ModelPrice(
                    model=norm_key,
                    input_per_mtok=float(val.get("input_per_mtok", 0.0)),
                    output_per_mtok=float(val.get("output_per_mtok", 0.0)),
                    thinking_per_mtok=float(val.get("thinking_per_mtok", 0.0)),
                    context_tier_threshold_tokens=(
                        int(val["context_tier_threshold_tokens"])
                        if val.get("context_tier_threshold_tokens") is not None
                        else None
                    ),
                    input_per_mtok_high_tier=(
                        float(val["input_per_mtok_high_tier"])
                        if val.get("input_per_mtok_high_tier") is not None
                        else None
                    ),
                    output_per_mtok_high_tier=(
                        float(val["output_per_mtok_high_tier"])
                        if val.get("output_per_mtok_high_tier") is not None
                        else None
                    ),
                    verified_on=str(val.get("verified_on", meta.get("verified_on", "UNVERIFIED"))),
                    source_url=str(
                        val.get("source_url")
                        or val.get("reference_url")
                        or meta.get("source_url")
                        or meta.get("reference_url")
                        or ""
                    ),
                )
                prices[norm_key] = mp
            except Exception as exc:
                logger.warning(f"Failed to parse pricing entry for '{key}': {exc}")

        return cls(prices=prices, meta=meta)

    def _normalize_model_name(self, model: str) -> str:
        """Normalizes model string by trimming and stripping 'models/' prefix."""
        m = str(model).strip()
        if m.startswith("models/"):
            m = m[len("models/"):]
        return m.lower()

    def is_priced(self, model: str) -> bool:
        """Returns True if the specified model identifier is defined in the price table."""
        return self._normalize_model_name(model) in self.prices

    def cost_usd(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        thinking_tokens: int = 0,
    ) -> Optional[float]:
        """Calculates total call cost in USD.

        Args:
            model: Gemini model identifier.
            input_tokens: Prompt/input token count.
            output_tokens: Output/candidate token count.
            thinking_tokens: Reasoning/thinking token count.

        Returns:
            Calculated cost in USD as float, or None if the model is unpriced (with warning logged).
            Never silently returns 0 for unpriced models.
        """
        norm_model = self._normalize_model_name(model)

        if norm_model not in self.prices:
            logger.warning(
                f"Unpriced model '{model}' encountered in PriceTable; cannot compute USD cost. "
                f"Returning None (never silently defaulting to $0.00)."
            )
            return None

        mp = self.prices[norm_model]

        # Determine effective input and output rates based on context tier thresholds
        in_rate = mp.input_per_mtok
        out_rate = mp.output_per_mtok
        think_rate = mp.thinking_per_mtok

        if (
            mp.context_tier_threshold_tokens is not None
            and input_tokens > mp.context_tier_threshold_tokens
            and mp.input_per_mtok_high_tier is not None
        ):
            in_rate = mp.input_per_mtok_high_tier
            if mp.output_per_mtok_high_tier is not None:
                out_rate = mp.output_per_mtok_high_tier

        cost = (
            (max(0, input_tokens) / 1_000_000.0) * in_rate
            + (max(0, output_tokens) / 1_000_000.0) * out_rate
            + (max(0, thinking_tokens) / 1_000_000.0) * think_rate
        )
        return float(cost)


class CostLedger(UsageLedger):
    """Usage ledger implementation recording token consumption, latency, and USD cost (WP-B1)."""

    def __init__(
        self,
        prices: PriceTable,
        guard: Optional["BudgetGuard"] = None,
        flush_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.prices = prices
        self.guard = guard
        self.flush_path = Path(flush_path).resolve() if flush_path is not None else None
        if self.flush_path is not None:
            self.flush_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: List[UsageRecord] = []
        self.total_usd: float = 0.0
        self.calls: int = 0
        self.unpriced_calls: int = 0

    def record(
        self,
        stage: Union[LLMStage, str],
        model: str,
        usage: UsageTokens,
        latency_ms: float,
        *,
        request_fingerprint: Optional[str] = None,
        attempts: int = 1,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Records an LLM generation call, calculates its USD cost, and updates totals."""
        stage_str = stage.value if isinstance(stage, Enum) else str(stage)
        cost = self.prices.cost_usd(
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            thinking_tokens=usage.thinking_tokens,
        )

        if cost is not None:
            self.total_usd += cost
        else:
            self.unpriced_calls += 1

        self.calls += 1

        meta = dict(metadata or {})
        meta["cost_usd"] = cost

        record = UsageRecord(
            stage=stage_str,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
            request_fingerprint=request_fingerprint or "",
            attempts=attempts,
            metadata=meta,
        )
        # Store cost_usd attribute directly on record for convenient access
        object.__setattr__(record, "cost_usd", cost)
        self._records.append(record)

        # Incremental append-only JSONL flush (WP-SCALE Task 2)
        if self.flush_path is not None:
            try:
                call_entry = {
                    "type": "call",
                    "stage": stage_str,
                    "model": model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "thinking_tokens": usage.thinking_tokens,
                    "total_tokens": usage.total_tokens,
                    "latency_ms": latency_ms,
                    "attempts": attempts,
                    "timestamp": record.timestamp.isoformat(),
                }
                if cost is not None:
                    call_entry["cost_usd"] = float(cost)
                totals_entry = {
                    "type": "totals",
                    "calls": self.calls,
                    "input_tokens": self.total_input_tokens,
                    "output_tokens": self.total_output_tokens,
                    "thinking_tokens": self.total_thinking_tokens,
                    "total_tokens": self.total_tokens,
                    "latency_ms": self.total_latency_ms,
                    "total_usd": round(self.total_usd, 6),
                    "timestamp": record.timestamp.isoformat(),
                }
                with open(self.flush_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(call_entry) + "\n")
                    f.write(json.dumps(totals_entry) + "\n")
                    f.flush()
            except Exception as flush_err:
                logger.warning("Failed to incrementally flush ledger: %s", flush_err)

    @classmethod
    def load_from_jsonl(cls, path: Union[str, Path]) -> Dict[str, Any]:
        """Loads calls and totals dictionary from an incremental JSONL ledger file."""
        p = Path(path).resolve()
        if not p.exists():
            return {
                "calls": [],
                "totals": {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "thinking_tokens": 0,
                    "total_tokens": 0,
                    "latency_ms": 0.0,
                },
            }

        calls: List[Dict[str, Any]] = []
        latest_totals: Optional[Dict[str, Any]] = None

        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    item = json.loads(line_str)
                    if item.get("type") == "call":
                        call_entry = {
                            "stage": item.get("stage"),
                            "model": item.get("model"),
                            "input_tokens": item.get("input_tokens", 0),
                            "output_tokens": item.get("output_tokens", 0),
                            "thinking_tokens": item.get("thinking_tokens", 0),
                            "latency_ms": item.get("latency_ms", 0.0),
                            "attempts": item.get("attempts", 1),
                        }
                        if "cost_usd" in item:
                            call_entry["cost_usd"] = float(item["cost_usd"])
                        calls.append(call_entry)
                    elif item.get("type") == "totals":
                        latest_totals = item
                except Exception:
                    pass

        if latest_totals is None:
            total_in = sum(c.get("input_tokens", 0) for c in calls)
            total_out = sum(c.get("output_tokens", 0) for c in calls)
            total_think = sum(c.get("thinking_tokens", 0) for c in calls)
            total_lat = sum(c.get("latency_ms", 0.0) for c in calls)
            total_usd = sum(c.get("cost_usd", 0.0) for c in calls if "cost_usd" in c)
            latest_totals = {
                "calls": len(calls),
                "input_tokens": total_in,
                "output_tokens": total_out,
                "thinking_tokens": total_think,
                "total_tokens": total_in + total_out,
                "latency_ms": total_lat,
                "total_usd": round(total_usd, 6),
            }
        else:
            latest_totals = dict(latest_totals)
            latest_totals.pop("type", None)
            latest_totals.pop("timestamp", None)

        return {"calls": calls, "totals": latest_totals}

    def get_records(self, stage: Optional[Union[LLMStage, str]] = None) -> List[UsageRecord]:
        """Returns recorded entries, optionally filtered by stage."""
        if stage is None:
            return list(self._records)
        stage_str = stage.value if isinstance(stage, Enum) else str(stage)
        return [r for r in self._records if r.stage == stage_str]

    @property
    def total_input_tokens(self) -> int:
        return sum(r.usage.input_tokens for r in self._records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.usage.output_tokens for r in self._records)

    @property
    def total_thinking_tokens(self) -> int:
        return sum(r.usage.thinking_tokens for r in self._records)

    @property
    def total_tokens(self) -> int:
        return sum(r.usage.total_tokens for r in self._records)

    @property
    def total_latency_ms(self) -> float:
        return sum(r.latency_ms for r in self._records)

    def clear(self) -> None:
        self._records.clear()
        self.total_usd = 0.0
        self.calls = 0
        self.unpriced_calls = 0

    def snapshot(self) -> Dict[str, Any]:
        """Produces a breakdown snapshot of token and USD spend by stage and model."""
        by_stage: Dict[str, Dict[str, Any]] = {}
        by_model: Dict[str, Dict[str, Any]] = {}

        for r in self._records:
            # Stage aggregation
            s_entry = by_stage.setdefault(
                r.stage,
                {
                    "calls": 0,
                    "usd": 0.0,
                    "unpriced_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "thinking_tokens": 0,
                    "total_tokens": 0,
                    "latency_ms": 0.0,
                },
            )
            s_entry["calls"] += 1
            cost = getattr(r, "cost_usd", None) or r.metadata.get("cost_usd")
            if cost is not None:
                s_entry["usd"] += float(cost)
            else:
                s_entry["unpriced_calls"] += 1
            s_entry["input_tokens"] += r.usage.input_tokens
            s_entry["output_tokens"] += r.usage.output_tokens
            s_entry["thinking_tokens"] += r.usage.thinking_tokens
            s_entry["total_tokens"] += r.usage.total_tokens
            s_entry["latency_ms"] += r.latency_ms

            # Model aggregation
            m_entry = by_model.setdefault(
                r.model,
                {
                    "calls": 0,
                    "usd": 0.0,
                    "unpriced_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "thinking_tokens": 0,
                    "total_tokens": 0,
                    "latency_ms": 0.0,
                },
            )
            m_entry["calls"] += 1
            if cost is not None:
                m_entry["usd"] += float(cost)
            else:
                m_entry["unpriced_calls"] += 1
            m_entry["input_tokens"] += r.usage.input_tokens
            m_entry["output_tokens"] += r.usage.output_tokens
            m_entry["thinking_tokens"] += r.usage.thinking_tokens
            m_entry["total_tokens"] += r.usage.total_tokens
            m_entry["latency_ms"] += r.latency_ms

        return {
            "total_calls": self.calls,
            "calls": self.calls,
            "total_usd": round(self.total_usd, 6),
            "unpriced_calls": self.unpriced_calls,
            "total_tokens": self.total_tokens,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_thinking_tokens": self.total_thinking_tokens,
            "total_latency_ms": self.total_latency_ms,
            "by_stage": by_stage,
            "by_model": by_model,
        }

    def summary(self) -> Dict[str, Any]:
        """Compatibility alias for snapshot()."""
        return self.snapshot()


class BudgetGuard:
    """Enforces financial spend caps and call limits prior to initiating LLM requests (WP-B1)."""

    def __init__(
        self,
        cap_usd: Optional[float] = None,
        max_calls: Optional[int] = None,
    ) -> None:
        self.cap_usd = float(cap_usd) if cap_usd is not None else None
        self.max_calls = int(max_calls) if max_calls is not None else None

    def check_before_call(
        self,
        ledger: CostLedger,
        est_usd: float = 0.0,
        model: Optional[str] = None,
    ) -> None:
        """Inspects current ledger state and raises BudgetExceeded if limits are reached.

        Args:
            ledger: Active CostLedger recording spend.
            est_usd: Estimated cost of the impending call.
            model: Optional model identifier for the impending call.

        Raises:
            BudgetExceeded: If max_calls or cap_usd ceiling would be breached.
            BudgetUnpriced: If USD cap is active but pricing is unknown (unpriced model or empty table).
        """
        current_calls = getattr(ledger, "calls", 0)
        current_usd = getattr(ledger, "total_usd", 0.0)

        # 1. Enforce call count limit
        if self.max_calls is not None and current_calls >= self.max_calls:
            msg = f"Budget guard tripped: max_calls limit of {self.max_calls} reached (calls={current_calls})."
            logger.warning(msg)
            raise BudgetExceeded(
                msg,
                details={
                    "cap_usd": self.cap_usd,
                    "max_calls": self.max_calls,
                    "current_usd": current_usd,
                    "calls": current_calls,
                    "breach": "max_calls",
                },
            )

        # 2. Enforce USD spend cap & Fail closed if pricing is unknown
        if self.cap_usd is not None:
            # Check price table existence and contents
            prices = getattr(ledger, "prices", None)
            if prices is None or not getattr(prices, "prices", {}):
                msg = f"Budget guard cannot enforce cap_usd=${self.cap_usd:.4f}: price table is empty or uninitialized (BUDGET_UNPRICED)."
                logger.error(msg)
                raise BudgetUnpriced(
                    msg,
                    details={"cap_usd": self.cap_usd, "breach": "BUDGET_UNPRICED", "reason": "empty_price_table"},
                )

            unpriced_calls = getattr(ledger, "unpriced_calls", 0)
            if unpriced_calls > 0:
                msg = f"Budget guard cannot enforce cap_usd=${self.cap_usd:.4f}: {unpriced_calls} prior calls were unpriced (BUDGET_UNPRICED)."
                logger.error(msg)
                raise BudgetUnpriced(
                    msg,
                    details={"cap_usd": self.cap_usd, "unpriced_calls": unpriced_calls, "breach": "BUDGET_UNPRICED", "reason": "prior_unpriced_calls"},
                )

            if model is not None and not prices.is_priced(model):
                msg = f"Budget guard cannot enforce cap_usd=${self.cap_usd:.4f}: model '{model}' is not priced in pricing table (BUDGET_UNPRICED)."
                logger.error(msg)
                raise BudgetUnpriced(
                    msg,
                    details={"cap_usd": self.cap_usd, "model": model, "breach": "BUDGET_UNPRICED", "reason": "unpriced_model"},
                )

            # Tripped if already at/above cap or if estimated impending call exceeds cap
            projected = current_usd + max(0.0, est_usd)
            if projected > self.cap_usd or (self.cap_usd == 0.0 and current_calls > 0) or (current_usd >= self.cap_usd and self.cap_usd > 0.0):
                msg = (
                    f"Budget guard tripped: cap of ${self.cap_usd:.4f} USD exceeded "
                    f"(current spend=${current_usd:.4f}, estimated call=${est_usd:.4f})."
                )
                logger.warning(msg)
                raise BudgetExceeded(
                    msg,
                    details={
                        "cap_usd": self.cap_usd,
                        "max_calls": self.max_calls,
                        "current_usd": current_usd,
                        "est_usd": est_usd,
                        "projected_usd": projected,
                        "calls": current_calls,
                        "breach": "spend_cap_usd",
                    },
                )


# Base token assumptions per stage and profile for cost estimation (Plan §7.1)
DEFAULT_STAGE_ASSUMPTIONS: Dict[str, Dict[str, Dict[str, int]]] = {
    "economy": {
        "FINGERPRINT": {"input_tokens": 400, "output_tokens": 150, "thinking_tokens": 0},
        "SET_VERIFY": {"input_tokens": 800, "output_tokens": 100, "thinking_tokens": 0},
        "TRANSCRIBE": {"input_tokens": 1000, "output_tokens": 500, "thinking_tokens": 0},
        "STRUCTURE": {"input_tokens": 800, "output_tokens": 400, "thinking_tokens": 0},
        "GROUNDING": {"input_tokens": 1000, "output_tokens": 200, "thinking_tokens": 0},
        "REPAIR": {"input_tokens": 1200, "output_tokens": 400, "thinking_tokens": 0},
        "SELF_CONSISTENCY": {"input_tokens": 1000, "output_tokens": 400, "thinking_tokens": 0},
    },
    "balanced": {
        "FINGERPRINT": {"input_tokens": 600, "output_tokens": 150, "thinking_tokens": 0},
        "SET_VERIFY": {"input_tokens": 1000, "output_tokens": 150, "thinking_tokens": 0},
        "TRANSCRIBE": {"input_tokens": 1500, "output_tokens": 600, "thinking_tokens": 256},
        "STRUCTURE": {"input_tokens": 1000, "output_tokens": 500, "thinking_tokens": 0},
        "GROUNDING": {"input_tokens": 1200, "output_tokens": 200, "thinking_tokens": 0},
        "REPAIR": {"input_tokens": 1500, "output_tokens": 500, "thinking_tokens": 0},
        "SELF_CONSISTENCY": {"input_tokens": 1500, "output_tokens": 600, "thinking_tokens": 0},
    },
    "best": {
        "FINGERPRINT": {"input_tokens": 1000, "output_tokens": 200, "thinking_tokens": 0},
        "SET_VERIFY": {"input_tokens": 1500, "output_tokens": 200, "thinking_tokens": 0},
        "TRANSCRIBE": {"input_tokens": 2000, "output_tokens": 800, "thinking_tokens": 2048},
        "STRUCTURE": {"input_tokens": 1500, "output_tokens": 600, "thinking_tokens": 0},
        "GROUNDING": {"input_tokens": 1500, "output_tokens": 300, "thinking_tokens": 0},
        "REPAIR": {"input_tokens": 2000, "output_tokens": 600, "thinking_tokens": 0},
        "SELF_CONSISTENCY": {"input_tokens": 2000, "output_tokens": 800, "thinking_tokens": 2048},
    },
}


def estimate_run_cost(
    n_cards: int,
    profile: str = "balanced",
    prices: Optional[PriceTable] = None,
    calibration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Estimates pre-run dollar cost using REMEDIATION_PLAN.md §7.1 formula.

    Formula:
        calls ≈ N_cards × 1 (fingerprint) + N_cards × 1 (set-level verify)
              + M_recipes × (1 transcribe + 1 structure + ≤1 grounding + ≤2 repair + 0–1 self-consistency)
        cost  ≈ Σ calls × (input_tokens × price_in + output_tokens × price_out [+ thinking tokens])

    Args:
        n_cards: Number of card image scans to process.
        profile: Active quality profile ('economy', 'balanced', 'best').
        prices: PriceTable instance (defaults to loading config/pricing.toml).
        calibration: Optional dictionary overriding stage token assumptions (produced by WP-B0).

    Returns:
        Dictionary with keys:
            'low': Best-case cost in USD (or None if unpriced models exist).
            'expected': Expected realistic cost in USD (or None).
            'high': Worst-case cost in USD (or None).
            'assumptions': Documentation of stage models, tokens, recipe counts, and calibration used.
    """
    n = max(0, int(n_cards))
    prof = str(profile).lower()
    if prof not in DEFAULT_STAGE_ASSUMPTIONS:
        prof = "balanced"

    if prices is None:
        prices = PriceTable.load()

    profile_models = QUALITY_PROFILES.get(prof, QUALITY_PROFILES["balanced"])

    # Base stage assumptions for the selected profile
    stage_tokens: Dict[str, Dict[str, int]] = {
        stg: dict(toks) for stg, toks in DEFAULT_STAGE_ASSUMPTIONS[prof].items()
    }

    # Apply calibration overrides if provided
    if calibration and isinstance(calibration, dict):
        cal_stages = calibration.get("stages") or calibration.get("tokens_per_stage") or calibration
        if isinstance(cal_stages, dict):
            for stg, custom_toks in cal_stages.items():
                stg_upper = str(stg).upper()
                if stg_upper in stage_tokens and isinstance(custom_toks, dict):
                    stage_tokens[stg_upper].update({
                        k: int(v) for k, v in custom_toks.items() if k in ("input_tokens", "output_tokens", "thinking_tokens")
                    })

    # Estimate recipe counts M for low, expected, and high scenarios
    if n == 0:
        m_low, m_expected, m_high = 0, 0, 0
    else:
        m_low = max(1, int(math.ceil(n * 0.4)))
        m_expected = max(1, int(round(n * 0.6)))
        m_high = max(1, n)

    scenarios = {
        "low": {"m": m_low, "repair_mult": 0.0, "grounding_mult": 0.0, "sc_mult": 0.0},
        "expected": {
            "m": m_expected,
            "repair_mult": 0.2 if prof != "economy" else 0.0,
            "grounding_mult": 1.0 if prof != "economy" else 0.0,
            "sc_mult": 0.15 if prof == "balanced" else (0.5 if prof == "best" else 0.0),
        },
        "high": {
            "m": m_high,
            "repair_mult": 2.0 if prof != "economy" else 0.5,
            "grounding_mult": 1.0 if prof != "economy" else 0.0,
            "sc_mult": 0.5 if prof == "balanced" else (1.0 if prof == "best" else 0.0),
        },
    }

    results: Dict[str, Optional[float]] = {}
    unpriced_models: set[str] = set()

    for scen_name, scen in scenarios.items():
        m = scen["m"]
        total_cost = 0.0
        scen_unpriced = False

        # Stage call counts for this scenario
        stage_calls = {
            "FINGERPRINT": n,
            "SET_VERIFY": n,
            "TRANSCRIBE": m,
            "STRUCTURE": m,
            "GROUNDING": int(round(m * scen["grounding_mult"])),
            "REPAIR": int(round(m * scen["repair_mult"])),
            "SELF_CONSISTENCY": int(round(m * scen["sc_mult"])),
        }

        for stg, n_calls in stage_calls.items():
            if n_calls <= 0:
                continue

            model = profile_models.get(stg, profile_models.get("STRUCTURE", DEFAULT_FLASH_MODEL))
            tokens = stage_tokens.get(stg, {"input_tokens": 1000, "output_tokens": 500, "thinking_tokens": 0})

            cost_per_call = prices.cost_usd(
                model=model,
                input_tokens=tokens["input_tokens"],
                output_tokens=tokens["output_tokens"],
                thinking_tokens=tokens["thinking_tokens"],
            )

            if cost_per_call is None:
                unpriced_models.add(model)
                scen_unpriced = True
            else:
                total_cost += cost_per_call * n_calls

        if scen_unpriced:
            results[scen_name] = None
        else:
            results[scen_name] = round(total_cost, 4)

    assumptions = {
        "n_cards": n,
        "profile": prof,
        "recipe_count_scenarios": {"low": m_low, "expected": m_expected, "high": m_high},
        "stage_tokens": stage_tokens,
        "stage_models": {stg: profile_models.get(stg) for stg in stage_tokens},
        "formula": "calls ≈ N_cards*(fp + verify) + M_recipes*(transcribe + struct + grounding + repair + sc)",
        "unpriced_models": sorted(list(unpriced_models)),
        "calibration_applied": bool(calibration),
    }

    return {
        "low": results["low"],
        "expected": results["expected"],
        "high": results["high"],
        "assumptions": assumptions,
    }


__all__ = [
    "ModelPrice",
    "PriceTable",
    "CostLedger",
    "BudgetGuard",
    "BudgetExceeded",
    "BudgetUnpriced",
    "estimate_run_cost",
    "DEFAULT_STAGE_ASSUMPTIONS",
]
