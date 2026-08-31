/**
 * Initiates browser download from backend binary export endpoints (/api/export/...) with curation support.
 */

import { ExportCurationOptions } from '../types/export';

export async function downloadFromApiEndpoint(
  endpoint: string,
  params: Record<string, string | boolean | undefined> = {},
  fallbackFilename: string
): Promise<void> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value));
    }
  }

  const url = `${endpoint}${query.toString() ? '?' + query.toString() : ''}`;
  try {
    const res = await fetch(url);
    if (!res.ok) {
      throw new Error(`Export failed with HTTP status ${res.status}`);
    }
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = blobUrl;
    link.setAttribute('download', fallbackFilename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(blobUrl);
  } catch (err) {
    console.error(`Failed to download export from ${endpoint}:`, err);
    // Fallback: direct window navigation
    window.location.href = url;
  }
}

export function downloadDocx(
  sessionId?: string,
  filename = 'recipeboxer_cookbook.docx',
  curation?: ExportCurationOptions
): void {
  downloadFromApiEndpoint(
    '/api/export/docx',
    {
      session_id: sessionId,
      filename,
      recipe_order: curation?.recipe_order?.join(','),
      grouping_preset: curation?.grouping_preset,
      include_section_dividers: curation?.include_section_dividers,
      include_toc: curation?.include_toc,
      toc_title: curation?.toc_title,
    },
    filename
  );
}

export function downloadPdf(
  sessionId?: string,
  filename = 'recipeboxer_cookbook.pdf',
  curation?: ExportCurationOptions
): void {
  downloadFromApiEndpoint(
    '/api/export/pdf',
    {
      session_id: sessionId,
      filename,
      recipe_order: curation?.recipe_order?.join(','),
      grouping_preset: curation?.grouping_preset,
      include_section_dividers: curation?.include_section_dividers,
      include_toc: curation?.include_toc,
      toc_title: curation?.toc_title,
    },
    filename
  );
}

export function downloadPaprika(
  sessionId?: string,
  filename = 'recipeboxer_export.paprikarecipes',
  curation?: ExportCurationOptions
): void {
  downloadFromApiEndpoint(
    '/api/export/paprika',
    {
      session_id: sessionId,
      filename,
      recipe_order: curation?.recipe_order?.join(','),
      grouping_preset: curation?.grouping_preset,
      include_section_dividers: curation?.include_section_dividers,
      include_toc: curation?.include_toc,
      toc_title: curation?.toc_title,
    },
    filename
  );
}

export function downloadProjectArchive(
  sessionId?: string,
  filename = 'recipeboxer_project.recipeboxer'
): void {
  downloadFromApiEndpoint('/api/project/export', { session_id: sessionId, filename }, filename);
}
