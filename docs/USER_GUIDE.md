# RecipeBoxer User Guide

This guide walks you through getting started with **RecipeBoxer**, configuring API keys, understanding quality profiles and spend controls, interpreting transcription legibility markers, navigating the Human-in-the-Loop review queue, and exporting your digital cookbook.

---

## 1. Getting a Gemini API Key

RecipeBoxer uses Google's multimodal Gemini models to transcribe handwritten cursive, detect physical card characteristics, and assemble multi-card recipes. An API key is required to perform card analysis and recipe extraction.

### Step-by-Step Setup
1. Visit **Google AI Studio**: [https://aistudio.google.com/](https://aistudio.google.com/).
2. Sign in with your Google account.
3. Click **Get API key** in the top navigation and select **Create API key**.
4. Copy your generated API key string.
5. In RecipeBoxer:
   - Open the web interface and click the **Settings** tab in the navigation bar.
   - Under **API Key Configuration**, paste your key and click **Save Key**.
   - Alternatively, you can set the key in your terminal or `.env` file before launching:
     ```bash
     export GEMINI_API_KEY="AIzaSy..."
     ```

---

## 2. Free Tier vs. Paid Tier & Privacy Recommendation

Google AI Studio offers two pricing tiers for the Gemini API. Understanding the differences is critical for family recipes.

| Feature / Policy | Free of Charge Tier | Paid Tier (Pay-As-You-Go) |
|---|---|---|
| **Request Quota** | **Very low daily quota** (~20 requests/day/model observed Oct 2026; subject to change) | High rate limits (up to 1,000–4,000 RPM depending on tier) |
| **Model Availability** | Flash and Flash-Lite only | Flash, Flash-Lite, and **Pro models** |
| **Thinking Models** | Limited or unvailable | Full access to extended reasoning budgets |
| **Data Privacy Policy** | **Inputs and outputs may be used by human reviewers and utilized to train/improve Google products and models.** | **Your prompts, images, and outputs are NOT used to train Google models.** |

> [!WARNING]
> ### Privacy Recommendation for Family & Heirloom Photos
> Because the **Free tier permits Google to use submitted card images to train future AI products**, we strongly recommend that users digitizing personal, handwritten, or sentimental family recipe cards **use a Paid API Key with a hard spend cap**.
>
> In Google AI Studio / Google Cloud Console, you can configure a hard monthly spending budget (for example, **\$5.00 or \$10.00**). This ensures:
> 1. Your private family photos and handwritten notes remain strictly confidential.
> 2. You will never incur unexpected billing charges.
> 3. Your processing will not halt after a handful of cards due to free-tier daily quotas.

---

## 3. Quality Profiles & Approximate Cost per Card

RecipeBoxer provides three pre-configured **Quality Profiles** balancing speed, transcription accuracy, and operational cost:

| Profile | Target Modalities | Models Used | Thinking Budget | Grounding & Checks | Estimated Cost / Card |
|---|---|---|:---:|:---:|:---:|
| **Economy** | Clear typewriting, magazine clippings, printed cards | Flash-Lite for fingerprinting & grouping; Flash for extraction | Off (0 tokens) | Off | **~\$0.005 – \$0.015** |
| **Balanced**<br>*(Default)* | General collections, mixed handwriting, aged index cards | Flash across all stages | Adaptive (-1)<br>*(Engaged on cursive & damaged cards)* | Full grounding enabled | **~\$0.02 – \$0.05** |
| **Best** | Difficult or faded cursive, damaged cards, multi-generational heirlooms | Flash fingerprinting; **Pro models** for set verification, transcription, structuring, and repair | Extended (2,048 tokens) | Full grounding + secondary self-consistency | **~\$0.10 – \$0.25** |

*(Note: Costs above are approximate estimates calculated from Standard paid-tier token rates in `config/pricing.toml`. Actual cost depends on image resolution, card text density, and retry rounds).*

### When to Use Which Profile
- Choose **Economy** if you are processing clean, printed newspaper clippings or modern recipe cards and wish to minimize API costs.
- Choose **Balanced** for 90% of collections. It delivers excellent accuracy on standard cursive handwriting while keeping costs under a few cents per card.
- Choose **Best** if your cards feature faded pencil, heavy stains, 19th-century cursive script, or if you require the absolute highest transcription fidelity. *(Requires a paid key because Pro models are not available on the free tier).*

---

## 4. Spend Caps & Budget Ceiling Controls

RecipeBoxer includes built-in safeguards to ensure you remain in complete control of API spending:

```
                  ┌──────────────────────────────────────────────┐
                  │           CostEstimateCalculator             │
                  │   "Estimated cost for 25 cards: $0.75 - $1.25"│
                  └──────────────────────┬───────────────────────┘
                                         │ Start Ingestion
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │             Active Pipeline Run              │
                  │   Running Spend: $0.48 / Spend Cap: $2.00    │
                  └──────────────────────┬───────────────────────┘
                                         │ Exceeds Cap?
                                         ├──► [Paused: Budget Exceeded]
                                         │    (Prompt in Review Queue)
                                         ▼
                  ┌──────────────────────────────────────────────┐
                  │            Successful Completion             │
                  │   Total Cost Recorded in Provenance: $0.82   │
                  └──────────────────────────────────────────────┘
```

### Setting Spend Caps in Settings
In the **Settings** page:
1. **Per-Run Spending Cap (`spend_cap_usd`)**: Set a ceiling in USD (e.g. `$2.00`). If a batch reaches this limit, the pipeline automatically pauses.
2. **Monthly Spending Cap (`monthly_cap_usd`)**: Prevents cumulative spending from exceeding a monthly threshold.
3. **Pre-Run Cost Estimator**: On the Settings page or ingestion screen, enter the number of cards you plan to scan to view low, expected, and high spending estimates.
4. **Live Spend Meter**: The status bar displays real-time cumulative spend (`$X.XX`) and a visual progress bar tracking against your cap.
5. **Handling Budget Pauses**: If the cap is reached, processing pauses with status `BUDGET_EXCEEDED`. You can review already-extracted recipes, increase the cap to resume, or export completed work.

---

## 5. Reading Legibility Markers & Model Disagreements

Vintage cards often have illegible handwriting, grease stains, or torn edges. RecipeBoxer uses rich visual indicators in the **Recipe Editor** rather than guessing or silently altering text:

### 5.1 Legibility Indicators
- **Amber Underline (`UNCERTAIN`)**: Indicates the model transcribed the word but identified ambiguity in the handwriting. Double-check this word against the card scan.
- **Red Pill Badges (`ILLEGIBLE`)**: Rendered when handwriting is obscured. The badge displays the physical cause:
  - `[faded]`: Ink or pencil has deteriorated over time.
  - `[torn]`: Edge of the card or paper is missing.
  - `[stain]`: Grease or liquid stain obscures the text.
  - `[crossed out]`: The original author struck out a word or line.
  - *Hover Tooltip*: Hovering over an illegible badge reveals the model's best speculative reading.
- **Source Face Highlighting**: Click any ingredient line or instruction step to immediately pan and zoom the card viewer directly to the corresponding card face (`Face 1`, `Face 2`, etc.).

### 5.2 Model Disagreements (`MODEL_DISAGREEMENT`)
When the **Best** profile is active or confidence is marginal, RecipeBoxer performs a secondary independent read:
- If both reads disagree on a quantity or word, a **Needs Review** badge appears on the recipe card.
- Clicking the flagged field opens a side-by-side comparison modal displaying **Reading A** vs. **Reading B**.
- Click the preferred reading to accept it, or type your own correction in the editor.

### 5.3 Grounding Warnings (`GROUNDING_UNSUPPORTED`)
If the model includes an ingredient line that cannot be aligned with text on the card scan, a yellow warning icon highlights the line:
- *"Grounding unsupported: This item does not appear in the card transcript."*
- Review the card to confirm whether the ingredient was genuinely present in marginalia or mistakenly added by the model.

---

## 6. Human-in-the-Loop (HITL) Review Queue

When RecipeBoxer analyzes a collection, cards with ambiguous continuation evidence are routed to the **Review Queue** (`/review`):

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ Candidate Match: Part 1 (Front) + Part 2 (Back)       Confidence: 84%   │
  ├─────────────────────────────────────────────────────────────────────────┤
  │ CITED EVIDENCE:                                                         │
  │ • Verbatim quote: "...bake in moderate oven until golden brown."         │
  │ • Cross-reference: "dates" in directions matches front ingredient list. │
  │ • Physical match: Mirrored oil stain on lower right corner.             │
  │                                                                         │
  │ [ Space ] Approve Sequence       [ B ] Treat as Separate Recipes       │
  │ [ S ] Skip for Later             [ X ] Reject Link                      │
  └─────────────────────────────────────────────────────────────────────────┘
```

### 6.1 Review Queue Shortcuts
The review queue is fully keyboard-navigable:
- `Space`: **Approve Sequence** (Accepts the suggested card pairing).
- `B`: **Both Standalone / Split** (Treats both cards as independent, single-card recipes).
- `S`: **Skip** (Moves the proposal to the end of the queue).
- `X`: **Reject** (Breaks the proposed link).
- `Tab`: **Open Candidate Drawer** (Views alternative potential continuation cards).

*(Input focus trapping guarantees that pressing these keys while typing recipe notes or ingredient text will never trigger review actions).*

### 6.2 Sequence Reordering
For recipes spanning 3 or more cards, the review interface displays drag-and-drop sequence tiles (`Part 1 of 3`, `Part 2 of 3`, `Part 3 of 3`). Drag tiles horizontally to adjust reading order before approving.

---

## 7. Exporting Your Recipes

Once recipes are reviewed and approved, navigate to **Export** to publish your collection across 9 supported formats:

| Format | Target Platform | Description |
|---|---|---|
| **Plan to Eat CSV** | Plan to Eat | Strict 9-column RFC 4180 CSV with CRLF line terminators for 1-click import into Plan to Eat recipe manager. |
| **Schema.org JSON-LD** | Food Blogs / Web | Standardized web structured data with `@graph`, `HowToStep`, and ISO 8601 duration tags. |
| **Heirloom DOCX** | Microsoft Word | Formatted Word cookbook with cover page, formatted ingredient tables, and embedded high-resolution card scans. |
| **Printable PDF** | Print / Archival | Publication-ready PDF cookbook with 2-pass pagination, styled section dividers, and right-aligned dot-leader Table of Contents. |
| **Google Docs** | Google Drive | Creates a native Google Doc in your Google Drive with embedded card photos and clickable outline links. |
| **Obsidian Markdown** | Obsidian / PKM | Clean Markdown files with YAML frontmatter, tags, and wikilink image attachments. |
| **Cooklang (`.cook`)** | Cooklang | Plain-text culinary markup format with `@ingredient{}`, `#cookware{}`, and `~timer{}` annotations. |
| **Paprika (`.paprikarecipes`)**| Paprika App | Compressed archive containing recipe data and embedded card photos for instant import into the Paprika 3 app. |
| **Project Archive (`.recipeboxer`)**| Backup / Merge | Complete portable ZIP archive storing session state, original card images, and metadata for transfer between machines. |

### Curation Presets
Before exporting DOCX, PDF, or Google Docs cookbooks, select a **Grouping Preset** in the export dialog:
- **Course**: Automatically organizes recipes into *Appetizers*, *Breads*, *Soups & Salads*, *Main Dishes*, and *Desserts*.
- **Contributor**: Groups recipes by the family member who wrote them (e.g., *Grandma Rose*, *Aunt Clara*).
- **Tags**: Groups by recipe categories or dietary tags.
- **Alphabetical**: Alphabetizes recipes by title with alphabetical section dividers.
- **Custom**: Drag and drop recipes into your own preferred cookbook chapter order.
