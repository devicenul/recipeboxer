import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import React from 'react';
import { SourceSelector } from '../src/components/ingestion/SourceSelector';
import { AlbumPickerModal } from '../src/components/ingestion/AlbumPickerModal';
import { SessionLauncher } from '../src/components/ingestion/SessionLauncher';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    startPipeline: vi.fn(),
    uploadCards: vi.fn(),
    listSourceProviders: vi.fn(),
    listSourceContainers: vi.fn(),
    listSourceItems: vi.fn(),
    connectSourceProvider: vi.fn(),
    getSourceThumbnailUrl: vi.fn((providerId, itemId) => `/api/sources/${providerId}/thumbnail/${itemId}`),
    listSessions: vi.fn().mockResolvedValue([]),
    browseDirectory: vi.fn(),
  },
}));

describe('Ingestion Sources & Provider Standardization', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('SourceSelector Component', () => {
    it('renders main source options with Google Photos and Google Drive disabled, showing Coming Soon badges and tooltips', () => {
      const onSourceChange = vi.fn();
      const onSelectContainer = vi.fn();

      render(
        <SourceSelector
          sourceType="local"
          onSourceTypeChange={onSourceChange}
          localMethod="folder"
          onLocalMethodChange={vi.fn()}
          folderPath=""
          onFolderPathChange={vi.fn()}
          uploadedFiles={[]}
          onUploadedFilesChange={vi.fn()}
          selectedContainer={null}
          onSelectContainer={onSelectContainer}
          categoryFilter="FOOD"
          onCategoryFilterChange={vi.fn()}
        />
      );

      // Verify all 3 top buttons exist
      expect(screen.getByText('Local Folder / Uploads')).toBeInTheDocument();
      expect(screen.getByText('Google Photos')).toBeInTheDocument();
      expect(screen.getByText('Google Drive')).toBeInTheDocument();

      // Verify Local button is enabled
      const localButton = screen.getByText('Local Folder / Uploads').closest('button');
      expect(localButton).toBeEnabled();

      // Verify Google Photos is disabled with "Support In Progress" tooltip and "Coming Soon" badge
      const gpButton = screen.getByText('Google Photos').closest('button');
      expect(gpButton).toBeDisabled();
      expect(gpButton).toHaveAttribute('title', 'Support In Progress');
      expect(within(gpButton!).getByText('Coming Soon')).toBeInTheDocument();

      // Verify Google Drive is disabled with "Support In Progress" tooltip and "Coming Soon" badge
      const gdriveButton = screen.getByText('Google Drive').closest('button');
      expect(gdriveButton).toBeDisabled();
      expect(gdriveButton).toHaveAttribute('title', 'Support In Progress');
      expect(within(gdriveButton!).getByText('Coming Soon')).toBeInTheDocument();

      // Assert complete absence of S3 and GCS options, tabs, and inputs
      expect(screen.queryByText('Cloud Drive & Storage')).not.toBeInTheDocument();
      expect(screen.queryByText(/Amazon S3/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/Google Cloud Storage/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/S3 Bucket Name/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/GCS Bucket Name/i)).not.toBeInTheDocument();
    });

    it('renders Google Drive connection panel when sourceType is google_drive without S3/GCS tabs or inputs', () => {
      const onSourceChange = vi.fn();

      render(
        <SourceSelector
          sourceType="google_drive"
          onSourceTypeChange={onSourceChange}
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
        />
      );

      // Google Drive connection panel is present
      expect(screen.getByText(/Google Drive Connection/i)).toBeInTheDocument();
      expect(screen.getByText(/Target Google Drive Folder/i)).toBeInTheDocument();

      // Browse Google Drive button is disabled with tooltip
      const browseDriveBtn = screen.getByRole('button', { name: /Browse & Select Google Drive Folder.../i });
      expect(browseDriveBtn).toBeDisabled();
      expect(browseDriveBtn).toHaveAttribute('title', 'Support In Progress');

      // Assert complete absence of S3/GCS tabs and bucket/prefix inputs
      expect(screen.queryByText('Amazon S3 / MinIO')).not.toBeInTheDocument();
      expect(screen.queryByText('Google Cloud Storage')).not.toBeInTheDocument();
      expect(screen.queryByText('S3 Bucket Name')).not.toBeInTheDocument();
      expect(screen.queryByText('GCS Bucket Name')).not.toBeInTheDocument();
      expect(screen.queryByPlaceholderText('batch-2026/box-1/')).not.toBeInTheDocument();
    });

    it('renders Google Photos panel when sourceType is google_photos with disabled browse button and filter', () => {
      const onSourceChange = vi.fn();

      render(
        <SourceSelector
          sourceType="google_photos"
          onSourceTypeChange={onSourceChange}
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
        />
      );

      expect(screen.getByText(/Google Photos Library Connection/i)).toBeInTheDocument();
      const browseAlbumsBtn = screen.getByRole('button', { name: /Browse Google Photos Albums.../i });
      expect(browseAlbumsBtn).toBeDisabled();
      expect(browseAlbumsBtn).toHaveAttribute('title', 'Support In Progress');
      expect(screen.getByText(/Category Search Filter/i)).toBeInTheDocument();
    });

    it('fetches provider statuses and renders Auth Required badge when disconnected', async () => {
      vi.mocked(api.listSourceProviders).mockResolvedValueOnce([
        {
          provider_id: 'google_photos',
          name: 'Google Photos',
          description: '',
          connected: false,
          auth_required: true,
          authenticated: false,
          message: 'Not authorized',
          capabilities: {
            supports_containers: true,
            supports_search: true,
            supports_thumbnails: true,
            supports_pagination: true,
            supports_streaming: true,
            requires_auth: true,
            supported_mime_types: ['image/jpeg'],
            category_filters: [],
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
        />
      );

      await waitFor(() => {
        expect(api.listSourceProviders).toHaveBeenCalled();
        expect(screen.getByText('Auth Required')).toBeInTheDocument();
      });
    });
  });

  describe('AlbumPickerModal Component', () => {
    it('fetches remote containers, filters by search term, and selects an album', async () => {
      const mockContainers = [
        {
          id: 'album_1',
          name: 'Grandmas Desserts',
          item_count: 15,
          container_type: 'album',
        },
        {
          id: 'album_2',
          name: 'Sunday Roasts',
          item_count: 8,
          container_type: 'album',
        },
      ];

      vi.mocked(api.listSourceContainers).mockResolvedValueOnce({
        containers: mockContainers,
        next_page_token: null,
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

      await waitFor(() => {
        expect(screen.getByText('Grandmas Desserts')).toBeInTheDocument();
        expect(screen.getByText('Sunday Roasts')).toBeInTheDocument();
      });

      // Filter with search input
      const searchInput = screen.getByPlaceholderText(/Search Google Photos albums or folders.../i);
      fireEvent.change(searchInput, { target: { value: 'Desserts' } });

      expect(screen.getByText('Grandmas Desserts')).toBeInTheDocument();
      expect(screen.queryByText('Sunday Roasts')).not.toBeInTheDocument();

      // Click container card
      const albumCard = screen.getByText('Grandmas Desserts').closest('[role="button"]');
      if (albumCard) fireEvent.click(albumCard);

      // Confirm selection
      const selectBtn = screen.getByRole('button', { name: /Select Container/i });
      fireEvent.click(selectBtn);

      expect(onSelect).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'album_1', name: 'Grandmas Desserts' })
      );
      expect(onClose).toHaveBeenCalled();
    });

    it('handles keyboard navigation and error displays in AlbumPickerModal', async () => {
      const mockContainers = [
        {
          id: 'album_kb_1',
          name: 'Keyboard Selectable Album',
          item_count: 5,
          container_type: 'album',
        },
      ];

      vi.mocked(api.listSourceContainers).mockResolvedValueOnce({
        containers: mockContainers,
        next_page_token: null,
      });

      const onSelect = vi.fn();
      const onClose = vi.fn();

      const { rerender } = render(
        <AlbumPickerModal
          isOpen={true}
          onClose={onClose}
          providerId="google_photos"
          providerName="Google Photos"
          onSelectContainer={onSelect}
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Keyboard Selectable Album')).toBeInTheDocument();
      });

      // Select via Enter key
      const albumCard = screen.getByText('Keyboard Selectable Album').closest('[role="button"]');
      if (albumCard) {
        fireEvent.keyDown(albumCard, { key: 'Enter', code: 'Enter' });
      }

      // Confirm selection
      const selectBtn = screen.getByRole('button', { name: /Select Container/i });
      fireEvent.click(selectBtn);

      expect(onSelect).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'album_kb_1', name: 'Keyboard Selectable Album' })
      );

      // Now test error handling
      vi.mocked(api.listSourceContainers).mockRejectedValueOnce(new Error('Network connection timeout'));
      rerender(
        <AlbumPickerModal
          isOpen={true}
          onClose={onClose}
          providerId="google_photos"
          providerName="Google Photos"
          onSelectContainer={onSelect}
        />
      );

      // Click refresh to trigger error
      const refreshBtn = screen.getByRole('button', { name: /Refresh/i });
      fireEvent.click(refreshBtn);

      await waitFor(() => {
        expect(screen.getByText(/Network connection timeout/i)).toBeInTheDocument();
      });
    });

    it('supports double-click on album card to immediately select and close', async () => {
      const mockContainers = [
        {
          id: 'album_dc_1',
          name: 'Quick Double Click Album',
          item_count: 12,
          container_type: 'album',
        },
      ];

      vi.mocked(api.listSourceContainers).mockResolvedValueOnce({
        containers: mockContainers,
        next_page_token: null,
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

      await waitFor(() => {
        expect(screen.getByText('Quick Double Click Album')).toBeInTheDocument();
      });

      // Double-click container card
      const albumCard = screen.getByText('Quick Double Click Album').closest('[role="button"]');
      if (albumCard) {
        fireEvent.doubleClick(albumCard);
      }

      expect(onSelect).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'album_dc_1', name: 'Quick Double Click Album' })
      );
      expect(onClose).toHaveBeenCalled();
    });
  });

  describe('SessionLauncher Integration', () => {
    it('verifies Google Photos and Google Drive buttons are disabled with Coming Soon badges in SessionLauncher, and launches pipeline with local directory', async () => {
      const onStarted = vi.fn();
      vi.mocked(api.startPipeline).mockResolvedValueOnce({
        session_id: 'sess_local_101',
        status: 'SCANNING',
        total_scans_found: 15,
        mode: 'parallel',
        message: 'Started',
      });

      render(<SessionLauncher onSessionStarted={onStarted} />);

      // Verify Google Photos button is disabled with "Support In Progress" tooltip and "Coming Soon" badge
      const gpBtn = screen.getByText('Google Photos').closest('button');
      expect(gpBtn).toBeDisabled();
      expect(gpBtn).toHaveAttribute('title', 'Support In Progress');
      expect(within(gpBtn!).getByText('Coming Soon')).toBeInTheDocument();

      // Verify Google Drive button is disabled with "Support In Progress" tooltip and "Coming Soon" badge
      const gdriveBtn = screen.getByText('Google Drive').closest('button');
      expect(gdriveBtn).toBeDisabled();
      expect(gdriveBtn).toHaveAttribute('title', 'Support In Progress');
      expect(within(gdriveBtn!).getByText('Coming Soon')).toBeInTheDocument();

      // Verify absence of S3 and GCS options
      expect(screen.queryByText('Cloud Drive & Storage')).not.toBeInTheDocument();
      expect(screen.queryByText(/Amazon S3/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/Google Cloud Storage/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/S3 Bucket Name/i)).not.toBeInTheDocument();

      // Enter local scans folder path
      const folderInput = screen.getByLabelText(/Local Scans Directory Path/i);
      fireEvent.change(folderInput, { target: { value: 'C:/scans/heirloom-box' } });

      // Launch
      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      await waitFor(() => {
        expect(api.startPipeline).toHaveBeenCalledWith(
          expect.objectContaining({
            scans_dir: 'C:/scans/heirloom-box',
            directory_path: 'C:/scans/heirloom-box',
          })
        );
        expect(onStarted).toHaveBeenCalledWith('sess_local_101');
      });
    });
  });
});
