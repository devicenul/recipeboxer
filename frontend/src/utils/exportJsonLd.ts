import { RecipeSchema, SchemaOrgRecipe, HowToStep } from '../types/recipe';
import { toISO8601Duration } from './formatters';

/**
 * Converts a single RecipeSchema to a Schema.org Recipe JSON-LD object.
 */
export function recipeToSchemaOrg(recipe: RecipeSchema): SchemaOrgRecipe {
  const steps: HowToStep[] = (recipe.directions || []).map((stepText, idx) => ({
    "@type": "HowToStep",
    position: idx + 1,
    text: stepText.replace(/^\d+[\.\)\-:]\s*/, "").replace(/^Step\s*\d+[\.\)\-:]\s*/i, "").trim(),
  }));

  const jsonLd: SchemaOrgRecipe = {
    "@context": "https://schema.org",
    "@type": "Recipe",
    name: recipe.title || "Untitled Recipe",
    description: recipe.description || undefined,
    recipeYield: recipe.yield_servings || undefined,
    prepTime: recipe.prep_time ? toISO8601Duration(recipe.prep_time) : undefined,
    cookTime: recipe.cook_time ? toISO8601Duration(recipe.cook_time) : undefined,
    totalTime: recipe.total_time ? toISO8601Duration(recipe.total_time) : undefined,
    recipeIngredient: recipe.ingredients || [],
    recipeInstructions: steps,
    keywords: recipe.tags && recipe.tags.length > 0 ? recipe.tags.join(", ") : undefined,
    comment: recipe.source_notes || undefined,
  };

  return jsonLd;
}

/**
 * Serializes a list of recipes into Schema.org JSON-LD formatted string.
 */
export function generateJsonLd(recipes: RecipeSchema[]): string {
  if (recipes.length === 1) {
    return JSON.stringify(recipeToSchemaOrg(recipes[0]), null, 2);
  }
  const items = recipes.map(recipeToSchemaOrg);
  return JSON.stringify(items, null, 2);
}

/**
 * Initiates browser download of the Schema.org JSON-LD file.
 */
export function downloadJsonLd(recipes: RecipeSchema[], filename = "recipeboxer_recipes_json_ld.json"): void {
  const content = generateJsonLd(recipes);
  const blob = new Blob([content], { type: "application/ld+json;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.setAttribute("download", filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
