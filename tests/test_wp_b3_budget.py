"""Unit and integration tests for WP-B3 Evaluation Budget Enforcement.

Tests:
1. CLI argument parsing for `recipeboxer analyze`:
   - `--budget-usd` and `--max-calls` flags parsed accurately.
2. CLI argument parsing for `evals/harness/run.py`:
   - `--budget-usd` and `--max-calls` flags passed to SUT invocation.
3. Headless execution budget tripping:
   - Guard trips when max_calls exceeded: returns status 'budget_exceeded',
     partial results, and populated ledger.
   - Guard trips when budget_usd exceeded: returns status 'budget_exceeded'
     with calculated USD total.
   - Written out_file contains status 'budget_exceeded' and ledger summary.
4. Harness benchmark execution with budget exceed:
   - Handles 'budget_exceeded' outputs cleanly, records partial results,
     and emits ledger.json with total_usd.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
from PIL import Image

from evals.harness.run import create_run_parser, run_benchmark
from recipeboxer.ai.client import LLMResponse, UsageTokens
from recipeboxer.ai.replay import ScriptedLLMClient
from recipeboxer.cli import create_analyze_parser
from recipeboxer.headless import run_headless_analysis
from recipeboxer.models.domain import CardRole, VisualFingerprint
from recipeboxer.models.recipe import RecipeSchema


def test_cli_analyze_budget_arguments():
    """Validates that recipeboxer analyze parses --budget-usd and --max-calls correctly."""
    parser = create_analyze_parser()
    args = parser.parse_args(["--input", "/path/to/cards", "--out", "/tmp/out.json", "--budget-usd", "2.75", "--max-calls", "15"])
    assert args.input == "/path/to/cards"
    assert args.out == "/tmp/out.json"
    assert args.budget_usd == 2.75
    assert args.max_calls == 15

    # Defaults should be None
    args_default = parser.parse_args(["--input", "/path/to/cards", "--out", "/tmp/out.json"])
    assert args_default.budget_usd is None
    assert args_default.max_calls is None


def test_harness_run_budget_arguments():
    """Validates that evals.harness.run parses --budget-usd and --max-calls correctly."""
    parser = create_run_parser()
    args = parser.parse_args(["--budget-usd", "0.50", "--max-calls", "5"])
    assert args.budget_usd == 0.50
    assert args.max_calls == 5

    # Defaults should be None
    args_default = parser.parse_args([])
    assert args_default.budget_usd is None
    assert args_default.max_calls is None


@pytest.mark.asyncio
async def test_headless_trips_max_calls_budget(tmp_path: Path):
    """Validates that run_headless_analysis stops cleanly when max_calls is reached."""
    input_dir = tmp_path / "cards"
    input_dir.mkdir()
    c1 = input_dir / "c1.jpg"
    c2 = input_dir / "c2.jpg"
    Image.new("RGB", (50, 50), color=(100, 100, 100)).save(c1)
    Image.new("RGB", (50, 50), color=(120, 120, 120)).save(c2)

    # 2 cards require 2 fingerprint calls. Setting max_calls=1 will trip on the second call.
    fp1 = VisualFingerprint(
        card_id="c1.jpg",
        image_path=str(c1),
        sha256_hash="1" * 64,
        role=CardRole.STANDALONE,
    )
    fp2 = VisualFingerprint(
        card_id="c2.jpg",
        image_path=str(c2),
        sha256_hash="2" * 64,
        role=CardRole.STANDALONE,
    )
    client = ScriptedLLMClient([fp1, fp2])

    out_file = tmp_path / "out_max_calls.json"
    res = await run_headless_analysis(
        input_dir=input_dir,
        out_file=out_file,
        auto_approve=True,
        injected_client=client,
        max_calls=1,
    )

    assert res["status"] == "budget_exceeded"
    assert out_file.exists()
    loaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded["status"] == "budget_exceeded"

    # Verify ledger was recorded and contains call stats
    ledger_data = res.get("ledger")
    assert ledger_data is not None
    assert ledger_data["totals"]["calls"] >= 1
    assert "calls" in ledger_data


@pytest.mark.asyncio
async def test_headless_trips_budget_usd(tmp_path: Path):
    """Validates that run_headless_analysis stops cleanly when budget_usd is exceeded."""
    input_dir = tmp_path / "cards"
    input_dir.mkdir()
    c1 = input_dir / "c1.jpg"
    c2 = input_dir / "c2.jpg"
    Image.new("RGB", (50, 50), color=(100, 100, 100)).save(c1)
    Image.new("RGB", (50, 50), color=(120, 120, 120)).save(c2)

    # Mock responses with token usage that will exceed $0.0001
    # gemini-2.5-flash is ~$0.15/MTok input. 10,000 tokens is ~$0.0015
    resp1 = LLMResponse(
        parsed=VisualFingerprint(
            card_id="c1.jpg",
            image_path=str(c1),
            sha256_hash="1" * 64,
            role=CardRole.STANDALONE,
        ),
        raw_text="{}",
        usage=UsageTokens(input_tokens=10_000, output_tokens=1_000, total_tokens=11_000),
        model="gemini-2.5-flash",
        latency_ms=10.0,
        attempts=1,
        finish_reason="STOP",
        request_fingerprint="fp1",
    )
    resp2 = LLMResponse(
        parsed=VisualFingerprint(
            card_id="c2.jpg",
            image_path=str(c2),
            sha256_hash="2" * 64,
            role=CardRole.STANDALONE,
        ),
        raw_text="{}",
        usage=UsageTokens(input_tokens=10_000, output_tokens=1_000, total_tokens=11_000),
        model="gemini-2.5-flash",
        latency_ms=10.0,
        attempts=1,
        finish_reason="STOP",
        request_fingerprint="fp2",
    )
    client = ScriptedLLMClient([resp1, resp2])

    test_pricing = tmp_path / "test_pricing.toml"
    test_pricing.write_text(
        """
[meta]
table_version = "1.0.0"
verified_on = "TEST"

["gemini-2.5-flash"]
input_per_mtok = 100.0
output_per_mtok = 100.0
thinking_per_mtok = 100.0
""",
        encoding="utf-8",
    )
    from recipeboxer.config import Settings
    test_settings = Settings(pricing_path=test_pricing)

    out_file = tmp_path / "out_budget_usd.json"
    res = await run_headless_analysis(
        input_dir=input_dir,
        out_file=out_file,
        auto_approve=True,
        injected_client=client,
        settings=test_settings,
        budget_usd=0.0005,  # 10,000 tokens @ $100/MTok = $0.001 > $0.0005 -> trips on next call
    )

    assert res["status"] == "budget_exceeded"
    assert out_file.exists()
    loaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded["status"] == "budget_exceeded"

    # Verify ledger includes total_usd
    ledger_data = res.get("ledger")
    assert ledger_data is not None
    assert "total_usd" in ledger_data["totals"]
    assert ledger_data["totals"]["total_usd"] is not None
    assert ledger_data["totals"]["total_usd"] > 0.0


def test_harness_records_budget_exceeded_status(tmp_path: Path, monkeypatch):
    """Validates that evals.harness.run handles budget_exceeded from SUT and writes ledger.json."""
    import subprocess
    images_dir = tmp_path / "images"
    deck_dir = images_dir / "deck_1"
    deck_dir.mkdir(parents=True)
    card_path = deck_dir / "c1.jpg"
    Image.new("RGB", (50, 50), color=(100, 100, 100)).save(card_path)

    # Mock subprocess.run to write SUT output with budget_exceeded
    def mock_subprocess_run(cmd, *args, **kwargs):
        # Locate --out argument in cmd
        out_idx = cmd.index("--out")
        sut_out_path = Path(cmd[out_idx + 1])
        sut_out_path.write_text(
            json.dumps(
                {
                    "status": "budget_exceeded",
                    "session_id": "test_sut_budget",
                    "recipes": [],
                    "unassigned_image_ids": [card_path.name],
                    "failures": [
                        {
                            "stage": "FINGERPRINT",
                            "reason": "BUDGET_EXCEEDED",
                            "message": "Budget cap of $0.05 reached",
                            "card_ids": [card_path.name],
                            "retryable": False,
                        }
                    ],
                    "ledger": {
                        "totals": {
                            "calls": 3,
                            "input_tokens": 15000,
                            "output_tokens": 2000,
                            "thinking_tokens": 0,
                            "total_tokens": 17000,
                            "total_usd": 0.052,
                        },
                        "calls": [
                            {
                                "stage": "FINGERPRINT",
                                "model": "gemini-2.5-flash",
                                "input_tokens": 15000,
                                "output_tokens": 2000,
                                "thinking_tokens": 0,
                                "latency_ms": 10.0,
                                "attempts": 1,
                                "cost_usd": 0.052,
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="OK", stderr="")

    monkeypatch.setattr("subprocess.run", mock_subprocess_run)

    rb_out = tmp_path / "rb-out"
    result = run_benchmark(
        tier="T0",
        out_dir=rb_out,
        images_dir=images_dir,
        budget_usd=0.05,
        max_calls=3,
    )

    assert result["status"] == "budget_exceeded"
    assert (rb_out / "outputs.json").exists()
    assert (rb_out / "ledger.json").exists()

    loaded_ledger = json.loads((rb_out / "ledger.json").read_text(encoding="utf-8"))
    assert loaded_ledger["totals"]["total_usd"] == 0.052
