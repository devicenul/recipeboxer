"""Stage-keyed offline LLM test double for anticheat canaries (WP-RT3FIX, RT3-04).

Answers every pipeline stage (FINGERPRINT, SET_VERIFY incl. set_verify_v2, TRANSCRIBE, STRUCTURE, GROUNDING,
consistency second read, REPAIR) with a schema-valid response. Responses are chosen by **stage and per-stage
call order only** -- never by filename, card id or image content (INV-AC1/AC2 policy for test doubles) -- and
every request is recorded so canaries can scan the full prompt text.

This module is self-contained (imports only ``recipeboxer``) so it can also be copied next to a subprocess
script that runs under the ``sut_boot`` audit hook (which forbids importing ``tests.*``).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from recipeboxer.ai import LLMClient, LLMRequest, LLMResponse, UsageTokens
from recipeboxer.ai.client import LLMStage

# Card-face cycle for a deck: (role, looks_like_back, title, has_ingredients, has_directions)
_FACE_CYCLE = [
    ("SEQUENCE_START", False, "Plum Cake", True, False),
    ("SEQUENCE_CONTINUATION", True, None, False, True),
    ("STANDALONE", False, "Corn Fritters", True, True),
    ("SEQUENCE_START", False, "Barley Soup", True, False),
    ("SEQUENCE_CONTINUATION", False, None, True, True),
    ("SEQUENCE_CONTINUATION", True, None, False, True),
]


class StageScriptedClient(LLMClient):
    """Offline client keyed by (stage, per-stage call index)."""

    def __init__(self, sv_confidence: Optional[float] = 0.9, low_confidence_lines: bool = True) -> None:
        self.requests: List[LLMRequest] = []
        self.counts: Dict[str, int] = {}
        self.sv_confidence = sv_confidence
        self.low_confidence_lines = low_confidence_lines

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _stage_name(request: LLMRequest) -> str:
        st = request.stage
        return str(getattr(st, "value", st))

    @staticmethod
    def _n_images(request: LLMRequest) -> int:
        from recipeboxer.ai import ImagePart

        return sum(1 for p in request.parts if isinstance(p, ImagePart))

    def _fingerprint(self, idx: int) -> Dict[str, Any]:
        role, back, title, ing, dirs = _FACE_CYCLE[idx % len(_FACE_CYCLE)]
        n = idx // len(_FACE_CYCLE)
        t = f"{title} {n}" if title else None
        return {
            "visible_title": t,
            "first_lines": [f"{t}" if t else f"continued from previous card {n}"],
            "last_lines": ["(continued on next card)" if role == "SEQUENCE_START" else "Serve warm."],
            "printed_markers": [],
            "text_cut_off_start": role == "SEQUENCE_CONTINUATION",
            "text_cut_off_end": role == "SEQUENCE_START",
            "sections_detected": (["ingredients"] if ing else []) + (["directions"] if dirs else []),
            "script_descriptor": "blue ballpoint cursive",
            "orientation_deg": 0,
            "looks_like_back": back,
            "has_ingredients": ing,
            "has_directions": dirs,
            "role": role,
            "role_confidence": 0.6 + 0.05 * (idx % 5),
            "paper_tone": "WHITE",
            "paper_color_hex": "#F5F0E6",
            "paper_ruling": "BLANK" if idx % 2 else "LINED",
            "ink_color": "BLUE",
            "ink_type": "BALLPOINT",
            "stains_or_damage": [],
        }

    def _structure(self, idx: int, stage: str) -> Dict[str, Any]:
        conf_note = "faded" if self.low_confidence_lines else "clear"
        # The very first STRUCTURE answer is schema-invalid (numeric quantity) so the REPAIR stage runs once.
        bad = stage == LLMStage.STRUCTURE.value and idx == 0
        return {
            "title": f"Fixture Recipe {idx}",
            "title_is_inferred": False,
            "notes": [],
            "sections": [{
                "name": None,
                "ingredients": [
                    {"original_text": "2 cups flour", "quantity": 2 if bad else "2", "unit": "cup", "item": "flour", "source_face": 0,
                     "legibility": "CLEAR"},
                    {"original_text": "1 tsp salt", "quantity": "1", "unit": "tsp", "item": "salt", "source_face": 0,
                     "legibility": "UNCERTAIN" if conf_note == "faded" else "CLEAR"},
                ],
                "steps": [
                    {"original_text": "Mix well.", "text": "Mix well.", "source_face": 0, "legibility": "CLEAR"},
                    # not present in the transcript -> fuzzy grounding cannot support it -> GROUNDING stage runs
                    {"original_text": "Fold in three beaten eggs and chopped walnuts.",
                     "text": "Fold in three beaten eggs and chopped walnuts.", "source_face": 0, "legibility": "CLEAR"},
                    {"original_text": "Bake 30 minutes.", "text": "Bake 30 minutes.", "source_face": 0,
                     "legibility": "CLEAR"},
                ],
            }],
            "tags": [],
        }

    def _payload(self, stage: str, idx: int, request: LLMRequest) -> Any:
        if stage == LLMStage.FINGERPRINT.value:
            return self._fingerprint(idx)
        if stage == LLMStage.SET_VERIFY.value:
            return {
                "continuations": ["Candidate 1"],
                "rejected": [],
                "evidence_quotes": [],
                "confidence": self.sv_confidence,
                "reasoning": "Text continues mid-sentence.",
            }
        if stage == LLMStage.TRANSCRIBE.value:
            n = max(1, self._n_images(request))
            return {"faces": [
                {
                    "face_index": i,
                    "text": f"Fixture Recipe {idx}\n2 cups flour\n1 tsp salt\nMix well.\nBake 30 minutes.",
                    "illegible_spans": [{"text_guess": "salt", "reason": "faded"}] if self.low_confidence_lines else [],
                    "margin_notes": ["good for church supper"] if i == 0 else [],
                    "crossed_out": [],
                    "confidence": 0.55 if self.low_confidence_lines else 0.95,
                }
                for i in range(n)
            ]}
        if stage in (LLMStage.STRUCTURE.value, LLMStage.REPAIR.value):
            return self._structure(idx, stage)
        if stage == LLMStage.GROUNDING.value:
            return {"checks": []}
        if stage == LLMStage.JUDGE.value:
            return {"score": 3.0, "rationale": "ok"}
        return {}

    # ------------------------------------------------------------------ LLMClient
    async def generate(self, request: LLMRequest) -> LLMResponse[Any]:
        self.requests.append(request)
        stage = self._stage_name(request)
        idx = self.counts.get(stage, 0)
        self.counts[stage] = idx + 1
        payload = self._payload(stage, idx, request)
        parsed = None
        if request.response_model is not None and isinstance(payload, dict):
            try:
                parsed = request.response_model.model_validate(payload)
            except Exception:
                parsed = None
        return LLMResponse(
            parsed=parsed,
            raw_text=json.dumps(payload),
            usage=UsageTokens(input_tokens=10, output_tokens=10, total_tokens=20),
            model=request.model,
            latency_ms=1.0,
            attempts=1,
            finish_reason="STOP",
            request_fingerprint=request.compute_fingerprint(),
        )

    # ------------------------------------------------------------------ inspection
    def request_texts(self) -> List[tuple]:
        """[(stage, prompt_id_hint, full_text)] for every recorded request (system instruction + text parts)."""
        from recipeboxer.ai import TextPart

        out = []
        for r in self.requests:
            texts = [r.system_instruction or ""] + [p.text for p in r.parts if isinstance(p, TextPart)]
            meta = json.dumps(r.metadata, default=str) if getattr(r, "metadata", None) else ""
            out.append((self._stage_name(r), meta, "\n".join(texts)))
        return out
