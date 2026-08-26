export interface RecipeSchema {
  recipe_id: string;
  title: string;
  description?: string;
  yield_servings?: string;
  prep_time?: string;
  cook_time?: string;
  total_time?: string;
  ingredients: string[];
  directions: string[];
  tags: string[];
  source_notes?: string;
  card_ids?: string[];
  image_urls?: string[];
  confidence_score?: number;
  extracted_at?: string;
}

export interface PlanToEatRow {
  Title: string;
  Description: string;
  Ingredients: string;
  Directions: string;
  "Prep Time": string;
  "Total Time": string;
  Yield: string;
  Tags: string;
  Source: string;
}

export interface HowToStep {
  "@type": "HowToStep";
  position: number;
  text: string;
}

export interface SchemaOrgRecipe {
  "@context": "https://schema.org";
  "@type": "Recipe";
  name: string;
  description?: string;
  recipeYield?: string;
  prepTime?: string;
  cookTime?: string;
  totalTime?: string;
  recipeIngredient: string[];
  recipeInstructions: HowToStep[];
  keywords?: string;
  comment?: string;
}
