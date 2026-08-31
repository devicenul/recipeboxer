/**
 * Client-side Curation, Grouping, and Recipe Ordering Engine for RecipeBoxer (Phase 9 Milestone 3)
 */

import { RecipeSchema } from '../types/recipe';
import {
  GroupingPreset,
  CuratedSection,
  CuratedCookbook,
  ExportCurationOptions,
} from '../types/export';

export interface CourseTaxonomyEntry {
  id: string;
  title: string;
  keywords: string[];
}

export const CANONICAL_COURSES: CourseTaxonomyEntry[] = [
  {
    id: 'course_breakfast_brunch',
    title: 'Breakfast & Brunch',
    keywords: [
      'breakfast', 'brunch', 'pancake', 'pancakes', 'waffle', 'waffles',
      'egg', 'eggs', 'omelet', 'omelette', 'frittata', 'toast', 'muffin',
      'muffins', 'scone', 'scones', 'bacon', 'sausage', 'cereal', 'granola',
      'quiche', 'crepe', 'crepes', 'biscuit', 'biscuits', 'bagel', 'bagels',
    ],
  },
  {
    id: 'course_appetizers_starters',
    title: 'Appetizers & Starters',
    keywords: [
      'appetizer', 'appetizers', 'starter', 'starters', 'dip', 'dips',
      'spread', 'spreads', 'snack', 'snacks', 'finger food', 'tapas',
      'hors d\'oeuvre', 'hors d\'oeuvres', 'crostini', 'bruschetta', 'wings',
      'nachos', 'guacamole', 'salsa', 'antipasto', 'skewer', 'skewers',
    ],
  },
  {
    id: 'course_soups_stews',
    title: 'Soups, Stews & Chilies',
    keywords: [
      'soup', 'soups', 'stew', 'stews', 'chili', 'chilies', 'chowder',
      'chowders', 'bisque', 'broth', 'gumbo', 'bouillabaisse', 'potage',
      'minestrone', 'gazpacho',
    ],
  },
  {
    id: 'course_salads_sides',
    title: 'Salads & Side Dishes',
    keywords: [
      'salad', 'salads', 'side', 'sides', 'side dish', 'slaw', 'coleslaw',
      'dressing', 'vinaigrette', 'vegetable', 'vegetables', 'potato', 'potatoes',
      'stuffing', 'casserole', 'rice', 'pilaf', 'greens',
    ],
  },
  {
    id: 'course_main_courses',
    title: 'Main Courses & Entrées',
    keywords: [
      'main', 'mains', 'main course', 'entree', 'entrées', 'dinner', 'supper',
      'roast', 'steak', 'chicken', 'poultry', 'beef', 'pork', 'lamb', 'turkey',
      'fish', 'seafood', 'salmon', 'shrimp', 'pasta', 'lasagna', 'pizza',
      'burger', 'burgers', 'curry', 'tacos', 'enchiladas', 'pie', 'pot pie',
    ],
  },
  {
    id: 'course_breads_baking',
    title: 'Breads & Rolls',
    keywords: [
      'bread', 'breads', 'loaf', 'loaves', 'roll', 'rolls', 'bun', 'buns',
      'sourdough', 'focaccia', 'brioche', 'baguette', 'cornbread', 'pita',
      'flatbread', 'challah', 'dough',
    ],
  },
  {
    id: 'course_desserts_sweets',
    title: 'Desserts & Sweets',
    keywords: [
      'dessert', 'desserts', 'cake', 'cakes', 'pie', 'pies', 'cookie',
      'cookies', 'bar', 'bars', 'brownie', 'brownies', 'candy', 'candies',
      'pastry', 'pastries', 'tart', 'tarts', 'pudding', 'puddings',
      'ice cream', 'sweet', 'sweets', 'fudge', 'cobbler', 'cobblers',
      'crisp', 'crisps', 'frosting', 'icing', 'cheesecake', 'truffle',
      'truffles', 'cupcake', 'cupcakes', 'parfait', 'sorbet', 'custard',
    ],
  },
  {
    id: 'course_beverages_drinks',
    title: 'Beverages & Drinks',
    keywords: [
      'drink', 'drinks', 'beverage', 'beverages', 'cocktail', 'cocktails',
      'punch', 'punches', 'tea', 'teas', 'coffee', 'smoothie', 'smoothies',
      'cider', 'lemonade', 'mocktail', 'mocktails', 'juice', 'juices',
      'spritz', 'shake', 'shakes', 'latte', 'cocoa',
    ],
  },
];

/**
 * Strips leading English articles ('The ', 'A ', 'An ') and whitespace for clean alphabetical sorting.
 */
export const normalizeTitleForSorting = (title?: string): string => {
  const t = (title || '').trim();
  return t.replace(/^(the|a|an)\s+/i, '').trim();
};

/**
 * Extracts a clean contributor attribution from source notes.
 */
export const extractContributorName = (sourceNotes?: string): string => {
  if (!sourceNotes || !sourceNotes.trim()) {
    return 'Family Collection / Unattributed';
  }

  const raw = sourceNotes.trim();

  // Check for possessive box/kitchen notes like "Grandma Clara's Sunday Suppers", "Mom's Kitchen"
  const possessiveMatch = raw.match(/^([A-Za-z0-9\s]+?)'s(?:\s+|$)/i);
  if (possessiveMatch) {
    const name = possessiveMatch[1].trim();
    if (name && name.length >= 2) {
      return name;
    }
  }

  // Strip prefixes
  let cleaned = raw.replace(
    /^(from\s+the\s+kitchen\s+of|from\s*:?|source\s*:?|submitted\s+by\s*:?|handed\s+down\s+by\s*:?|courtesy\s+of\s*:?|recipe\s+from\s*:?)\s*/i,
    ''
  ).trim();

  // Remove trailing parentheticals and punctuation
  cleaned = cleaned.replace(/\s*\([^)]*\)/g, '').trim();
  cleaned = cleaned.replace(/[\."']+$/g, '').trim();

  if (!cleaned) {
    return 'Family Collection / Unattributed';
  }

  if (cleaned.length > 50) {
    const firstClause = cleaned.split(/[,;—\-\.]/)[0].trim();
    if (firstClause && firstClause.length >= 3) {
      return firstClause;
    }
  }

  return cleaned;
};

/**
 * Reorders a list of recipes according to an explicit sequence of recipe IDs.
 */
export const reorderRecipes = (
  recipes: RecipeSchema[],
  recipeOrder?: string[] | string
): RecipeSchema[] => {
  if (!recipeOrder) {
    return [...recipes];
  }

  const orderIds: string[] = Array.isArray(recipeOrder)
    ? recipeOrder.map((s) => String(s).trim()).filter(Boolean)
    : recipeOrder.split(',').map((s) => s.trim()).filter(Boolean);

  if (orderIds.length === 0) {
    return [...recipes];
  }

  const recipeMap = new Map<string, RecipeSchema>();
  recipes.forEach((r) => recipeMap.set(r.recipe_id, r));

  const seenIds = new Set<string>();
  const reordered: RecipeSchema[] = [];

  orderIds.forEach((rid) => {
    const rec = recipeMap.get(rid);
    if (rec && !seenIds.has(rid)) {
      reordered.push(rec);
      seenIds.add(rid);
    }
  });

  // Append remaining recipes not listed in custom order
  recipes.forEach((r) => {
    if (!seenIds.has(r.recipe_id)) {
      reordered.push(r);
      seenIds.add(r.recipe_id);
    }
  });

  return reordered;
};

/**
 * Determines matching course taxonomy category for a recipe.
 */
export const matchCourseTaxonomy = (recipe: RecipeSchema): CourseTaxonomyEntry | null => {
  const searchable = [
    ...(recipe.tags || []),
    recipe.title || '',
    recipe.description || '',
  ]
    .join(' ')
    .toLowerCase();

  for (const entry of CANONICAL_COURSES) {
    for (const kw of entry.keywords) {
      const regex = new RegExp(`\\b${kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'i');
      if (regex.test(searchable)) {
        return entry;
      }
    }
  }
  return null;
};

/**
 * Primary Curation Engine function matching the Python backend.
 */
export const curateRecipes = (
  recipes: RecipeSchema[],
  options?: Partial<ExportCurationOptions>,
  cookbookTitle: string = 'RecipeBoxer Heirloom Cookbook'
): CuratedCookbook => {
  const preset: GroupingPreset = options?.grouping_preset || 'none';
  const includeDividers = options?.include_section_dividers ?? true;
  const includeToc = options?.include_toc ?? true;
  const tocTitle = (options?.toc_title || 'Table of Contents').trim() || 'Table of Contents';

  const orderedBaseRecipes = reorderRecipes(recipes, options?.recipe_order);

  if (orderedBaseRecipes.length === 0) {
    return {
      cookbook_title: cookbookTitle,
      grouping_preset: preset,
      sections: [],
      total_recipe_count: 0,
      include_section_dividers: includeDividers,
      include_toc: includeToc,
      toc_title: tocTitle,
    };
  }

  let sections: CuratedSection[] = [];

  switch (preset) {
    case 'none':
    case 'custom': {
      sections = [
        {
          section_id: 'section_all',
          title: 'All Recipes',
          subtitle: `${orderedBaseRecipes.length} Heirloom ${orderedBaseRecipes.length === 1 ? 'Recipe' : 'Recipes'}`,
          recipes: orderedBaseRecipes,
        },
      ];
      break;
    }

    case 'alphabetical': {
      const sorted = [...orderedBaseRecipes].sort((a, b) =>
        normalizeTitleForSorting(a.title).localeCompare(normalizeTitleForSorting(b.title), undefined, {
          sensitivity: 'base',
        })
      );
      sections = [
        {
          section_id: 'section_alphabetical',
          title: 'Alphabetical Recipe Index',
          subtitle: `${sorted.length} Recipes (A to Z)`,
          recipes: sorted,
        },
      ];
      break;
    }

    case 'course': {
      const courseMap = new Map<string, RecipeSchema[]>();
      CANONICAL_COURSES.forEach((c) => courseMap.set(c.id, []));
      const miscRecipes: RecipeSchema[] = [];

      orderedBaseRecipes.forEach((recipe) => {
        const matched = matchCourseTaxonomy(recipe);
        if (matched) {
          courseMap.get(matched.id)!.push(recipe);
        } else {
          miscRecipes.push(recipe);
        }
      });

      CANONICAL_COURSES.forEach((c) => {
        const list = courseMap.get(c.id)!;
        if (list.length > 0) {
          sections.push({
            section_id: c.id,
            title: c.title,
            subtitle: `${list.length} ${list.length === 1 ? 'Recipe' : 'Recipes'}`,
            recipes: list,
          });
        }
      });

      if (miscRecipes.length > 0) {
        sections.push({
          section_id: 'course_miscellaneous',
          title: 'Other Family Favorites',
          subtitle: `${miscRecipes.length} ${miscRecipes.length === 1 ? 'Recipe' : 'Recipes'}`,
          recipes: miscRecipes,
        });
      }
      break;
    }

    case 'contributor': {
      const groups = new Map<string, RecipeSchema[]>();
      orderedBaseRecipes.forEach((recipe) => {
        const contributor = extractContributorName(recipe.source_notes);
        if (!groups.has(contributor)) {
          groups.set(contributor, []);
        }
        groups.get(contributor)!.push(recipe);
      });

      const sortedContributors = Array.from(groups.keys()).sort((a, b) =>
        a.localeCompare(b, undefined, { sensitivity: 'base' })
      );

      sortedContributors.forEach((cName) => {
        const recList = groups.get(cName)!;
        const slug = cName.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
        sections.push({
          section_id: `contributor_${slug || 'unattributed'}`,
          title: cName,
          subtitle: `From the kitchen of ${cName} (${recList.length} ${recList.length === 1 ? 'recipe' : 'recipes'})`,
          recipes: recList,
        });
      });
      break;
    }

    case 'tags': {
      const tagMap = new Map<string, RecipeSchema[]>();
      const untagged: RecipeSchema[] = [];

      orderedBaseRecipes.forEach((recipe) => {
        const cleanTags = (recipe.tags || []).map((t) => t.trim()).filter(Boolean);
        if (cleanTags.length > 0) {
          const primaryTag = cleanTags[0];
          if (!tagMap.has(primaryTag)) {
            tagMap.set(primaryTag, []);
          }
          tagMap.get(primaryTag)!.push(recipe);
        } else {
          untagged.push(recipe);
        }
      });

      const sortedTags = Array.from(tagMap.keys()).sort((a, b) =>
        a.localeCompare(b, undefined, { sensitivity: 'base' })
      );

      sortedTags.forEach((tag) => {
        const list = tagMap.get(tag)!;
        const slug = tag.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
        sections.push({
          section_id: `tag_${slug || 'general'}`,
          title: tag,
          subtitle: `${list.length} ${list.length === 1 ? 'Recipe' : 'Recipes'}`,
          recipes: list,
        });
      });

      if (untagged.length > 0) {
        sections.push({
          section_id: 'tag_untagged',
          title: 'General & Untagged',
          subtitle: `${untagged.length} ${untagged.length === 1 ? 'Recipe' : 'Recipes'}`,
          recipes: untagged,
        });
      }
      break;
    }
  }

  const totalRecipeCount = sections.reduce((acc, sec) => acc + sec.recipes.length, 0);

  return {
    cookbook_title: cookbookTitle,
    grouping_preset: preset,
    sections,
    total_recipe_count: totalRecipeCount,
    include_section_dividers: includeDividers,
    include_toc: includeToc,
    toc_title: tocTitle,
  };
};

/**
 * Extracts a flattened list of all recipes in curated section order.
 */
export const flattenCuratedRecipes = (cookbook: CuratedCookbook): RecipeSchema[] => {
  return cookbook.sections.flatMap((sec) => sec.recipes);
};
