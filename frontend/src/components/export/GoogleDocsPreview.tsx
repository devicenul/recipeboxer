import React, { useState } from 'react';
import {
  Copy,
  Check,
  ExternalLink,
  BookOpen,
  Download,
  Loader2,
  AlertCircle,
  FileCode,
} from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';
import { generateGoogleDocsHtml, downloadGoogleDocsHtml } from '../../utils/exportGoogleDocs';
import {
  requestGoogleAccessToken,
  GOOGLE_SCOPES,
} from '../../utils/googleAuth';
import { CuratedCookbook, ExportCurationOptions } from '../../types/export';
import { Button } from '../common/Button';
import { Badge } from '../common/Badge';

export interface GoogleDocsPreviewProps {
  recipes: RecipeSchema[];
  sessionId?: string;
  customFilename?: string;
  curation?: CuratedCookbook;
  curationOptions?: ExportCurationOptions;
}

export const GoogleDocsPreview: React.FC<GoogleDocsPreviewProps> = ({
  recipes,
  sessionId,
  customFilename,
  curation,
  curationOptions,
}) => {
  const [copiedHtml, setCopiedHtml] = useState(false);
  const [copiedJson, setCopiedJson] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [createdDocUrl, setCreatedDocUrl] = useState<string | null>(null);
  const [createdDocTitle, setCreatedDocTitle] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const documentTitle =
    recipes.length === 1 && recipes[0].title
      ? `RecipeBoxer - ${recipes[0].title}`
      : 'RecipeBoxer Archival Cookbook';

  const htmlContent = generateGoogleDocsHtml(recipes, documentTitle, curation);

  const sanitizeAccessToken = (raw: string): string => {
    return raw
      .trim()
      .replace(/^["']|["']$/g, '')
      .replace(/^Bearer\s+/i, '')
      .trim();
  };

  const handleCopyHtml = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(htmlContent);
      }
      setCopiedHtml(true);
      setTimeout(() => setCopiedHtml(false), 2000);
    } catch {
      // ignore clipboard error
    }
  };

  const handleCopyBatchJson = async () => {
    let textToCopy = '';
    try {
      const res = await fetch(
        `/api/export/google-docs?${sessionId ? `session_id=${encodeURIComponent(sessionId)}&` : ''}format=batch_requests&title=${encodeURIComponent(documentTitle)}`
      );
      if (res.ok) {
        const json = await res.json();
        textToCopy = JSON.stringify(json, null, 2);
      }
    } catch {
      // ignore, proceed to fallback
    }
    if (!textToCopy) {
      textToCopy = JSON.stringify({ title: documentTitle, recipe_count: recipes.length }, null, 2);
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(textToCopy);
      }
      setCopiedJson(true);
      setTimeout(() => setCopiedJson(false), 2000);
    } catch {
      // ignore clipboard error
    }
  };

  const handleDownloadHtml = () => {
    const filename = customFilename || 'recipeboxer_google_docs.html';
    downloadGoogleDocsHtml(recipes, filename);
  };

  const handleCreateDocumentWithToken = async (token: string) => {
    const cleanToken = sanitizeAccessToken(token);
    if (!cleanToken) {
      setErrorMessage('Please provide a valid Google OAuth access token');
      return;
    }

    setIsCreating(true);
    setErrorMessage(null);
    setCreatedDocUrl(null);

    try {
      const response = await fetch('/api/export/google-docs/create-document', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          session_id: sessionId,
          access_token: cleanToken,
          document_title: documentTitle,
          folder_name: 'RecipeBoxer Scans',
        }),
      });

      let data: any = null;
      try {
        data = await response.json();
      } catch {
        // Non-JSON or empty response from server
      }

      if (!response.ok) {
        let detail = '';
        if (typeof data?.detail === 'string') {
          detail = data.detail;
        } else if (Array.isArray(data?.detail)) {
          detail = data.detail.map((d: any) => d?.msg || JSON.stringify(d)).join('; ');
        } else if (data?.detail && typeof data.detail === 'object') {
          detail = JSON.stringify(data.detail);
        } else if (typeof data?.message === 'string') {
          detail = data.message;
        } else {
          detail = `Server returned HTTP ${response.status}${response.statusText ? ` (${response.statusText})` : ''}`;
        }
        throw new Error(detail);
      }

      setCreatedDocUrl(data?.url);
      setCreatedDocTitle(data?.title || documentTitle);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(`Google Docs export failed: ${msg}`);
    } finally {
      setIsCreating(false);
    }
  };

  const triggerGoogleOAuthFlow = async () => {
    setIsCreating(true);
    setErrorMessage(null);
    setCreatedDocUrl(null);

    try {
      // Direct 1-click Google Identity Services OAuth popup flow
      const token = await requestGoogleAccessToken({
        scope: GOOGLE_SCOPES.DOCS_EXPORT,
      });
      const cleanToken = sanitizeAccessToken(token);
      await handleCreateDocumentWithToken(cleanToken);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(`Google Docs export failed: ${msg}`);
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Primary Action Card: Authentic Google Docs Integration */}
      <div className="p-4 rounded-xl border border-emerald-200 dark:border-emerald-900/60 bg-emerald-50/40 dark:bg-emerald-950/20 flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
            <div>
              <h4 className="text-xs font-bold text-slate-900 dark:text-slate-100">
                Direct Google Docs &amp; Drive API Publishing
              </h4>
              <p className="text-[11px] text-slate-600 dark:text-slate-400">
                Creates an authentic Google Doc in your Google Drive with formatted typography and embedded card scans.
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="warning" size="sm">Coming Soon</Badge>
            <Button
              variant="success"
              size="sm"
              onClick={triggerGoogleOAuthFlow}
              disabled={true}
              title="Support In Progress"
              className="disabled:opacity-50 disabled:cursor-not-allowed"
              leftIcon={
                isCreating ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <BookOpen className="w-3.5 h-3.5" />
                )
              }
            >
              {isCreating ? 'Creating Google Doc...' : 'Export directly to Google Docs'}
            </Button>
          </div>
        </div>

        {/* Success Alert */}
        {createdDocUrl && (
          <div className="p-3 rounded-lg bg-emerald-100 dark:bg-emerald-900/50 border border-emerald-300 dark:border-emerald-800 flex items-center justify-between gap-3 text-xs text-emerald-900 dark:text-emerald-100">
            <div className="flex items-center gap-2">
              <Check className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
              <span>
                <strong>Document Created:</strong> {createdDocTitle} ({recipes.length} recipe{recipes.length > 1 ? 's' : ''})
              </span>
            </div>
            <a
              href={createdDocUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-xs transition-colors shrink-0"
            >
              <span>Open in Google Docs</span>
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </div>
        )}

        {/* Error Alert */}
        {errorMessage && (
          <div
            role="alert"
            aria-live="assertive"
            className="p-3 rounded-lg bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 flex flex-col gap-2 text-xs text-rose-800 dark:text-rose-200"
          >
            <div className="flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-rose-600 shrink-0 mt-0.5" />
              <span className="flex-1">{errorMessage}</span>
            </div>
          </div>
        )}
      </div>

      {/* Offline Fallbacks & Preview Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600 dark:text-slate-400">
        <span>Google Docs Interchange Archive ({recipes.length} recipes)</span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleCopyHtml}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
          >
            {copiedHtml ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copiedHtml ? 'Copied HTML' : 'Copy HTML'}</span>
          </button>

          <button
            type="button"
            onClick={handleCopyBatchJson}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
          >
            {copiedJson ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <FileCode className="w-3.5 h-3.5" />}
            <span>{copiedJson ? 'Copied JSON' : 'Copy Batch JSON'}</span>
          </button>

          <button
            type="button"
            onClick={handleDownloadHtml}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md text-emerald-600 dark:text-emerald-400 font-semibold hover:bg-emerald-50 dark:hover:bg-emerald-950/40 transition-colors cursor-pointer"
          >
            <Download className="w-3.5 h-3.5" />
            <span>Download HTML</span>
          </button>
        </div>
      </div>

      {/* Interactive Rich Preview */}
      <div className="border border-slate-200 dark:border-slate-800 rounded-xl overflow-hidden bg-white dark:bg-slate-900 max-h-96 overflow-y-auto p-4 shadow-inner">
        <div className="text-xs text-slate-500 dark:text-slate-400 pb-2 mb-3 border-b border-slate-100 dark:border-slate-800 flex items-center gap-1">
          <ExternalLink className="w-3 h-3" />
          <span>Interactive Rich Document Preview</span>
        </div>
        <div
          className="prose dark:prose-invert max-w-none text-xs"
          dangerouslySetInnerHTML={{ __html: htmlContent }}
        />
      </div>
    </div>
  );
};
