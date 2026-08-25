import { RecipeSchema } from '../types/recipe';

/**
 * Serializes recipes to raw formatted JSON string.
 */
export function generateRawJson(recipes: RecipeSchema[]): string {
  return JSON.stringify(recipes, null, 2);
}

/**
 * Initiates browser download of raw Recipe JSON.
 */
export function downloadRawJson(recipes: RecipeSchema[], filename = "recipeboxer_recipes.json"): void {
  const content = generateRawJson(recipes);
  const blob = new Blob([content], { type: "application/json;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.setAttribute("download", filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
