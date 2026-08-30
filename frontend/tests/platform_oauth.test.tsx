import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import {
  detectClientPlatform,
  getOsPathPlaceholder,
  ClientPlatform,
} from '../src/utils/platform';
import {
  requestGoogleAccessToken,
  GOOGLE_SCOPES,
  getGoogleClientId,
  isGoogleIdentityLoaded,
} from '../src/utils/googleAuth';
import { SourceSelector } from '../src/components/ingestion/SourceSelector';
import { AlbumPickerModal } from '../src/components/ingestion/AlbumPickerModal';
import { GoogleDocsPreview } from '../src/components/export/GoogleDocsPreview';
import { api } from '../src/utils/api';
import {
  setMockGisResponse,
  resetMockGisResponse,
  mockInitTokenClient,
} from '../src/setupTests';

vi.mock('../src/utils/api', () => ({
  api: {
    startPipeline: vi.fn(),
    uploadCards: vi.fn(),
    listSourceProviders: vi.fn(),
    listSourceContainers: vi.fn(),
    listSourceItems: vi.fn(),
    connectSourceProvider: vi.fn(),
    browseDirectory: vi.fn(),
    getSourceThumbnailUrl: vi.fn((providerId, itemId) => `/api/sources/${providerId}/thumbnail/${itemId}`),
  },
}));

describe('Milestone 1: Platform Detection & Google Identity Services (GIS) OAuth', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetMockGisResponse();
  });

  describe('1. Platform Detection & OS Path Placeholders (R1)', () => {
    it('detects platform via Client Hints (navigator.userAgentData.platform)', () => {
      const mockNavWin = {
        userAgentData: { platform: 'Windows' },
      } as unknown as Navigator;
      expect(detectClientPlatform(mockNavWin)).toBe('windows');

      const mockNavMac = {
        userAgentData: { platform: 'macOS' },
      } as unknown as Navigator;
      expect(detectClientPlatform(mockNavMac)).toBe('macos');

      const mockNavLinux = {
        userAgentData: { platform: 'Linux' },
      } as unknown as Navigator;
      expect(detectClientPlatform(mockNavLinux)).toBe('linux');
    });

    it('detects platform via standard navigator.platform and userAgent fallbacks', () => {
      const mockNavWinLegacy = {
        platform: 'Win32',
        userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
      } as unknown as Navigator;
      expect(detectClientPlatform(mockNavWinLegacy)).toBe('windows');

      const mockNavMacLegacy = {
        platform: 'MacIntel',
        userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
      } as unknown as Navigator;
      expect(detectClientPlatform(mockNavMacLegacy)).toBe('macos');

      const mockNavLinuxLegacy = {
        platform: 'Linux x86_64',
        userAgent: 'Mozilla/5.0 (X11; Linux x86_64)',
      } as unknown as Navigator;
      expect(detectClientPlatform(mockNavLinuxLegacy)).toBe('linux');

      // Undefined or empty fallback
      expect(detectClientPlatform({} as unknown as Navigator)).toBe('linux');
    });

    it('returns exact OS-specific path placeholders for Windows, macOS, and Linux', () => {
      expect(getOsPathPlaceholder('windows')).toBe('C:\\Users\\username\\Documents\\RecipeScans');
      expect(getOsPathPlaceholder('macos')).toBe('/Users/username/Documents/RecipeScans');
      expect(getOsPathPlaceholder('linux')).toBe('/home/username/recipes');
    });

    it('renders OS-adaptive path placeholder in SourceSelector component', () => {
      render(
        <SourceSelector
          sourceType="local"
          onSourceTypeChange={vi.fn()}
          localMethod="folder"
          onLocalMethodChange={vi.fn()}
          folderPath=""
          onFolderPathChange={vi.fn()}
          uploadedFiles={[]}
          onUploadedFilesChange={vi.fn()}
          selectedContainer={null}
          onSelectContainer={vi.fn()}
          categoryFilter="FOOD"
          onCategoryFilterChange={vi.fn()}
          bucketName=""
          onBucketNameChange={vi.fn()}
          prefix=""
          onPrefixChange={vi.fn()}
        />
      );

      const input = screen.getByLabelText(/Local Scans Directory Path/i);
      expect(input).toBeInTheDocument();
      // Placeholder matches getOsPathPlaceholder()
      expect(input.getAttribute('placeholder')).toBe(getOsPathPlaceholder());
    });
  });

  describe('2. Google Identity Services (GIS) Utility Wrapper (R4)', () => {
    it('initializes token client and resolves access token on success', async () => {
      setMockGisResponse({ access_token: 'ya29.live_mock_token_123' });

      const tokenPromise = requestGoogleAccessToken({
        scope: GOOGLE_SCOPES.DOCS_EXPORT,
      });

      const token = await tokenPromise;
      expect(token).toBe('ya29.live_mock_token_123');
      expect(mockInitTokenClient).toHaveBeenCalledWith(
        expect.objectContaining({
          scope: GOOGLE_SCOPES.DOCS_EXPORT,
        })
      );
    });

    it('rejects with error when GIS returns authorization error response', async () => {
      setMockGisResponse({
        error: 'access_denied',
        error_description: 'User denied OAuth consent',
      });

      await expect(
        requestGoogleAccessToken({
          scope: GOOGLE_SCOPES.PHOTOS_INGEST,
        })
      ).rejects.toThrow(/User denied OAuth consent/i);
    });

    it('handles empty response or user cancellation cleanly', async () => {
      setMockGisResponse({});

      await expect(
        requestGoogleAccessToken({
          scope: GOOGLE_SCOPES.DRIVE_INGEST,
        })
      ).rejects.toThrow(/Google authorization was cancelled or returned no token/i);
    });

    it('checks GIS loaded status correctly', () => {
      expect(isGoogleIdentityLoaded()).toBe(true);
      expect(getGoogleClientId()).toBeTruthy();
    });
  });

  describe('3. GoogleDocsPreview 1-Click Interactive OAuth Flow (R4)', () => {
    const mockRecipes = [
      {
        recipe_id: 'rec_docs_01',
        title: 'Heirloom Apple Crisp',
        ingredients: ['4 apples', '1 cup oats', '1/2 cup butter'],
        directions: ['Slice apples.', 'Top with oat crumble and bake.'],
        tags: ['Dessert', 'Baking'],
      },
    ];

    it('initiates 1-click GIS popup flow and creates Google Doc without manual token drawer', async () => {
      const originalFetch = global.fetch;
      global.fetch = vi.fn().mockImplementation((url) => {
        if (typeof url === 'string' && url.includes('/api/export/google-docs/create-document')) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                document_id: 'doc_gis_789',
                url: 'https://docs.google.com/document/d/doc_gis_789/edit',
                title: 'RecipeBoxer - Heirloom Apple Crisp',
                recipe_count: 1,
              }),
          } as Response);
        }
        return originalFetch(url);
      });

      render(<GoogleDocsPreview recipes={mockRecipes} sessionId="sess_gis_test" />);

      // Verify no manual token input drawer exists
      expect(screen.queryByPlaceholderText(/ya29\./i)).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Token/i })).not.toBeInTheDocument();

      // Click "Export directly to Google Docs"
      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      fireEvent.click(exportBtn);

      // Verify success link
      const successLink = await screen.findByRole('link', { name: /Open in Google Docs/i });
      expect(successLink).toBeInTheDocument();
      expect(successLink).toHaveAttribute('href', 'https://docs.google.com/document/d/doc_gis_789/edit');

      global.fetch = originalFetch;
    });

    it('displays error banner when GIS consent is denied during Docs export', async () => {
      setMockGisResponse({
        error: 'access_denied',
        error_description: 'Consent prompt closed by user',
      });

      render(<GoogleDocsPreview recipes={mockRecipes} sessionId="sess_gis_denied" />);

      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      fireEvent.click(exportBtn);

      await waitFor(() => {
        expect(screen.getByText(/Google Docs export failed: Google authorization error: Consent prompt closed by user/i)).toBeInTheDocument();
      });
    });
  });

  describe('4. SourceSelector Interactive Drive & Photos OAuth (R4)', () => {
    it('provides interactive Connect Account button for Google Photos and updates status to Ready', async () => {
      vi.mocked(api.listSourceProviders).mockResolvedValueOnce([
        {
          provider_id: 'google_photos',
          name: 'Google Photos',
          description: '',
          connected: false,
          auth_required: true,
          authenticated: false,
          message: 'Auth required',
          capabilities: {
            supports_containers: true,
            supports_search: true,
            supports_thumbnails: true,
            supports_pagination: true,
            supports_streaming: true,
            requires_auth: true,
          },
        },
      ]);

      vi.mocked(api.connectSourceProvider).mockResolvedValueOnce({
        provider_id: 'google_photos',
        name: 'Google Photos',
        description: '',
        connected: true,
        auth_required: false,
        authenticated: true,
        message: 'Connected',
        capabilities: {
          supports_containers: true,
          supports_search: true,
          supports_thumbnails: true,
          supports_pagination: true,
          supports_streaming: true,
          requires_auth: true,
        },
      });

      render(
        <SourceSelector
          sourceType="google_photos"
          onSourceTypeChange={vi.fn()}
          localMethod="folder"
          onLocalMethodChange={vi.fn()}
          folderPath=""
          onFolderPathChange={vi.fn()}
          uploadedFiles={[]}
          onUploadedFilesChange={vi.fn()}
          selectedContainer={null}
          onSelectContainer={vi.fn()}
          categoryFilter="FOOD"
          onCategoryFilterChange={vi.fn()}
          bucketName=""
          onBucketNameChange={vi.fn()}
          prefix=""
          onPrefixChange={vi.fn()}
        />
      );

      // Wait for provider status to load
      await waitFor(() => {
        expect(screen.getByText('Auth Required')).toBeInTheDocument();
      });

      const connectBtn = screen.getByRole('button', { name: /Connect Google Photos|Connect Account/i });
      fireEvent.click(connectBtn);

      await waitFor(() => {
        expect(api.connectSourceProvider).toHaveBeenCalledWith(
          'google_photos',
          expect.objectContaining({
            provider_id: 'google_photos',
            auth_token: expect.any(String),
          })
        );
        expect(screen.getByText('Ready')).toBeInTheDocument();
      });
    });

    it('provides interactive Connect Account button for Google Drive and updates status to Ready', async () => {
      vi.mocked(api.listSourceProviders).mockResolvedValueOnce([
        {
          provider_id: 'google_drive',
          name: 'Google Drive',
          description: '',
          connected: false,
          auth_required: true,
          authenticated: false,
          message: 'Auth required',
          capabilities: {
            supports_containers: true,
            supports_search: true,
            supports_thumbnails: true,
            supports_pagination: true,
            supports_streaming: true,
            requires_auth: true,
          },
        },
      ]);

      vi.mocked(api.connectSourceProvider).mockResolvedValueOnce({
        provider_id: 'google_drive',
        name: 'Google Drive',
        description: '',
        connected: true,
        auth_required: false,
        authenticated: true,
        message: 'Connected',
        capabilities: {
          supports_containers: true,
          supports_search: true,
          supports_thumbnails: true,
          supports_pagination: true,
          supports_streaming: true,
          requires_auth: true,
        },
      });

      render(
        <SourceSelector
          sourceType="google_drive"
          onSourceTypeChange={vi.fn()}
          localMethod="folder"
          onLocalMethodChange={vi.fn()}
          folderPath=""
          onFolderPathChange={vi.fn()}
          uploadedFiles={[]}
          onUploadedFilesChange={vi.fn()}
          selectedContainer={null}
          onSelectContainer={vi.fn()}
          categoryFilter="FOOD"
          onCategoryFilterChange={vi.fn()}
          bucketName=""
          onBucketNameChange={vi.fn()}
          prefix=""
          onPrefixChange={vi.fn()}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Auth Required')).toBeInTheDocument();
      });

      const connectBtn = screen.getByRole('button', { name: /Connect Google Drive|Connect Account/i });
      fireEvent.click(connectBtn);

      await waitFor(() => {
        expect(api.connectSourceProvider).toHaveBeenCalledWith(
          'google_drive',
          expect.objectContaining({
            provider_id: 'google_drive',
            auth_token: expect.any(String),
          })
        );
        expect(screen.getByText('Ready')).toBeInTheDocument();
      });
    });

    it('renders error banner when Google Drive OAuth fails', async () => {
      setMockGisResponse({
        error: 'popup_closed_by_user',
        error_description: 'OAuth popup closed by user',
      });

      vi.mocked(api.listSourceProviders).mockResolvedValueOnce([
        {
          provider_id: 'google_drive',
          name: 'Google Drive',
          description: '',
          connected: false,
          auth_required: true,
          authenticated: false,
          message: 'Auth required',
          capabilities: {
            supports_containers: true,
            supports_search: true,
            supports_thumbnails: true,
            supports_pagination: true,
            supports_streaming: true,
            requires_auth: true,
          },
        },
      ]);

      render(
        <SourceSelector
          sourceType="google_drive"
          onSourceTypeChange={vi.fn()}
          localMethod="folder"
          onLocalMethodChange={vi.fn()}
          folderPath=""
          onFolderPathChange={vi.fn()}
          uploadedFiles={[]}
          onUploadedFilesChange={vi.fn()}
          selectedContainer={null}
          onSelectContainer={vi.fn()}
          categoryFilter="FOOD"
          onCategoryFilterChange={vi.fn()}
          bucketName=""
          onBucketNameChange={vi.fn()}
          prefix=""
          onPrefixChange={vi.fn()}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Auth Required')).toBeInTheDocument();
      });

      const connectBtn = screen.getByRole('button', { name: /Connect Google Drive|Connect Account/i });
      fireEvent.click(connectBtn);

      await waitFor(() => {
        expect(screen.getByText(/OAuth popup closed by user/i)).toBeInTheDocument();
      });
    });
  });

  describe('5. AlbumPickerModal 401 Re-Auth & Auto-Retry Flow (R4)', () => {
    it('handles 401 unauthorized in AlbumPickerModal with interactive Google sign-in and auto-fetches containers', async () => {
      // First attempt fails with 401 Unauthorized
      vi.mocked(api.listSourceContainers).mockRejectedValueOnce(
        new Error('HTTP 401: Unauthorized / Auth required')
      );

      // Second attempt (after OAuth connect) succeeds
      vi.mocked(api.listSourceContainers).mockResolvedValueOnce({
        containers: [
          {
            id: 'gphotos_autumn_2026',
            name: 'Autumn Baking 2026',
            item_count: 24,
            container_type: 'album',
          },
        ],
        next_page_token: null,
      });

      vi.mocked(api.connectSourceProvider).mockResolvedValueOnce({
        provider_id: 'google_photos',
        name: 'Google Photos',
        description: '',
        connected: true,
        auth_required: false,
        authenticated: true,
        message: 'Connected',
        capabilities: {
          supports_containers: true,
          supports_search: true,
          supports_thumbnails: true,
          supports_pagination: true,
          supports_streaming: true,
          requires_auth: true,
        },
      });

      const onSelect = vi.fn();
      const onClose = vi.fn();

      render(
        <AlbumPickerModal
          isOpen={true}
          onClose={onClose}
          providerId="google_photos"
          providerName="Google Photos"
          onSelectContainer={onSelect}
        />
      );

      // Should show error & Sign in with Google button
      await waitFor(() => {
        expect(screen.getByText(/HTTP 401: Unauthorized/i)).toBeInTheDocument();
      });

      const signInButtons = screen.getAllByRole('button', { name: /Sign in with Google/i });
      expect(signInButtons.length).toBeGreaterThan(0);
      fireEvent.click(signInButtons[0]);

      // Verify connectSourceProvider called and containers auto-loaded
      await waitFor(() => {
        expect(api.connectSourceProvider).toHaveBeenCalledWith(
          'google_photos',
          expect.objectContaining({
            provider_id: 'google_photos',
            auth_token: expect.any(String),
          })
        );
        expect(screen.getByText('Autumn Baking 2026')).toBeInTheDocument();
      });
    });
  });
});
