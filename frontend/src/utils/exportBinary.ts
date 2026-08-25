/**
 * Initiates browser download from backend binary export endpoints (/api/export/...).
 */
export async function downloadFromApiEndpoint(
  endpoint: string,
  params: Record<string, string | undefined> = {},
  fallbackFilename: string
): Promise<void> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, value);
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

export function downloadDocx(sessionId?: string, filename = 'recipeboxer_cookbook.docx'): void {
  downloadFromApiEndpoint('/api/export/docx', { session_id: sessionId }, filename);
}

export function downloadPdf(sessionId?: string, filename = 'recipeboxer_cookbook.pdf'): void {
  downloadFromApiEndpoint('/api/export/pdf', { session_id: sessionId }, filename);
}

export function downloadPaprika(sessionId?: string, filename = 'recipeboxer_export.paprikarecipes'): void {
  downloadFromApiEndpoint('/api/export/paprika', { session_id: sessionId }, filename);
}
