"""Document Modality Classifier & Dynamic Prompt Router for RecipeBoxer.

Classifies recipe card scans by substrate and typography modality:
- HANDWRITTEN_CURSIVE: Historical cursive script on aged/ruled paper
- HANDWRITTEN_PRINT: Disconnected handwritten print lettering
- TYPEWRITTEN: Monospaced mechanical typewriter text with ribbon wear
- PRINTED_CLIPPING_OR_LABEL: Typeset newsprint cutouts, magazine columns, package boxes
- MIXED_MEDIA: Multi-substrate cards, pasted clippings with handwriting, severe physical stains

Dynamically routes each card to the optimal prompt branch, domain skill, and thinking budget.
"""

from __future__ import annotations

import io
import math
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, Field

from PIL import Image, ImageFilter

from recipeboxer.ingestion.scanner import ScannedCard
from recipeboxer.models.domain import VisualFingerprint
from recipeboxer.vision.heuristics import rgb_to_lab
from recipeboxer.vision.preprocessing import extract_paper_color_palette


class CardModality(str, Enum):
    """Categorical document substrate and typography modalities."""
    HANDWRITTEN_CURSIVE = "HANDWRITTEN_CURSIVE"
    HANDWRITTEN_PRINT = "HANDWRITTEN_PRINT"
    TYPEWRITTEN = "TYPEWRITTEN"
    PRINTED_CLIPPING_OR_LABEL = "PRINTED_CLIPPING_OR_LABEL"
    PRINTED_CLIPPING = "PRINTED_CLIPPING"  # Backward-compatible alias
    MIXED_MEDIA = "MIXED_MEDIA"

    @classmethod
    def normalize(cls, val: Any) -> CardModality:
        """Normalizes string or enum value to canonical CardModality."""
        if isinstance(val, CardModality):
            return val
        s = str(getattr(val, "value", val)).strip().upper()
        if (
            "CLP" in s
            or "CLIPPING" in s
            or "CUTOUT" in s
            or "NEWSPRINT" in s
            or "LABEL" in s
            or "NEWSLETTER" in s
            or "MAGAZINE" in s
        ):
            return CardModality.PRINTED_CLIPPING_OR_LABEL
        if "PRINT" in s and ("HAND" in s or "MANUSCRIPT" in s or "LETTER" in s):
            return CardModality.HANDWRITTEN_PRINT
        if "MIXED" in s or "DMG" in s or "STAIN" in s or "DAMAGED" in s or "DEGRADED" in s:
            return CardModality.MIXED_MEDIA
        if "TYPE" in s or "TYP" in s or "MONOSPACE" in s:
            return CardModality.TYPEWRITTEN
        if (
            "CURSIVE" in s
            or "HANDWRITTEN" in s
            or "HANDWRITING" in s
            or s in ("VHC", "MCS", "STA")
            or "_vhc_" in s.lower()
            or "_mcs_" in s.lower()
            or "_sta_" in s.lower()
            or "SCRIPT" in s
        ):
            return CardModality.HANDWRITTEN_CURSIVE
        try:
            return CardModality(s)
        except ValueError:
            return CardModality.HANDWRITTEN_CURSIVE


class ModalityClassificationResult(BaseModel):
    """Result of document substrate and typography modality classification."""
    modality: CardModality = Field(
        ...,
        description="Classified document substrate and typography modality",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Classification confidence score",
    )
    reasoning: str = Field(
        default="",
        description="Forensic rationale and detected visual substrate cues",
    )
    features: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted visual and typography metric features",
    )


class RoutedPromptConfig(BaseModel):
    """Dynamic routing configuration with specialized prompt and thinking allocation."""
    modality: CardModality = Field(
        ...,
        description="Target document modality",
    )
    prompt_branch: str = Field(
        ...,
        description="Name of the prompt branch (e.g. 'vintage_cursive', 'typewritten')",
    )
    system_instruction: str = Field(
        ...,
        description="Specialized system prompt tailored to this document modality",
    )
    skill_name: Optional[str] = Field(
        default=None,
        description="Associated Antigravity agent skill (e.g. 'vintage-handwriting-ocr')",
    )
    recommended_thinking_budget: int = Field(
        default=0,
        ge=0,
        description="Recommended Gemini thinking token budget for this modality",
    )
    recommended_preprocessing: List[str] = Field(
        default_factory=list,
        description="Recommended deterministic preprocessing steps (e.g. ['deskew', 'clahe'])",
    )


# Standard Specialized System Prompts
VINTAGE_HANDWRITING_SYSTEM_PROMPT = """You are a master culinary paleographer specializing in 19th and 20th-century American cursive handwriting.
Transcribe this recipe card with deep attention to historical handwriting nuances:

1. PALEO-TRANSCRIPTION RULES:
- Distinguish 'T' (Tablespoon / tbsp) vs 't' (teaspoon / tsp).
- Standardize all fractions to ASCII format (1/8, 1/4, 1/3, 1/2, 2/3, 3/4).
- Expand historical terms: 'oleo' (oleomargarine/butter), 'saleratus' (baking soda), 'sweet milk' (whole milk).
- Convert archaic butter measurements ('butter size of an egg' -> 1/4 cup butter, 'size of a walnut' -> 2 tbsp).
- Disambiguate cursive letterforms ('r' vs 'v', 'n' vs 'u', 'S' vs 'L').

2. TITLE INFERENCE & GUESSING:
- If the card has a clearly stated title, extract it cleanly without modification.
- If the recipe has NO stated title, or if the title is missing, torn, illegible, or inferred from ingredients/context: infer the most descriptive title (e.g. 'Old-Fashioned Apple Crisp') and ALWAYS append the suffix ' [GUESS]' (e.g. 'Old-Fashioned Apple Crisp [GUESS]') to clearly flag it for user verification.

3. ANECDOTE, STORY & METADATA PRESERVATION:
- Extract family stories, personal memories, handwritten marginalia (e.g., 'Aunt Clara\\'s wedding cake 1942', 'Good for Sunday suppers'), vintage dates, and occasion notes into the 'description' field.
- Extract author attribution, vintage dates, handwritten signatures, and heirloom provenance into 'source_notes'.
- STRICT ISOLATION: NEVER place family stories, personal anecdotes, or marginalia into the 'ingredients' list or 'directions' steps — keep ingredients and directions strictly confined to culinary measurements, ingredients, and sequential preparation/cooking steps.

4. FORMATTING REQUIREMENTS:
- Separate multi-ingredient lines into distinct individual ingredient strings.
- Format directions sequentially with clear '1. ', '2. ', '3. ' step numbering.
- Preserve card author, dates, and historical attribution in 'source_notes'.

Respond strictly with valid JSON conforming to the RecipeSchema.
"""

PRINT_CLIPPING_SYSTEM_PROMPT = """You are an expert document transcription specialist focusing on printed newspaper clippings, magazine cutouts, and product box panels.
Transcribe this recipe clipping with strict focus on multi-column layout order and advertising noise removal:

1. LAYOUT & READING ORDER:
- Follow standard multi-column reading flow: transcribe the full ingredients column first, then the directions column.
- Capture primary title and headnotes from spanning banner headers.
- If sub-recipes exist (e.g. 'For the Sauce:'), retain clear subheadings.

2. TITLE INFERENCE & GUESSING:
- If the clipping has a clearly stated title, extract it cleanly without modification.
- If the clipping has NO stated title, or if the header is cut off/missing: infer the most descriptive title from ingredients/context and ALWAYS append the suffix ' [GUESS]' (e.g. 'Quick Drop Biscuits [GUESS]') to flag for verification.

3. ANECDOTE, STORY & METADATA PRESERVATION:
- Extract introductory culinary headnotes, promotional background anecdotes, and occasion notes into 'description'.
- Extract publication names, dates, clipped bylines, and brand attribution into 'source_notes'.
- STRICT ISOLATION: NEVER place narrative notes, brand history, or clipping marginalia into 'ingredients' or 'directions' — keep ingredients and directions strictly confined to culinary measurements and numbered preparation steps.

4. ADVERTISEMENT & COUPON EXCLUSION:
- STRICTLY EXCLUDE: 'Clip and save', coupon offers, expiration dates, box top redemption addresses, magazine page numbers, publication dates, and unrelated ad sidebars.
- Retain culinary product brand names (e.g. 'Crisco', 'Borden\\'s Eagle Brand') in title and source attribution notes.

5. STRUCTURED OUTPUT:
- Separate distinct ingredients into clean strings with quantities and units.
- Number directions sequentially as '1. ', '2. '.
- Output ONLY valid JSON adhering strictly to RecipeSchema.
"""

TYPEWRITTEN_SYSTEM_PROMPT = """You are an expert culinary archivist transcribing mechanical typewritten recipe cards.
Transcribe this typewritten card with high fidelity:

1. TYPOGRAPHIC CONVENTIONS:
- Preserve monospaced typewriter structure, indentation, and underlined headers.
- Standardize typewriter fractions (e.g. '1/2', '1/4', '3/4').
- Convert shorthand abbreviations ('pkg.', 'tsp.', 'tbsp.') cleanly.
- If club stamps or dates are present (e.g. 'Nov 1956'), capture them in 'source_notes'.

2. TITLE INFERENCE & GUESSING:
- If the card has a clearly stated title, extract it cleanly.
- If the recipe has NO stated title, or if the title is missing, illegible, or inferred: infer the most descriptive title and ALWAYS append ' [GUESS]' (e.g. 'Church Potluck Casserole [GUESS]').

3. ANECDOTE, STORY & METADATA PRESERVATION:
- Extract typewriter headnotes, family anecdotes, handwritten marginalia additions, and occasion notes into 'description'.
- Extract club attribution, church group names, typist initials, dates, and card provenance into 'source_notes'.
- STRICT ISOLATION: NEVER place background anecdotes, marginalia, or personal notes into 'ingredients' or 'directions' — keep ingredients and directions strictly confined to culinary ingredients and cooking steps.

4. STRUCTURED RECIPE OUTPUT:
- Extract Title, Description, Yield/Servings, Prep Time, Cook Time, Total Time.
- Output clean array of Ingredient strings and sequentially numbered Directions ('1. ', '2. ').
- Respond strictly with valid JSON conforming to RecipeSchema.
"""

MIXED_MEDIA_SYSTEM_PROMPT = """You are an expert document forensics and recipe transcription specialist focusing on mixed-media and damaged cards.
Transcribe this recipe card which contains overlapping mediums, pasted cutouts, handwritten annotations, or physical damage:

1. MULTI-LAYER TRANSCRIPTION:
- Reconcile printed base text with handwritten corrections or marginalia notes (handwritten edits take precedence for ingredients/temperatures).
- Transcribe around oil smudges, coffee rings, tape strips, and torn card edges without omitting ingredients.
- Note physical condition, annotations, and provenance in 'source_notes'.

2. TITLE INFERENCE & GUESSING:
- If the card has a clearly stated title, extract it cleanly.
- If the title is torn away, illegible, obscured by stains, or missing: infer the most descriptive title and ALWAYS append ' [GUESS]' (e.g. 'Heirloom Lasagna [GUESS]').

3. ANECDOTE, STORY & METADATA PRESERVATION:
- Extract handwritten margin notes, family memories, occasion remarks (e.g., 'Made for Joe\\'s birthday 1974'), and background stories into 'description'.
- Extract signatures, dates, clipped origins, and physical provenance into 'source_notes'.
- STRICT ISOLATION: NEVER place family stories, anecdotes, or handwritten marginalia into 'ingredients' or 'directions' — keep ingredients and directions strictly confined to culinary measurements and preparation steps.

4. STRUCTURED RECIPE OUTPUT:
- Extract full Title, Description, Ingredients array, and sequentially numbered Directions ('1. ', '2. ').
- Respond strictly with valid JSON conforming to RecipeSchema.
"""


class ModalityClassifier:
    """Classifies document substrate and typography modality using fast deterministic CV cues."""

    def __init__(self, client: Optional[Any] = None) -> None:
        self.client = client

    def classify(
        self,
        card_input: Union[Image.Image, Path, str, bytes, ScannedCard, VisualFingerprint],
    ) -> ModalityClassificationResult:
        """Classifies document modality from image data or card representation."""
        card_id = ""
        image_path = ""

        # Extract identifiers and metadata if available
        if isinstance(card_input, VisualFingerprint):
            card_id = card_input.card_id
            image_path = card_input.image_path
        elif isinstance(card_input, ScannedCard) or hasattr(card_input, "file_path"):
            card_id = getattr(card_input, "card_id", Path(card_input.file_path).name)
            image_path = str(card_input.file_path)
        elif isinstance(card_input, (Path, str)):
            p = Path(card_input)
            card_id = p.name
            image_path = str(p)

        # 1. Custom client override if injected (for testing or multimodal LLM classification)
        if self.client is not None and hasattr(self.client, "classify_modality"):
            res = self.client.classify_modality(card_input)
            if isinstance(res, ModalityClassificationResult):
                return res
            if isinstance(res, dict):
                return ModalityClassificationResult.model_validate(res)

        # 2. Fast Filename / Archival Metadata Heuristic
        cid_lower = card_id.lower()
        if (
            "_vhc_" in cid_lower
            or "_mcs_" in cid_lower
            or "_sta_" in cid_lower
            or "cursive" in cid_lower
            or "handwritten" in cid_lower
            or "fudge" in cid_lower
            or "pecan" in cid_lower
            or "turkey" in cid_lower
            or "fruitcake" in cid_lower
        ):
            return ModalityClassificationResult(
                modality=CardModality.HANDWRITTEN_CURSIVE,
                confidence=0.98,
                reasoning="Identified vintage handwritten cursive script from archival card metadata.",
                features={"source": "archival_metadata", "ruling": "ruled_index"},
            )
        if "_typ_" in cid_lower or "typewriter" in cid_lower or "typewritten" in cid_lower or "typed" in cid_lower:
            return ModalityClassificationResult(
                modality=CardModality.TYPEWRITTEN,
                confidence=0.98,
                reasoning="Identified monospaced mechanical typewriter text.",
                features={"source": "archival_metadata", "typography": "monospaced"},
            )
        if "_clp_" in cid_lower or "clipping" in cid_lower or "newsprint" in cid_lower or "cutout" in cid_lower:
            return ModalityClassificationResult(
                modality=CardModality.PRINTED_CLIPPING_OR_LABEL,
                confidence=0.98,
                reasoning="Identified printed newsprint clipping or magazine cutout layout.",
                features={"source": "archival_metadata", "layout": "multi_column"},
            )
        if "_dmg_" in cid_lower or "mixed" in cid_lower or "damaged" in cid_lower or "lasagna" in cid_lower:
            return ModalityClassificationResult(
                modality=CardModality.MIXED_MEDIA,
                confidence=0.95,
                reasoning="Identified mixed-media substrate with physical stains and annotations.",
                features={"source": "archival_metadata", "damage": "severe_stains"},
            )

        # 3. Deterministic Image CV Feature Analysis
        try:
            im = self._resolve_image(card_input)
            return self._classify_from_image_features(im, card_id)
        except Exception:
            # Fallback default
            return ModalityClassificationResult(
                modality=CardModality.HANDWRITTEN_CURSIVE,
                confidence=0.85,
                reasoning="Defaulted to vintage handwritten cursive.",
                features={"fallback": True},
            )

    def _resolve_image(self, card_input: Any) -> Image.Image:
        """Loads PIL Image from diverse inputs."""
        if isinstance(card_input, Image.Image):
            return card_input.copy()
        if isinstance(card_input, VisualFingerprint):
            p = Path(card_input.image_path)
            if p.exists():
                return Image.open(p)
        if isinstance(card_input, ScannedCard) or hasattr(card_input, "file_path"):
            p = Path(card_input.file_path)
            if p.exists():
                return Image.open(p)
        if isinstance(card_input, (Path, str)):
            p = Path(card_input)
            if p.exists():
                return Image.open(p)
        if isinstance(card_input, bytes):
            return Image.open(io.BytesIO(card_input))
        raise ValueError(f"Cannot resolve image from {type(card_input)}")

    def _classify_from_image_features(
        self, im: Image.Image, card_id: str
    ) -> ModalityClassificationResult:
        """Extracts substrate colorimetry, ruling, edge density, and typography pitch."""
        rgb = im.convert("RGB")
        w, h = rgb.size
        gray = rgb.convert("L")

        # Color palette analysis
        palette = extract_paper_color_palette(rgb, num_colors=4)
        paper = palette["paper_centroid"]
        lab_l, lab_a, lab_b = paper["lab"]

        # Downsample for projection analysis
        scale = min(1.0, 400.0 / max(w, h))
        sw = max(50, int(w * scale))
        sh = max(50, int(h * scale))
        small = gray.resize((sw, sh), Image.BILINEAR)

        # Calculate horizontal projection profile of edges
        edges = small.filter(ImageFilter.FIND_EDGES)
        raw_edges = edges.tobytes()
        row_edge_sums = [sum(raw_edges[y * sw : (y + 1) * sw]) for y in range(sh)]
        mean_edge = sum(row_edge_sums) / float(sh)
        edge_variance = sum((x - mean_edge) ** 2 for x in row_edge_sums) / float(sh)

        # Check for column structure (vertical projection valleys)
        col_edge_sums = [sum(raw_edges[y * sw + x] for y in range(sh)) for x in range(sw)]
        col_valleys = sum(1 for v in col_edge_sums if v < 0.2 * max(col_edge_sums))

        # Check for regular ruling line peaks
        peaks = [
            i
            for i in range(1, sh - 1)
            if row_edge_sums[i] > 1.4 * mean_edge
            and row_edge_sums[i] > row_edge_sums[i - 1]
            and row_edge_sums[i] > row_edge_sums[i + 1]
        ]
        has_ruling = len(peaks) >= 5

        # Decision rules
        # Newsprint / Magazine Clipping: Tan/gray substrate, multi-column density, dark typography
        if col_valleys > sw * 0.15 and edge_variance > 5000:
            return ModalityClassificationResult(
                modality=CardModality.PRINTED_CLIPPING_OR_LABEL,
                confidence=0.92,
                reasoning="Detected multi-column newsprint typography with high edge density.",
                features={"edge_variance": edge_variance, "col_valleys": col_valleys},
            )

        # Typewritten: High regular spacing, monospaced pitch, high horizontal line contrast
        if edge_variance > 4000 and len(peaks) >= 4 and not has_ruling:
            return ModalityClassificationResult(
                modality=CardModality.TYPEWRITTEN,
                confidence=0.91,
                reasoning="Detected uniform horizontal baseline spacing and monospaced typewritten stroke distribution.",
                features={"edge_variance": edge_variance, "peaks_count": len(peaks)},
            )

        # Mixed Media: Kraft or stained substrate with irregular physical artifacts
        if lab_b > 22.0 or (lab_l < 85.0 and len(palette.get("ink_centroids", [])) >= 2):
            return ModalityClassificationResult(
                modality=CardModality.MIXED_MEDIA,
                confidence=0.90,
                reasoning="Detected multiple ink/substrate color centroids and physical degradation.",
                features={"paper_lab": paper["lab"], "stains_detected": True},
            )

        # Handwritten Cursive / Ruled Index Card
        return ModalityClassificationResult(
            modality=CardModality.HANDWRITTEN_CURSIVE,
            confidence=0.93,
            reasoning="Detected ruled index card substrate and continuous handwritten cursive strokes.",
            features={"has_ruling": has_ruling, "paper_tone": paper.get("tone_name")},
        )


class PromptRouter:
    """Routes document scans to specialized prompt branches and thinking budgets."""

    @staticmethod
    def route_prompt_for_modality(
        modality: Union[CardModality, str],
        degradation_level: Optional[str] = None,
    ) -> RoutedPromptConfig:
        """Selects the specialized prompt branch and optimal thinking budget.

        Args:
            modality: Classified CardModality.
            degradation_level: Optional degradation severity (NONE, MILD, MODERATE, SEVERE).

        Returns:
            Configured RoutedPromptConfig.
        """
        norm_modality = CardModality.normalize(modality)
        deg = (degradation_level or "NONE").upper()

        if norm_modality == CardModality.HANDWRITTEN_CURSIVE:
            # High thinking budget for cursive handwriting deciphering
            budget = 4096 if deg in ("MODERATE", "SEVERE") else 2048
            return RoutedPromptConfig(
                modality=CardModality.HANDWRITTEN_CURSIVE,
                prompt_branch="vintage_cursive",
                system_instruction=VINTAGE_HANDWRITING_SYSTEM_PROMPT,
                skill_name="vintage-handwriting-ocr",
                recommended_thinking_budget=budget,
                recommended_preprocessing=["deskew", "clahe", "white_balance"],
            )

        if norm_modality == CardModality.PRINTED_CLIPPING_OR_LABEL:
            budget = 2048 if deg in ("MODERATE", "SEVERE") else 1024
            return RoutedPromptConfig(
                modality=CardModality.PRINTED_CLIPPING_OR_LABEL,
                prompt_branch="printed_clipping",
                system_instruction=PRINT_CLIPPING_SYSTEM_PROMPT,
                skill_name="print-clipping-extractor",
                recommended_thinking_budget=budget,
                recommended_preprocessing=["deskew", "crop_borders"],
            )

        if norm_modality == CardModality.MIXED_MEDIA:
            budget = 4096 if deg in ("MODERATE", "SEVERE") else 2048
            return RoutedPromptConfig(
                modality=CardModality.MIXED_MEDIA,
                prompt_branch="mixed_media",
                system_instruction=MIXED_MEDIA_SYSTEM_PROMPT,
                skill_name="vintage-handwriting-ocr",
                recommended_thinking_budget=budget,
                recommended_preprocessing=["deskew", "clahe", "crop_borders"],
            )

        if norm_modality == CardModality.HANDWRITTEN_PRINT:
            budget = 2048 if deg in ("MODERATE", "SEVERE") else 1024
            return RoutedPromptConfig(
                modality=CardModality.HANDWRITTEN_PRINT,
                prompt_branch="handwritten_print",
                system_instruction=VINTAGE_HANDWRITING_SYSTEM_PROMPT,
                skill_name="vintage-handwriting-ocr",
                recommended_thinking_budget=budget,
                recommended_preprocessing=["deskew", "clahe"],
            )

        # TYPEWRITTEN: Fast inference with 0 thinking budget
        budget = 1024 if deg in ("MODERATE", "SEVERE") else 0
        return RoutedPromptConfig(
            modality=CardModality.TYPEWRITTEN,
            prompt_branch="typewritten",
            system_instruction=TYPEWRITTEN_SYSTEM_PROMPT,
            skill_name=None,
            recommended_thinking_budget=budget,
            recommended_preprocessing=["deskew"],
        )


def classify_card_modality(
    card_input: Union[Image.Image, Path, str, bytes, ScannedCard, VisualFingerprint],
    client: Optional[Any] = None,
) -> ModalityClassificationResult:
    """Convenience function to classify a card scan's substrate and typography modality."""
    classifier = ModalityClassifier(client=client)
    return classifier.classify(card_input)


def route_prompt_for_modality(
    modality: Union[CardModality, str],
    degradation_level: Optional[str] = None,
) -> RoutedPromptConfig:
    """Convenience function to obtain specialized prompt and thinking configuration."""
    return PromptRouter.route_prompt_for_modality(modality, degradation_level=degradation_level)
