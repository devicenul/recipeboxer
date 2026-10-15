"""Unit tests for feature toggle ablations matrix and scale stress scaling (WP-GE, G9, G13).

Enforces:
- Ablation matrix variants (8 configurations) adhering to FeatureToggles schema
- Homogeneous vs mixed deck_type breakout
- Offline matrix table generation with N/A markers (no offline accuracy assertions)
- Homogeneous stress deck generation with >= 70% dominant stationery stock
- Scale stress call-count scaling ~O(N*k) (calls bounded linearly by N, not N^2)
- ScriptedLLMClient call counting and latency recording without accuracy claims
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import pytest

from evals.ablations import (
    ABLATION_VARIANTS,
    build_ablation_matrix_table,
    run_ablations,
)
from evals.stress import (
    GroupingEngineDouble,
    TempDirImageProvider,
    create_scripted_verification_client,
    format_stress_report,
    generate_homogeneous_stress_deck,
    run_stress_sweep,
    run_stress_test,
)
from recipeboxer.ai.replay import ScriptedLLMClient


class TestAblationsMatrix:
    """Tests for correlation feature toggle ablations (G9)."""

    def test_ablation_variants_schema_and_count(self) -> None:
        assert len(ABLATION_VARIANTS) == 8
        variant_names = [v[0] for v in ABLATION_VARIANTS]
        expected_names = [
            "all_on",
            "-pixels",
            "-content",
            "-physical",
            "-capture",
            "-set_verify",
            "+embeddings",
            "+ocr",
        ]
        assert variant_names == expected_names

        expected_keys = {
            "pixels",
            "capture",
            "physical",
            "content",
            "set_verify",
            "embeddings",
            "ocr",
        }
        for name, toggles in ABLATION_VARIANTS:
            assert set(toggles.keys()) == expected_keys
            assert all(isinstance(v, bool) for v in toggles.values())

    def test_all_on_baseline_and_toggles(self) -> None:
        toggles_dict = dict(ABLATION_VARIANTS)

        # Baseline: pixels, capture, physical, content, set_verify on; embeddings, ocr off
        baseline = toggles_dict["all_on"]
        assert baseline["pixels"] is True
        assert baseline["content"] is True
        assert baseline["physical"] is True
        assert baseline["capture"] is True
        assert baseline["set_verify"] is True
        assert baseline["embeddings"] is False
        assert baseline["ocr"] is False

        # Check isolated off toggles
        assert toggles_dict["-pixels"]["pixels"] is False
        assert toggles_dict["-pixels"]["content"] is True

        assert toggles_dict["-content"]["content"] is False
        assert toggles_dict["-content"]["pixels"] is True

        assert toggles_dict["-physical"]["physical"] is False
        assert toggles_dict["-capture"]["capture"] is False
        assert toggles_dict["-set_verify"]["set_verify"] is False

        # Check additive toggles
        assert toggles_dict["+embeddings"]["embeddings"] is True
        assert toggles_dict["+ocr"]["ocr"] is True

    def test_offline_ablation_table_formatting_no_accuracy_assert(self) -> None:
        table_md = build_ablation_matrix_table(results=None, offline=True)
        assert "| Variant |" in table_md
        assert "| `all_on` |" in table_md
        assert "| `-pixels` |" in table_md
        assert "| `+ocr` |" in table_md
        assert "N/A" in table_md
        assert "Homogeneous B-Cubed F1" in table_md
        assert "Mixed B-Cubed F1" in table_md

    def test_ablation_table_with_mock_results(self) -> None:
        mock_results = {
            "all_on": {
                "status": "ok",
                "homogeneous": {"bcubed_f1": 0.88},
                "mixed": {"bcubed_f1": 0.92},
            },
            "-pixels": {
                "status": "ok",
                "homogeneous": {"bcubed_f1": 0.87},
                "mixed": {"bcubed_f1": 0.84},
            },
        }
        table_md = build_ablation_matrix_table(results=mock_results, offline=False)
        assert "0.880" in table_md
        assert "0.920" in table_md
        assert "0.870" in table_md
        assert "0.840" in table_md

    def test_run_ablations_offline_returns_cleanly(self) -> None:
        res = run_ablations(tier="T0", offline=True)
        assert res["mode"] == "offline"
        assert len(res["variants"]) == 8
        assert "markdown_table" in res


class TestScaleStressBenchmark:
    """Tests for scale & homogeneity stress benchmark (G13)."""

    def test_homogeneous_deck_stationery_consistency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            card_ids, fps = generate_homogeneous_stress_deck(
                target_dir=Path(tmp_dir),
                n_cards=100,
                seed=42,
                render_images=False,
            )
            assert len(card_ids) == 100
            assert len(fps) == 100

            # Verify that >= 70% share the dominant stock characteristics
            cream_count = sum(1 for fp in fps if fp.paper_color.tone_name == "cream")
            ruled_count = sum(1 for fp in fps if fp.ruling.ruling_type == "STANDARD_RULED")
            blue_count = sum(1 for fp in fps if fp.ink.ink_color == "blue")

            assert cream_count >= 70, f"Expected >= 70% cream cards, got {cream_count}%"
            assert ruled_count >= 70, f"Expected >= 70% ruled cards, got {ruled_count}%"
            assert blue_count >= 70, f"Expected >= 70% blue ink cards, got {blue_count}%"

    @pytest.mark.asyncio
    async def test_stress_call_count_scaling_production_engine(self) -> None:
        # Measures the PRODUCTION GroupingEngine (no double): LLM calls must grow
        # linearly with N. Small N keeps the suite fast; evals/stress.py CLI does 300+.
        sweep_ns = [10, 30]
        results = await asyncio.wait_for(
            run_stress_sweep(card_counts=sweep_ns, seed=123, k=5),
            timeout=300.0,
        )
        assert all(r["engine"] == "Production GroupingEngine" for r in results)

        assert len(results) == len(sweep_ns)
        for r, n in zip(results, sweep_ns):
            assert r["n_cards"] == n
            calls = r["llm_calls"]
            calls_per_n = r["calls_per_card"]

            # Linear scaling invariant: calls must be bounded by N (calls <= N)
            assert calls <= n
            assert calls_per_n <= 1.0
            assert r["is_linear_scaling"] is True
            # Zero accuracy claims invariant
            assert "NONE" in r["accuracy_claims"]

    @pytest.mark.asyncio
    async def test_stress_single_run_300_cards(self) -> None:
        res = await asyncio.wait_for(
            run_stress_test(n_cards=300, seed=777, k=5, use_double=True),
            timeout=10.0,
        )
        assert res["n_cards"] == 300
        assert res["llm_calls"] <= 300
        assert res["calls_per_card"] <= 1.0
        assert res["is_linear_scaling"] is True
        assert res["latency_seconds"] < 5.0  # Offline test double execution must be fast

    def test_format_stress_report(self) -> None:
        sample_results = [
            {
                "n_cards": 50,
                "k_screening": 5,
                "llm_calls": 50,
                "calls_per_card": 1.0,
                "latency_seconds": 0.002,
                "is_linear_scaling": True,
            },
            {
                "n_cards": 300,
                "k_screening": 5,
                "llm_calls": 300,
                "calls_per_card": 1.0,
                "latency_seconds": 0.008,
                "is_linear_scaling": True,
            },
        ]
        report_md = format_stress_report(sample_results)
        assert "| Cards (N) |" in report_md
        assert "| 50 | 5 | 50 |" in report_md
        assert "| 300 | 5 | 300 |" in report_md
        assert "PASS (~O(N*k))" in report_md
        assert "No accuracy claims are asserted offline" in report_md
