import { RecipeSchema } from '../types/recipe';

/**
 * Serializes a single recipe into Obsidian Markdown with YAML frontmatter.
 */
export function recipeToMarkdown(recipe: RecipeSchema, useWikilinks = true): string {
  const frontmatter: string[] = ['---'];
  frontmatter.push(`title: ${JSON.stringify(recipe.title || 'Untitled Recipe')}`);

  if (recipe.description) {
    frontmatter.push(`description: ${JSON.stringify(recipe.description)}`);
  }
  if (recipe.tags && recipe.tags.length > 0) {
    frontmatter.push('tags:');
    for (const tag of recipe.tags) {
      frontmatter.push(`  - ${tag}`);
    }
  }
  if (recipe.prep_time) {
    frontmatter.push(`prep_time: ${JSON.stringify(recipe.prep_time)}`);
  }
  if (recipe.cook_time) {
    frontmatter.push(`cook_time: ${JSON.stringify(recipe.cook_time)}`);
  }
  if (recipe.total_time) {
    frontmatter.push(`total_time: ${JSON.stringify(recipe.total_time)}`);
  }
  if (recipe.yield_servings) {
    frontmatter.push(`yield: ${JSON.stringify(recipe.yield_servings)}`);
  }
  if (recipe.source_notes) {
    frontmatter.push(`source: ${JSON.stringify(recipe.source_notes)}`);
  }
  if (recipe.card_ids && recipe.card_ids.length > 0) {
    frontmatter.push('card_ids:');
    for (const cid of recipe.card_ids) {
      frontmatter.push(`  - ${cid}`);
    }
  }
  frontmatter.push('---');

  const body: string[] = [
    frontmatter.join('\n'),
    '',
    `# ${recipe.title || 'Untitled Recipe'}`,
    '',
  ];

  if (recipe.description) {
    body.push(`> ${recipe.description}`);
    body.push('');
  }

  const metaParts: string[] = [];
  if (recipe.yield_servings) metaParts.push(`**Yield:** ${recipe.yield_servings}`);
  if (recipe.prep_time) metaParts.push(`**Prep Time:** ${recipe.prep_time}`);
  if (recipe.cook_time) metaParts.push(`**Cook Time:** ${recipe.cook_time}`);
  if (recipe.total_time) metaParts.push(`**Total Time:** ${recipe.total_time}`);
  if (metaParts.length > 0) {
    body.push(metaParts.join(' | '));
    body.push('');
  }

  if (recipe.ingredients && recipe.ingredients.length > 0) {
    body.push('## Ingredients');
    body.push('');
    for (const ing of recipe.ingredients) {
      body.push(`- ${ing}`);
    }
    body.push('');
  }

  if (recipe.directions && recipe.directions.length > 0) {
    body.push('## Directions');
    body.push('');
    for (const dir of recipe.directions) {
      body.push(dir);
    }
    body.push('');
  }

  if (recipe.source_notes) {
    body.push('## Notes & Source');
    body.push('');
    body.push(recipe.source_notes);
    body.push('');
  }

  if (recipe.card_ids && recipe.card_ids.length > 0) {
    body.push('## Archival Card Scans');
    body.push('');
    for (const cid of recipe.card_ids) {
      const fname = cid.toLowerCase().endsWith('.jpg') || cid.toLowerCase().endsWith('.png') ? cid : `${cid}.jpg`;
      if (useWikilinks) {
        body.push(`![[${fname}]]`);
      } else {
        body.push(`![Archival Card Scan](${fname})`);
      }
    }
    body.push('');
  }

  return body.join('\n');
}

/**
 * Serializes a list of recipes into Obsidian Markdown string.
 */
export function generateMarkdown(recipes: RecipeSchema[], useWikilinks = true): string {
  return recipes.map((r) => recipeToMarkdown(r, useWikilinks)).join('\n\n---\n\n');
}

/**
 * Initiates browser download of Obsidian Markdown file.
 */
export function downloadMarkdown(recipes: RecipeSchema[], filename = 'recipeboxer_recipes.md', useWikilinks = true): void {
  const content = generateMarkdown(recipes, useWikilinks);
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
