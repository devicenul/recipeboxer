export type Legibility = 'clear' | 'uncertain' | 'illegible' | 'CLEAR' | 'UNCERTAIN' | 'ILLEGIBLE';

export interface TextSpan {
  start: number;
  end: number;
  text: string;
}

export interface IllegibleSpan {
  start: number;
  end: number;
  reason?: string;
}

export interface RecipeWarning {
  message: string;
  severity?: string;
  field?: string;
}

export interface Provenance {
  card_id?: string;
  face?: number;
  bounding_box?: number[];
  transcription_notes?: string[];
}

export interface TimeValue {
  original_text: string;
  normalized_minutes?: number;
  display_text?: string;
}

export interface YieldValue {
  original_text: string;
  normalized_servings?: number;
  normalized_unit?: string;
  display_text?: string;
}

export interface IngredientLine {
  original_text: string;
  normalized_text?: string;
  quantity?: string;
  unit?: string;
  item?: string;
  confidence?: number;
  source_face?: number;
  legibility?: Legibility;
  illegible_spans?: IllegibleSpan[];
  display_text?: string;
}

export interface Step {
  original_text?: string;
  normalized_text?: string;
  text?: string;
  confidence?: number;
  source_face?: number;
  legibility?: Legibility;
  illegible_spans?: IllegibleSpan[];
  display_text?: string;
}

export interface RecipeSection {
  name?: string | null;
  title?: string | null;
  ingredients: IngredientLine[];
  steps: Step[];
}

export interface Recipe {
  recipe_id: string;
  title: string;
  description?: string;
  sections?: RecipeSection[];
  headnote?: Step;
  notes?: Step[];
  prep_time?: TimeValue | string;
  cook_time?: TimeValue | string;
  total_time?: TimeValue | string;
  yield_value?: YieldValue;
  yield_servings?: string;
  ingredients?: string[];
  directions?: string[];
  tags: string[];
  source_notes?: string;
  provenance?: Provenance;
  warnings?: RecipeWarning[];
  confidence?: number;
  card_ids?: string[];
  image_urls?: string[];
  created_at?: string;
  updated_at?: string;
  transcription_notes?: string[];
}

export type RecipeSchema = Recipe;


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
