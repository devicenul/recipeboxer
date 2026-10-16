"""Text-Only Ground-Truth Structured Recipe Drafter (WP-T3GT).

Drafts Schema v2 structured ground truth for real-world (T3, era-neutral) evaluation recipes from verbatim
human transcriptions. In accordance with Remediation Plan §3.3 & WAVE3_CONTRACTS:
- TEXT ONLY: Never sends or references the image; prevents model's visual bias from
  contaminating the ground truth.
- INDEPENDENT MODEL TIER: Uses a model tier distinct from the production extractor
  (gemini-3.1-pro-preview vs production's Flash tier) to reduce self-preference bias.
- STRUCTURED OUTPUT: Uses google-genai structured output (response_schema) matching
  EXACTLY the structured GT shape consumed by evals.score and evals.metrics.extraction
  (matching T1 and T2).
- EXPLICIT RULES: Verbatim-faithful, null quantity when absent, no invention,
  preserve original spelling in raw text.
- VALIDATION: Every drafted ingredient raw text and direction step must fuzzy-match
  the verbatim transcription with CER <= 0.15.
- OFFLINE-SAFE: When no API key is available, cleanly reports pending status without error.
- RE-ENCRYPTION: Decrypts in memory and re-encrypts to --vault-file (default evals/vault/t3.gt.enc;
  archived antique tier T3A: evals/vault/archive/t3a.gt.enc). Plaintext GT
  never touches disk in the repository.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evals import vault
from evals.metrics.text import cer, levenshtein_distance, normalize_text

logger = logging.getLogger("evals.curation_t3.draft_structured")

DEFAULT_DRAFT_MODEL = "gemini-3.1-pro-preview"
ENV_API_KEY = "GEMINI_API_KEY"
ENV_RECIPEBOXER_KEY = "RECIPEBOXER_GEMINI_API_KEY"


class IngredientSchema(BaseModel):
    """Structured ingredient representation matching T1/T2 ground-truth schema."""

    model_config = ConfigDict(populate_by_name=True)

    raw: str = Field(
        description=(
            "Exact raw clause or line from transcription describing this ingredient, "
            "preserving original spelling, punctuation, and wording verbatim."
        )
    )
    quantity: Optional[str] = Field(
        default=None,
        description=(
            "Numeric or fractional quantity string (e.g. '1/2', '2', '1 1/2'), "
            "or null if no quantity is specified in the text."
        ),
    )
    unit: Optional[str] = Field(
        default=None,
        description=(
            "Measurement unit string (e.g. 'cup', 'lb', 'pinch', 'spoonful'), "
            "or null if no unit is specified in the text."
        ),
    )
    item: str = Field(
        description="The name of the ingredient item, preserving original spelling verbatim."
    )
    section: Optional[str] = Field(
        default=None,
        description="Section header under which this ingredient falls, or null if none.",
    )

    @field_validator("quantity", "unit", "section", mode="before")
    @classmethod
    def normalize_optional_fields(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ("null", "none"):
                return None
            return s
        return str(v).strip() or None


class StructuredRecipeSchema(BaseModel):
    """Complete structured recipe ground truth matching T1/T2 schema."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(
        description="Title of the recipe as given in transcription, preserving original spelling."
    )
    yield_: Optional[str] = Field(
        default=None,
        alias="yield",
        description="Yield or servings if explicitly stated in text, otherwise null.",
    )
    source_note: Optional[str] = Field(
        default=None,
        description="Attribution, author note, or source credit if present, otherwise null.",
    )
    ingredients: List[IngredientSchema] = Field(
        description="List of structured ingredients faithfully extracted from the transcription."
    )
    directions: List[str] = Field(
        description=(
            "Sequential preparation and cooking steps as strings faithfully "
            "extracted from the transcription."
        )
    )
    notes: List[str] = Field(
        default_factory=list,
        description=(
            "Additional notes, marginalia, headnotes or commentary, "
            "or attributions outside core cooking steps."
        ),
    )

    @field_validator("yield_", "source_note", mode="before")
    @classmethod
    def normalize_optional_recipe_fields(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            if not s or s.lower() in ("null", "none"):
                return None
            return s
        return str(v).strip() or None


def get_api_key() -> Optional[str]:
    """Retrieve Gemini API key from environment if configured."""
    return os.environ.get(ENV_API_KEY) or os.environ.get(ENV_RECIPEBOXER_KEY)


def draft_structured_prompt(verbatim_faces: List[str], correction_note: Optional[str] = None) -> str:
    """Construct text-only structuring prompt from verbatim transcriptions."""
    combined_transcript = "\n\n--- NEXT CARD FACE ---\n\n".join(verbatim_faces)
    correction_block = f"\nATTENTION / CORRECTION REQUIRED:\n{correction_note}\n" if correction_note else ""

    return (
        "You are an expert recipe curator for real-world recipe documents (handwritten or typed cards, "
        "notebook pages, clippings) from any era. "
        "Convert the following verbatim recipe transcription into a structured RecipeSchema JSON object.\n\n"
        "EXPLICIT RULES:\n"
        "1. VERBATIM-FAITHFUL: Extract strictly what is present in the transcription. "
        "Do NOT invent missing ingredients, missing quantities, missing units, or missing preparation steps.\n"
        "2. PRESERVE ORIGINAL SPELLING: In ingredient 'raw', 'item', 'title', and 'directions', "
        "preserve the original spelling verbatim (including misspellings, abbreviations, regional or dated terms) "
        "and punctuation exactly as transcribed. Do not modernize or correct.\n"
        "3. NULL QUANTITY WHEN ABSENT: If an ingredient has no explicit quantity mentioned in the raw text, "
        "set quantity to null. Never default to '1' or guess.\n"
        "4. NULL UNIT WHEN ABSENT: If an ingredient has no measurement unit mentioned in the raw text, "
        "set unit to null.\n"
        "5. INGREDIENTS BREAKDOWN: For each ingredient:\n"
        "   - 'raw': the exact clause, phrase, or line from the transcription describing this ingredient.\n"
        "   - 'quantity': numeric/fractional quantity string, or null.\n"
        "   - 'unit': measurement unit, or null.\n"
        "   - 'item': ingredient name, preserving original spelling.\n"
        "   - 'section': section name if present, or null.\n"
        "6. DIRECTIONS: Sequential step strings capturing each cooking or preparation action faithfully "
        "from the transcription text.\n"
        "7. NOTES: Any marginalia, headnotes, remarks, or attributions (e.g. 'from Aunt May') outside "
        "the cooking steps.\n"
        f"{correction_block}\n"
        f"TRANSCRIPTION:\n{combined_transcript}\n"
    )


def fuzzy_match_substring_cer(haystack: str, needle: str) -> float:
    """Calculates the minimum Character Error Rate (CER) of needle aligned as a substring in haystack.

    CER = Levenshtein(needle, best_substring) / max(len(needle), 1).
    Uses fast exact-substring check first, followed by semi-global Levenshtein alignment.
    """
    norm_h = normalize_text(haystack)
    norm_n = normalize_text(needle)
    if not norm_n:
        return 0.0
    if norm_n in norm_h:
        return 0.0

    m, n = len(norm_n), len(norm_h)
    if n == 0:
        return 1.0

    # Semi-global dynamic programming with free start and free end in haystack
    dp = [0] * (n + 1)
    for i in range(1, m + 1):
        new_dp = [i] + [0] * n
        ch_n = norm_n[i - 1]
        for j in range(1, n + 1):
            cost = 0 if ch_n == norm_h[j - 1] else 1
            new_dp[j] = min(
                dp[j] + 1,        # deletion from needle
                new_dp[j - 1] + 1,# insertion into needle
                dp[j - 1] + cost  # match or substitution
            )
        dp = new_dp

    return float(min(dp) / m)


def validate_draft_against_verbatim(
    structured: Dict[str, Any],
    verbatim_faces: List[str],
    max_cer: float = 0.15,
) -> List[Dict[str, Any]]:
    """Validates that all ingredient raw strings and direction steps fuzzy-match verbatim text (CER <= max_cer)."""
    haystack = "\n\n".join(verbatim_faces)
    mismatches = []

    # Validate ingredients raw strings
    for idx, ing in enumerate(structured.get("ingredients", [])):
        raw_text = ing.get("raw") or ""
        if raw_text:
            err = fuzzy_match_substring_cer(haystack, raw_text)
            if err > max_cer:
                mismatches.append({
                    "field": f"ingredients[{idx}].raw",
                    "text": raw_text,
                    "cer": round(err, 4),
                })

    # Validate directions steps
    for idx, step in enumerate(structured.get("directions", [])):
        if step:
            err = fuzzy_match_substring_cer(haystack, step)
            if err > max_cer:
                mismatches.append({
                    "field": f"directions[{idx}]",
                    "text": step,
                    "cer": round(err, 4),
                })

    return mismatches


class CurationResult(int):
    """Exit code subclass that preserves integer semantics while providing summary data and unpacking."""

    def __new__(cls, code: int, summary: Optional[Dict[str, Any]] = None):
        obj = super().__new__(cls, code)
        obj.summary = summary or {}
        return obj

    def __iter__(self):
        return iter((int(self), self.summary))


def draft_structured_gt(
    vault_file: Path,
    identity_path: Optional[Path] = None,
    recipients_path: Optional[Path] = None,
    model_name: str = DEFAULT_DRAFT_MODEL,
    dry_run: bool = False,
    limit: Optional[int] = None,
    force: bool = False,
    max_retries: int = 2,
    client: Optional[Any] = None,
) -> CurationResult:
    """Draft structured GT for recipes with pending status.

    Returns:
        CurationResult (acts as int exit code, also unpackable as (exit_code, summary))
    """
    resolved_vault = Path(vault_file).expanduser().resolve()
    if not resolved_vault.exists():
        raise FileNotFoundError(f"Vault archive not found at '{resolved_vault}'")

    ciphertext = resolved_vault.read_bytes()
    plaintext = vault.decrypt_bytes(ciphertext, identity_path=identity_path)
    gt_data: Dict[str, Any] = json.loads(plaintext.decode("utf-8"))

    decks: List[Dict[str, Any]] = gt_data.get("decks", [])
    pending_recipes: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    for deck in decks:
        for recipe in deck.get("recipes", []):
            is_pending = (
                recipe.get("structured") is None
                or recipe.get("structured_status") in ("pending_text_only_draft", None)
            )
            if force or is_pending:
                pending_recipes.append((deck, recipe))

    total_pending = len(pending_recipes)
    print(f"Found {total_pending} recipe(s) to draft in {resolved_vault.name}.")

    summary: Dict[str, Any] = {
        "total_recipes": sum(len(d.get("recipes", [])) for d in decks),
        "pending": total_pending,
        "drafted": 0,
        "failed": 0,
        "mismatches": [],
        "tokens": {
            "prompt_tokens": 0,
            "candidate_tokens": 0,
            "thoughts_tokens": 0,
            "total_tokens": 0,
        },
        "cost_usd": 0.0,
    }

    if total_pending == 0:
        print("All recipes already have structured ground truth.")
        return CurationResult(0, summary)

    api_key = get_api_key()
    if not api_key and client is None:
        print(
            "\n[NOTICE] No Gemini API key detected in environment ($GEMINI_API_KEY / $RECIPEBOXER_GEMINI_API_KEY).\n"
            "Structured GT drafting is deferred until a live API key is provided.\n"
            f"All {total_pending} items remain marked as 'pending_text_only_draft'.\n"
            f"When a key is configured, re-run with: python -m evals.curation_t3.draft_structured --model-tier {model_name}"
        )
        return CurationResult(0, summary)

    if dry_run:
        print(f"[DRY-RUN] Would draft {total_pending} recipes using text-only model '{model_name}'.")
        return CurationResult(0, summary)

    # Initialize GenAI client if not injected
    if client is None:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
        except Exception as e:
            sys.stderr.write(f"Could not initialize google-genai Client: {e}\n")
            return CurationResult(1, summary)

    from google.genai import types

    to_process = pending_recipes[:limit] if limit else pending_recipes
    drafted_count = 0
    total_prompt_tokens = 0
    total_candidate_tokens = 0
    total_thoughts_tokens = 0
    all_mismatches: List[Dict[str, Any]] = []

    for deck, recipe in to_process:
        rid = recipe.get("recipe_id", "unknown")
        verbatim_faces = recipe.get("verbatim", [])

        success = False
        parsed_structured: Optional[Dict[str, Any]] = None
        used_prompt_hash: str = ""
        correction_note: Optional[str] = None

        for attempt in range(max_retries + 1):
            prompt = draft_structured_prompt(verbatim_faces, correction_note=correction_note)
            prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

            try:
                # Text-only structured output call
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=StructuredRecipeSchema,
                        temperature=0.0,
                        thinking_config=types.ThinkingConfig(thinking_budget=1024),
                    ),
                )

                # Record token usage
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    um = response.usage_metadata
                    p_tok = getattr(um, "prompt_token_count", 0) or 0
                    c_tok = getattr(um, "candidates_token_count", 0) or 0
                    t_tok = getattr(um, "thoughts_token_count", 0) or 0
                    total_prompt_tokens += p_tok
                    total_candidate_tokens += c_tok
                    total_thoughts_tokens += t_tok

                resp_text = response.text or ""
                clean_json = resp_text.strip()
                if clean_json.startswith("```json"):
                    clean_json = clean_json[7:]
                if clean_json.startswith("```"):
                    clean_json = clean_json[3:]
                if clean_json.endswith("```"):
                    clean_json = clean_json[:-3]

                data_dict = json.loads(clean_json.strip())
                validated_model = StructuredRecipeSchema.model_validate(data_dict)
                parsed_structured = validated_model.model_dump(by_alias=True)
                used_prompt_hash = prompt_hash

                # Validate against verbatim transcript
                recipe_mismatches = validate_draft_against_verbatim(
                    parsed_structured, verbatim_faces, max_cer=0.15
                )

                if recipe_mismatches and attempt < max_retries:
                    bad_items = [f"{m['field']}: '{m['text']}' (CER={m['cer']})" for m in recipe_mismatches[:3]]
                    correction_note = (
                        "The previous draft contained text that differed from the verbatim transcription:\n"
                        + "\n".join(bad_items)
                        + "\nPlease ensure all 'raw' ingredient texts and direction steps copy EXACT verbatim phrases."
                    )
                    logger.warning(
                        f"Attempt {attempt+1} for {rid} had {len(recipe_mismatches)} mismatch(es). Retrying with correction..."
                    )
                    continue

                if recipe_mismatches:
                    logger.warning(f"{rid} has {len(recipe_mismatches)} residual mismatch(es) (CER > 0.15).")
                    for m in recipe_mismatches:
                        m["recipe_id"] = rid
                        all_mismatches.append(m)

                success = True
                break

            except Exception as e:
                sys.stderr.write(f"Attempt {attempt+1} failed for {rid}: {e}\n")

        if success and parsed_structured is not None:
            now_iso = datetime.now(timezone.utc).isoformat()
            recipe["structured"] = parsed_structured
            recipe["structured_status"] = "drafted_unreviewed"
            recipe["structured_model"] = model_name
            recipe["prompt_hash"] = used_prompt_hash
            recipe["drafted_on"] = now_iso
            drafted_count += 1
            print(f"[DRAFTED] {rid} successfully using {model_name} (hash: {used_prompt_hash[:8]})")
        else:
            summary["failed"] += 1
            sys.stderr.write(f"[FAILED] Could not draft structured GT for {rid}\n")

    summary["drafted"] = drafted_count
    summary["mismatches"] = all_mismatches
    summary["tokens"]["prompt_tokens"] = total_prompt_tokens
    summary["tokens"]["candidate_tokens"] = total_candidate_tokens
    summary["tokens"]["thoughts_tokens"] = total_thoughts_tokens
    total_all_tokens = total_prompt_tokens + total_candidate_tokens + total_thoughts_tokens
    summary["tokens"]["total_tokens"] = total_all_tokens

    # Pricing according to config/pricing.toml:
    # gemini-3.1-pro-preview: input $2.00 / MTok, output $12.00 / MTok, thinking $12.00 / MTok
    cost_usd = (
        (total_prompt_tokens * 2.00)
        + ((total_candidate_tokens + total_thoughts_tokens) * 12.00)
    ) / 1_000_000
    summary["cost_usd"] = round(cost_usd, 6)

    print("\n" + "=" * 60)
    print("DRAFTING RUN SUMMARY")
    print(f"  Drafted:          {drafted_count}/{total_pending}")
    print(f"  Prompt tokens:    {total_prompt_tokens:,}")
    print(f"  Candidate tokens: {total_candidate_tokens:,}")
    print(f"  Thoughts tokens:  {total_thoughts_tokens:,}")
    print(f"  Total tokens:     {total_all_tokens:,}")
    print(f"  Estimated cost:   ${cost_usd:.4f} USD")
    print(f"  Mismatches:       {len(all_mismatches)}")
    print("=" * 60 + "\n")

    if drafted_count > 0:
        # Re-encrypt updated GT in memory
        new_gt_bytes = json.dumps(gt_data, indent=2).encode("utf-8")
        new_enc = vault.encrypt_bytes(new_gt_bytes, recipients_path=recipients_path)
        resolved_vault.write_bytes(new_enc)
        print(f"Re-encrypted {resolved_vault.name} with {drafted_count} drafted recipe(s).")

    return CurationResult(0, summary)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Draft structured GT text-only from verbatim text")
    parser.add_argument(
        "--vault-file",
        type=Path,
        default=vault.get_repo_root() / "evals" / "vault" / "t3.gt.enc",
        help="Path to encrypted vault file (default: evals/vault/t3.gt.enc)",
    )
    parser.add_argument(
        "--identity",
        type=Path,
        default=None,
        help="Path to age identity file (default: ~/.config/recipeboxer/age.key)",
    )
    parser.add_argument(
        "--recipients",
        type=Path,
        default=None,
        help="Path to recipients.txt (default: evals/vault/recipients.txt)",
    )
    parser.add_argument(
        "--model-tier",
        type=str,
        default=DEFAULT_DRAFT_MODEL,
        help=f"Model tier for drafting (default: {DEFAULT_DRAFT_MODEL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate run without invoking LLM API",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-draft even if recipe already has structured data",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of recipes to draft in this run",
    )
    args = parser.parse_args(argv)

    try:
        code, _ = draft_structured_gt(
            vault_file=args.vault_file,
            identity_path=args.identity,
            recipients_path=args.recipients,
            model_name=args.model_tier,
            dry_run=args.dry_run,
            limit=args.limit,
            force=args.force,
        )
        return code
    except Exception as e:
        sys.stderr.write(f"draft_structured error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
