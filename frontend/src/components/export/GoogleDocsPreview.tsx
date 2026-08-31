import React, { useState, useEffect } from 'react';
import {
  Copy,
  Check,
  ExternalLink,
  BookOpen,
  Download,
  Loader2,
  AlertCircle,
  FileCode,
  Key,
  Settings,
  HelpCircle,
} from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';
import { generateGoogleDocsHtml, downloadGoogleDocsHtml } from '../../utils/exportGoogleDocs';
import {
  requestGoogleAccessToken,
  GOOGLE_SCOPES,
  getGoogleClientId,
  getStoredGoogleClientId,
  setStoredGoogleClientId,
  clearStoredGoogleClientId,
  getEnvGoogleClientId,
  hasGoogleClientId,
} from '../../utils/googleAuth';
import { CuratedCookbook, ExportCurationOptions } from '../../types/export';
import { Button } from '../common/Button';

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
  const [accessToken, setAccessToken] = useState('');
  const [manualToken, setManualToken] = useState('');
  const [isManualTokenOpen, setIsManualTokenOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [showSetupGuide, setShowSetupGuide] = useState(false);
  const [clientIdInput, setClientIdInput] = useState('');
  const [storedClientId, setStoredClientId] = useState('');
  const [activeClientId, setActiveClientId] = useState('');
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const [isCreating, setIsCreating] = useState(false);
  const [createdDocUrl, setCreatedDocUrl] = useState<string | null>(null);
  const [createdDocTitle, setCreatedDocTitle] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const documentTitle =
    recipes.length === 1 && recipes[0].title
      ? `RecipeBoxer - ${recipes[0].title}`
      : 'RecipeBoxer Archival Cookbook';

  const htmlContent = generateGoogleDocsHtml(recipes, documentTitle, curation);

  // Sync client ID states on mount or change
  useEffect(() => {
    const stored = getStoredGoogleClientId();
    const active = getGoogleClientId();
    setStoredClientId(stored);
    setActiveClientId(active);
    setClientIdInput(stored || active || '');
  }, []);

  const refreshClientIds = () => {
    const stored = getStoredGoogleClientId();
    const active = getGoogleClientId();
    setStoredClientId(stored);
    setActiveClientId(active);
    setClientIdInput(stored || active || '');
  };

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

  const handleSaveClientId = () => {
    const trimmed = clientIdInput.trim().replace(/^["']|["']$/g, '').trim();
    if (!trimmed) {
      handleClearClientId();
      return;
    }
    setStoredGoogleClientId(trimmed);
    setAccessToken('');
    refreshClientIds();
    setStatusMessage('Client ID saved to local storage.');
    setErrorMessage(null);
    setTimeout(() => setStatusMessage(null), 3000);
  };

  const handleClearClientId = () => {
    clearStoredGoogleClientId();
    setAccessToken('');
    refreshClientIds();
    setStatusMessage('Stored Client ID cleared. Reverted to environment default.');
    setTimeout(() => setStatusMessage(null), 3000);
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
        if (response.status === 401 || response.status === 403) {
          setAccessToken('');
        }
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
      if (
        msg.includes('invalid_client') ||
        msg.includes('not configured') ||
        msg.includes('Client ID')
      ) {
        setIsSettingsOpen(true);
        setIsManualTokenOpen(false);
      } else if (
        msg.includes('401') ||
        msg.includes('403') ||
        msg.toLowerCase().includes('expired') ||
        msg.toLowerCase().includes('scope') ||
        msg.toLowerCase().includes('permission') ||
        msg.toLowerCase().includes('unauthorized') ||
        msg.toLowerCase().includes('popup') ||
        msg.toLowerCase().includes('blocked') ||
        msg.toLowerCase().includes('sdk') ||
        msg.toLowerCase().includes('script') ||
        msg.toLowerCase().includes('offline') ||
        msg.toLowerCase().includes('failed to load') ||
        msg.toLowerCase().includes('unavailable')
      ) {
        setIsManualTokenOpen(true);
        setIsSettingsOpen(false);
      }
    } finally {
      setIsCreating(false);
    }
  };

  const handleManualTokenSubmit = async () => {
    const cleanToken = sanitizeAccessToken(manualToken);
    if (!cleanToken) {
      setErrorMessage('Please provide a valid Google OAuth access token');
      return;
    }
    setAccessToken(cleanToken);
    await handleCreateDocumentWithToken(cleanToken);
  };

  const triggerGoogleOAuthFlow = async () => {
    // 1. Check if an active token is already stored in state
    if (accessToken.trim()) {
      await handleCreateDocumentWithToken(accessToken.trim());
      return;
    }

    const currentClientId = getGoogleClientId();
    if (!currentClientId) {
      setErrorMessage(
        'Google OAuth Client ID is not configured. Please configure your Client ID in OAuth Settings or use the Manual Access Token fallback.'
      );
      setIsSettingsOpen(true);
      setIsManualTokenOpen(false);
      return;
    }

    setIsCreating(true);
    setErrorMessage(null);
    setCreatedDocUrl(null);

    try {
      // 2. Interactive 1-click Google Identity Services OAuth popup flow
      const token = await requestGoogleAccessToken({
        scope: GOOGLE_SCOPES.DOCS_EXPORT,
      });
      const cleanToken = sanitizeAccessToken(token);
      setAccessToken(cleanToken);
      await handleCreateDocumentWithToken(cleanToken);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(`Google Docs export failed: ${msg}`);
      if (
        msg.includes('invalid_client') ||
        msg.includes('Client ID is not configured') ||
        msg.includes('not configured') ||
        msg.includes('Client ID')
      ) {
        setIsSettingsOpen(true);
        setIsManualTokenOpen(false);
      } else if (
        msg.toLowerCase().includes('popup') ||
        msg.toLowerCase().includes('blocked') ||
        msg.toLowerCase().includes('401') ||
        msg.toLowerCase().includes('403') ||
        msg.toLowerCase().includes('sdk') ||
        msg.toLowerCase().includes('script') ||
        msg.toLowerCase().includes('offline') ||
        msg.toLowerCase().includes('failed to load') ||
        msg.toLowerCase().includes('unavailable') ||
        msg.toLowerCase().includes('scope') ||
        msg.toLowerCase().includes('permission')
      ) {
        setIsManualTokenOpen(true);
        setIsSettingsOpen(false);
      }
      setIsCreating(false);
    }
  };

  const isConfigured = hasGoogleClientId();
  const envClientId = getEnvGoogleClientId();

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
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setIsManualTokenOpen(!isManualTokenOpen);
                if (!isManualTokenOpen) {
                  setIsSettingsOpen(false);
                }
              }}
              aria-expanded={isManualTokenOpen}
              aria-controls="manual-access-token-drawer"
              leftIcon={<Key className="w-3.5 h-3.5" />}
            >
              {isManualTokenOpen ? 'Hide Manual Access' : 'Manual Access Fallback'}
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setIsSettingsOpen(!isSettingsOpen);
                if (!isSettingsOpen) {
                  setIsManualTokenOpen(false);
                }
              }}
              aria-expanded={isSettingsOpen}
              aria-controls="google-client-id-drawer"
              leftIcon={<Settings className="w-3.5 h-3.5" />}
            >
              {isSettingsOpen ? 'Hide Settings' : 'OAuth Settings'}
            </Button>

            <Button
              variant="success"
              size="sm"
              onClick={triggerGoogleOAuthFlow}
              disabled={isCreating}
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

        {/* Unconfigured Alert Banner */}
        {!isConfigured && (
          <div className="p-3 rounded-lg bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800/80 flex flex-wrap items-center justify-between gap-2 text-xs text-amber-900 dark:text-amber-200">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />
              <span>
                <strong>Google OAuth Client ID is not configured:</strong> 1-click export requires an OAuth Client ID, or you can paste a manual access token below.
              </span>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                onClick={() => {
                  setIsSettingsOpen(true);
                  setIsManualTokenOpen(false);
                }}
                className="px-2.5 py-1 text-xs font-semibold rounded-md bg-amber-200 dark:bg-amber-800 hover:bg-amber-300 dark:hover:bg-amber-700 text-amber-900 dark:text-amber-100 transition-colors cursor-pointer"
              >
                Configure Client ID
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsManualTokenOpen(true);
                  setIsSettingsOpen(false);
                }}
                className="px-2.5 py-1 text-xs font-semibold rounded-md bg-amber-100 dark:bg-amber-900/60 hover:bg-amber-200 dark:hover:bg-amber-800 text-amber-800 dark:text-amber-200 transition-colors cursor-pointer"
              >
                Enter Manual Token
              </button>
            </div>
          </div>
        )}

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

        {/* Error Alert with Quick Diagnostic Actions */}
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

            <div className="pt-2 mt-1 border-t border-rose-200/60 dark:border-rose-800/60 flex flex-wrap items-center gap-2">
              <span className="text-[11px] font-semibold text-rose-900 dark:text-rose-300">
                Troubleshooting:
              </span>
              <button
                type="button"
                onClick={() => {
                  setIsSettingsOpen(true);
                  setIsManualTokenOpen(false);
                }}
                className="px-2 py-0.5 rounded text-[11px] font-medium bg-rose-100 dark:bg-rose-900/60 hover:bg-rose-200 dark:hover:bg-rose-800 text-rose-900 dark:text-rose-200 transition-colors cursor-pointer"
              >
                Configure Client ID
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsManualTokenOpen(true);
                  setIsSettingsOpen(false);
                }}
                className="px-2 py-0.5 rounded text-[11px] font-medium bg-rose-100 dark:bg-rose-900/60 hover:bg-rose-200 dark:hover:bg-rose-800 text-rose-900 dark:text-rose-200 transition-colors cursor-pointer"
              >
                Enter Manual Token
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsSettingsOpen(true);
                  setShowSetupGuide(true);
                  setIsManualTokenOpen(false);
                }}
                className="px-2 py-0.5 rounded text-[11px] font-medium bg-rose-100 dark:bg-rose-900/60 hover:bg-rose-200 dark:hover:bg-rose-800 text-rose-900 dark:text-rose-200 transition-colors cursor-pointer"
              >
                View Setup Guide
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Manual Access Token Fallback Drawer */}
      {isManualTokenOpen && (
        <div
          id="manual-access-token-drawer"
          role="region"
          aria-label="Manual Access Token Fallback"
          className="p-4 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/60 flex flex-col gap-3"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Key className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
              <h5 className="text-xs font-bold text-slate-900 dark:text-slate-100">
                Manual Google OAuth Access Token Fallback
              </h5>
            </div>
            <button
              type="button"
              onClick={() => setIsManualTokenOpen(false)}
              className="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 cursor-pointer"
            >
              Close
            </button>
          </div>

          <p className="text-[11px] text-slate-600 dark:text-slate-400">
            Paste a temporary Google OAuth Bearer token (with <code className="px-1 py-0.5 rounded bg-slate-200 dark:bg-slate-800 text-[10px]">documents</code> and <code className="px-1 py-0.5 rounded bg-slate-200 dark:bg-slate-800 text-[10px]">drive.file</code> scopes) from{' '}
            <a
              href="https://developers.google.com/oauthplayground"
              target="_blank"
              rel="noopener noreferrer"
              className="text-emerald-600 dark:text-emerald-400 underline font-medium hover:text-emerald-700 inline-flex items-center gap-0.5"
            >
              <span>Google OAuth 2.0 Playground</span>
              <ExternalLink className="w-2.5 h-2.5" />
            </a>{' '}
            or <code className="px-1 py-0.5 rounded bg-slate-200 dark:bg-slate-800 text-[10px]">gcloud auth print-access-token</code> to export directly without requiring GIS popup configuration.
          </p>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="manual-access-token-input"
              className="text-xs font-semibold text-slate-700 dark:text-slate-300"
            >
              Google OAuth Access Token
            </label>
            <div className="flex flex-wrap sm:flex-nowrap gap-2">
              <input
                id="manual-access-token-input"
                type="password"
                value={manualToken}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                onChange={(e) => setManualToken(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && sanitizeAccessToken(manualToken) && !isCreating) {
                    e.preventDefault();
                    handleManualTokenSubmit();
                  }
                }}
                placeholder="ya29.a0..."
                className="flex-1 min-w-[200px] px-3 py-2 text-xs font-mono rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
              <Button
                variant="success"
                size="sm"
                disabled={isCreating || !sanitizeAccessToken(manualToken)}
                onClick={handleManualTokenSubmit}
                leftIcon={
                  isCreating ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <BookOpen className="w-3.5 h-3.5" />
                  )
                }
              >
                {isCreating ? 'Exporting...' : 'Export with Access Token'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* OAuth Client ID Settings Drawer */}
      {isSettingsOpen && (
        <div
          id="google-client-id-drawer"
          role="region"
          aria-label="OAuth Client ID Settings"
          className="p-4 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/60 flex flex-col gap-3"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Settings className="w-4 h-4 text-blue-600 dark:text-blue-400" />
              <h5 className="text-xs font-bold text-slate-900 dark:text-slate-100">
                Google OAuth Client ID Configuration
              </h5>
            </div>
            <button
              type="button"
              onClick={() => setIsSettingsOpen(false)}
              className="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 cursor-pointer"
            >
              Close
            </button>
          </div>

          <p className="text-[11px] text-slate-600 dark:text-slate-400">
            Configure the Google OAuth 2.0 Web Client ID used for 1-click Google Docs publishing.
            Settings are stored locally in your browser (<code className="px-1 py-0.5 rounded bg-slate-200 dark:bg-slate-800 text-[10px]">localStorage</code>).
          </p>

          <div className="flex items-center gap-2 text-xs">
            <span className="text-slate-500 dark:text-slate-400">Active Source:</span>
            {storedClientId ? (
              <span className="px-2 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-900/40 text-emerald-800 dark:text-emerald-300 font-medium text-[11px]">
                Custom (localStorage)
              </span>
            ) : envClientId ? (
              <span className="px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-300 font-medium text-[11px]">
                Environment (VITE_GOOGLE_CLIENT_ID)
              </span>
            ) : (
              <span className="px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/40 text-amber-800 dark:text-amber-300 font-medium text-[11px]">
                Not Configured
              </span>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor="google-client-id-input"
              className="text-xs font-semibold text-slate-700 dark:text-slate-300"
            >
              Google OAuth Client ID
            </label>
            <div className="flex flex-wrap gap-2">
              <input
                id="google-client-id-input"
                type="text"
                value={clientIdInput}
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                onChange={(e) => setClientIdInput(e.target.value)}
                onKeyDown={(e) => {
                  const cleaned = clientIdInput.trim().replace(/^["']|["']$/g, '').trim();
                  if (e.key === 'Enter' && cleaned && cleaned !== storedClientId) {
                    e.preventDefault();
                    handleSaveClientId();
                  }
                }}
                placeholder="1234567890-abc.apps.googleusercontent.com"
                className="flex-1 min-w-[240px] px-3 py-2 text-xs font-mono rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <Button
                variant="primary"
                size="sm"
                onClick={handleSaveClientId}
                disabled={!clientIdInput.trim().replace(/^["']|["']$/g, '').trim() || clientIdInput.trim().replace(/^["']|["']$/g, '').trim() === storedClientId}
              >
                Save Client ID
              </Button>
              {storedClientId && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleClearClientId}
                >
                  Reset / Clear
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowSetupGuide(!showSetupGuide)}
                aria-expanded={showSetupGuide}
                aria-controls="google-setup-guide"
                leftIcon={<HelpCircle className="w-3.5 h-3.5" />}
              >
                {showSetupGuide ? 'Hide Guide' : 'Setup Guide'}
              </Button>
            </div>
          </div>

          {statusMessage && (
            <div
              role="status"
              aria-live="polite"
              className="text-xs text-emerald-600 dark:text-emerald-400 font-medium"
            >
              {statusMessage}
            </div>
          )}

          {/* Setup Guidance */}
          {showSetupGuide && (
            <div
              id="google-setup-guide"
              role="region"
              aria-label="Google Cloud Setup Guide"
              className="mt-2 p-3 rounded-lg bg-blue-50/60 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-900 text-xs text-slate-700 dark:text-slate-300 flex flex-col gap-2"
            >
              <div className="font-semibold text-blue-900 dark:text-blue-200 flex items-center gap-1.5">
                <HelpCircle className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
                <span>How to create a Google OAuth Client ID:</span>
              </div>
              <ol className="list-decimal list-inside space-y-1.5 text-[11px] text-slate-600 dark:text-slate-400">
                <li>
                  Go to the{' '}
                  <a
                    href="https://console.cloud.google.com/apis/credentials"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-600 dark:text-blue-400 underline font-medium hover:text-blue-700 inline-flex items-center gap-0.5"
                  >
                    <span>Google Cloud Console Credentials</span>
                    <ExternalLink className="w-2.5 h-2.5" />
                  </a>{' '}
                  page.
                </li>
                <li>
                  Ensure <strong>Google Docs API</strong> and <strong>Google Drive API</strong> are enabled in your project&apos;s API Library.
                </li>
                <li>
                  Click <strong>Create Credentials</strong> &rarr; <strong>OAuth client ID</strong>.
                </li>
                <li>
                  Select <strong>Web application</strong> as Application type.
                </li>
                <li>
                  Under <strong>Authorized JavaScript origins</strong>, add your frontend origin (e.g.{' '}
                  <code className="px-1 py-0.5 rounded bg-slate-200 dark:bg-slate-800 text-[10px]">
                    {typeof window !== 'undefined' && window.location?.origin
                      ? window.location.origin
                      : 'http://localhost:5173'}
                  </code>
                  ).
                </li>
                <li>
                  Copy the generated <strong>Client ID</strong> and paste it into the field above, then click <strong>Save Client ID</strong>.
                </li>
              </ol>
            </div>
          )}
        </div>
      )}

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
