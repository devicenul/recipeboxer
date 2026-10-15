export type Legibility = 'CLEAR' | 'UNCERTAIN' | 'ILLEGIBLE' | 'clear' | 'uncertain' | 'illegible';

export interface IllegibleSpan {
  text_guess?: string;
  reason?: string; // faded, torn, stain, crossed_out, ambiguous
  start?: number;
  end?: number;
}

export interface TextSpan {
  text: string;
  kind?: string; // headnote, note, family_story, margin_note, ad
  source_face?: number;
  confidence?: number;
  legibility?: Legibility;
  illegible_spans?: IllegibleSpan[];
  start?: number;
  end?: number;
}

export interface RecipeWarning {
  code: string; // GROUNDING_UNSUPPORTED, LOW_CONFIDENCE, MODEL_DISAGREEMENT, ILLEGIBLE_CONTENT, REPAIRED_OUTPUT, INFERRED_TITLE, CONTINUITY_GAP
  message: string;
  field_path?: string;
  severity?: string;
  field?: string;
}

export interface Provenance {
  faces?: string[];
  models?: string[];
  prompt_versions?: Record<string, string>;
  profile?: string;
  cost?: number;
  card_id?: string;
  face?: number;
  bounding_box?: number[];
  transcription_notes?: string[];
}

export interface TimeValue {
  original_text: string;
  normalized_iso?: string;
  minutes?: number;
  normalized_minutes?: number;
  display_text?: string;
}

export interface YieldValue {
  original_text: string;
  normalized_servings?: number;
  unit?: string;
  normalized_unit?: string;
  display_text?: string;
}

export interface IngredientLine {
  original_text: string;
  quantity?: string | null;
  quantity_max?: string | null;
  unit?: string | null;
  item: string;
  preparation?: string | null;
  normalized_text?: string | null;
  normalized_quantity?: number | null;
  normalized_unit?: string | null;
  confidence?: number;
  source_face?: number;
  legibility?: Legibility;
  illegible_spans?: IllegibleSpan[];
  display_text?: string;
}

export interface Step {
  original_text: string;
  text?: string | null;
  normalized_text?: string | null;
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
  title_is_inferred?: boolean;
  description?: string;
  headnote?: TextSpan | Step | null;
  notes?: Array<TextSpan | Step>;
  sections?: RecipeSection[];
  prep_time?: TimeValue | string;
  cook_time?: TimeValue | string;
  total_time?: TimeValue | string;
  yield_value?: YieldValue;
  yield_servings?: string;
  tags: string[];
  source_notes?: string;
  provenance?: Provenance;
  warnings?: RecipeWarning[];
  confidence?: number;
  card_ids?: string[];
  image_urls?: string[];
  created_at?: string;
  updated_at?: string;
  ingredients?: string[];
  directions?: string[];
  transcription_notes?: string[];
  flagged_count?: number;
  needs_review?: boolean;
}

export type RecipeSchema = Recipe;

/** Returns display text for an ingredient line, step, or text span. Prefers normalized_text, then original_text/text/item. */
export function displayText(line: IngredientLine | Step | TextSpan | string | null | undefined): string {
  if (!line) return '';
  if (typeof line === 'string') return line;
  if ('display_text' in line && line.display_text && line.display_text.trim()) {
    return line.display_text.trim();
  }
  if ('normalized_text' in line && line.normalized_text && line.normalized_text.trim()) {
    return line.normalized_text.trim();
  }
  if ('text' in line && line.text && line.text.trim()) {
    return line.text.trim();
  }
  if ('original_text' in line && line.original_text && line.original_text.trim()) {
    return line.original_text.trim();
  }
  if ('item' in line && line.item) {
    const parts = [line.quantity, line.unit, line.item].filter(Boolean);
    return parts.join(' ').trim();
  }
  return '';
}

/** Formats TimeValue or string into user-facing display string (e.g. '20 mins'). */
export function formatTimeValue(time: TimeValue | string | null | undefined): string {
  if (!time) return '';
  if (typeof time === 'string') return time;
  if (time.display_text && time.display_text.trim()) return time.display_text.trim();
  if (time.original_text && time.original_text.trim()) return time.original_text.trim();
  if (time.minutes != null) return `${time.minutes} mins`;
  if (time.normalized_minutes != null) return `${time.normalized_minutes} mins`;
  if (time.normalized_iso) return time.normalized_iso;
  return '';
}

/** Formats YieldValue or string into user-facing display string (e.g. '24 cookies'). */
export function formatYieldValue(yv: YieldValue | string | null | undefined): string {
  if (!yv) return '';
  if (typeof yv === 'string') return yv;
  if (yv.display_text && yv.display_text.trim()) return yv.display_text.trim();
  if (yv.original_text && yv.original_text.trim()) return yv.original_text.trim();
  if (yv.normalized_servings != null) {
    const u = yv.unit || yv.normalized_unit ? ` ${yv.unit || yv.normalized_unit}` : '';
    return `${yv.normalized_servings}${u}`.trim();
  }
  return '';
}

/** Extracts all IngredientLines across recipe sections, falling back to flat ingredients if needed. */
export function getAllIngredients(recipe: Recipe): IngredientLine[] {
  if (recipe.sections && recipe.sections.length > 0) {
    const list: IngredientLine[] = [];
    for (const sec of recipe.sections) {
      if (sec.ingredients) {
        list.push(...sec.ingredients);
      }
    }
    if (list.length > 0) return list;
  }
  if (recipe.ingredients && recipe.ingredients.length > 0) {
    return recipe.ingredients.map((ing) => ({
      original_text: ing,
      item: ing,
      confidence: 1.0,
      source_face: 0,
      legibility: 'CLEAR',
    }));
  }
  return [];
}

/** Extracts all Steps across recipe sections, falling back to flat directions if needed. */
export function getAllSteps(recipe: Recipe): Step[] {
  if (recipe.sections && recipe.sections.length > 0) {
    const list: Step[] = [];
    for (const sec of recipe.sections) {
      if (sec.steps) {
        list.push(...sec.steps);
      }
    }
    if (list.length > 0) return list;
  }
  if (recipe.directions && recipe.directions.length > 0) {
    return recipe.directions.map((dir) => ({
      original_text: dir,
      text: dir,
      confidence: 1.0,
      source_face: 0,
      legibility: 'CLEAR',
    }));
  }
  return [];
}

/** Parses MODEL_DISAGREEMENT warning message into structured reading1 and reading2. */
export function parseModelDisagreement(warning: RecipeWarning | string): {
  reading1: string;
  reading2: string;
  field?: string;
} | null {
  const msg = typeof warning === 'string' ? warning : warning.message;
  if (!msg) return null;
  const field = typeof warning !== 'string' ? (warning.field_path || undefined) : undefined;

  // Pattern 1: reading 1 has '<val1>', reading 2 has '<val2>'
  const r1r2 = /reading 1 has ['"]([^'"]*)['"], reading 2 has ['"]([^'"]*)['"]/i.exec(msg);
  if (r1r2) {
    return {
      reading1: r1r2[1],
      reading2: r1r2[2],
      field,
    };
  }

  // Pattern 2: recipe has '<val1>', OCR has '<val2>'
  const recOcr = /recipe has ['"]([^'"]*)['"], OCR has ['"]([^'"]*)['"]/i.exec(msg);
  if (recOcr) {
    return {
      reading1: recOcr[1],
      reading2: recOcr[2],
      field,
    };
  }

  // Pattern 3: Reading 2 contains additional ingredient/step: '<val>'
  const addR2 = /reading 2 contains additional [^:]*:\s*['"]([^'"]*)['"]/i.exec(msg);
  if (addR2) {
    return {
      reading1: '(absent)',
      reading2: addR2[1],
      field,
    };
  }

  return null;
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
