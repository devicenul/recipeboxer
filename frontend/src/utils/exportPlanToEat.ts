import { RecipeSchema } from '../types/recipe';

/**
 * Escapes a CSV field according to RFC 4180 rules.
 */
export function escapeCsvField(val: string | undefined | null): string {
  if (val === undefined || val === null) return "";
  const str = String(val);
  const needsQuotes =
    str.includes(",") ||
    str.includes('"') ||
    str.includes("\n") ||
    str.includes("\r");

  if (needsQuotes) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/**
 * Normalizes directions into sequential numbered lines: "1. Step 1\n2. Step 2".
 */
export function formatDirectionsForCsv(directions: string[] = []): string {
  return directions
    .map((step, idx) => {
      const clean = step.trim().replace(/^\d+[\.\)\-:]\s*/, "").replace(/^Step\s*\d+[\.\)\-:]\s*/i, "");
      return `${idx + 1}. ${clean}`;
    })
    .join("\n");
}

/**
 * Generates RFC 4180 compliant 9-column Plan To Eat CSV content with CRLF line terminators.
 */
export function generatePlanToEatCsv(recipes: RecipeSchema[]): string {
  const headers = [
    "Title",
    "Description",
    "Ingredients",
    "Directions",
    "Prep Time",
    "Total Time",
    "Yield",
    "Tags",
    "Source",
  ];

  const rows: string[] = [headers.join(",")];

  for (const recipe of recipes) {
    const ingredientsJoined = (recipe.ingredients || []).join("\n");
    const directionsJoined = formatDirectionsForCsv(recipe.directions || []);
    const tagsJoined = (recipe.tags || []).join(", ");

    const row = [
      escapeCsvField(recipe.title || ""),
      escapeCsvField(recipe.description || ""),
      escapeCsvField(ingredientsJoined),
      escapeCsvField(directionsJoined),
      escapeCsvField(recipe.prep_time || ""),
      escapeCsvField(recipe.total_time || ""),
      escapeCsvField(recipe.yield_servings || ""),
      escapeCsvField(tagsJoined),
      escapeCsvField(recipe.source_notes || "RecipeBoxer OCR"),
    ];

    rows.push(row.join(","));
  }

  return rows.join("\r\n") + "\r\n";
}

/**
 * Initiates browser download of the Plan To Eat CSV.
 */
export function downloadPlanToEatCsv(recipes: RecipeSchema[], filename = "recipeboxer_export.csv"): void {
  const csvContent = generatePlanToEatCsv(recipes);
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.setAttribute("download", filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
