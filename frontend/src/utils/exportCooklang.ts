import { RecipeSchema } from '../types/recipe';
import { formatTimeValue } from './formatters';

/**
 * Parses ingredient into Cooklang notation @name{qty%unit} or @name{qty} or @name{}.
 */
export function formatCooklangIngredient(ing: string): string {
  const raw = ing.trim();
  if (!raw) return '';

  const qtyMatch = raw.match(/^((?:\d+\s+)?\d+\/\d+|\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)\s*(.*)/);
  if (!qtyMatch) {
    return `@${raw}{}`;
  }

  const qty = qtyMatch[1].trim();
  const remainder = qtyMatch[2].trim();

  const unitMatch = remainder.match(/^(tablespoons?|tbsp?|teaspoons?|tsp?|cups?|c|ounces?|oz|pounds?|lbs?|grams?|g|ml|liters?|pinches?|pinch|cloves?|slices?|packages?|pkgs?|can|cans|stick|sticks|large|medium|small)\s*,?\s*(?:of\s+)?(.*)/i);
  if (unitMatch) {
    const unit = unitMatch[1].trim();
    const name = unitMatch[2].trim() || remainder;
    return `@${name}{${qty}%${unit}}`;
  }

  return `@${remainder}{${qty}}`;
}

/**
 * Serializes a single recipe into Cooklang (.cook) string.
 */
export function recipeToCooklang(recipe: RecipeSchema): string {
  const lines: string[] = [];

  lines.push(`>> title: ${recipe.title || 'Untitled Recipe'}`);
  if (recipe.description) lines.push(`>> description: ${recipe.description}`);
  if (recipe.prep_time) lines.push(`>> prep time: ${formatTimeValue(recipe.prep_time)}`);
  if (recipe.cook_time) lines.push(`>> cook time: ${formatTimeValue(recipe.cook_time)}`);
  if (recipe.total_time) lines.push(`>> total time: ${formatTimeValue(recipe.total_time)}`);
  if (recipe.yield_servings) lines.push(`>> servings: ${recipe.yield_servings}`);
  if (recipe.tags && recipe.tags.length > 0) lines.push(`>> tags: ${recipe.tags.join(', ')}`);
  if (recipe.source_notes) lines.push(`>> source: ${recipe.source_notes}`);

  lines.push('');

  if (recipe.ingredients && recipe.ingredients.length > 0) {
    lines.push('-- Ingredients:');
    for (const ing of recipe.ingredients) {
      lines.push(formatCooklangIngredient(ing));
    }
    lines.push('');
  }

  if (recipe.directions && recipe.directions.length > 0) {
    lines.push('-- Directions:');
    for (const step of recipe.directions) {
      const cleanStep = step.replace(/^\d+[\.\)\-:]\s*/, '').replace(/^Step\s*\d+[\.\)\-:]\s*/i, '').trim();
      lines.push(cleanStep);
      lines.push('');
    }
  }

  return lines.join('\n').trim();
}

/**
 * Serializes list of recipes into Cooklang (.cook) string.
 */
export function generateCooklang(recipes: RecipeSchema[]): string {
  return recipes.map(recipeToCooklang).join('\n\n---\n\n');
}

/**
 * Initiates browser download of Cooklang (.cook) file.
 */
export function downloadCooklang(recipes: RecipeSchema[], filename = 'recipeboxer_recipes.cook'): void {
  const content = generateCooklang(recipes);
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
