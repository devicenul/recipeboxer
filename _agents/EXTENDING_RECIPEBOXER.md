# RecipeBoxer Developer Extension Guide & Integration Playbooks

This guide provides practical, step-by-step tutorials and complete code examples for extending **RecipeBoxer** with new source providers, custom prompt branches, and additional export formats.

---

## 1. Extension Architecture Overview

RecipeBoxer is organized around modular contracts that can be extended without altering the core pipeline state machine:

```
[ EXTENSION AXIS 1 ]           [ EXTENSION AXIS 2 ]           [ EXTENSION AXIS 3 ]
Source Providers               Custom Prompt Branches         Export Serializers
────────────────────           ──────────────────────         ──────────────────
• Google Photos                • Cursive & Shorthand          • Markdown / Obsidian
• S3 / Cloud Storage           • Vintage Clippings            • Cooklang
• Direct URL Batch             • Thinking Budget Tokens       • Paprika / JSON-LD
```

---

## 2. Playbook 1: Adding Ingestion Source Providers

All source providers must ultimately produce validated `ScannedCard` domain dataclasses or write image files to disk for processing.

### The `ScannedCard` Contract
```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ScannedCard:
    card_id: str
    file_path: Path
    filename: str
    sha256_hash: str
    file_size_bytes: int
    width: int
    height: int
    format: str
    mode: str
```

### Implementing a Custom Provider (e.g. `GooglePhotosSourceProvider`)

```python
import hashlib
import io
from pathlib import Path
from typing import List, Optional
from PIL import Image
import httpx
from recipeboxer.ingestion.scanner import ScannedCard

class GooglePhotosSourceProvider:
    """Source provider that ingests scanned recipe cards from Google Photos albums."""

    def __init__(self, access_token: str, cache_dir: Path) -> None:
        self.access_token = access_token
        self.cache_dir = cache_dir / "google_photos"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=30.0,
        )

    async def list_album_media(self, album_id: str, page_size: int = 50) -> List[dict]:
        """Fetch media items from the specified Google Photos album."""
        url = "https://photoslibrary.googleapis.com/v1/mediaItems:search"
        payload = {"albumId": album_id, "pageSize": page_size}
        response = await self.client.post(url, json=payload)
        response.raise_for_status()
        return response.json().get("mediaItems", [])

    async def download_and_validate_card(self, media_item: dict) -> Optional[ScannedCard]:
        """Download image, verify with PIL, calculate SHA-256, and return ScannedCard."""
        media_id = media_item["id"]
        filename = media_item.get("filename", f"{media_id}.jpg")
        download_url = f"{media_item['baseUrl']}=d"  # Request full-resolution download

        response = await self.client.get(download_url)
        if response.status_code != 200:
            return None

        image_bytes = response.content

        # 1. Verify image integrity with PIL
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img.verify()
            with Image.open(io.BytesIO(image_bytes)) as img:
                width, height = img.size
                img_format = img.format or "JPEG"
                img_mode = img.mode or "RGB"
        except Exception:
            return None  # Skip corrupted image

        # 2. Compute streaming SHA-256 hash
        sha256_hash = hashlib.sha256(image_bytes).hexdigest().lower()

        # 3. Save to local cache directory
        destination_path = self.cache_dir / f"{sha256_hash[:16]}_{filename}"
        if not destination_path.exists():
            destination_path.write_bytes(image_bytes)

        # 4. Construct ScannedCard
        return ScannedCard(
            card_id=f"gphoto_{media_id[:12]}",
            file_path=destination_path,
            filename=filename,
            sha256_hash=sha256_hash,
            file_size_bytes=len(image_bytes),
            width=width,
            height=height,
            format=img_format,
            mode=img_mode,
        )

    async def ingest_album(self, album_id: str) -> List[ScannedCard]:
        """Ingest all valid cards from a photo album."""
        media_items = await self.list_album_media(album_id)
        cards: List[ScannedCard] = []
        for item in media_items:
            card = await self.download_and_validate_card(item)
            if card:
                cards.append(card)
        return cards
```

---

## 3. Playbook 2: Custom Prompt Branches & LLM Customization

RecipeBoxer provides three multimodal LLM extension points:

### 1. Modality-Specific System Instructions
You can specialize prompts for vintage cursive, printed clippings, or manual typewriting by subclassing `GeminiRecipeExtractor`:

```python
from typing import Optional
from recipeboxer.extraction.extractor import GeminiRecipeExtractor
from recipeboxer.models.recipe import RecipeSchema

CURSIVE_EXTRACTION_SYSTEM_INSTRUCTION = """
You are a master culinary archivist specializing in transcribing 19th and 20th century handwritten cursive recipe cards.
Pay close attention to archaic abbreviations and measurements:
- "T" or "Tbs" = Tablespoon, "t" or "tsp" = Teaspoon
- "Oleo" = Margarine, "Saleratus" = Baking Soda, "Sweet milk" = Whole milk
- Transcribe cursive fractions carefully (e.g. 1/4 vs 1/2).
- Output must conform strictly to the Recipe JSON schema.
"""

class CursiveRecipeExtractor(GeminiRecipeExtractor):
    """Specialized recipe extractor for handwritten cursive card collections."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        thinking_budget_tokens: int = 2048,
        system_instruction: str = CURSIVE_EXTRACTION_SYSTEM_INSTRUCTION,
    ) -> None:
        super().__init__(
            api_key=api_key,
            thinking_mode=True,
            thinking_budget_tokens=thinking_budget_tokens,
        )
        self.system_instruction = system_instruction

    async def _call_gemini_extraction_api(
        self,
        front_bytes: bytes,
        front_id: str,
        back_bytes: Optional[bytes] = None,
        back_id: Optional[str] = None,
    ) -> RecipeSchema:
        """Execute multimodal JSON extraction call using specialized cursive system instruction."""
        import base64
        import json
        import re
        import httpx

        parts = [{"text": self.system_instruction}]
        if front_bytes:
            parts.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(front_bytes).decode("utf-8"),
                }
            })
            parts.append({"text": f"Image A (Front Card): {front_id}"})
        if back_bytes:
            parts.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(back_bytes).decode("utf-8"),
                }
            })
            parts.append({"text": f"Image B (Back Card): {back_id}"})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
        gen_config = {
            "response_mime_type": "application/json",
            "temperature": 0.1,
        }
        if self.thinking_mode and self.thinking_budget_tokens:
            gen_config["thinking_config"] = {"thinking_budget": self.thinking_budget_tokens}

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json={"contents": [{"parts": parts}], "generationConfig": gen_config})
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            cleaned_text = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.IGNORECASE)
            cleaned_text = re.sub(r"\s*```$", "", cleaned_text.strip()).strip()
            return RecipeSchema.model_validate(json.loads(cleaned_text))
```

### 2. Customizing Heuristic and Semantic Weights
The relative balance between physical heuristics and semantic continuity reasoning can be customized in `Settings` or when initializing `PairingOptimizer`:

```python
from recipeboxer.correlation.optimizer import PairingOptimizer
from recipeboxer.correlation.semantic import GeminiSemanticVerifier

# 1. Customize heuristic vs semantic weights in Stage 2 verifier
custom_verifier = GeminiSemanticVerifier(
    heuristic_weight=0.20,  # 20% weight on physical features
    semantic_weight=0.80,   # 80% weight on Gemini semantic continuity
)

# 2. Pass custom verifier and minimum pair confidence threshold to optimizer
optimizer = PairingOptimizer(
    min_pair_confidence=0.55,
    verifier=custom_verifier,
)
```

---

## 4. Playbook 3: Adding New Export Format Serializers

All serializers should implement the standard exporter interface:
- `export_to_string(recipes)` $\rightarrow$ `str`
- `export_to_file(recipes, path)` $\rightarrow$ `Path`
- `export_to_bytes(recipes)` $\rightarrow$ `bytes`
- `validate_export(content)` $\rightarrow$ `bool`

### 4.1 Backend Implementation: Markdown / Obsidian Exporter

Create `recipeboxer/exporter/markdown_exporter.py`:

```python
from pathlib import Path
from typing import List, Union
from recipeboxer.models.recipe import RecipeSchema

class MarkdownRecipeExporter:
    """Exports RecipeSchema objects into Obsidian-compatible Markdown files with YAML frontmatter."""

    def __init__(self, encoding: str = "utf-8") -> None:
        self.encoding = encoding

    def _format_single_recipe(self, recipe: RecipeSchema) -> str:
        tags_yaml = "\n".join(f"  - {t}" for t in recipe.tags) if recipe.tags else "  - Recipes"
        ingredients_md = "\n".join(f"- {ing}" for ing in recipe.ingredients)
        directions_md = "\n".join(f"{idx}. {step}" for idx, step in enumerate(recipe.directions, start=1))

        return f"""---
title: "{recipe.title.replace('"', '\\"')}"
prep_time: "{recipe.prep_time or ''}"
cook_time: "{recipe.cook_time or ''}"
total_time: "{recipe.total_time or ''}"
yield: "{recipe.yield_servings or ''}"
tags:
{tags_yaml}
source: "{recipe.source_notes or 'Digitized by RecipeBoxer'}"
---

# {recipe.title}

{recipe.description or ''}

## Ingredients
{ingredients_md}

## Directions
{directions_md}
"""

    def export_to_string(self, recipes: Union[RecipeSchema, List[RecipeSchema]]) -> str:
        recipe_list = [recipes] if isinstance(recipes, RecipeSchema) else recipes
        return "\n\n---\n\n".join(self._format_single_recipe(r) for r in recipe_list)

    def export_to_file(self, recipes: Union[RecipeSchema, List[RecipeSchema]], output_path: Union[str, Path]) -> Path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        content = self.export_to_string(recipes)
        out.write_text(content, encoding=self.encoding)
        return out

    def export_to_bytes(self, recipes: Union[RecipeSchema, List[RecipeSchema]]) -> bytes:
        return self.export_to_string(recipes).encode(self.encoding)

    def validate_export(self, content: str) -> bool:
        """Verify that output contains frontmatter headers and essential sections."""
        return "---" in content and "# " in content and "## Ingredients" in content
```

### 4.2 FastAPI REST Endpoint Integration
In `recipeboxer/api/routes.py`, register the download route:

```python
@router.get("/export/markdown", response_class=Response)
async def export_recipes_markdown(
    session_id: Optional[str] = None,
    store: SessionStore = Depends(get_session_store),
):
    recipes = await store.alist_recipes(session_id)
    exporter = MarkdownRecipeExporter()
    md_content = exporter.export_to_string(recipes)

    return Response(
        content=md_content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="recipes_{session_id or "export"}.md"'},
    )
```

### 4.3 Frontend Integration

1. Create `frontend/src/utils/exportMarkdown.ts`:
```typescript
import { RecipeSchema } from '../types/recipe';

export function generateMarkdownExport(recipes: RecipeSchema[]): string {
  return recipes.map(r => {
    const tags = r.tags?.map(t => `  - ${t}`).join('\n') || '  - Recipes';
    const ings = r.ingredients.map(i => `- ${i}`).join('\n');
    const dirs = r.directions.map((d, i) => `${i + 1}. ${d}`).join('\n');

    return `---
title: "${r.title.replace(/"/g, '\\"')}"
prep_time: "${r.prep_time || ''}"
cook_time: "${r.cook_time || ''}"
total_time: "${r.total_time || ''}"
yield: "${r.yield_servings || ''}"
tags:
${tags}
---

# ${r.title}

${r.description || ''}

## Ingredients
${ings}

## Directions
${dirs}
`;
  }).join('\n\n---\n\n');
}
```

2. Register the Markdown tab in `frontend/src/components/export/ExportDialog.tsx`.
