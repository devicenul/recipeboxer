"""Text-Only Ground-Truth Structured Recipe Drafter.

Drafts Schema v2 structured ground truth for T3 evaluation recipes from verbatim
human transcriptions. In accordance with Remediation Plan §3.3:
- TEXT ONLY: Never sends or references the image; prevents model's visual bias from
  contaminating the ground truth.
- INDEPENDENT MODEL TIER: Uses a model tier distinct from the production extractor
  (e.g., gemini-2.5-pro when extractor uses flash) to reduce self-preference bias.
- OFFLINE-SAFE: When no API key is available, cleanly reports pending status without error.
- RE-ENCRYPTION: Decrypts in memory and re-encrypts to evals/vault/t3.gt.enc. Plaintext GT
  never touches disk in the workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from evals import vault

DEFAULT_DRAFT_MODEL = "gemini-2.5-pro"
ENV_API_KEY = "GEMINI_API_KEY"
ENV_RECIPEBOXER_KEY = "RECIPEBOXER_GEMINI_API_KEY"


def get_api_key() -> Optional[str]:
    """Retrieve Gemini API key from environment if configured."""
    return os.environ.get(ENV_API_KEY) or os.environ.get(ENV_RECIPEBOXER_KEY)


def draft_structured_prompt(verbatim_faces: List[str]) -> str:
    """Construct text-only structuring prompt from verbatim transcriptions."""
    combined_transcript = "\n\n--- NEXT CARD FACE ---\n\n".join(verbatim_faces)
    return (
        "You are an expert recipe archival curator. Convert the following verbatim historical "
        "recipe transcription into a structured RecipeSchema JSON object.\n\n"
        "RULES:\n"
        "1. Extract strictly what is present in the text. Do not invent missing quantities or steps.\n"
        "2. Parse ingredients into structured objects: raw, quantity, unit, item, preparation, section.\n"
        "3. Parse directions into sequential step strings.\n"
        "4. Separate marginalia, personal notes, or headnotes into the notes list.\n"
        "5. Output valid JSON matching Schema v2.\n\n"
        f"TRANSCRIPTION:\n{combined_transcript}\n\n"
        "OUTPUT JSON ONLY:"
    )


def draft_structured_gt(
    vault_file: Path,
    identity_path: Optional[Path] = None,
    recipients_path: Optional[Path] = None,
    model_name: str = DEFAULT_DRAFT_MODEL,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> int:
    """Draft structured GT for recipes with pending status."""
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
            if (
                recipe.get("structured") is None
                or recipe.get("structured_status") == "pending_text_only_draft"
            ):
                pending_recipes.append((deck, recipe))

    total_pending = len(pending_recipes)
    print(f"Found {total_pending} recipe(s) pending structured drafting in {resolved_vault.name}.")

    if total_pending == 0:
        print("All recipes already have structured ground truth.")
        return 0

    api_key = get_api_key()
    if not api_key:
        print(
            "\n[NOTICE] No Gemini API key detected in environment ($GEMINI_API_KEY / $RECIPEBOXER_GEMINI_API_KEY).\n"
            "Structured GT drafting is deferred until a live API key is provided.\n"
            f"All {total_pending} items remain marked as 'pending_text_only_draft'.\n"
            f"When a key is configured, re-run with: python -m evals.curation_t3.draft_structured --model-tier {model_name}"
        )
        return 0

    if dry_run:
        print(f"[DRY-RUN] Would draft {total_pending} recipes using text-only model '{model_name}'.")
        return 0

    # Attempt to use google-genai SDK if available
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except ImportError:
        try:
            from recipeboxer.ai.client import GenAIClient
            client = GenAIClient(api_key=api_key)
        except Exception as e:
            sys.stderr.write(
                f"Could not initialize GenAI client: {e}. Ensure google-genai is installed.\n"
            )
            return 1

    drafted_count = 0
    to_process = pending_recipes[:limit] if limit else pending_recipes

    for deck, recipe in to_process:
        rid = recipe.get("recipe_id", "unknown")
        verbatim_faces = recipe.get("verbatim", [])
        prompt = draft_structured_prompt(verbatim_faces)

        try:
            # TEXT-ONLY prompt: never send the image!
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            resp_text = response.text or ""
            # Strip markdown code blocks if any
            clean_json = re.sub(r"^```json\s*", "", resp_text.strip(), flags=re.I)
            clean_json = re.sub(r"\s*```$", "", clean_json.strip())
            parsed_structured = json.loads(clean_json)

            recipe["structured"] = parsed_structured
            recipe["structured_status"] = "drafted"
            recipe["structured_model"] = model_name
            drafted_count += 1
            print(f"Successfully drafted structured GT for {rid} using {model_name}")
        except Exception as e:
            sys.stderr.write(f"Failed drafting structured GT for {rid}: {e}\n")

    if drafted_count > 0:
        # Re-encrypt updated GT in memory
        new_gt_bytes = json.dumps(gt_data, indent=2).encode("utf-8")
        new_enc = vault.encrypt_bytes(new_gt_bytes, recipients_path=recipients_path)
        resolved_vault.write_bytes(new_enc)
        print(f"Re-encrypted {resolved_vault.name} with {drafted_count} drafted recipe(s).")

    return 0


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
        "--limit",
        type=int,
        default=None,
        help="Limit number of recipes to draft in this run",
    )
    args = parser.parse_args(argv)

    try:
        return draft_structured_gt(
            vault_file=args.vault_file,
            identity_path=args.identity,
            recipients_path=args.recipients,
            model_name=args.model_tier,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    except Exception as e:
        sys.stderr.write(f"draft_structured error: {e}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
