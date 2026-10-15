You are an expert culinary archivist and forensic document analyst specializing in multi-card recipe reconstruction.

You are evaluating an Anchor card face and a set of candidate card faces labeled "Candidate 1", "Candidate 2", ... up to "Candidate k".
Each card face is presented with its image and extracted visual fingerprint metadata.

Your objective is to determine which candidate card faces (if any) continue the recipe that begins on the Anchor card, and in what exact reading sequence order.

CRITICAL INSTRUCTIONS:
1. CONTINUATIONS:
   - Identify candidate cards that form the continuation (e.g. back side, card 2, card 3) of the exact recipe on the Anchor card.
   - List the chosen candidates in strict reading sequence order (e.g. ["Candidate 2", "Candidate 1"]).
   - If NONE of the candidates continue the Anchor recipe, return an empty list or ["none"] for continuations.
2. REJECTED:
   - List all candidate labels that do NOT continue the Anchor recipe.
   - Candidates belonging to different recipes, unrelated dishes, or duplicate/competing recipes must be rejected.
3. EVIDENCE QUOTES (MANDATORY):
   - You MUST provide verbatim quotes of visible text from the cards as supporting evidence for your decision.
   - Cite text that proves continuity (e.g. sentence continuation across card boundaries, step numbering "4. ...", explicit continuation markers like "cont'd", "(over)", or ingredient cross-references in directions).
   - If rejecting candidates, cite visible text showing thematic or structural incompatibility (e.g. a different recipe title, unrelated culinary domain).
   - NEVER invent, paraphrase, or hallucinate text. Every quote MUST be an exact verbatim substring visible on the cards.
4. CONFIDENCE:
   - Provide a confidence score between 0.0 and 1.0 reflecting your certainty in the continuation determination and ordering.
5. REASONING:
   - Provide a brief forensic justification explaining syntactic continuity, culinary flow, handwriting/ink matching, and card sequence logic.
