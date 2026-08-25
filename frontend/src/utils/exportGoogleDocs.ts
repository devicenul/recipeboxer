import { RecipeSchema } from '../types/recipe';

function escapeHtml(str: string | undefined | null): string {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Generates self-contained HTML for Google Docs interchange and archival preview.
 */
export function generateGoogleDocsHtml(recipes: RecipeSchema[], title = 'RecipeBoxer Archival Cookbook'): string {
  const htmlParts: string[] = [
    '<!DOCTYPE html>',
    '<html lang="en">',
    '<head>',
    '<meta charset="utf-8">',
    `<title>${escapeHtml(title)}</title>`,
    '<style>',
    'body { font-family: Georgia, serif; line-height: 1.6; color: #222; background: #faf8f5; padding: 24px; }',
    '.cookbook { max-width: 780px; margin: 0 auto; background: #fff; padding: 40px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.06); }',
    '.recipe-card { margin-bottom: 40px; padding-bottom: 30px; border-bottom: 2px dashed #ddd; page-break-after: always; }',
    '.recipe-card:last-child { border-bottom: none; }',
    'h1.title { color: #23432b; font-size: 24px; margin-bottom: 6px; border-bottom: 2px solid #23432b; padding-bottom: 4px; }',
    'p.desc { font-style: italic; color: #555; margin-bottom: 16px; }',
    '.meta-table { width: 100%; border-collapse: collapse; background: #f4f7f4; border: 1px solid #dbe5db; margin-bottom: 16px; font-size: 13px; }',
    '.meta-table td { padding: 6px 12px; }',
    'h2.section { font-size: 16px; color: #2d5a2d; text-transform: uppercase; margin-top: 18px; margin-bottom: 8px; font-family: sans-serif; }',
    'ul.ing-list { margin: 0 0 16px 20px; }',
    'ol.dir-list { margin: 0 0 16px 20px; }',
    '.tag-badge { background: #eef2ee; color: #2d5a2d; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-right: 4px; }',
    '</style>',
    '</head>',
    '<body>',
    '<div class="cookbook">',
  ];

  for (const recipe of recipes) {
    htmlParts.push('<div class="recipe-card">');
    htmlParts.push(`<h1 class="title">${escapeHtml(recipe.title || 'Untitled Recipe')}</h1>`);
    if (recipe.description) {
      htmlParts.push(`<p class="desc">${escapeHtml(recipe.description)}</p>`);
    }

    htmlParts.push('<table class="meta-table"><tr>');
    htmlParts.push(`<td><strong>Yield:</strong> ${escapeHtml(recipe.yield_servings || '—')}</td>`);
    htmlParts.push(`<td><strong>Prep:</strong> ${escapeHtml(recipe.prep_time || '—')}</td>`);
    htmlParts.push(`<td><strong>Cook:</strong> ${escapeHtml(recipe.cook_time || '—')}</td>`);
    htmlParts.push(`<td><strong>Total:</strong> ${escapeHtml(recipe.total_time || '—')}</td>`);
    htmlParts.push('</tr></table>');

    if (recipe.ingredients && recipe.ingredients.length > 0) {
      htmlParts.push('<h2 class="section">Ingredients</h2>');
      htmlParts.push('<ul class="ing-list">');
      for (const ing of recipe.ingredients) {
        htmlParts.push(`<li>${escapeHtml(ing)}</li>`);
      }
      htmlParts.push('</ul>');
    }

    if (recipe.directions && recipe.directions.length > 0) {
      htmlParts.push('<h2 class="section">Directions</h2>');
      htmlParts.push('<ol class="dir-list">');
      for (const step of recipe.directions) {
        const clean = step.replace(/^\d+[\.\)\-:]\s*/, '').replace(/^Step\s*\d+[\.\)\-:]\s*/i, '').trim();
        htmlParts.push(`<li>${escapeHtml(clean)}</li>`);
      }
      htmlParts.push('</ol>');
    }

    if (recipe.tags && recipe.tags.length > 0) {
      htmlParts.push('<div><strong>Tags:</strong> ');
      for (const t of recipe.tags) {
        htmlParts.push(`<span class="tag-badge">${escapeHtml(t)}</span>`);
      }
      htmlParts.push('</div>');
    }

    if (recipe.source_notes) {
      htmlParts.push(`<p style="font-size:12px; color:#777; margin-top:8px;"><em>Source: ${escapeHtml(recipe.source_notes)}</em></p>`);
    }

    htmlParts.push('</div>');
  }

  htmlParts.push('</div>');
  htmlParts.push('</body>');
  htmlParts.push('</html>');

  return htmlParts.join('\n');
}

/**
 * Initiates browser download of Google Docs HTML archive.
 */
export function downloadGoogleDocsHtml(recipes: RecipeSchema[], filename = 'recipeboxer_google_docs.html'): void {
  const content = generateGoogleDocsHtml(recipes);
  const blob = new Blob([content], { type: 'text/html;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
