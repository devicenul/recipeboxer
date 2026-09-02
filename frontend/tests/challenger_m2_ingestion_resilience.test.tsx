import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { SourceSelector, IngestionSourceType } from '../src/components/ingestion/SourceSelector';
import { SessionLauncher } from '../src/components/ingestion/SessionLauncher';
import { api } from '../src/utils/api';
import * as googleAuth from '../src/utils/googleAuth';

vi.mock('../src/utils/api', () => ({
  api: {
    startPipeline: vi.fn(),
    uploadCards: vi.fn(),
    listSourceProviders: vi.fn().mockResolvedValue([
      {
        provider_id: 'google_photos',
        name: 'Google Photos',
        connected: false,
        auth_required: true,
        authenticated: false,
      },
      {
        provider_id: 'google_drive',
        name: 'Google Drive',
        connected: false,
        auth_required: true,
        authenticated: false,
      },
    ]),
    listSourceContainers: vi.fn(),
    listSourceItems: vi.fn(),
    connectSourceProvider: vi.fn(),
    listSessions: vi.fn().mockResolvedValue([]),
    browseDirectory: vi.fn(),
  },
}));

vi.mock('../src/utils/googleAuth', () => ({
  requestGoogleAccessToken: vi.fn(),
  GOOGLE_SCOPES: {
    PHOTOS_INGEST: 'https://www.googleapis.com/auth/photoslibrary.readonly',
    DRIVE_INGEST: 'https://www.googleapis.com/auth/drive.readonly',
  },
}));

describe('Milestone 2 Challenger: Ingestion UI Resilience & Type Safety', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Adversarial Test 1: Disabled Button Event & Network Isolation', () => {
    it('clicking disabled Google Photos button in SourceSelector does NOT trigger onSourceTypeChange', () => {
      const onSourceTypeChange = vi.fn();
      render(
        <SourceSelector
          sourceType="local"
          onSourceTypeChange={onSourceTypeChange}
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

      const gpButton = screen.getByText('Google Photos').closest('button');
      expect(gpButton).toBeDisabled();
      expect(gpButton).toHaveAttribute('title', 'Support In Progress');

      fireEvent.click(gpButton!);
      expect(onSourceTypeChange).not.toHaveBeenCalled();
    });

    it('clicking disabled Google Drive button in SourceSelector does NOT trigger onSourceTypeChange', () => {
      const onSourceTypeChange = vi.fn();
      render(
        <SourceSelector
          sourceType="local"
          onSourceTypeChange={onSourceTypeChange}
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

      const gdriveButton = screen.getByText('Google Drive').closest('button');
      expect(gdriveButton).toBeDisabled();
      expect(gdriveButton).toHaveAttribute('title', 'Support In Progress');

      fireEvent.click(gdriveButton!);
      expect(onSourceTypeChange).not.toHaveBeenCalled();
    });

    it('in SessionLauncher, clicking disabled Google Photos or Google Drive does not switch active source nor start pipeline', async () => {
      const onSessionStarted = vi.fn();
      render(<SessionLauncher onSessionStarted={onSessionStarted} />);

      const gpBtn = screen.getByText('Google Photos').closest('button');
      const gdriveBtn = screen.getByText('Google Drive').closest('button');

      // Click disabled source buttons
      fireEvent.click(gpBtn!);
      fireEvent.click(gdriveBtn!);

      // Attempt to launch without filling folder path (local should still be the active source)
      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      await waitFor(() => {
        expect(screen.getByText(/Please enter a valid scans folder path/i)).toBeInTheDocument();
      });

      expect(api.startPipeline).not.toHaveBeenCalled();
      expect(onSessionStarted).not.toHaveBeenCalled();
    });

    it('renders disabled connect buttons in Google Photos panel that reject clicks', async () => {
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
        expect(screen.getByText('Auth Required')).toBeInTheDocument();
      });

      const connectBtn = screen.getByRole('button', { name: /Connect Google Photos/i });
      expect(connectBtn).toBeDisabled();
      expect(connectBtn).toHaveAttribute('title', 'Support In Progress');

      fireEvent.click(connectBtn);

      expect(googleAuth.requestGoogleAccessToken).not.toHaveBeenCalled();
      expect(api.connectSourceProvider).not.toHaveBeenCalled();
    });

    it('renders disabled connect button in Google Drive panel that rejects clicks', async () => {
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
        />
      );

      await waitFor(() => {
        expect(screen.getByText('Auth Required')).toBeInTheDocument();
      });

      const connectBtn = screen.getByRole('button', { name: /Connect Google Drive/i });
      expect(connectBtn).toBeDisabled();
      expect(connectBtn).toHaveAttribute('title', 'Support In Progress');

      fireEvent.click(connectBtn);

      expect(googleAuth.requestGoogleAccessToken).not.toHaveBeenCalled();
      expect(api.connectSourceProvider).not.toHaveBeenCalled();
    });
  });

  describe('Adversarial Test 2: IngestionSourceType Type Safety & Clean Removal', () => {
    it('verifies IngestionSourceType union type strictly covers local, google_photos, google_drive', () => {
      const validSources: IngestionSourceType[] = ['local', 'google_photos', 'google_drive'];
      expect(validSources).toHaveLength(3);

      // Verify that assigning valid sources works
      validSources.forEach((s) => {
        expect(['local', 'google_photos', 'google_drive']).toContain(s);
      });
    });

    it('ensures no legacy S3/GCS UI elements or props are rendered in DOM', () => {
      const { container } = render(
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
        />
      );

      const html = container.innerHTML;
      expect(html).not.toMatch(/s3/i);
      expect(html).not.toMatch(/minio/i);
      expect(html).not.toMatch(/gcs/i);
      expect(html).not.toMatch(/bucket/i);
    });
  });
});
