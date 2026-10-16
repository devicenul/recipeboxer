"""Title-Only Model Memorization Probe (WP-T3GT).

Audits evaluation candidate recipes for pre-training memorization by prompting
the model with the recipe title alone (never showing the image or transcription).

In accordance with Remediation Plan §3.3 & WAVE3_CONTRACTS:
- Items the model reproduces closely from pre-training data are flagged with
  excluded_reason = 'memorization_probe_match (overlap: X.XX)' in the GT vault.
- Per-recipe memorization_score and memorization_model are recorded into GT so reports
  can break them out.
- TITLE-ONLY PROMPT: Never sends images or verbatim texts.
- MODEL TIER: Defaults to gemini-3.1-pro-preview (matching curation tier).
- OFFLINE-SAFE: When no API key is available, cleanly reports status without failing.
- IN-MEMORY: Decrypts and re-encrypts vault archives strictly in-memory.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

from evals import vault

logger = logging.getLogger("evals.curation_t3.memorization_probe")

DEFAULT_PROBE_MODEL = "gemini-3.1-pro-preview"
ENV_API_KEY = "GEMINI_API_KEY"
ENV_RECIPEBOXER_KEY = "RECIPEBOXER_GEMINI_API_KEY"


def get_api_key() -> Optional[str]:
    """Retrieve Gemini API key from environment if configured."""
    return os.environ.get(ENV_API_KEY) or os.environ.get(ENV_RECIPEBOXER_KEY)


def tokenize(text: str) -> Set[str]:
    """Basic alphanumeric token set for overlap calculation."""
    return set(re.findall(r"\b[a-zA-Z]{3,}\b", text.lower()))


def compute_token_overlap(completion: str, verbatim_text: str) -> float:
    """Compute token Jaccard overlap between completion and verbatim text."""
    comp_tokens = tokenize(completion)
    gt_tokens = tokenize(verbatim_text)
    if not gt_tokens:
        return 0.0
    intersection = comp_tokens.intersection(gt_tokens)
    return len(intersection) / len(gt_tokens)


def extract_title_candidate(verbatim_faces: List[str]) -> str:
    """Extract candidate title from verbatim text lines."""
    for face_text in verbatim_faces:
        for line in face_text.splitlines():
            clean = line.strip()
            if clean and len(clean) < 80:
                # Remove common recipe headers
                clean = re.sub(r"^(to make|recipe for|receipt for|how to make)\s+", "", clean, flags=re.I)
                return clean.strip(" :.-")
    return "Recipe"


class CurationResult(int):
    """Exit code subclass that preserves integer semantics while providing summary data and unpacking."""

    def __new__(cls, code: int, summary: Optional[Dict[str, Any]] = None):
        obj = super().__new__(cls, code)
        obj.summary = summary or {}
        return obj

    def __iter__(self):
        return iter((int(self), self.summary))


def run_memorization_probe(
    vault_file: Path,
    identity_path: Optional[Path] = None,
    recipients_path: Optional[Path] = None,
    model_name: str = DEFAULT_PROBE_MODEL,
    threshold: float = 0.65,
    dry_run: bool = False,
    limit: Optional[int] = None,
    client: Optional[Any] = None,
) -> CurationResult:
    """Run title-only completion probe against vault recipes.

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
    all_recipes: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []

    for deck in decks:
        for recipe in deck.get("recipes", []):
            all_recipes.append((deck, recipe))

    total = len(all_recipes)
    print(f"Loaded {total} recipe(s) from {resolved_vault.name} for memorization audit.")

    summary: Dict[str, Any] = {
        "total": total,
        "probed": 0,
        "flagged": 0,
        "tokens": {
            "prompt_tokens": 0,
            "candidate_tokens": 0,
            "thoughts_tokens": 0,
            "total_tokens": 0,
        },
        "cost_usd": 0.0,
        "scores": {},
    }

    api_key = get_api_key()
    if not api_key and client is None:
        print(
            "\n[NOTICE] No Gemini API key detected in environment ($GEMINI_API_KEY / $RECIPEBOXER_GEMINI_API_KEY).\n"
            "Memorization probe execution is deferred until a live API key is configured.\n"
            f"All {total} items retain their existing excluded_reason status.\n"
            f"When a key is available, run with: python -m evals.curation_t3.memorization_probe --model {model_name}"
        )
        return CurationResult(0, summary)

    if dry_run:
        print(f"[DRY-RUN] Would run probe for {total} recipes using title-only model '{model_name}'.")
        for deck, r in all_recipes[:3]:
            title = (r.get("structured") or {}).get("title") or extract_title_candidate(r.get("verbatim", []))
            print(f"  Sample prompt target: 'Provide the complete recipe for {title}'")
        return CurationResult(0, summary)

    if client is None:
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
        except Exception as e:
            sys.stderr.write(f"Could not initialize google-genai Client: {e}\n")
            return CurationResult(1, summary)

    flagged_count = 0
    probed_count = 0
    total_prompt_tokens = 0
    total_candidate_tokens = 0
    total_thoughts_tokens = 0
    to_process = all_recipes[:limit] if limit else all_recipes

    for deck, recipe in to_process:
        rid = recipe.get("recipe_id", "unknown")
        verbatim_faces = recipe.get("verbatim", [])
        title = (recipe.get("structured") or {}).get("title") or extract_title_candidate(verbatim_faces)
        full_gt = " ".join(verbatim_faces)

        prompt = (
            f"Provide the complete recipe for '{title}', "
            f"including all ingredients, exact quantities, and full cooking instructions."
        )

        try:
            from google.genai import types
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    thinking_config=types.ThinkingConfig(thinking_budget=512),
                ),
            )

            if hasattr(resp, "usage_metadata") and resp.usage_metadata:
                um = resp.usage_metadata
                total_prompt_tokens += getattr(um, "prompt_token_count", 0) or 0
                total_candidate_tokens += getattr(um, "candidates_token_count", 0) or 0
                total_thoughts_tokens += getattr(um, "thoughts_token_count", 0) or 0

            comp_text = resp.text or ""
            overlap = compute_token_overlap(comp_text, full_gt)
            recipe["memorization_score"] = round(overlap, 4)
            recipe["memorization_model"] = model_name
            recipe["memorization_tested_on"] = datetime.now(timezone.utc).isoformat()
            summary["scores"][rid] = round(overlap, 4)
            probed_count += 1

            if overlap >= threshold:
                recipe["excluded_reason"] = f"memorization_probe_match (overlap: {overlap:.2f})"
                flagged_count += 1
                print(f"[FLAGGED] {rid} ('{title[:30]}...') memorization overlap {overlap:.2f} >= {threshold}")
            else:
                recipe["excluded_reason"] = None
                print(f"[CLEAN]   {rid} ('{title[:30]}...') overlap {overlap:.2f} < {threshold}")
        except Exception as e:
            sys.stderr.write(f"Probe call failed for {rid}: {e}\n")

    summary["probed"] = probed_count
    summary["flagged"] = flagged_count
    summary["tokens"]["prompt_tokens"] = total_prompt_tokens
    summary["tokens"]["candidate_tokens"] = total_candidate_tokens
    summary["tokens"]["thoughts_tokens"] = total_thoughts_tokens
    total_all_tokens = total_prompt_tokens + total_candidate_tokens + total_thoughts_tokens
    summary["tokens"]["total_tokens"] = total_all_tokens

    cost_usd = (
        (total_prompt_tokens * 2.00)
        + ((total_candidate_tokens + total_thoughts_tokens) * 12.00)
    ) / 1_000_000
    summary["cost_usd"] = round(cost_usd, 6)

    print("\n" + "=" * 60)
    print("MEMORIZATION PROBE RUN SUMMARY")
    print(f"  Probed:           {probed_count}/{len(to_process)}")
    print(f"  Flagged (>= {threshold}): {flagged_count}")
    print(f"  Prompt tokens:    {total_prompt_tokens:,}")
    print(f"  Candidate tokens: {total_candidate_tokens:,}")
    print(f"  Thoughts tokens:  {total_thoughts_tokens:,}")
    print(f"  Total tokens:     {total_all_tokens:,}")
    print(f"  Estimated cost:   ${cost_usd:.4f} USD")
    print("=" * 60 + "\n")

    if probed_count > 0:
        new_gt_bytes = json.dumps(gt_data, indent=2).encode("utf-8")
        new_enc = vault.encrypt_bytes(new_gt_bytes, recipients_path=recipients_path)
        resolved_vault.write_bytes(new_enc)
        print(f"Updated vault {resolved_vault.name} with memorization scores (flagged {flagged_count}).")

    return CurationResult(0, summary)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run title-only memorization probe against T3 GT")
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
        "--model",
        type=str,
        default=DEFAULT_PROBE_MODEL,
        help=f"Model for completion probe (default: {DEFAULT_PROBE_MODEL})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.65,
        help="Jaccard overlap threshold for exclusion (default: 0.65)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate probe without making LLM calls",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of recipes to probe",
    )
    args = parser.parse_args(argv)

    try:
        code, _ = run_memorization_probe(
            vault_file=args.vault_file,
            identity_path=args.identity,
            recipients_path=args.recipients,
            model_name=args.model,
            threshold=args.threshold,
            dry_run=args.dry_run,
            limit=args.limit,
        )
        return code
    except Exception as e:
        sys.stderr.write(f"memorization_probe error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
