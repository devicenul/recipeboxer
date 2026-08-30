import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { SourceSelector, isDirectoryPickerSupported } from '../src/components/ingestion/SourceSelector';
import { ConfigPanel } from '../src/components/ingestion/ConfigPanel';
import { SessionLauncher } from '../src/components/ingestion/SessionLauncher';
import { ProgressDashboard } from '../src/components/ingestion/ProgressDashboard';
import { SessionHistoryTable } from '../src/components/ingestion/SessionHistoryTable';
import { IngestionMode, PipelineState, PipelineStatus } from '../src/types/pipeline';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    startPipeline: vi.fn(),
    uploadCards: vi.fn(),
    listSessions: vi.fn(),
    deleteSession: vi.fn(),
    getCardImageUrl: vi.fn((id) => `/api/cards/${id}/image`),
  },
}));

describe('R2: Ingestion Controller & Session Manager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('ConfigPanel', () => {
    it('updates concurrency slider within bounds (1-50)', () => {
      const onChange = vi.fn();
      render(
        <ConfigPanel
          config={{
            mode: IngestionMode.PARALLEL,
            max_concurrency: 5,
            model: 'gemini-3.7-flash',
          }}
          onChange={onChange}
        />
      );

      const slider = screen.getByRole('slider', { name: /Max Concurrent Workers/i });
      fireEvent.change(slider, { target: { value: '15' } });

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ max_concurrency: 15 })
      );
    });

    it('switches between parallel and slow-batch mode displaying batch sliders', () => {
      const onChange = vi.fn();
      const { rerender } = render(
        <ConfigPanel
          config={{
            mode: IngestionMode.PARALLEL,
            max_concurrency: 5,
          }}
          onChange={onChange}
        />
      );

      // In parallel mode, slow-batch sliders are hidden
      expect(screen.queryByLabelText(/Slow-Batch Size/i)).not.toBeInTheDocument();

      const slowBatchBtn = screen.getByRole('button', { name: /Slow-Batch Rate Control/i });
      fireEvent.click(slowBatchBtn);
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ mode: IngestionMode.SLOW_BATCH })
      );

      // Re-render with slow-batch config
      rerender(
        <ConfigPanel
          config={{
            mode: IngestionMode.SLOW_BATCH,
            slow_batch_size: 4,
            slow_batch_interval_seconds: 2.0,
          }}
          onChange={onChange}
        />
      );

      expect(screen.getByRole('slider', { name: /Slow-Batch Size/i })).toBeInTheDocument();
      expect(screen.getByRole('slider', { name: /Slow-Batch Interval/i })).toBeInTheDocument();
    });

    it('toggles thinking mode and updates thinking budget slider', () => {
      const onChange = vi.fn();
      const { rerender } = render(
        <ConfigPanel
          config={{
            mode: IngestionMode.PARALLEL,
            thinking_mode: false,
          }}
          onChange={onChange}
        />
      );

      const toggle = screen.getByRole('checkbox', { name: /Enable Gemini Thinking Mode/i });
      fireEvent.click(toggle);

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ thinking_mode: true })
      );

      rerender(
        <ConfigPanel
          config={{
            mode: IngestionMode.PARALLEL,
            thinking_mode: true,
            thinking_budget_tokens: 2048,
          }}
          onChange={onChange}
        />
      );

      expect(screen.getByRole('slider', { name: /Thinking Token Budget/i })).toBeInTheDocument();
    });

    it('manages batch default tags adding and removing tags cleanly', () => {
      const onChange = vi.fn();
      render(
        <ConfigPanel
          config={{
            mode: IngestionMode.PARALLEL,
            default_tags: ['Vintage', 'Dessert'],
          }}
          onChange={onChange}
        />
      );

      expect(screen.getByText('Vintage')).toBeInTheDocument();
      expect(screen.getByText('Dessert')).toBeInTheDocument();

      // Add a new tag
      const tagInput = screen.getByPlaceholderText('+ Add tag (press Enter)');
      fireEvent.change(tagInput, { target: { value: 'Baking' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ default_tags: ['Vintage', 'Dessert', 'Baking'] })
      );

      // Remove a tag
      const removeBtn = screen.getByTitle('Remove tag Vintage');
      fireEvent.click(removeBtn);
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ default_tags: ['Dessert'] })
      );
    });
  });

  describe('SourceSelector Directory Picker', () => {
    it('uses window.showDirectoryPicker to select folder and files without manual typing', async () => {
      const onFolderPathChange = vi.fn();
      const onUploadedFilesChange = vi.fn();

      const mockFile = new File(['mock scan'], 'card_001.jpg', { type: 'image/jpeg' });
      const mockDirHandle = {
        name: 'Grandmas_Recipe_Box',
        values: async function* () {
          yield {
            kind: 'file',
            name: 'card_001.jpg',
            getFile: async () => mockFile,
          };
        },
      };

      (window as any).showDirectoryPicker = vi.fn().mockResolvedValue(mockDirHandle);

      render(
        <SourceSelector
          sourceType="local"
          onSourceTypeChange={vi.fn()}
          localMethod="folder"
          onLocalMethodChange={vi.fn()}
          folderPath=""
          onFolderPathChange={onFolderPathChange}
          uploadedFiles={[]}
          onUploadedFilesChange={onUploadedFilesChange}
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

      const browseBtn = screen.getByRole('button', { name: /Browse Folder\.\.\./i });
      fireEvent.click(browseBtn);

      await waitFor(() => {
        expect((window as any).showDirectoryPicker).toHaveBeenCalledWith({ mode: 'read' });
        expect(onFolderPathChange).toHaveBeenCalledWith('Grandmas_Recipe_Box');
        expect(onUploadedFilesChange).toHaveBeenCalledWith([mockFile]);
      });

      delete (window as any).showDirectoryPicker;
    });

    it('falls back to hidden input when showDirectoryPicker is not supported', () => {
      delete (window as any).showDirectoryPicker;
      expect(isDirectoryPickerSupported()).toBe(false);

      const onFolderPathChange = vi.fn();
      const onUploadedFilesChange = vi.fn();

      const { container } = render(
        <SourceSelector
          sourceType="local"
          onSourceTypeChange={vi.fn()}
          localMethod="folder"
          onLocalMethodChange={vi.fn()}
          folderPath=""
          onFolderPathChange={onFolderPathChange}
          uploadedFiles={[]}
          onUploadedFilesChange={onUploadedFilesChange}
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

      const hiddenInput = container.querySelector('input[webkitdirectory]') as HTMLInputElement;
      expect(hiddenInput).toBeInTheDocument();

      const testFile = new File(['content'], 'card_002.png', { type: 'image/png' });
      Object.defineProperty(testFile, 'webkitRelativePath', {
        value: 'Family_Box/card_002.png',
      });

      fireEvent.change(hiddenInput, {
        target: { files: [testFile] },
      });

      expect(onFolderPathChange).toHaveBeenCalledWith('Family_Box');
      expect(onUploadedFilesChange).toHaveBeenCalledWith([testFile]);
    });
  });

  describe('SessionLauncher', () => {
    it('validates folder path input and calls api.startPipeline with default_tags', async () => {
      const onStarted = vi.fn();
      vi.mocked(api.startPipeline).mockResolvedValueOnce({
        session_id: 'sess_abc123',
        status: 'SCANNING',
        total_scans_found: 12,
        mode: 'parallel',
        message: 'Success',
      });

      render(<SessionLauncher onSessionStarted={onStarted} />);

      const input = screen.getByLabelText(/Local Scans Directory Path/i);
      fireEvent.change(input, { target: { value: '/valid/scans/folder' } });

      // Add a default tag
      const tagInput = screen.getByPlaceholderText('+ Add tag (press Enter)');
      fireEvent.change(tagInput, { target: { value: 'Vintage Box' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });

      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      await waitFor(() => {
        expect(api.startPipeline).toHaveBeenCalledWith(
          expect.objectContaining({
            scans_dir: '/valid/scans/folder',
            default_tags: ['Vintage Box'],
          })
        );
        expect(onStarted).toHaveBeenCalledWith('sess_abc123');
      });
    });

    it('passes default_tags in formData when uploading files directly', async () => {
      const onStarted = vi.fn();
      vi.mocked(api.uploadCards).mockResolvedValueOnce({
        session_id: 'sess_upload_tags_123',
        card_ids: ['card_1'],
        count: 1,
      });

      render(<SessionLauncher onSessionStarted={onStarted} />);

      // Switch to Direct File Upload
      const uploadTab = screen.getByRole('button', { name: /Direct File Upload/i });
      fireEvent.click(uploadTab);

      // Add a default tag
      const tagInput = screen.getByPlaceholderText('+ Add tag (press Enter)');
      fireEvent.change(tagInput, { target: { value: 'Baking 1950s' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });

      // Simulate dropping a file into dropzone
      const file = new File(['fake image'], 'pie.jpg', { type: 'image/jpeg' });
      const dropzoneInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      if (dropzoneInput) {
        fireEvent.change(dropzoneInput, { target: { files: [file] } });
      }

      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      await waitFor(() => {
        expect(api.uploadCards).toHaveBeenCalled();
        const formDataArg = vi.mocked(api.uploadCards).mock.calls[0][0] as FormData;
        expect(formDataArg.get('default_tags')).toBe(JSON.stringify(['Baking 1950s']));
        expect(onStarted).toHaveBeenCalledWith('sess_upload_tags_123');
      });
    });

    it('shows error message if launching with empty directory path', async () => {
      const onStarted = vi.fn();
      render(<SessionLauncher onSessionStarted={onStarted} />);

      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      expect(screen.getByText(/Please enter a valid scans folder path/i)).toBeInTheDocument();
      expect(api.startPipeline).not.toHaveBeenCalled();
    });
  });

  describe('ProgressDashboard & Telemetry', () => {
    const mockStatus: PipelineStatus = {
      session_id: 'sess_telemetry_001',
      status: PipelineState.CORRELATING,
      mode: IngestionMode.PARALLEL,
      progress: {
        total_scans: 20,
        fingerprinted: 20,
        proposals_generated: 10,
        approved_pairs: 0,
        recipes_extracted: 0,
        current_stage: 'Correlating',
        percent_complete: 50.0,
      },
      token_usage: {
        prompt_tokens: 15400,
        candidates_tokens: 4200,
        thinking_tokens: 1024,
        total_tokens: 20624,
      },
      elapsed_seconds: 45,
    };

    it('renders progress bar, stage indicators, token telemetry and elapsed time', () => {
      render(<ProgressDashboard status={mockStatus} />);

      expect(screen.getByText('50%')).toBeInTheDocument();
      expect(screen.getByText('sess_telemetry_001')).toBeInTheDocument();
      expect(screen.getAllByText('20').length).toBeGreaterThanOrEqual(1); // total scans & fingerprinted
      expect(screen.getByText('15,400')).toBeInTheDocument(); // prompt tokens
      expect(screen.getByText('20,624')).toBeInTheDocument(); // total tokens
      expect(screen.getByText('45s')).toBeInTheDocument(); // elapsed
    });

    it('renders active extraction progress panel with pulse indicator, dynamic counter, sub-bar, and auto-transition notice during EXTRACTING state', () => {
      const extractingStatus: PipelineStatus = {
        session_id: 'sess_extracting_001',
        status: PipelineState.EXTRACTING,
        mode: IngestionMode.PARALLEL,
        progress: {
          total_scans: 10,
          fingerprinted: 10,
          proposals_generated: 4,
          approved_recipes: 4,
          recipes_extracted: 2,
          current_stage: 'Extracting',
          percent_complete: 72.5,
        },
        token_usage: {
          prompt_tokens: 24000,
          candidates_tokens: 6500,
          thinking_tokens: 0,
          total_tokens: 30500,
        },
        elapsed_seconds: 52,
      };

      render(<ProgressDashboard status={extractingStatus} />);

      // Heading with active transcription indicator
      expect(screen.getByText('Multimodal Recipe Extraction in Progress')).toBeInTheDocument();

      // Dynamic counter: Recipe 3 of 4 (currently on 3rd after 2 extracted)
      expect(screen.getByText('Recipe 3 of 4')).toBeInTheDocument();

      // Secondary sub-bar label and percentage (2/4 = 50%)
      expect(screen.getByText('Extraction Stage Progress')).toBeInTheDocument();
      expect(screen.getByText('50%')).toBeInTheDocument();

      // Completed recipes count and auto-transition notice
      expect(screen.getByText(/Completed recipes:/i)).toBeInTheDocument();
      expect(screen.getByText('Auto-transitioning to Recipe Editor upon completion...')).toBeInTheDocument();
    });
  });

  describe('SessionHistoryTable', () => {
    const mockSessions = [
      {
        session_id: 'sess_past_01',
        status: PipelineState.COMPLETED,
        card_count: 8,
        recipe_count: 4,
        created_at: '2026-08-25T10:00:00Z',
      },
      {
        session_id: 'sess_past_02',
        status: PipelineState.HITL_PENDING,
        card_count: 6,
        recipe_count: 0,
        created_at: '2026-08-25T11:00:00Z',
      },
    ];

    it('renders list of sessions and triggers resume and view actions', () => {
      const onResume = vi.fn();
      const onView = vi.fn();
      const onDelete = vi.fn();
      const onRefresh = vi.fn();

      render(
        <SessionHistoryTable
          sessions={mockSessions}
          onResumeSession={onResume}
          onViewRecipes={onView}
          onDeleteSession={onDelete}
          onRefresh={onRefresh}
        />
      );

      expect(screen.getByText('sess_past_01')).toBeInTheDocument();
      expect(screen.getByText('sess_past_02')).toBeInTheDocument();

      // Click resume on HITL session
      const resumeBtn = screen.getByRole('button', { name: /Resume/i });
      fireEvent.click(resumeBtn);
      expect(onResume).toHaveBeenCalledWith('sess_past_02');

      // Click view recipes on completed session
      const recipesBtn = screen.getByRole('button', { name: /Recipes/i });
      fireEvent.click(recipesBtn);
      expect(onView).toHaveBeenCalledWith('sess_past_01');
    });
  });
});
