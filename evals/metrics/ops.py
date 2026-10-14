"""Operational, cost, and latency metrics for evals.

Pure functions summarizing LLM execution ledgers and token spend.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def summarize_ledger(
    ledger: Dict[str, Any],
    pricing: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Summarizes an LLM call ledger with token counts, latency, retries, and optional USD cost.

    Args:
        ledger: Dictionary containing a `"calls"` list and optional `"failures"` list.
            Each call in `"calls"` can include:
            - `"stage"`: Name of the pipeline stage
            - `"model"`: Model identifier string
            - `"input_tokens"`: Count of input tokens
            - `"output_tokens"`: Count of output tokens
            - `"thinking_tokens"`: Count of reasoning/thinking tokens
            - `"latency_ms"`: Call duration in milliseconds
            - `"attempts"`: Number of attempts (retries = attempts - 1)
        pricing: Optional mapping of model names to rates per million tokens:
            `{model_id: {"input_per_mtok": float, "output_per_mtok": float, "thinking_per_mtok": float}}`

    Returns:
        Structured summary dictionary containing:
        - `total_input_tokens`: Total input tokens across all calls
        - `total_output_tokens`: Total output tokens across all calls
        - `total_thinking_tokens`: Total thinking tokens across all calls
        - `total_tokens`: Total tokens
        - `total_latency_ms`: Total latency in milliseconds
        - `total_calls`: Total number of calls
        - `total_retries`: Total retry attempts
        - `total_cost_usd`: Total cost in USD (None if pricing is None)
        - `by_stage`: Per-stage aggregation of tokens, latency, retries, failures, and cost
        - `by_model`: Per-model aggregation of tokens, calls, and cost
        - `failures_by_stage`: Failure counts indexed by stage
    """
    calls: List[Dict[str, Any]] = ledger.get("calls", []) if isinstance(ledger, dict) else []
    failures_list: List[Dict[str, Any]] = ledger.get("failures", []) if isinstance(ledger, dict) else []

    total_in = 0
    total_out = 0
    total_think = 0
    total_lat = 0.0
    total_retries = 0
    total_cost: Optional[float] = 0.0 if pricing is not None else None

    by_stage: Dict[str, Dict[str, Any]] = {}
    by_model: Dict[str, Dict[str, Any]] = {}
    failures_by_stage: Dict[str, int] = {}

    # Count top-level failures if present
    for fail in failures_list:
        stage = str(fail.get("stage", "unknown"))
        failures_by_stage[stage] = failures_by_stage.get(stage, 0) + 1

    for call in calls:
        stage = str(call.get("stage", "default"))
        model = str(call.get("model", "unknown"))

        in_tok = int(call.get("input_tokens", 0) or 0)
        out_tok = int(call.get("output_tokens", 0) or 0)
        think_tok = int(call.get("thinking_tokens", 0) or 0)
        lat = float(call.get("latency_ms", 0.0) or 0.0)

        attempts = int(call.get("attempts", 1) or 1)
        call_retries = max(0, attempts - 1) + int(call.get("retries", 0) or 0)

        # Cost calculation
        call_cost: Optional[float] = None
        if pricing is not None:
            model_rates = pricing.get(model, {})
            in_rate = float(model_rates.get("input_per_mtok", 0.0) or 0.0)
            out_rate = float(model_rates.get("output_per_mtok", 0.0) or 0.0)
            think_rate = float(model_rates.get("thinking_per_mtok", 0.0) or 0.0)

            cost = (
                (in_tok * in_rate)
                + (out_tok * out_rate)
                + (think_tok * think_rate)
            ) / 1_000_000.0
            call_cost = cost
            if total_cost is not None:
                total_cost += cost

        # Accumulate totals
        total_in += in_tok
        total_out += out_tok
        total_think += think_tok
        total_lat += lat
        total_retries += call_retries

        # Per-stage
        if stage not in by_stage:
            by_stage[stage] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "thinking_tokens": 0,
                "total_tokens": 0,
                "latency_ms": 0.0,
                "calls": 0,
                "retries": 0,
                "failures": failures_by_stage.get(stage, 0),
                "cost_usd": 0.0 if pricing is not None else None,
            }
        st = by_stage[stage]
        st["input_tokens"] += in_tok
        st["output_tokens"] += out_tok
        st["thinking_tokens"] += think_tok
        st["total_tokens"] += (in_tok + out_tok + think_tok)
        st["latency_ms"] = round(st["latency_ms"] + lat, 3)
        st["calls"] += 1
        st["retries"] += call_retries
        if call_cost is not None and st["cost_usd"] is not None:
            st["cost_usd"] = round(st["cost_usd"] + call_cost, 6)

        # Per-model
        if model not in by_model:
            by_model[model] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "thinking_tokens": 0,
                "total_tokens": 0,
                "calls": 0,
                "cost_usd": 0.0 if pricing is not None else None,
            }
        mo = by_model[model]
        mo["input_tokens"] += in_tok
        mo["output_tokens"] += out_tok
        mo["thinking_tokens"] += think_tok
        mo["total_tokens"] += (in_tok + out_tok + think_tok)
        mo["calls"] += 1
        if call_cost is not None and mo["cost_usd"] is not None:
            mo["cost_usd"] = round(mo["cost_usd"] + call_cost, 6)

    # Ensure stages with failures but no calls are recorded
    for stage, fail_count in failures_by_stage.items():
        if stage not in by_stage:
            by_stage[stage] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "thinking_tokens": 0,
                "total_tokens": 0,
                "latency_ms": 0.0,
                "calls": 0,
                "retries": 0,
                "failures": fail_count,
                "cost_usd": 0.0 if pricing is not None else None,
            }

    return {
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_thinking_tokens": total_think,
        "total_tokens": total_in + total_out + total_think,
        "total_latency_ms": round(total_lat, 3),
        "total_calls": len(calls),
        "total_retries": total_retries,
        "total_cost_usd": round(total_cost, 6) if total_cost is not None else None,
        "by_stage": by_stage,
        "by_model": by_model,
        "failures_by_stage": failures_by_stage,
    }
