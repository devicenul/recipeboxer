import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
  },
}));

describe('Phase 5: Cloud & External Source Providers UI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('SourceSelector Component', () => {
    it('renders main source options with Google Photos disabled and switches to Cloud Drive', () => {
      const onSourceChange = vi.fn();
      const onSelectContainer = vi.fn();

      const { rerender } = render(
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
          bucketName=""
          onBucketNameChange={vi.fn()}
          prefix=""
          onPrefixChange={vi.fn()}
        />
      );

      // Verify all 3 top buttons exist
      expect(screen.getByText('Local Folder / Uploads')).toBeInTheDocument();
      expect(screen.getByText('Google Photos')).toBeInTheDocument();
      expect(screen.getByText('Cloud Drive & Storage')).toBeInTheDocument();

      // Verify Google Photos is disabled with "Support In Progress" tooltip
      const gpButton = screen.getByText('Google Photos').closest('button');
      expect(gpButton).toBeDisabled();
      expect(gpButton).toHaveAttribute('title', 'Support In Progress');

      // Click Cloud Drive & Storage
      const cloudButton = screen.getByText('Cloud Drive & Storage').closest('button');
      if (cloudButton) fireEvent.click(cloudButton);
      expect(onSourceChange).toHaveBeenCalledWith('s3');

      // Re-render as Google Photos to verify inner controls are disabled
      rerender(
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
          onSelectContainer={onSelectContainer}
          categoryFilter="FOOD"
          onCategoryFilterChange={vi.fn()}
          bucketName=""
          onBucketNameChange={vi.fn()}
          prefix=""
          onPrefixChange={vi.fn()}
        />
      );

      expect(screen.getByText(/Google Photos Library Connection/i)).toBeInTheDocument();
      const browseAlbumsBtn = screen.getByRole('button', { name: /Browse Google Photos Albums.../i });
      expect(browseAlbumsBtn).toBeDisabled();
      expect(browseAlbumsBtn).toHaveAttribute('title', 'Support In Progress');
    });

    it('renders Cloud Drive and object storage options (Google Drive disabled, S3 and GCS enabled)', () => {
      const onSourceChange = vi.fn();

      render(
        <SourceSelector
          sourceType="s3"
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
          bucketName="my-recipe-bucket"
          onBucketNameChange={vi.fn()}
          prefix="box-1/"
          onPrefixChange={vi.fn()}
        />
      );

      // Google Drive option is disabled with tooltip
      const gdriveBtn = screen.getByRole('button', { name: /^Google Drive$/i });
      expect(gdriveBtn).toBeDisabled();
      expect(gdriveBtn).toHaveAttribute('title', 'Support In Progress');

      expect(screen.getByText('Amazon S3 / MinIO')).toBeInTheDocument();
      expect(screen.getByText('Google Cloud Storage')).toBeInTheDocument();
      expect(screen.getByText('S3 Bucket Name')).toBeInTheDocument();
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
          bucketName=""
          onBucketNameChange={vi.fn()}
          prefix=""
          onPrefixChange={vi.fn()}
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
  });

  describe('SessionLauncher with Remote Sources Integration', () => {
    it('verifies Google Photos tab is disabled in SessionLauncher and launches pipeline with GCS provider payload', async () => {
      const onStarted = vi.fn();
      vi.mocked(api.startPipeline).mockResolvedValueOnce({
        session_id: 'sess_gcs_101',
        status: 'SCANNING',
        total_scans_found: 15,
        mode: 'parallel',
        message: 'Started',
      });

      render(<SessionLauncher onSessionStarted={onStarted} />);

      // Verify Google Photos tab is disabled with "Support In Progress" tooltip
      const gpTab = screen.getByText('Google Photos').closest('button');
      expect(gpTab).toBeDisabled();
      expect(gpTab).toHaveAttribute('title', 'Support In Progress');

      // Switch to Cloud Drive & Storage
      const cloudTab = screen.getByText('Cloud Drive & Storage').closest('button');
      if (cloudTab) fireEvent.click(cloudTab);

      // Switch to GCS
      const gcsBtn = screen.getByText('Google Cloud Storage');
      fireEvent.click(gcsBtn);

      const bucketInput = screen.getByPlaceholderText('recipeboxer-archive');
      fireEvent.change(bucketInput, { target: { value: 'my-gcs-bucket' } });

      // Launch
      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      await waitFor(() => {
        expect(api.startPipeline).toHaveBeenCalledWith(
          expect.objectContaining({
            source_provider: 'gcs',
            source_config: expect.objectContaining({ bucket_name: 'my-gcs-bucket' }),
          })
        );
        expect(onStarted).toHaveBeenCalledWith('sess_gcs_101');
      });
    });

    it('launches pipeline with S3 provider payload and custom prefix', async () => {
      const onStarted = vi.fn();
      vi.mocked(api.startPipeline).mockResolvedValueOnce({
        session_id: 'sess_s3_303',
        status: 'SCANNING',
        total_scans_found: 20,
        mode: 'parallel',
        message: 'Started',
      });

      render(<SessionLauncher onSessionStarted={onStarted} />);

      // Switch to Cloud Drive
      const cloudTab = screen.getByText('Cloud Drive & Storage').closest('button');
      if (cloudTab) fireEvent.click(cloudTab);

      // Switch to Amazon S3
      const s3Btn = screen.getByText('Amazon S3 / MinIO');
      fireEvent.click(s3Btn);

      // Enter bucket and prefix
      const bucketInput = screen.getByPlaceholderText('recipeboxer-scans');
      fireEvent.change(bucketInput, { target: { value: 'custom-scans-bucket' } });

      const prefixInput = screen.getByPlaceholderText('batch-2026/box-1/');
      fireEvent.change(prefixInput, { target: { value: 'vintage-recipes/' } });

      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      await waitFor(() => {
        expect(api.startPipeline).toHaveBeenCalledWith(
          expect.objectContaining({
            source_provider: 's3',
            source_container_id: 'vintage-recipes/',
            source_config: expect.objectContaining({
              bucket_name: 'custom-scans-bucket',
              prefix: 'vintage-recipes/',
            }),
          })
        );
        expect(onStarted).toHaveBeenCalledWith('sess_s3_303');
      });
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
});

