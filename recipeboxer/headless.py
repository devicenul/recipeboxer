"""Production headless entry point for RecipeBoxer (WP-E3).

Provides the standalone headless execution engine driving the real production pipeline
(scanning, visual fingerprinting, correlation, and multimodal recipe extraction) with
HITL auto-approve.

Enforces:
- INV-AC6: Requires usable API key or test-only injected LLM client. Emits StageFailure
  NO_API_KEY and status 'no_live_model' when no key exists.
- Black-box evaluation readiness: Outputs standardized predictions JSON, failure records,
  and cost/usage ledger for downstream scoring.
- Oracle image sets support: Allows bypassing correlation grouping when given explicit image sets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from PIL import Image

from recipeboxer.agent.orchestrator import ConcurrentGroupingEngine, RecipeBoxerAgent
from recipeboxer.agent.session import PipelineSession
from recipeboxer.ai.client import (
    ImagePart,
    InMemoryUsageLedger,
    LLMClient,
    LLMRequest,
    LLMResponse,
    LLMStage,
    TextPart,
    UsageLedger,
    UsageTokens,
)
from recipeboxer.config import Settings, get_settings, has_usable_gemini_key, is_usable_gemini_key
from recipeboxer.correlation.engine import GroupingEngine
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier
from recipeboxer.extraction.extractor import RecipeExtractor
from recipeboxer.models.correlation import CorrelationProposal, CorrelationResult
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.vision.fingerprint import GeminiVisualExtractor
from recipeboxer.ai.cost import BudgetGuard, CostLedger, PriceTable
from recipeboxer.models.failures import (
    BudgetExceeded,
    ExtractionUnavailable,
    FailureReason,
    FingerprintUnavailable,
    PipelineStage,
    StageFailure,
    StageUnavailableError,
)
from recipeboxer.models.hitl import CardPairApproval, HITLDecision, HitlItemType
from recipeboxer.models.pipeline import FailedRecipe, PipelineState
from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger("recipeboxer.headless")

# Suppress debug/info logging from HTTP transports to prevent credential leakage
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)



class OracleGroupingOptimizer:
    """Correlation optimizer that skips model grouping and uses predefined image sets."""

    def __init__(self, image_sets: List[List[str]]) -> None:
        self.image_sets = image_sets

    async def optimize(
        self, fingerprints: List[VisualFingerprint]
    ) -> CorrelationResult:
        """Constructs CorrelationProposals directly from configured image sets."""
        fp_by_id = {fp.card_id: fp for fp in fingerprints}
        fp_by_stem = {Path(fp.card_id).stem: fp for fp in fingerprints}
        assigned_card_ids: set[str] = set()
        proposals: List[CorrelationProposal] = []

        for idx, group in enumerate(self.image_sets, 1):
            resolved_cids: List[str] = []
            for item in group:
                item_str = str(item).strip()
                item_stem = Path(item_str).stem
                if item_str in fp_by_id:
                    resolved_cids.append(fp_by_id[item_str].card_id)
                elif item_stem in fp_by_stem:
                    resolved_cids.append(fp_by_stem[item_stem].card_id)
                else:
                    resolved_cids.append(item_str)

            for cid in resolved_cids:
                assigned_card_ids.add(cid)

            is_standalone = len(resolved_cids) <= 1
            proposal = CorrelationProposal(
                proposal_id=f"prop_oracle_{idx:04d}",
                ordered_card_ids=resolved_cids,
                part_count=len(resolved_cids),
                heuristic_score=1.0,
                semantic_score=1.0,
                confidence=1.0,
                is_standalone=is_standalone,
                continuity_explanation="Oracle ground-truth grouping set.",
                recommended_action="APPROVE",
            )
            proposals.append(proposal)

        unmatched = [
            fp.card_id
            for fp in fingerprints
            if fp.card_id not in assigned_card_ids
            and Path(fp.card_id).stem not in assigned_card_ids
        ]

        return CorrelationResult(
            proposals=proposals,
            unmatched_card_ids=unmatched,
        )


class _LedgerRecordingClient(LLMClient):
    """Wraps an injected LLMClient to record usage into UsageLedger while delegating generation."""

    def __init__(self, inner: LLMClient, ledger: Any) -> None:
        self._inner = inner
        self._ledger = ledger

    @property
    def requests(self) -> Any:
        return getattr(self._inner, "requests", [])

    async def generate(self, request: LLMRequest) -> LLMResponse[Any]:
        # Enforce budget guard before call
        guard = getattr(self._ledger, "guard", None)
        if guard is not None:
            est_usd = float(request.metadata.get("est_usd", 0.0)) if request.metadata else 0.0
            guard.check_before_call(self._ledger, est_usd=est_usd)

        resp = await self._inner.generate(request)
        if self._ledger is not None and hasattr(self._ledger, "record"):
            self._ledger.record(
                stage=request.stage,
                model=resp.model or request.model,
                usage=resp.usage,
                latency_ms=resp.latency_ms,
                attempts=resp.attempts,
            )
        return resp



class HeadlessClientAdapter:
    """Compatibility wiring delegating to production extractors with injected LLMClient (FIND-03)."""

    def __init__(
        self,
        client: LLMClient,
        ledger: UsageLedger,
        model_name: str = "scripted-test-model",
        settings: Optional[Settings] = None,
    ) -> None:
        self.client = client
        self.ledger = ledger
        self.model_name = model_name
        self.settings = settings or get_settings()
        self._recording_client = _LedgerRecordingClient(client, ledger)
        self.visual_extractor = GeminiVisualExtractor(
            llm_client=self._recording_client,
            model_name=self.settings.get_stage_model(LLMStage.FINGERPRINT),
        )
        self.recipe_extractor = RecipeExtractor(
            llm_client=self._recording_client,
            model_name=self.settings.get_stage_model(LLMStage.STRUCTURE),
            settings=self.settings,
        )

    async def extract_fingerprint(
        self, card_input: Any, sha_hash: Optional[str] = None
    ) -> VisualFingerprint:
        return await self.visual_extractor.extract(card_input, sha256_hash=sha_hash)

    extract = extract_fingerprint
    fingerprint_card = extract_fingerprint

    async def extract_cards(self, cards: Sequence[Any]) -> RecipeSchema:
        return await self.recipe_extractor.extract_cards(list(cards))

    extract_recipe = extract_cards


def parse_image_sets_file(
    file_or_data: Union[str, Path, List[List[str]], Dict[str, Any]],
) -> List[List[str]]:
    """Loads and normalizes image sets representation from file or data structures."""
    if isinstance(file_or_data, list):
        return [list(item) for item in file_or_data]

    if isinstance(file_or_data, dict):
        if "sets" in file_or_data:
            return [list(s) for s in file_or_data["sets"]]
        if "decks" in file_or_data:
            combined = []
            for deck in file_or_data["decks"]:
                for s in deck.get("sets", []):
                    combined.append(list(s))
            return combined

    p = Path(file_or_data).resolve()
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"Image sets file not found: {p}")

    content = p.read_text(encoding="utf-8")
    data = json.loads(content)

    if isinstance(data, list):
        return [list(item) for item in data]
    if isinstance(data, dict):
        if "sets" in data:
            return [list(s) for s in data["sets"]]
        if "decks" in data:
            combined = []
            for deck in data["decks"]:
                for s in deck.get("sets", []):
                    combined.append(list(s))
            return combined

    raise ValueError(f"Unrecognized image-sets format in {p}")


async def run_headless_analysis(
    input_dir: Union[str, Path],
    out_file: Optional[Union[str, Path]] = None,
    auto_approve: bool = True,
    profile: str = "balanced",
    image_sets: Optional[Union[str, Path, List[List[str]], Dict[str, Any]]] = None,
    *,
    injected_client: Optional[Any] = None,
    settings: Optional[Settings] = None,
    budget_usd: Optional[float] = None,
    max_calls: Optional[int] = None,
) -> Dict[str, Any]:
    """Executes the full RecipeBoxer digitization pipeline headlessly.

    Args:
        input_dir: Path to directory containing card scans.
        out_file: Optional path where output JSON is written.
        auto_approve: If True, auto-approves HITL pairing proposals.
        profile: Model profile tier ('economy', 'balanced', 'best').
        image_sets: Optional path or data structure of image sets for oracle grouping.
        injected_client: Test-only LLM client instance (e.g. ScriptedLLMClient).
        settings: Optional Settings instance override.
        budget_usd: Optional spend cap limit in USD (WP-B1, WP-B3).
        max_calls: Optional maximum LLM calls limit (WP-B1, WP-B3).

    Returns:
        Structured prediction output dictionary with recipes, failures, ledger, and status.
    """
    input_path = Path(input_dir).resolve()
    if not input_path.exists() or not input_path.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {input_path}")

    eff_settings = settings or get_settings()

    # Load PriceTable and configure CostLedger with BudgetGuard
    price_path = getattr(eff_settings, "pricing_path", None)
    price_table = PriceTable.load(price_path)

    eff_cap_usd = budget_usd if budget_usd is not None else getattr(eff_settings, "spend_cap_usd", None)
    eff_max_calls = max_calls if max_calls is not None else getattr(eff_settings, "max_calls", None)

    guard = None
    if eff_cap_usd is not None or eff_max_calls is not None:
        guard = BudgetGuard(cap_usd=eff_cap_usd, max_calls=eff_max_calls)

    flush_path = (Path(out_file).resolve().parent / "ledger.jsonl") if out_file else None
    ledger = CostLedger(prices=price_table, guard=guard, flush_path=flush_path)

    # Determine key availability (INV-AC6)
    has_key = has_usable_gemini_key(eff_settings)
    has_client = injected_client is not None

    # Resolve image sets if provided
    parsed_image_sets: Optional[List[List[str]]] = None
    if image_sets is not None:
        parsed_image_sets = parse_image_sets_file(image_sets)

    # Build agent and session
    session_id = "headless_session"
    session_config = {
        "scans_dir": str(input_path),
        "auto_approve": auto_approve,
        "profile": profile,
        "spend_cap_usd": eff_cap_usd,
        "max_calls": eff_max_calls,
    }

    from recipeboxer.storage.cache import FingerprintCache

    # Use isolated in-memory cache for headless run to guarantee zero cross-run contamination
    isolated_cache = FingerprintCache(":memory:")

    session = PipelineSession(
        session_id=session_id,
        config=session_config,
        settings=eff_settings,
        cache=isolated_cache,
    )

    # Ensure auto_approve is preserved on session.config even if PipelineStartRequest dropped extra fields
    if hasattr(session, "config") and session.config is not None:
        try:
            object.__setattr__(session.config, "auto_approve", auto_approve)
        except Exception:
            pass
        if isinstance(session.config, dict):
            session.config["auto_approve"] = auto_approve

    agent_kwargs: Dict[str, Any] = {
        "settings": eff_settings,
        "cache": isolated_cache,
    }

    if has_client:
        if isinstance(injected_client, LLMClient):
            recording_client = _LedgerRecordingClient(injected_client, ledger)
            vis_extractor = GeminiVisualExtractor(
                llm_client=recording_client,
                model_name=eff_settings.get_stage_model(LLMStage.FINGERPRINT),
            )
            sem_verifier = GeminiSemanticVerifier(
                llm_client=recording_client,
                model_name=eff_settings.get_stage_model(LLMStage.SET_VERIFY),
            )
            rec_extractor = RecipeExtractor(
                llm_client=recording_client,
                model_name=eff_settings.get_stage_model(LLMStage.STRUCTURE),
                settings=eff_settings,
            )
            pairing_opt = PairingOptimizer(verifier=sem_verifier)
            grouping_engine = ConcurrentGroupingEngine()
            session.mock_client = recording_client
            session.grouping_engine = grouping_engine
            session.extractor = vis_extractor
            session.recipe_extractor_fn = rec_extractor.extract
            agent_kwargs["visual_extractor"] = vis_extractor
            agent_kwargs["semantic_verifier"] = sem_verifier
            agent_kwargs["pairing_optimizer"] = pairing_opt
            agent_kwargs["grouping_engine"] = grouping_engine
            agent_kwargs["recipe_extractor"] = rec_extractor
        else:
            session.mock_client = injected_client
            session.extractor = injected_client
            agent_kwargs["visual_extractor"] = injected_client
            agent_kwargs["recipe_extractor"] = injected_client
    elif has_key:
        from recipeboxer.ai.client import GenAIClient
        prod_client = GenAIClient(ledger=ledger, guard=guard, settings=eff_settings)
        vis_extractor = GeminiVisualExtractor(
            llm_client=prod_client,
            model_name=eff_settings.get_stage_model(LLMStage.FINGERPRINT),
        )
        sem_verifier = GeminiSemanticVerifier(
            llm_client=prod_client,
            model_name=eff_settings.get_stage_model(LLMStage.SET_VERIFY),
        )
        rec_extractor = RecipeExtractor(
            llm_client=prod_client,
            model_name=eff_settings.get_stage_model(LLMStage.STRUCTURE),
            settings=eff_settings,
        )
        pairing_opt = PairingOptimizer(verifier=sem_verifier)
        grouping_engine = ConcurrentGroupingEngine()
        session.extractor = vis_extractor
        session.grouping_engine = grouping_engine
        session.recipe_extractor_fn = rec_extractor.extract
        agent_kwargs["visual_extractor"] = vis_extractor
        agent_kwargs["semantic_verifier"] = sem_verifier
        agent_kwargs["pairing_optimizer"] = pairing_opt
        agent_kwargs["grouping_engine"] = grouping_engine
        agent_kwargs["recipe_extractor"] = rec_extractor

    # Configure oracle grouping if image sets provided AND we have a usable key/client
    # (If no key/client, do not bypass INV-AC6 fail-fast)
    if parsed_image_sets is not None and (has_key or has_client):
        oracle_opt = OracleGroupingOptimizer(parsed_image_sets)
        session.optimizer = oracle_opt
        agent_kwargs["pairing_optimizer"] = oracle_opt

    def _flush_partial_output(_extracted_item: Optional[Any] = None) -> None:
        if not out_file:
            return
        try:
            p_recipes_out: List[Dict[str, Any]] = []
            p_assigned_cids: set[str] = set()
            for p_idx, r in enumerate(session.recipes, 1):
                if (
                    isinstance(r, FailedRecipe)
                    or getattr(r, "status", None) == "FAILED"
                    or (hasattr(r, "title") and str(r.title).startswith("Extraction Failed"))
                ):
                    continue
                cids = list(getattr(r, "card_ids", []) or [])
                for cid in cids:
                    p_assigned_cids.add(cid)
                r_dump = r.model_dump(mode="json") if hasattr(r, "model_dump") else (r if isinstance(r, dict) else {})
                if isinstance(r_dump, dict):
                    r_dump["created_at"] = None
                    r_dump["updated_at"] = None
                    if "image_urls" in r_dump:
                        r_dump["image_urls"] = [f"/api/cards/{cid}/image" for cid in cids]
                pred_id = getattr(r, "recipe_id", None) or f"p{p_idx}"
                p_recipes_out.append({
                    "pred_id": pred_id,
                    "image_ids": cids,
                    "recipe": r_dump,
                    "confidence": float(r.confidence) if getattr(r, "confidence", None) is not None else None,
                    "flags": [],
                })
            p_unassigned = [c.card_id for c in session.cards if c.card_id not in p_assigned_cids]

            p_failures_out: List[Dict[str, Any]] = []
            for f in session.failures:
                stage_str = f.stage.value if hasattr(f.stage, "value") else str(f.stage)
                reason_str = f.reason.value if hasattr(f.reason, "value") else str(f.reason)
                p_failures_out.append({
                    "stage": stage_str,
                    "reason": reason_str,
                    "image_ids": list(f.card_ids or []),
                    "message": f.message or "",
                })

            p_res = {
                "status": "partial" if p_recipes_out else ("failed" if session.failures else "ok"),
                "recipes": p_recipes_out,
                "unassigned_image_ids": p_unassigned,
                "failures": p_failures_out,
                "ledger": {
                    "calls": [
                        {
                            "stage": rc.stage,
                            "model": rc.model,
                            "input_tokens": rc.usage.input_tokens,
                            "output_tokens": rc.usage.output_tokens,
                            "thinking_tokens": rc.usage.thinking_tokens,
                            "latency_ms": rc.latency_ms,
                            "attempts": rc.attempts,
                            **({"cost_usd": float(getattr(rc, "cost_usd", 0.0))} if getattr(rc, "cost_usd", None) is not None else {}),
                        }
                        for rc in ledger.get_records()
                    ],
                    "totals": {
                        "calls": len(ledger.get_records()),
                        "input_tokens": ledger.total_input_tokens,
                        "output_tokens": ledger.total_output_tokens,
                        "thinking_tokens": ledger.total_thinking_tokens,
                        "total_tokens": ledger.total_tokens,
                        "latency_ms": ledger.total_latency_ms,
                        "total_usd": round(getattr(ledger, "total_usd", 0.0), 6),
                    },
                },
            }
            out_p = Path(out_file).resolve()
            out_p.parent.mkdir(parents=True, exist_ok=True)
            tmp_write = out_p.with_suffix(out_p.suffix + ".tmp")
            tmp_write.write_text(json.dumps(p_res, indent=2, default=str), encoding="utf-8")
            tmp_write.replace(out_p)
        except Exception as p_exc:
            logger.debug(f"Failed to flush partial output: {p_exc}")

    session.on_recipe_extracted = _flush_partial_output

    agent = RecipeBoxerAgent(**agent_kwargs)

    # Background task to auto-approve in the event pause_for_hitl is ever triggered
    async def _auto_hitl_bridge() -> None:
        terminal_states = (
            PipelineState.COMPLETED,
            PipelineState.FAILED,
            PipelineState.CANCELLED,
        )
        try:
            while session.state not in terminal_states and not session.is_cancelled:
                if session.state == PipelineState.HITL_PENDING:
                    # If paused due to budget cap, stop cleanly in headless mode
                    has_budget = any(
                        getattr(it, "item_type", None) == HitlItemType.BUDGET or getattr(it, "kind", "") == "budget"
                        for it in session.hitl_queue
                    )
                    if has_budget:
                        session.cancel()
                        break
                    elif auto_approve:
                        approvals = [
                            CardPairApproval(
                                proposal_id=p.proposal_id,
                                card_ids=p.ordered_card_ids,
                                decision=HITLDecision.APPROVE,
                            )
                            for p in session.proposals
                        ]
                        try:
                            session.resume_with_approvals(approvals)
                        except Exception:
                            session.approvals = approvals
                            session.approval_event.set()
                        _flush_partial_output()
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            pass
        except Exception as bridge_exc:
            logger.warning(f"[_auto_hitl_bridge] Exception in bridge: {bridge_exc}")

    bridge_task = asyncio.create_task(_auto_hitl_bridge())
    budget_exceeded_caught = False
    try:
        await agent.run_pipeline(session)
    except BudgetExceeded as be:
        budget_exceeded_caught = True
        fail = be.to_failure()
        if fail not in session.failures:
            session.add_failure(fail)
    finally:
        bridge_task.cancel()

    # Determine overall status
    # Assemble failures output first
    failures_out: List[Dict[str, Any]] = []
    for f in session.failures:
        stage_str = f.stage.value if hasattr(f.stage, "value") else str(f.stage)
        reason_str = f.reason.value if hasattr(f.reason, "value") else str(f.reason)
        failures_out.append({
            "stage": stage_str,
            "reason": reason_str,
            "image_ids": list(f.card_ids or []),
            "message": f.message or "",
        })

    # Separate valid recipes from FailedRecipe stubs (FIND-03)
    valid_recipes: List[Any] = []
    for r in session.recipes:
        is_failed = (
            isinstance(r, FailedRecipe)
            or getattr(r, "status", None) == "FAILED"
            or (hasattr(r, "title") and str(r.title).startswith("Extraction Failed"))
        )
        if is_failed:
            fail_obj = getattr(r, "failure", None)
            cids = list(getattr(r, "card_ids", []) or [])
            if fail_obj:
                stage_str = fail_obj.stage.value if hasattr(fail_obj.stage, "value") else str(fail_obj.stage)
                reason_str = fail_obj.reason.value if hasattr(fail_obj.reason, "value") else str(fail_obj.reason)
                failures_out.append({
                    "stage": stage_str,
                    "reason": reason_str,
                    "image_ids": cids,
                    "message": fail_obj.message or getattr(r, "description", ""),
                })
            else:
                failures_out.append({
                    "stage": "EXTRACTION",
                    "reason": "EXTRACTION_FAILED",
                    "image_ids": cids,
                    "message": getattr(r, "description", "") or "Recipe extraction failed",
                })
        else:
            valid_recipes.append(r)

    # Deduplicate failures per (stage, image set) (WP-LIVE1 Task 3)
    deduped_failures: List[Dict[str, Any]] = []
    seen_failure_keys: set[tuple[str, tuple[str, ...]]] = set()
    for item in failures_out:
        key = (item["stage"], tuple(sorted(str(cid) for cid in item.get("image_ids", []))))
        if key not in seen_failure_keys:
            seen_failure_keys.add(key)
            deduped_failures.append(item)
    failures_out = deduped_failures

    # Determine overall status
    is_no_live_model = False
    for f in session.failures:
        if f.reason == FailureReason.NO_API_KEY:
            is_no_live_model = True
            break

    if not has_key and not has_client:
        is_no_live_model = True

    is_budget_exceeded = budget_exceeded_caught or any(
        f.reason == FailureReason.BUDGET_EXCEEDED for f in session.failures
    ) or any(
        getattr(it, "item_type", None) == HitlItemType.BUDGET or getattr(it, "kind", "") == "budget"
        for it in session.hitl_queue
    )

    if is_budget_exceeded:
        status = "budget_exceeded"
    elif is_no_live_model:
        status = "no_live_model"
    elif session.state == PipelineState.FAILED or (len(valid_recipes) == 0 and len(session.cards) > 0):
        status = "failed"
    elif len(failures_out) > 0:
        status = "partial"
    else:
        status = "ok"

    # Assemble recipes output from valid extracted recipes only
    recipes_out: List[Dict[str, Any]] = []
    assigned_card_ids: set[str] = set()

    for idx, r in enumerate(valid_recipes, 1):
        cids = list(getattr(r, "card_ids", []) or [])
        for cid in cids:
            assigned_card_ids.add(cid)

        r_dump = (
            r.model_dump(mode="json")
            if hasattr(r, "model_dump")
            else (r if isinstance(r, dict) else {})
        )
        if isinstance(r_dump, dict):
            r_dump["created_at"] = None
            r_dump["updated_at"] = None
            if "image_urls" in r_dump:
                r_dump["image_urls"] = [f"/api/cards/{cid}/image" for cid in cids]

        # Scored recipe confidence (INV-AC4, FIND-05; WP-UP3 semantics).
        # recipes[].confidence := P(this predicted recipe is grouped correctly AND extracted correctly),
        # i.e. the scorer's correctness (content_sim >= 0.5 AND ingredient_f1 >= 0.7 on the matched gold).
        # Grouping factor: confidence of the proposal whose card set equals this recipe's cards (fallback:
        # min over overlapping proposals; then the recipe's own evidence-based confidence if any).
        # It is combined with extraction-quality signals by the fitted 'recipe' model
        # (recipeboxer/correlation/linkmodel.py::recipe_confidence). None when there is no evidence.
        rec_conf = None
        grp_conf = None
        cid_set = set(cids)
        exact_props = [
            p for p in session.proposals
            if set(getattr(p, "ordered_card_ids", []) or []) == cid_set and getattr(p, "confidence", None) is not None
        ]
        if exact_props:
            grp_conf = float(exact_props[0].confidence)
        else:
            matching_proposals = [
                p for p in session.proposals
                if any(cid in cids for cid in getattr(p, "ordered_card_ids", []))
                and getattr(p, "confidence", None) is not None
            ]
            if matching_proposals:
                grp_conf = min(float(p.confidence) for p in matching_proposals)
            elif getattr(r, "confidence", None) is not None:
                grp_conf = float(r.confidence)
        if grp_conf is not None:
            try:
                from recipeboxer.correlation.linkmodel import recipe_confidence
                rec_conf = recipe_confidence(grp_conf, r, len(cids))
            except Exception as conf_exc:  # never fabricate: fall back to the grouping factor
                logger.warning(f"recipe_confidence failed: {conf_exc}")
                rec_conf = grp_conf

        pred_id = getattr(r, "recipe_id", None) or f"p{idx}"
        recipes_out.append({
            "pred_id": pred_id,
            "image_ids": cids,
            "recipe": r_dump,
            "confidence": rec_conf,
            "flags": [],
        })

    # Assemble unassigned card IDs
    all_card_ids = [c.card_id for c in session.cards]
    unassigned_image_ids = [
        cid for cid in all_card_ids if cid not in assigned_card_ids
    ]

    # Assemble ledger output
    records = ledger.get_records()
    ledger_calls = []
    for r in records:
        call_entry = {
            "stage": r.stage,
            "model": r.model,
            "input_tokens": r.usage.input_tokens,
            "output_tokens": r.usage.output_tokens,
            "thinking_tokens": r.usage.thinking_tokens,
            "latency_ms": r.latency_ms,
            "attempts": r.attempts,
        }
        cost = getattr(r, "cost_usd", None) or r.metadata.get("cost_usd")
        if cost is not None:
            call_entry["cost_usd"] = float(cost)
        ledger_calls.append(call_entry)

    totals: Dict[str, Any] = {
        "calls": len(records),
        "input_tokens": ledger.total_input_tokens,
        "output_tokens": ledger.total_output_tokens,
        "thinking_tokens": ledger.total_thinking_tokens,
        "total_tokens": ledger.total_tokens,
        "latency_ms": ledger.total_latency_ms,
    }
    if hasattr(ledger, "total_usd"):
        totals["total_usd"] = round(ledger.total_usd, 6)

    ledger_out = {
        "calls": ledger_calls,
        "totals": totals,
    }


    result = {
        "status": status,
        "recipes": recipes_out,
        "unassigned_image_ids": unassigned_image_ids,
        "failures": failures_out,
        "ledger": ledger_out,
    }

    if out_file:
        out_p = Path(out_file).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        if os.environ.get("RB_DEBUG_GROUPING") == "1":
            dbg_src = Path("grouping_debug.json")
            if dbg_src.exists() and dbg_src.resolve() != (out_p.parent / "grouping_debug.json").resolve():
                try:
                    (out_p.parent / "grouping_debug.json").write_text(dbg_src.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass

    return result
