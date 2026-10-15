"""WP-UP3: single grouping code path.

Red-team finding (UP3): production (headless/eval) used ``orchestrator.ConcurrentGroupingEngine``, which
overrode ``group()`` with a stale copy of the engine (k=5, no verified-edge fix, raw-LLM chain
confidence). These tests pin that the orchestrator class is a thin alias with NO ``group`` override and
that the headless pipeline invokes ``GroupingEngine.group`` itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from recipeboxer.agent.orchestrator import ConcurrentGroupingEngine
from recipeboxer.correlation.engine import GroupingEngine

SAMPLE_CARDS_DIR = Path(__file__).parent / "fixtures" / "sample_cards"


def test_concurrent_engine_is_thin_alias_without_group_override() -> None:
    assert issubclass(ConcurrentGroupingEngine, GroupingEngine)
    assert "group" not in ConcurrentGroupingEngine.__dict__
    assert ConcurrentGroupingEngine.group is GroupingEngine.group


@pytest.mark.asyncio
async def test_headless_path_invokes_grouping_engine_group(tmp_path: Path, monkeypatch) -> None:
    from recipeboxer.ai.client import ScriptedLLMClient
    from recipeboxer.headless import run_headless_analysis
    from recipeboxer.models.domain import CardRole, VisualFingerprint
    from recipeboxer.models.recipe import RecipeSchema

    cards = sorted(SAMPLE_CARDS_DIR.glob("*.jpg"))[:2]
    assert len(cards) == 2
    input_dir = tmp_path / "scans"
    input_dir.mkdir()
    for c in cards:
        (input_dir / c.name).write_bytes(c.read_bytes())

    calls = []
    real_group = GroupingEngine.group

    async def spy_group(self, fps, images, **kwargs):
        calls.append((type(self).__name__, sorted(fp.card_id for fp in fps), kwargs.get("llm_client") is not None))
        return await real_group(self, fps, images, **kwargs)

    monkeypatch.setattr(GroupingEngine, "group", spy_group)

    client = ScriptedLLMClient([
        VisualFingerprint(card_id=cards[0].name, image_path=str(cards[0]), sha256_hash="0" * 64, role=CardRole.STANDALONE),
        VisualFingerprint(card_id=cards[1].name, image_path=str(cards[1]), sha256_hash="1" * 64, role=CardRole.STANDALONE),
        RecipeSchema(recipe_id="r1", title="One", ingredients=["a"], directions=["b"]),
        RecipeSchema(recipe_id="r2", title="Two", ingredients=["c"], directions=["d"]),
    ])
    await run_headless_analysis(input_dir=input_dir, out_file=tmp_path / "out.json", auto_approve=True,
                                injected_client=client)

    assert len(calls) == 1, calls
    eng_name, ids, had_client = calls[0]
    assert eng_name == "ConcurrentGroupingEngine"  # the orchestrator alias -> same GroupingEngine.group
    assert ids == sorted(c.name for c in cards)
    assert had_client
