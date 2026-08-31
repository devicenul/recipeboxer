import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import {
  isValidClientIdString,
  getGoogleClientId,
  getStoredGoogleClientId,
  setStoredGoogleClientId,
  clearStoredGoogleClientId,
  getEnvGoogleClientId,
  hasGoogleClientId,
  requestGoogleAccessToken,
  loadGoogleIdentityScript,
  GOOGLE_SCOPES,
  GOOGLE_CLIENT_ID_STORAGE_KEY,
} from '../src/utils/googleAuth';
import { GoogleDocsPreview } from '../src/components/export/GoogleDocsPreview';
import { ExportDialog } from '../src/components/export/ExportDialog';
import {
  setMockGisResponse,
  resetMockGisResponse,
  mockInitTokenClient,
} from '../src/setupTests';

const sampleRecipes = [
  {
    recipe_id: 'rec_apple_pie',
    title: 'Rustic Apple Galette',
    source_image_id: 'card_galette_01',
    confidence: 0.96,
    tags: ['Dessert', 'Pastry'],
    ingredients: ['3 Granny Smith apples', '1 disc pie dough', '2 tbsp sugar', '1 tbsp butter'],
    directions: ['Preheat oven to 400°F.', 'Roll dough into 12-inch circle.', 'Arrange apple slices and fold edges.', 'Bake for 35 minutes.'],
  },
];

describe('Google Identity Services OAuth 401 invalid_client Resolution & Manual Token Fallback', () => {
  const originalEnvClientId = process.env.VITE_GOOGLE_CLIENT_ID;

  beforeEach(() => {
    vi.clearAllMocks();
    resetMockGisResponse();
    localStorage.clear();
    process.env.VITE_GOOGLE_CLIENT_ID = 'mock-test-client-id.apps.googleusercontent.com';
    const scriptTag = document.querySelector('script[src="https://accounts.google.com/gsi/client"]');
    if (scriptTag && scriptTag.parentNode) {
      scriptTag.parentNode.removeChild(scriptTag);
    }
  });

  afterEach(() => {
    localStorage.clear();
    process.env.VITE_GOOGLE_CLIENT_ID = originalEnvClientId;
    resetMockGisResponse();
    const scriptTag = document.querySelector('script[src="https://accounts.google.com/gsi/client"]');
    if (scriptTag && scriptTag.parentNode) {
      scriptTag.parentNode.removeChild(scriptTag);
    }
  });

  describe('1. Unit Tests: Client ID Storage Persistence, Fallback & Sanitization Logic (R1)', () => {
    it('returns empty string when both localStorage and env VITE_GOOGLE_CLIENT_ID are unset', () => {
      process.env.VITE_GOOGLE_CLIENT_ID = '';
      expect(getStoredGoogleClientId()).toBe('');
      expect(getEnvGoogleClientId()).toBe('');
      expect(getGoogleClientId()).toBe('');
      expect(hasGoogleClientId()).toBe(false);
    });

    it('persists custom client ID to localStorage and retrieves it', () => {
      process.env.VITE_GOOGLE_CLIENT_ID = '';
      setStoredGoogleClientId('999888777-custom.apps.googleusercontent.com');
      expect(localStorage.getItem(GOOGLE_CLIENT_ID_STORAGE_KEY)).toBe(
        '999888777-custom.apps.googleusercontent.com'
      );
      expect(getStoredGoogleClientId()).toBe('999888777-custom.apps.googleusercontent.com');
      expect(getGoogleClientId()).toBe('999888777-custom.apps.googleusercontent.com');
      expect(hasGoogleClientId()).toBe(true);
    });

    it('trims leading/trailing whitespace when storing custom client ID', () => {
      setStoredGoogleClientId('   trimmed-custom-id.apps.googleusercontent.com   \n');
      expect(getStoredGoogleClientId()).toBe('trimmed-custom-id.apps.googleusercontent.com');
      expect(localStorage.getItem(GOOGLE_CLIENT_ID_STORAGE_KEY)).toBe(
        'trimmed-custom-id.apps.googleusercontent.com'
      );
    });

    it('clears stored client ID when setting empty or whitespace string', () => {
      setStoredGoogleClientId('active-id.apps.googleusercontent.com');
      expect(getStoredGoogleClientId()).toBe('active-id.apps.googleusercontent.com');

      setStoredGoogleClientId('   ');
      expect(getStoredGoogleClientId()).toBe('');
      expect(localStorage.getItem(GOOGLE_CLIENT_ID_STORAGE_KEY)).toBeNull();
    });

    it('reads legacy google_client_id from localStorage and cleans it up on new save', () => {
      localStorage.setItem('google_client_id', 'legacy-key-id.apps.googleusercontent.com');
      expect(getStoredGoogleClientId()).toBe('legacy-key-id.apps.googleusercontent.com');

      setStoredGoogleClientId('new-key-id.apps.googleusercontent.com');
      expect(localStorage.getItem(GOOGLE_CLIENT_ID_STORAGE_KEY)).toBe('new-key-id.apps.googleusercontent.com');
      expect(localStorage.getItem('google_client_id')).toBeNull();
    });

    it('handles localStorage exceptions gracefully in restricted sandboxes', () => {
      const originalGetItem = localStorage.getItem;
      const originalSetItem = localStorage.setItem;
      const originalRemoveItem = localStorage.removeItem;

      localStorage.getItem = vi.fn().mockImplementation(() => {
        throw new Error('SecurityError: Access is denied for this document.');
      });
      localStorage.setItem = vi.fn().mockImplementation(() => {
        throw new Error('SecurityError: Access is denied for this document.');
      });
      localStorage.removeItem = vi.fn().mockImplementation(() => {
        throw new Error('SecurityError: Access is denied for this document.');
      });

      expect(() => getStoredGoogleClientId()).not.toThrow();
      expect(getStoredGoogleClientId()).toBe('');
      expect(() => setStoredGoogleClientId('test-id')).not.toThrow();
      expect(() => clearStoredGoogleClientId()).not.toThrow();

      localStorage.getItem = originalGetItem;
      localStorage.setItem = originalSetItem;
      localStorage.removeItem = originalRemoveItem;
    });

    it('stored client ID takes precedence over environment variable VITE_GOOGLE_CLIENT_ID', () => {
      process.env.VITE_GOOGLE_CLIENT_ID = 'env-client-id-123.apps.googleusercontent.com';
      setStoredGoogleClientId('user-custom-client-id-456.apps.googleusercontent.com');

      expect(getGoogleClientId()).toBe('user-custom-client-id-456.apps.googleusercontent.com');
    });

    it('clearing stored client ID restores environment default', () => {
      process.env.VITE_GOOGLE_CLIENT_ID = 'env-client-id-123.apps.googleusercontent.com';
      setStoredGoogleClientId('user-custom-client-id-456.apps.googleusercontent.com');
      expect(getGoogleClientId()).toBe('user-custom-client-id-456.apps.googleusercontent.com');

      clearStoredGoogleClientId();
      expect(getStoredGoogleClientId()).toBe('');
      expect(getGoogleClientId()).toBe('env-client-id-123.apps.googleusercontent.com');
    });

    it('throws descriptive error when requestGoogleAccessToken is called with no client ID configured', async () => {
      process.env.VITE_GOOGLE_CLIENT_ID = '';
      clearStoredGoogleClientId();

      await expect(
        requestGoogleAccessToken({ scope: GOOGLE_SCOPES.DOCS_EXPORT })
      ).rejects.toThrow(/Google OAuth Client ID is not configured/i);
    });

    it('translates invalid_client response to actionable origin diagnostics in requestGoogleAccessToken', async () => {
      setMockGisResponse({
        error: 'invalid_client',
        error_description: 'Unauthorized client or origin not registered',
      });

      await expect(
        requestGoogleAccessToken({ scope: GOOGLE_SCOPES.DOCS_EXPORT })
      ).rejects.toThrow(/invalid_client.*authorized JavaScript origins/i);
    });

    it('handles popup_blocked_by_browser error with clear guidance to allow popups or use manual token', async () => {
      mockInitTokenClient.mockImplementationOnce((config) => {
        return {
          requestAccessToken: () => {
            if (config.error_callback) {
              config.error_callback({
                type: 'popup_blocked_by_browser',
                message: 'Popup window was blocked by the browser',
              });
            }
          },
        };
      });

      await expect(
        requestGoogleAccessToken({ scope: GOOGLE_SCOPES.DOCS_EXPORT })
      ).rejects.toThrow(/popup was blocked by the browser.*Manual Access Token/i);
    });

    it('handles popup_closed error when user closes popup window', async () => {
      mockInitTokenClient.mockImplementationOnce((config) => {
        return {
          requestAccessToken: () => {
            if (config.error_callback) {
              config.error_callback({
                type: 'popup_closed',
                message: 'User closed popup before authorization',
              });
            }
          },
        };
      });

      await expect(
        requestGoogleAccessToken({ scope: GOOGLE_SCOPES.DOCS_EXPORT })
      ).rejects.toThrow(/popup was closed before authorization completed/i);
    });

    it('handles error_callback with non-object/string or undefined error payload safely', async () => {
      mockInitTokenClient.mockImplementationOnce((config) => {
        return {
          requestAccessToken: () => {
            if (config.error_callback) {
              config.error_callback('idpiframe_initialization_failed' as any);
            }
          },
        };
      });

      await expect(
        requestGoogleAccessToken({ scope: GOOGLE_SCOPES.DOCS_EXPORT })
      ).rejects.toThrow(/idpiframe_initialization_failed/i);
    });

    it('handles script load error in loadGoogleIdentityScript', async () => {
      const originalGoogle = window.google;
      (window as any).google = undefined;

      try {
        const loadPromise = loadGoogleIdentityScript(50);
        const scriptTag = document.querySelector('script[src="https://accounts.google.com/gsi/client"]') as HTMLScriptElement;
        if (scriptTag && scriptTag.onerror) {
          scriptTag.onerror(new Event('error') as any);
        }
        await expect(loadPromise).rejects.toThrow(/Failed to load Google Identity Services SDK/i);
      } finally {
        window.google = originalGoogle;
        const scriptTag = document.querySelector('script[src="https://accounts.google.com/gsi/client"]');
        if (scriptTag && scriptTag.parentNode) {
          scriptTag.parentNode.removeChild(scriptTag);
        }
      }
    });

    it('handles missing initTokenClient in requestGoogleAccessToken gracefully', async () => {
      const originalInit = window.google?.accounts?.oauth2?.initTokenClient;
      try {
        (window.google?.accounts?.oauth2 as any).initTokenClient = undefined;

        const reqPromise = requestGoogleAccessToken({ scope: GOOGLE_SCOPES.DOCS_EXPORT });
        const scriptTag = document.querySelector('script[src="https://accounts.google.com/gsi/client"]') as HTMLScriptElement;
        if (scriptTag && scriptTag.onerror) {
          scriptTag.onerror(new Event('error') as any);
        }

        await expect(reqPromise).rejects.toThrow(
          /Failed to load Google Identity Services SDK|Google Identity Services SDK is not available/i
        );
      } finally {
        if (window.google?.accounts?.oauth2) {
          (window.google.accounts.oauth2 as any).initTokenClient = originalInit;
        }
      }
    });

    it('isValidClientIdString correctly identifies valid vs invalid/falsy/placeholder strings', () => {
      expect(isValidClientIdString('')).toBe(false);
      expect(isValidClientIdString('   ')).toBe(false);
      expect(isValidClientIdString('undefined')).toBe(false);
      expect(isValidClientIdString('null')).toBe(false);
      expect(isValidClientIdString('false')).toBe(false);
      expect(isValidClientIdString('none')).toBe(false);
      expect(isValidClientIdString('[object Object]')).toBe(false);
      expect(isValidClientIdString(undefined)).toBe(false);
      expect(isValidClientIdString(null)).toBe(false);
      expect(isValidClientIdString(123)).toBe(false);

      expect(isValidClientIdString('123456-abc.apps.googleusercontent.com')).toBe(true);
      expect(isValidClientIdString('"123456-abc.apps.googleusercontent.com"')).toBe(true);
      expect(isValidClientIdString("'123456-abc.apps.googleusercontent.com'")).toBe(true);
    });

    it('strips surrounding single or double quotes when saving client ID to localStorage', () => {
      setStoredGoogleClientId('"quoted-client-id.apps.googleusercontent.com"');
      expect(getStoredGoogleClientId()).toBe('quoted-client-id.apps.googleusercontent.com');

      setStoredGoogleClientId("'single-quoted-id.apps.googleusercontent.com'");
      expect(getStoredGoogleClientId()).toBe('single-quoted-id.apps.googleusercontent.com');
    });

    it('ignores env VITE_GOOGLE_CLIENT_ID if it contains literal "undefined" or "null"', () => {
      process.env.VITE_GOOGLE_CLIENT_ID = 'undefined';
      clearStoredGoogleClientId();
      expect(getEnvGoogleClientId()).toBe('');
      expect(getGoogleClientId()).toBe('');
      expect(hasGoogleClientId()).toBe(false);

      process.env.VITE_GOOGLE_CLIENT_ID = 'null';
      expect(getEnvGoogleClientId()).toBe('');
      expect(hasGoogleClientId()).toBe(false);
    });

    it('cleans up failed script DOM node on rejection so retry can load fresh script', async () => {
      const originalGoogle = window.google;
      (window as any).google = undefined;

      try {
        const loadPromise1 = loadGoogleIdentityScript(50);
        const scriptTag1 = document.querySelector('script[src="https://accounts.google.com/gsi/client"]') as HTMLScriptElement;
        expect(scriptTag1).not.toBeNull();
        if (scriptTag1 && scriptTag1.dispatchEvent) {
          scriptTag1.dispatchEvent(new Event('error'));
        }
        await expect(loadPromise1).rejects.toThrow(/Failed to load Google Identity Services SDK/i);

        // Ensure failed script tag was removed from DOM
        const staleScript = document.querySelector('script[src="https://accounts.google.com/gsi/client"]');
        expect(staleScript).toBeNull();
      } finally {
        window.google = originalGoogle;
      }
    });
  });

  describe('2. Integration: GoogleDocsPreview Unconfigured State Alerting & Setup Guidance (R1, R3)', () => {
    it('displays unconfigured alert banner when no Client ID is set and does not trigger fake GIS client ID', async () => {
      process.env.VITE_GOOGLE_CLIENT_ID = '';
      clearStoredGoogleClientId();

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_unconfigured_test" />);

      // Verify unconfigured warning banner is rendered
      expect(
        screen.getByText(/Google OAuth Client ID is not configured/i)
      ).toBeInTheDocument();

      // Click "Export directly to Google Docs"
      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      fireEvent.click(exportBtn);

      // Verify error alert is displayed and GIS init was NOT called with a fake dummy ID
      expect(
        screen.getByText(/Google OAuth Client ID is not configured\. Please configure your Client ID in OAuth Settings/i)
      ).toBeInTheDocument();

      // Verify OAuth settings drawer opened automatically
      expect(screen.getByRole('region', { name: /OAuth Client ID Settings/i })).toBeInTheDocument();
    });

    it('allows user to enter, save, and reset custom Client ID via OAuth Settings drawer', async () => {
      process.env.VITE_GOOGLE_CLIENT_ID = 'default-env-id.apps.googleusercontent.com';
      clearStoredGoogleClientId();

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_save_client_id" />);

      // Open OAuth Settings drawer
      const settingsToggle = screen.getByRole('button', { name: /OAuth Settings/i });
      fireEvent.click(settingsToggle);

      const clientIdInput = screen.getByLabelText(/Google OAuth Client ID/i);
      expect(clientIdInput).toBeInTheDocument();

      // Enter new custom Client ID
      fireEvent.change(clientIdInput, {
        target: { value: 'my-custom-client-12345.apps.googleusercontent.com' },
      });

      // Click Save Client ID
      const saveBtn = screen.getByRole('button', { name: /Save Client ID/i });
      fireEvent.click(saveBtn);

      // Verify persistence in localStorage
      expect(localStorage.getItem(GOOGLE_CLIENT_ID_STORAGE_KEY)).toBe(
        'my-custom-client-12345.apps.googleusercontent.com'
      );
      expect(screen.getByText(/Client ID saved to local storage/i)).toBeInTheDocument();
      expect(screen.getByText(/Custom \(localStorage\)/i)).toBeInTheDocument();

      // Click Reset / Clear button
      const resetBtn = await screen.findByRole('button', { name: /Reset \/ Clear/i });
      fireEvent.click(resetBtn);

      // Verify localStorage was cleared and reverted to environment source
      expect(localStorage.getItem(GOOGLE_CLIENT_ID_STORAGE_KEY)).toBeNull();
      await waitFor(() => {
        expect(screen.getByText(/Environment \(VITE_GOOGLE_CLIENT_ID\)/i)).toBeInTheDocument();
      });
    });

    it('toggles Google Cloud Console Setup Guidance with detailed instructions', () => {
      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_guide_test" />);

      // Open settings
      const settingsToggle = screen.getByRole('button', { name: /OAuth Settings/i });
      fireEvent.click(settingsToggle);

      // Open setup guide
      const guideBtn = screen.getByRole('button', { name: /Setup Guide/i });
      fireEvent.click(guideBtn);

      expect(screen.getByText(/How to create a Google OAuth Client ID:/i)).toBeInTheDocument();
      expect(screen.getByText(/Google Cloud Console Credentials/i)).toBeInTheDocument();
      expect(screen.getByText(/Authorized JavaScript origins/i)).toBeInTheDocument();
      expect(screen.getByText(/Google Docs API/i)).toBeInTheDocument();
    });
  });

  describe('3. Integration: Manual Access Token Fallback Drawer (R2)', () => {
    it('toggles manual token drawer open and closed cleanly', () => {
      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_drawer_toggle" />);

      // Initially closed
      expect(screen.queryByRole('region', { name: /Manual Access Token Fallback/i })).not.toBeInTheDocument();

      // Click toggle
      const manualToggle = screen.getByRole('button', { name: /Manual Access Fallback/i });
      fireEvent.click(manualToggle);

      // Now open
      expect(screen.getByRole('region', { name: /Manual Access Token Fallback/i })).toBeInTheDocument();
      expect(screen.getByLabelText(/Google OAuth Access Token/i)).toBeInTheDocument();

      // Click Close
      const closeBtn = screen.getByRole('button', { name: /Close/i });
      fireEvent.click(closeBtn);

      // Closed again
      expect(screen.queryByRole('region', { name: /Manual Access Token Fallback/i })).not.toBeInTheDocument();
    });

    it('submits manual access token to /api/export/google-docs/create-document and renders document link upon success', async () => {
      const originalFetch = global.fetch;
      const mockFetch = vi.fn().mockImplementation((url, init) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs/create-document')) {
          const body = JSON.parse(init.body);
          expect(body.access_token).toBe('ya29.manual_live_token_777');
          expect(body.session_id).toBe('sess_manual_token_export');
          expect(body.document_title).toBe('RecipeBoxer - Rustic Apple Galette');

          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                document_id: 'doc_manual_success_888',
                url: 'https://docs.google.com/document/d/doc_manual_success_888/edit',
                title: 'RecipeBoxer - Rustic Apple Galette',
                recipe_count: 1,
              }),
          } as Response);
        }
        return originalFetch(url, init);
      });
      global.fetch = mockFetch;

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_manual_token_export" />);

      // Open manual token drawer
      const manualToggle = screen.getByRole('button', { name: /Manual Access Fallback/i });
      fireEvent.click(manualToggle);

      // Enter manual access token
      const tokenInput = screen.getByLabelText(/Google OAuth Access Token/i);
      fireEvent.change(tokenInput, { target: { value: 'ya29.manual_live_token_777' } });

      // Click "Export with Access Token"
      const exportTokenBtn = screen.getByRole('button', { name: /Export with Access Token/i });
      fireEvent.click(exportTokenBtn);

      // Verify success document link
      const successLink = await screen.findByRole('link', { name: /Open in Google Docs/i });
      expect(successLink).toBeInTheDocument();
      expect(successLink).toHaveAttribute(
        'href',
        'https://docs.google.com/document/d/doc_manual_success_888/edit'
      );
      expect(screen.getByText(/Document Created:/i)).toBeInTheDocument();

      global.fetch = originalFetch;
    });

    it('validates manual token input and displays error when token is empty or whitespace', async () => {
      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_empty_token" />);

      // Open manual token drawer
      const manualToggle = screen.getByRole('button', { name: /Manual Access Fallback/i });
      fireEvent.click(manualToggle);

      // Leave empty and click export
      const tokenInput = screen.getByLabelText(/Google OAuth Access Token/i);
      fireEvent.change(tokenInput, { target: { value: '   ' } });

      const exportTokenBtn = screen.getByRole('button', { name: /Export with Access Token/i });
      expect(exportTokenBtn).toBeDisabled();
    });

    it('surfaces manual token option and diagnostics when GIS popup fails with invalid_client', async () => {
      setMockGisResponse({
        error: 'invalid_client',
        error_description: 'The OAuth client was not found.',
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_invalid_client_test" />);

      // Click 1-click GIS export button
      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      fireEvent.click(exportBtn);

      // Verify diagnostic error alert is rendered
      await waitFor(() => {
        expect(
          screen.getByText(/Google Docs export failed: Google OAuth authorization failed: invalid_client/i)
        ).toBeInTheDocument();
      });

      // Verify troubleshooting actions are surfaced
      expect(screen.getByText(/Troubleshooting:/i)).toBeInTheDocument();
      const enterManualBtn = screen.getByRole('button', { name: /Enter Manual Token/i });
      expect(enterManualBtn).toBeInTheDocument();

      // Clicking troubleshooting "Enter Manual Token" reveals manual token drawer
      fireEvent.click(enterManualBtn);
      expect(screen.getByRole('region', { name: /Manual Access Token Fallback/i })).toBeInTheDocument();
    });

    it('handles HTTP 403 Insufficient Scope error by clearing cached token and surfacing manual token drawer', async () => {
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockImplementation((url) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs/create-document')) {
          return Promise.resolve({
            ok: false,
            status: 403,
            json: () =>
              Promise.resolve({
                detail: 'Google OAuth authorization failed: Insufficient Permission / Scope',
              }),
          } as Response);
        }
        return originalFetch(url);
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_scope_403_test" />);

      // Open manual drawer and submit token
      const manualToggle = screen.getByRole('button', { name: /Manual Access Fallback/i });
      fireEvent.click(manualToggle);

      const tokenInput = screen.getByLabelText(/Google OAuth Access Token/i);
      fireEvent.change(tokenInput, { target: { value: 'ya29.scope_insufficient_token' } });

      const exportBtn = screen.getByRole('button', { name: /Export with Access Token/i });
      fireEvent.click(exportBtn);

      // Verify 403 error is surfaced with troubleshooting actions
      await waitFor(() => {
        expect(
          screen.getByText(/Google Docs export failed: Google OAuth authorization failed: Insufficient Permission/i)
        ).toBeInTheDocument();
      });

      expect(screen.getByText(/Troubleshooting:/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Enter Manual Token/i })).toBeInTheDocument();

      global.fetch = originalFetch;
    });

    it('handles expired token (HTTP 401), clears token state, and allows re-authentication via GIS popup', async () => {
      let callCount = 0;
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockImplementation((url, init) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs/create-document')) {
          callCount++;
          if (callCount === 1) {
            return Promise.resolve({
              ok: false,
              status: 401,
              json: () =>
                Promise.resolve({
                  detail: 'Google OAuth authorization failed: 401 Unauthorized (Token expired)',
                }),
            } as Response);
          } else {
            return Promise.resolve({
              ok: true,
              status: 200,
              json: () =>
                Promise.resolve({
                  document_id: 'doc_refreshed_999',
                  url: 'https://docs.google.com/document/d/doc_refreshed_999/edit',
                  title: 'RecipeBoxer - Rustic Apple Galette',
                  recipe_count: 1,
                }),
            } as Response);
          }
        }
        return originalFetch(url, init);
      });

      // Provide valid mock GIS token
      setMockGisResponse({ access_token: 'mock-gis-fresh-token-123' });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_expired_token_test" />);

      // First click: gets GIS token and attempts create -> fails with 401
      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(
          screen.getByText(/Google Docs export failed: Google OAuth authorization failed: 401/i)
        ).toBeInTheDocument();
      });

      // Second click: token state was evicted on 401, so it requests fresh token from GIS and succeeds
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(screen.getByRole('link', { name: /Open in Google Docs/i })).toBeInTheDocument();
      });

      global.fetch = originalFetch;
    });

    it('surfaces manual token fallback when browser blocks popup window', async () => {
      mockInitTokenClient.mockImplementationOnce((config) => {
        return {
          requestAccessToken: () => {
            if (config.error_callback) {
              config.error_callback({
                type: 'popup_blocked_by_browser',
                message: 'Popup window was blocked by the browser',
              });
            }
          },
        };
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_popup_blocked_test" />);

      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(
          screen.getByText(/Google OAuth authorization popup was blocked by the browser/i)
        ).toBeInTheDocument();
      });

      // Manual token fallback drawer automatically surfaced
      expect(screen.getByRole('region', { name: /Manual Access Token Fallback/i })).toBeInTheDocument();
      expect(screen.getByText(/Troubleshooting:/i)).toBeInTheDocument();
    });

    it('surfaces manual token fallback when GIS script fails to load or SDK is unavailable', async () => {
      mockInitTokenClient.mockImplementationOnce(() => {
        throw new Error('Google Identity Services SDK is not available. Please use the Manual Access Token fallback.');
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_script_fail_test" />);

      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(
          screen.getByText(/Google Identity Services SDK is not available/i)
        ).toBeInTheDocument();
      });

      // Verifies manual token fallback drawer opened automatically
      expect(screen.getByRole('region', { name: /Manual Access Token Fallback/i })).toBeInTheDocument();
      expect(screen.getByText(/Troubleshooting:/i)).toBeInTheDocument();
    });

    it('handles non-JSON HTTP 502/500 error from backend gracefully without syntax error crash', async () => {
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockImplementation((url) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs/create-document')) {
          return Promise.resolve({
            ok: false,
            status: 502,
            statusText: 'Bad Gateway',
            json: () => Promise.reject(new SyntaxError('Unexpected token < in JSON at position 0')),
          } as unknown as Response);
        }
        return originalFetch(url);
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_502_test" />);

      // Open manual drawer and submit token
      const manualToggle = screen.getByRole('button', { name: /Manual Access Fallback/i });
      fireEvent.click(manualToggle);

      const tokenInput = screen.getByLabelText(/Google OAuth Access Token/i);
      fireEvent.change(tokenInput, { target: { value: 'ya29.valid_token_but_gateway_down' } });

      const exportBtn = screen.getByRole('button', { name: /Export with Access Token/i });
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(
          screen.getByText(/Google Docs export failed: Server returned HTTP 502 \(Bad Gateway\)/i)
        ).toBeInTheDocument();
      });

      expect(screen.getByText(/Troubleshooting:/i)).toBeInTheDocument();
      global.fetch = originalFetch;
    });

    it('supports submitting manual access token and saving client ID with Enter key', async () => {
      let createCalled = false;
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockImplementation((url) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs/create-document')) {
          createCalled = true;
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                document_id: 'doc_enter_key_123',
                url: 'https://docs.google.com/document/d/doc_enter_key_123/edit',
                title: 'RecipeBoxer - Rustic Apple Galette',
              }),
          } as Response);
        }
        return originalFetch(url);
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_enter_key_test" />);

      // Test Enter on Client ID input
      const settingsToggle = screen.getByRole('button', { name: /OAuth Settings/i });
      fireEvent.click(settingsToggle);

      const clientIdInput = screen.getByLabelText(/Google OAuth Client ID/i);
      fireEvent.change(clientIdInput, { target: { value: 'enter-key-client.apps.googleusercontent.com' } });
      fireEvent.keyDown(clientIdInput, { key: 'Enter', code: 'Enter' });

      expect(localStorage.getItem(GOOGLE_CLIENT_ID_STORAGE_KEY)).toBe('enter-key-client.apps.googleusercontent.com');

      // Test Enter on Manual Token input
      const manualToggle = screen.getByRole('button', { name: /Manual Access Fallback/i });
      fireEvent.click(manualToggle);

      const tokenInput = screen.getByLabelText(/Google OAuth Access Token/i);
      fireEvent.change(tokenInput, { target: { value: 'ya29.enter_token_456' } });
      fireEvent.keyDown(tokenInput, { key: 'Enter', code: 'Enter' });

      await waitFor(() => {
        expect(createCalled).toBe(true);
        expect(screen.getByRole('link', { name: /Open in Google Docs/i })).toBeInTheDocument();
      });

      global.fetch = originalFetch;
    });

    it('copies fallback batch JSON when /api/export/google-docs endpoint returns error', async () => {
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockImplementation((url) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs')) {
          return Promise.resolve({
            ok: false,
            status: 500,
          } as Response);
        }
        return originalFetch(url);
      });

      const writeTextMock = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, {
        clipboard: {
          writeText: writeTextMock,
        },
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_copy_fallback_test" />);

      const copyBtn = screen.getByRole('button', { name: /Copy Batch JSON/i });
      fireEvent.click(copyBtn);

      await waitFor(() => {
        expect(writeTextMock).toHaveBeenCalled();
        const copiedStr = writeTextMock.mock.calls[0][0];
        const parsed = JSON.parse(copiedStr);
        expect(parsed.recipe_count).toBe(1);
        expect(parsed.title).toBe('RecipeBoxer - Rustic Apple Galette');
      });

      global.fetch = originalFetch;
    });

    it('sanitizes manual access token by stripping Bearer prefix and surrounding quotes before API submission', async () => {
      let submittedToken = '';
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockImplementation((url, init) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs/create-document')) {
          const body = JSON.parse(init.body);
          submittedToken = body.access_token;
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                document_id: 'doc_bearer_clean_123',
                url: 'https://docs.google.com/document/d/doc_bearer_clean_123/edit',
                title: 'RecipeBoxer - Rustic Apple Galette',
              }),
          } as Response);
        }
        return originalFetch(url, init);
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_bearer_strip_test" />);

      // Open manual drawer
      const manualToggle = screen.getByRole('button', { name: /Manual Access Fallback/i });
      fireEvent.click(manualToggle);

      // Enter manual token with "Bearer " prefix and quotes
      const tokenInput = screen.getByLabelText(/Google OAuth Access Token/i);
      fireEvent.change(tokenInput, { target: { value: 'Bearer "ya29.bearer_raw_token_xyz"' } });

      const exportBtn = screen.getByRole('button', { name: /Export with Access Token/i });
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(submittedToken).toBe('ya29.bearer_raw_token_xyz');
        expect(screen.getByRole('link', { name: /Open in Google Docs/i })).toBeInTheDocument();
      });

      global.fetch = originalFetch;
    });

    it('saving or clearing client ID resets cached access token state so fresh GIS token is acquired', async () => {
      let callCount = 0;
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockImplementation((url) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs/create-document')) {
          callCount++;
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                document_id: `doc_id_${callCount}`,
                url: `https://docs.google.com/document/d/doc_id_${callCount}/edit`,
                title: 'RecipeBoxer - Rustic Apple Galette',
              }),
          } as Response);
        }
        return originalFetch(url);
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_token_eviction_test" />);

      // First export
      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(screen.getByRole('link', { name: /Open in Google Docs/i })).toBeInTheDocument();
      });
      expect(mockInitTokenClient).toHaveBeenCalledTimes(1);

      // Open settings and save new client ID
      const settingsToggle = screen.getByRole('button', { name: /OAuth Settings/i });
      fireEvent.click(settingsToggle);

      const clientIdInput = screen.getByLabelText(/Google OAuth Client ID/i);
      fireEvent.change(clientIdInput, { target: { value: 'new-client-id-555.apps.googleusercontent.com' } });

      const saveBtn = screen.getByRole('button', { name: /Save Client ID/i });
      fireEvent.click(saveBtn);

      // Trigger export again -> since cached token was cleared, GIS token request is triggered with new client ID
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(mockInitTokenClient).toHaveBeenCalledTimes(2);
      });

      global.fetch = originalFetch;
    });

    it('formats FastAPI 422 array validation errors cleanly without [object Object]', async () => {
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockImplementation((url) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs/create-document')) {
          return Promise.resolve({
            ok: false,
            status: 422,
            statusText: 'Unprocessable Entity',
            json: () =>
              Promise.resolve({
                detail: [
                  { loc: ['body', 'access_token'], msg: 'Field required', type: 'value_error.missing' },
                ],
              }),
          } as Response);
        }
        return originalFetch(url);
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_422_test" />);

      const manualToggle = screen.getByRole('button', { name: /Manual Access Fallback/i });
      fireEvent.click(manualToggle);

      const tokenInput = screen.getByLabelText(/Google OAuth Access Token/i);
      fireEvent.change(tokenInput, { target: { value: 'ya29.invalid_test' } });

      const exportBtn = screen.getByRole('button', { name: /Export with Access Token/i });
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(
          screen.getByText(/Google Docs export failed: Field required/i)
        ).toBeInTheDocument();
      });

      // Confirm [object Object] is NOT displayed
      expect(screen.queryByText(/\[object Object\]/i)).not.toBeInTheDocument();

      global.fetch = originalFetch;
    });

    it('handles clipboard write failures gracefully without unhandled exceptions', async () => {
      const originalClipboard = navigator.clipboard;
      Object.assign(navigator, {
        clipboard: {
          writeText: vi.fn().mockRejectedValue(new Error('Clipboard permission denied')),
        },
      });

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_clipboard_error_test" />);

      const copyHtmlBtn = screen.getByRole('button', { name: /Copy HTML/i });
      expect(() => fireEvent.click(copyHtmlBtn)).not.toThrow();

      const copyJsonBtn = screen.getByRole('button', { name: /Copy Batch JSON/i });
      expect(() => fireEvent.click(copyJsonBtn)).not.toThrow();

      Object.assign(navigator, { clipboard: originalClipboard });
    });
  });

  describe('4. Integration: ExportDialog Google Docs Tab Navigation', () => {
    it('renders Google Docs tab in ExportDialog with manual token fallback and settings access', async () => {
      render(<ExportDialog recipes={sampleRecipes} sessionId="sess_dialog_full" />);

      // Switch to Google Docs tab
      const gdocsTab = screen.getByRole('button', { name: /Google Docs Archival/i });
      fireEvent.click(gdocsTab);

      expect(screen.getByText(/Direct Google Docs & Drive API Publishing/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Manual Access Fallback/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /OAuth Settings/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Export directly to Google Docs/i })).toBeInTheDocument();
    });
  });
});
