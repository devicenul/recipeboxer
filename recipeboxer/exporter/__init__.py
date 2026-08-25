"""Multi-format recipe exporter package for RecipeBoxer.

Provides Plan To Eat CSV, Schema.org Recipe JSON-LD, standard JSON, Google Docs Archival,
DOCX, Printable PDF, Obsidian Markdown, Cooklang, and Paprika export engines.
"""

from recipeboxer.exporter.cooklang import (
    CooklangExporter,
    export_recipes_to_cooklang,
    format_cooklang_ingredient,
    parse_ingredient_components,
)
from recipeboxer.exporter.docx_export import (
    DocxExporter,
    export_recipes_to_docx,
)
from recipeboxer.exporter.google_docs import (
    GoogleDocsExporter,
    export_recipes_to_google_docs_batch_requests,
    export_recipes_to_google_docs_html,
    export_recipes_to_google_docs_json,
)
from recipeboxer.exporter.json_export import (
    JsonExporter,
    export_recipes_to_json,
)
from recipeboxer.exporter.json_ld import (
    JsonLdExporter,
    export_recipes_to_json_ld,
    human_duration_to_iso8601,
)
from recipeboxer.exporter.markdown_export import (
    MarkdownExporter,
    export_recipes_to_markdown,
)
from recipeboxer.exporter.paprika import (
    PaprikaExporter,
    export_recipes_to_paprika,
)
from recipeboxer.exporter.pdf_export import (
    PdfExporter,
    export_recipes_to_pdf,
)
from recipeboxer.exporter.plan_to_eat import (
    PLAN_TO_EAT_COLUMNS,
    PlanToEatExporter,
    export_recipes_to_plan_to_eat_csv,
)

__all__ = [
    # Plan To Eat CSV (Primary R0)
    "PLAN_TO_EAT_COLUMNS",
    "PlanToEatExporter",
    "export_recipes_to_plan_to_eat_csv",
    # Schema.org JSON-LD (Primary R0)
    "JsonLdExporter",
    "export_recipes_to_json_ld",
    "human_duration_to_iso8601",
    # Raw JSON
    "JsonExporter",
    "export_recipes_to_json",
    # Google Docs Archival (R1)
    "GoogleDocsExporter",
    "export_recipes_to_google_docs_batch_requests",
    "export_recipes_to_google_docs_json",
    "export_recipes_to_google_docs_html",
    # Offline DOCX & Printable PDF (R2)
    "DocxExporter",
    "export_recipes_to_docx",
    "PdfExporter",
    "export_recipes_to_pdf",
    # Culinary Ecosystem Serializers (R3)
    "MarkdownExporter",
    "export_recipes_to_markdown",
    "CooklangExporter",
    "export_recipes_to_cooklang",
    "parse_ingredient_components",
    "format_cooklang_ingredient",
    "PaprikaExporter",
    "export_recipes_to_paprika",
]
