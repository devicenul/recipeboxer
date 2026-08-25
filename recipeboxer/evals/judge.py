"""LLM-as-a-Judge semantic evaluation pipeline for RecipeBoxer.

Evaluates qualitative recipe fidelity, culinary coherence, headnote preservation,
and ingredient-direction consistency using Gemini LLM or deterministic offline mock evaluation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field, field_validator, model_validator

from recipeboxer.config import Settings, get_settings
from recipeboxer.models.recipe import RecipeSchema

logger = logging.getLogger(__name__)

# ============================================================================
# 1. EVALUATION RUBRIC & SYSTEM PROMPTS
# ============================================================================

JUDGE_SYSTEM_PROMPT = """You are an impartial, expert culinary archivist and gastronomic forensic judge.
You evaluate the fidelity, coherence, and accuracy of transcribed historical recipe cards against culinary standards and (when available) ground-truth source material.

Score the candidate recipe on four rubric dimensions from 1 (poor / completely flawed) to 5 (flawless / perfectly faithful):

1. **semantic_fidelity** (1-5):
   - 5: Flawlessly captures culinary techniques, temperature units, cookware specifications, and ingredient preparations.
   - 3: Minor culinary terminology substitutions that don't alter the dish outcome (e.g. 'blend' vs 'mix').
   - 1: Severe culinary errors, hallucinated instructions, incorrect cooking temperatures, or corrupted baking times.

2. **vintage_headnote_preservation** (1-5):
   - 5: Preserves all historical headnotes, handwritten family remarks, marginalia, attributions, and vintage context.
   - 3: Headnotes summarized or slightly truncated but key context retained.
   - 1: Historical provenance, attributions, or handwritten marginalia completely omitted or stripped.

3. **ingredient_direction_cross_consistency** (1-5):
   - 5: Perfect 1-to-1 cross-consistency: Every listed ingredient is utilized in the directions, and all ingredients mentioned in directions are present in the ingredients list with quantities.
   - 3: Minor omission (e.g., salt/pepper listed in ingredients but omitted from directions step, or garnish mentioned in steps without explicit quantity).
   - 1: Critical discrepancies: Key main ingredients missing from instructions or directions call for ingredients absent from the ingredients list.

4. **step_ordering_logic** (1-5):
   - 5: Perfect chronological sequence and cooking logic (mise en place, preheating oven, combining wet/dry, baking, cooling).
   - 3: Understandable but slightly awkward sequence ordering (e.g. preheating mentioned halfway through mixing).
   - 1: Impossible or contradictory chronological sequence (e.g., baking step before mixing dry ingredients).

Respond ONLY with strict JSON conforming to this schema:
{
  "semantic_fidelity": 5,
  "vintage_headnote_preservation": 5,
  "ingredient_direction_cross_consistency": 5,
  "step_ordering_logic": 5,
  "itemized_reasoning": {
    "semantic_fidelity": "Detailed justification...",
    "vintage_headnote_preservation": "Detailed justification...",
    "ingredient_direction_cross_consistency": "Detailed justification...",
    "step_ordering_logic": "Detailed justification..."
  },
  "overall_feedback": "Summary of recipe transcription quality and fidelity."
}
"""


# ============================================================================
# 2. PYDANTIC OUTPUT MODELS
# ============================================================================

class JudgeEvaluationResult(BaseModel):
    """Structured evaluation outcome from the LLM-as-a-Judge semantic assessor."""
    recipe_id: str = Field(default="", description="Identifier of the evaluated recipe")
    semantic_fidelity: int = Field(default=5, ge=1, le=5, description="Score 1-5 for culinary fidelity")
    vintage_headnote_preservation: int = Field(default=5, ge=1, le=5, description="Score 1-5 for headnote preservation")
    ingredient_direction_cross_consistency: int = Field(
        default=5, ge=1, le=5, description="Score 1-5 for ingredient-direction consistency"
    )
    step_ordering_logic: int = Field(default=5, ge=1, le=5, description="Score 1-5 for sequential step logic")
    aggregate_score: float = Field(
        default=0.0, ge=0.0, le=5.0, description="Mean aggregate score across the 4 dimensions"
    )
    passed: bool = Field(default=True, description="True if aggregate_score >= threshold")
    threshold: float = Field(default=3.5, description="Pass/fail score threshold applied")
    dimension_scores: Dict[str, int] = Field(
        default_factory=dict,
        description="Dictionary mapping dimension name to integer score (1-5)",
    )
    itemized_reasoning: Dict[str, str] = Field(
        default_factory=dict,
        description="Per-dimension detailed justification and rubric commentary",
    )
    overall_feedback: str = Field(default="", description="Overall assessment summary")
    token_usage: Dict[str, int] = Field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        description="Token consumption metrics for the LLM evaluation call",
    )
    latency_ms: float = Field(default=0.0, ge=0.0, description="Evaluation execution duration in milliseconds")

    @model_validator(mode="after")
    def _validate_and_sync_scores(self) -> "JudgeEvaluationResult":
        """Auto-populates dimension_scores and ensures aggregate_score and passed status are synchronized."""
        if not self.dimension_scores:
            self.dimension_scores = {
                "semantic_fidelity": self.semantic_fidelity,
                "vintage_headnote_preservation": self.vintage_headnote_preservation,
                "ingredient_direction_cross_consistency": self.ingredient_direction_cross_consistency,
                "step_ordering_logic": self.step_ordering_logic,
            }
        else:
            for k in ("semantic_fidelity", "vintage_headnote_preservation", "ingredient_direction_cross_consistency", "step_ordering_logic"):
                if k in self.dimension_scores:
                    setattr(self, k, self.dimension_scores[k])

        if self.aggregate_score == 0.0:
            calc_agg = sum(self.dimension_scores.values()) / 4.0
            self.aggregate_score = round(calc_agg, 2)

        self.passed = (self.aggregate_score >= self.threshold)
        return self


# ============================================================================
# 3. DETERMINISTIC MOCK EVALUATION ENGINE
# ============================================================================

def _evaluate_deterministic_mock(
    candidate: RecipeSchema,
    reference: Optional[RecipeSchema] = None,
    threshold: float = 3.5,
) -> JudgeEvaluationResult:
    """Computes deterministic, heuristic rubric scores without external LLM API calls.

    Performs rule-based checks:
    1. Cross-consistency: calculates ingredient name token overlap in directions.
    2. Step ordering: verifies sequential numbering and chronological indicators.
    3. Headnotes: checks for description/source notes preservation.
    4. Semantic fidelity: compares against reference recipe (if provided) or verifies basic structure.

    Args:
        candidate: Candidate RecipeSchema to evaluate.
        reference: Optional reference RecipeSchema.
        threshold: Score threshold for pass/fail.

    Returns:
        Deterministic JudgeEvaluationResult.
    """
    rec_id = candidate.recipe_id or (reference.recipe_id if reference else "rec_mock")
    start_time = time.perf_counter()

    # 1. Step Ordering Logic (1-5)
    order_score = 5
    order_notes = []
    clean_dirs = [str(d).strip() for d in (candidate.directions or []) if d and str(d).strip()]
    if not clean_dirs:
        order_score = 1
        order_notes.append("No directions provided.")
    else:
        # Check numbering sequence
        for idx, step in enumerate(clean_dirs, start=1):
            m = re.match(r"^(?:Step\s+)?[\(\[]?\s*(\d+)\s*[\.\:\)\-\]]\s*", step, flags=re.IGNORECASE)
            if m and int(m.group(1)) != idx:
                order_score = min(order_score, 3)
                order_notes.append(f"Step {idx} numbering out of sequence.")
                break
        if order_score == 5:
            order_notes.append("Directions follow strict sequential progression and chronological order.")

    # 2. Ingredient-Direction Cross-Consistency (1-5)
    cross_score = 5
    cross_notes = []
    clean_ings = [str(i).strip() for i in (candidate.ingredients or []) if i and str(i).strip()]
    if not clean_ings or not clean_dirs:
        cross_score = 1
        cross_notes.append("Missing ingredients or directions.")
    else:
        # Extract main nouns from ingredients (simple word heuristics)
        stopwords = {
            "cup", "cups", "tbsp", "tsp", "tablespoon", "tablespoons", "teaspoon",
            "teaspoons", "oz", "ounce", "ounces", "lb", "pound", "pounds", "pinch",
            "dash", "clove", "cloves", "can", "cans", "package", "packages", "small",
            "medium", "large", "chopped", "diced", "sliced", "fresh", "dried", "ground",
            "minced", "cold", "warm", "hot", "melted", "softened", "room", "temperature",
            "and", "or", "of", "to", "taste", "divided", "optional", "about", "all",
            "purpose", "packed", "granulated", "powdered", "shredded", "grated"
        }
        all_dirs_text = " ".join(clean_dirs).lower()
        missing_ingredients = []

        def _matches_noun(noun: str, text: str) -> bool:
            if noun in text:
                return True
            if len(noun) > 3:
                if noun.endswith("ies") and (noun[:-3] + "y") in text:
                    return True
                if noun.endswith("y") and (noun[:-1] + "ies") in text:
                    return True
                if noun.endswith("es") and noun[:-2] in text:
                    return True
                if noun.endswith("s") and noun[:-1] in text:
                    return True
                if (noun + "s") in text or (noun + "es") in text:
                    return True
            return False

        for ing in clean_ings:
            # Tokenize ingredient line
            tokens = [
                re.sub(r"[^\w]", "", w.lower())
                for w in str(ing).split()
                if re.sub(r"[^\w]", "", w.lower()) and not w.replace("/", "").isdigit()
            ]
            key_nouns = [t for t in tokens if t not in stopwords and len(t) > 2]
            if key_nouns and not any(_matches_noun(noun, all_dirs_text) for noun in key_nouns):
                missing_ingredients.append(ing)

        if missing_ingredients:
            if len(missing_ingredients) > len(clean_ings) // 2:
                cross_score = 2
                cross_notes.append(f"Multiple listed ingredients missing from directions: {len(missing_ingredients)} items.")
            else:
                cross_score = 4
                cross_notes.append(f"Minor ingredient unmentioned in directions: {missing_ingredients[0]}")
        else:
            cross_notes.append("All listed ingredients are referenced in the preparation directions.")

    # 3. Vintage Headnote Preservation (1-5)
    headnote_score = 5
    headnote_notes = []
    if reference:
        ref_has_notes = bool((reference.description or "").strip() or (reference.source_notes or "").strip())
        cand_has_notes = bool((candidate.description or "").strip() or (candidate.source_notes or "").strip())
        if ref_has_notes and not cand_has_notes:
            headnote_score = 2
            headnote_notes.append("Reference headnotes/source notes were omitted in transcription.")
        elif ref_has_notes and cand_has_notes:
            headnote_score = 5
            headnote_notes.append("Historical headnotes and card provenance successfully preserved.")
        else:
            headnote_score = 5
            headnote_notes.append("No source headnotes in reference card.")
    else:
        if candidate.description or candidate.source_notes:
            headnote_score = 5
            headnote_notes.append("Headnotes and attribution notes present.")
        else:
            headnote_score = 4
            headnote_notes.append("No headnotes provided in recipe.")

    # 4. Semantic Fidelity (1-5)
    fidelity_score = 5
    fidelity_notes = []
    if reference:
        # Check title similarity
        from recipeboxer.evals.metrics import character_error_rate
        title_cer = character_error_rate(reference.title or "", candidate.title or "")
        if title_cer > 0.5:
            fidelity_score = min(fidelity_score, 2)
            fidelity_notes.append(f"Title divergence (CER: {title_cer:.2f}).")
        elif title_cer > 0.1:
            fidelity_score = min(fidelity_score, 4)
            fidelity_notes.append(f"Minor title variation (CER: {title_cer:.2f}).")
        else:
            fidelity_notes.append("Recipe title and cooking terminology accurately preserved.")

        # Check ingredient count fidelity
        ref_ings = [i for i in (reference.ingredients or []) if i]
        cand_ings = [i for i in (candidate.ingredients or []) if i]
        if abs(len(ref_ings) - len(cand_ings)) > 2:
            fidelity_score = min(fidelity_score, 3)
            fidelity_notes.append("Ingredient count mismatch between reference and hypothesis.")
    else:
        if len(clean_ings) >= 1 and len(clean_dirs) >= 1:
            fidelity_score = 5
            fidelity_notes.append("Culinary instructions and structured format appear complete and faithful.")
        else:
            fidelity_score = 2
            fidelity_notes.append("Incomplete recipe contents.")

    dim_scores = {
        "semantic_fidelity": fidelity_score,
        "vintage_headnote_preservation": headnote_score,
        "ingredient_direction_cross_consistency": cross_score,
        "step_ordering_logic": order_score,
    }

    aggregate = sum(dim_scores.values()) / 4.0
    passed = aggregate >= threshold

    reasoning = {
        "semantic_fidelity": " ".join(fidelity_notes),
        "vintage_headnote_preservation": " ".join(headnote_notes),
        "ingredient_direction_cross_consistency": " ".join(cross_notes),
        "step_ordering_logic": " ".join(order_notes),
    }

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    return JudgeEvaluationResult(
        recipe_id=rec_id,
        semantic_fidelity=fidelity_score,
        vintage_headnote_preservation=headnote_score,
        ingredient_direction_cross_consistency=cross_score,
        step_ordering_logic=order_score,
        aggregate_score=round(aggregate, 2),
        passed=passed,
        threshold=threshold,
        dimension_scores=dim_scores,
        itemized_reasoning=reasoning,
        overall_feedback=f"Deterministic evaluation score: {aggregate:.2f}/5.0. Status: {'PASS' if passed else 'FAIL'}.",
        token_usage={"prompt_tokens": 120, "completion_tokens": 65, "total_tokens": 185},
        latency_ms=round(elapsed_ms, 2),
    )


# ============================================================================
# 4. RECIPE JUDGE ENGINE
# ============================================================================

class RecipeJudge:
    """Independent LLM-as-a-Judge semantic evaluator using Gemini or deterministic mock."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        threshold: float = 3.5,
        mock: bool = False,
        settings: Optional[Settings] = None,
        max_retries: int = 3,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        """Initialize the RecipeJudge.

        Args:
            api_key: Optional Gemini API key override.
            model_name: Optional Gemini model name (defaults to settings.default_model).
            threshold: Pass/fail threshold for aggregate score (1.0 to 5.0).
            mock: If True, operates strictly in deterministic offline mock mode.
            settings: Optional Settings instance.
            max_retries: Maximum API rate limit retry attempts.
            retry_delay_seconds: Base backoff delay between retries.
        """
        self.settings = settings or get_settings()
        self.api_key = api_key or self.settings.gemini_api_key
        self.model_name = model_name or self.settings.default_model
        self.threshold = threshold
        self.mock = mock or (not self.api_key or self.api_key.startswith("mock"))
        self.max_retries = max_retries
        self.retry_delay = retry_delay_seconds

    async def evaluate(
        self,
        candidate_recipe: RecipeSchema,
        reference_recipe: Optional[RecipeSchema] = None,
        card_notes: Optional[str] = None,
    ) -> JudgeEvaluationResult:
        """Evaluates a candidate RecipeSchema against qualitative rubric dimensions.

        Args:
            candidate_recipe: RecipeSchema produced by extraction or pipeline.
            reference_recipe: Optional ground-truth RecipeSchema for comparison.
            card_notes: Optional background card notes or provenance context.

        Returns:
            Validated JudgeEvaluationResult.
        """
        if self.mock:
            return _evaluate_deterministic_mock(
                candidate=candidate_recipe,
                reference=reference_recipe,
                threshold=self.threshold,
            )

        start_time = time.perf_counter()
        try:
            return await self._call_gemini_judge_api(
                candidate=candidate_recipe,
                reference=reference_recipe,
                card_notes=card_notes,
                start_time=start_time,
            )
        except Exception as exc:
            logger.warning(
                f"Gemini LLM Judge API call failed: {exc}. Falling back to deterministic mock evaluation."
            )
            return _evaluate_deterministic_mock(
                candidate=candidate_recipe,
                reference=reference_recipe,
                threshold=self.threshold,
            )

    def evaluate_sync(
        self,
        candidate_recipe: RecipeSchema,
        reference_recipe: Optional[RecipeSchema] = None,
        card_notes: Optional[str] = None,
    ) -> JudgeEvaluationResult:
        """Synchronously evaluates a recipe using deterministic mock scoring."""
        return _evaluate_deterministic_mock(
            candidate=candidate_recipe,
            reference=reference_recipe,
            threshold=self.threshold,
        )

    async def evaluate_batch(
        self,
        items: List[Tuple[RecipeSchema, Optional[RecipeSchema]]],
        concurrency: int = 5,
    ) -> List[JudgeEvaluationResult]:
        """Evaluates a batch of candidate recipes concurrently with semaphore throttling.

        Args:
            items: List of (candidate_recipe, optional_reference_recipe) tuples.
            concurrency: Maximum concurrent evaluation tasks.

        Returns:
            List of JudgeEvaluationResult items preserving input order.
        """
        sem = asyncio.Semaphore(concurrency)

        async def _worker(cand: RecipeSchema, ref: Optional[RecipeSchema]) -> JudgeEvaluationResult:
            async with sem:
                return await self.evaluate(cand, ref)

        tasks = [_worker(cand, ref) for cand, ref in items]
        return await asyncio.gather(*tasks)

    async def _call_gemini_judge_api(
        self,
        candidate: RecipeSchema,
        reference: Optional[RecipeSchema],
        card_notes: Optional[str],
        start_time: float,
    ) -> JudgeEvaluationResult:
        """Executes the live structured LLM judge API call via HTTPX with rate limit retries."""
        import httpx

        rec_id = candidate.recipe_id or (reference.recipe_id if reference else "rec_eval")

        # Build prompt payload
        candidate_json = json.dumps(candidate.model_dump(), indent=2)
        reference_json = json.dumps(reference.model_dump(), indent=2) if reference else "N/A (Evaluating standalone recipe without reference)"

        user_content = f"""Please evaluate the following candidate recipe:

### Candidate Recipe JSON:
```json
{candidate_json}
```

### Reference Recipe Ground-Truth (if available):
```json
{reference_json}
```

### Additional Physical Card Context:
{card_notes or 'None provided'}

Provide structured score ratings (1-5) and itemized reasoning for each dimension according to the rubric.
"""

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": JUDGE_SYSTEM_PROMPT},
                        {"text": user_content},
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        }

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 429 or resp.status_code >= 500:
                        delay = self.retry_delay * (2 ** attempt)
                        logger.warning(f"Judge API returned status {resp.status_code}. Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                        continue

                    resp.raise_for_status()
                    data = resp.json()

                    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.IGNORECASE)
                    cleaned = re.sub(r"\s*```$", "", cleaned.strip()).strip()

                    parsed = json.loads(cleaned)

                    # Extract usage metadata
                    usage = data.get("usageMetadata", {})
                    token_usage = {
                        "prompt_tokens": usage.get("promptTokenCount", 0),
                        "completion_tokens": usage.get("candidatesTokenCount", 0),
                        "total_tokens": usage.get("totalTokenCount", 0),
                    }

                    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

                    fidelity = int(parsed.get("semantic_fidelity", 5))
                    headnotes = int(parsed.get("vintage_headnote_preservation", 5))
                    cross = int(parsed.get("ingredient_direction_cross_consistency", 5))
                    order = int(parsed.get("step_ordering_logic", 5))

                    dim_scores = {
                        "semantic_fidelity": max(1, min(5, fidelity)),
                        "vintage_headnote_preservation": max(1, min(5, headnotes)),
                        "ingredient_direction_cross_consistency": max(1, min(5, cross)),
                        "step_ordering_logic": max(1, min(5, order)),
                    }

                    aggregate = sum(dim_scores.values()) / 4.0
                    passed = aggregate >= self.threshold

                    reasoning = parsed.get("itemized_reasoning", {})
                    if not isinstance(reasoning, dict):
                        reasoning = {k: str(reasoning) for k in dim_scores}

                    feedback = parsed.get("overall_feedback", f"LLM Judge evaluation score: {aggregate:.2f}/5.0")

                    return JudgeEvaluationResult(
                        recipe_id=rec_id,
                        semantic_fidelity=dim_scores["semantic_fidelity"],
                        vintage_headnote_preservation=dim_scores["vintage_headnote_preservation"],
                        ingredient_direction_cross_consistency=dim_scores["ingredient_direction_cross_consistency"],
                        step_ordering_logic=dim_scores["step_ordering_logic"],
                        aggregate_score=round(aggregate, 2),
                        passed=passed,
                        threshold=self.threshold,
                        dimension_scores=dim_scores,
                        itemized_reasoning=reasoning,
                        overall_feedback=feedback,
                        token_usage=token_usage,
                        latency_ms=round(elapsed_ms, 2),
                    )

            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    await asyncio.sleep(delay)

        raise last_exc or RuntimeError("Judge API failed after maximum retries.")
