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

  describe('3. GoogleDocsPreview Streamlined Integration (R1, R2)', () => {
    const mockRecipes = [
      {
        recipe_id: 'rec_docs_01',
        title: 'Heirloom Apple Crisp',
        ingredients: ['4 apples', '1 cup oats', '1/2 cup butter'],
        directions: ['Slice apples.', 'Top with oat crumble and bake.'],
        tags: ['Dessert', 'Baking'],
      },
    ];

    it('verifies Google Docs export button is disabled with Support In Progress tooltip and no manual token drawer', () => {
      render(<GoogleDocsPreview recipes={mockRecipes} sessionId="sess_gis_test" />);

      // Verify no manual token input drawer exists
      expect(screen.queryByPlaceholderText(/ya29\./i)).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Manual Access/i })).not.toBeInTheDocument();

      // Verify Google Docs export button is disabled with Support In Progress tooltip
      const exportBtn = screen.getByRole('button', { name: /Export directly to Google Docs/i });
      expect(exportBtn).toBeInTheDocument();
      expect(exportBtn).toBeDisabled();
      expect(exportBtn).toHaveAttribute('title', 'Support In Progress');
    });
  });

  describe('4. SourceSelector Google Providers Disabled State (R2)', () => {
    it('provides disabled Connect Account button with Support In Progress tooltip for Google Photos', async () => {
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
      expect(connectBtn).toBeDisabled();
      expect(connectBtn).toHaveAttribute('title', 'Support In Progress');
    });

    it('provides disabled Connect Account button with Support In Progress tooltip for Google Drive', async () => {
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
      expect(connectBtn).toBeDisabled();
      expect(connectBtn).toHaveAttribute('title', 'Support In Progress');
    });
  });

  describe('5. AlbumPickerModal Google Disabled Actions (R2)', () => {
    it('disables Google Sign In button with Support In Progress tooltip when auth is required', async () => {
      vi.mocked(api.listSourceContainers).mockRejectedValueOnce(
        new Error('HTTP 401: Unauthorized / Auth required')
      );

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

      // Should show error & disabled Sign in with Google button
      await waitFor(() => {
        expect(screen.getByText(/HTTP 401: Unauthorized/i)).toBeInTheDocument();
      });

      const signInButtons = screen.getAllByRole('button', { name: /Sign in with Google/i });
      expect(signInButtons.length).toBeGreaterThan(0);
      expect(signInButtons[0]).toBeDisabled();
      expect(signInButtons[0]).toHaveAttribute('title', 'Support In Progress');
    });
  });
});
