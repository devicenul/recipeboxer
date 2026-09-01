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
      ).rejects.toThrow(/popup was blocked by the browser/i);
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
    it('displays unconfigured alert banner when no Client ID is set and export button is disabled with tooltip', async () => {
      process.env.VITE_GOOGLE_CLIENT_ID = '';
      clearStoredGoogleClientId();

      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_unconfigured_test" />);

      // Verify unconfigured warning banner is rendered
      expect(
        screen.getByText(/Google OAuth Client ID is not configured/i)
      ).toBeInTheDocument();

      // Verify export button is disabled with "Support In Progress" tooltip
      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      expect(exportBtn).toBeDisabled();
      expect(exportBtn).toHaveAttribute('title', 'Support In Progress');

      // Verify no manual token buttons or fallback drawers exist
      expect(screen.queryByRole('button', { name: /Manual Access/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Enter Manual Token/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('region', { name: /Manual Access Token Fallback/i })).not.toBeInTheDocument();

      // Click "Configure Client ID" in unconfigured banner
      const configBtn = screen.getByRole('button', { name: /Configure Client ID/i });
      fireEvent.click(configBtn);

      // Verify OAuth settings drawer opened
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

  describe('3. Integration: Streamlined OAuth Configuration & Removal of Manual Fallbacks (R1, R2)', () => {
    it('verifies manual token fallback drawer, manual inputs, and buttons are completely absent from DOM', () => {
      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_no_drawer" />);

      expect(screen.queryByRole('region', { name: /Manual Access Token Fallback/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Manual Access/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Enter Manual Token/i })).not.toBeInTheDocument();
      expect(screen.queryByLabelText(/Google OAuth Access Token/i)).not.toBeInTheDocument();
      expect(screen.queryByPlaceholderText(/ya29\./i)).not.toBeInTheDocument();
    });

    it('disables Google Docs export trigger button and renders Support In Progress tooltip', () => {
      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_disabled_tooltip" />);

      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      expect(exportBtn).toBeInTheDocument();
      expect(exportBtn).toBeDisabled();
      expect(exportBtn).toHaveAttribute('title', 'Support In Progress');
    });

    it('supports saving client ID with Enter key and verifies no manual token drawer exists', async () => {
      render(<GoogleDocsPreview recipes={sampleRecipes} sessionId="sess_enter_key_test" />);

      // Test Enter on Client ID input
      const settingsToggle = screen.getByRole('button', { name: /OAuth Settings/i });
      fireEvent.click(settingsToggle);

      const clientIdInput = screen.getByLabelText(/Google OAuth Client ID/i);
      fireEvent.change(clientIdInput, { target: { value: 'enter-key-client.apps.googleusercontent.com' } });
      fireEvent.keyDown(clientIdInput, { key: 'Enter', code: 'Enter' });

      expect(localStorage.getItem(GOOGLE_CLIENT_ID_STORAGE_KEY)).toBe('enter-key-client.apps.googleusercontent.com');

      // Verify no manual token fallback is triggered or visible
      expect(screen.queryByRole('region', { name: /Manual Access Token Fallback/i })).not.toBeInTheDocument();
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

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Copy Batch JSON|Copied JSON/i })).toBeInTheDocument();
      });

      Object.assign(navigator, { clipboard: originalClipboard });
    });
  });

  describe('4. Integration: ExportDialog Google Docs Tab Navigation', () => {
    it('renders Google Docs tab in ExportDialog without manual token fallback and with Support In Progress tooltip', async () => {
      render(<ExportDialog recipes={sampleRecipes} sessionId="sess_dialog_full" />);

      // Switch to Google Docs tab
      const gdocsTab = screen.getByRole('button', { name: /Google Docs Archival/i });
      fireEvent.click(gdocsTab);

      expect(screen.getByText(/Direct Google Docs & Drive API Publishing/i)).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Manual Access/i })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /OAuth Settings/i })).toBeInTheDocument();

      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      expect(exportBtn).toBeInTheDocument();
      expect(exportBtn).toBeDisabled();
      expect(exportBtn).toHaveAttribute('title', 'Support In Progress');
    });
  });
});
