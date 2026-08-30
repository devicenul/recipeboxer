import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, renderHook, act } from '@testing-library/react';
import React, { useState } from 'react';
import { SourceSelector, isDirectoryPickerSupported } from '../src/components/ingestion/SourceSelector';
import { ConfigPanel } from '../src/components/ingestion/ConfigPanel';
import { SessionLauncher } from '../src/components/ingestion/SessionLauncher';
import { TagManager } from '../src/components/recipe/TagManager';
import { useSSE } from '../src/hooks/useSSE';
import { PipelineState, IngestionMode, PipelineStartRequest } from '../src/types/pipeline';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    startPipeline: vi.fn(),
    uploadCards: vi.fn(),
    listSessions: vi.fn(),
    deleteSession: vi.fn(),
    listSourceProviders: vi.fn().mockResolvedValue([]),
    getCardImageUrl: vi.fn((id) => `/api/cards/${id}/image`),
  },
}));

// Mock EventSource for SSE testing
class MockEventSource {
  url: string;
  onopen: (() => void) | null = null;
  onerror: ((err: any) => void) | null = null;
  listeners: Record<string, ((e: any) => void)[]> = {};
  closed = false;

  constructor(url: string) {
    this.url = url;
    setTimeout(() => {
      if (!this.closed) {
        this.onopen?.();
      }
    }, 5);
  }

  addEventListener(type: string, listener: (e: any) => void) {
    if (!this.listeners[type]) {
      this.listeners[type] = [];
    }
    this.listeners[type].push(listener);
  }

  removeEventListener(type: string, listener: (e: any) => void) {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
    }
  }

  emit(type: string, data: any) {
    if (this.closed) return;
    const event = { data: typeof data === 'string' ? data : JSON.stringify(data) };
    if (this.listeners[type]) {
      this.listeners[type].forEach((l) => l(event));
    }
  }

  triggerError(err: any) {
    if (this.closed) return;
    this.onerror?.(err);
  }

  close() {
    this.closed = true;
    this.listeners = {};
  }
}

describe('Milestone 1 Empirical Challenger Adversarial Tests (Frontend)', () => {
  let mockEventSourceInstance: MockEventSource | null = null;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete (window as any).showDirectoryPicker;
  });

  // ==========================================================================
  // AREA 1: DIRECTORY PICKER ADVERSARIAL EDGE CASES
  // ==========================================================================
  describe('Area 1: Directory Picker Adversarial Edge Cases', () => {
    it('handles user cancellation (AbortError) silently without error or opening fallback input', async () => {
      const onFolderPathChange = vi.fn();
      const onUploadedFilesChange = vi.fn();

      const abortError = new Error('The user aborted a request.');
      abortError.name = 'AbortError';

      (window as any).showDirectoryPicker = vi.fn().mockRejectedValue(abortError);

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
      const inputClickSpy = vi.spyOn(hiddenInput, 'click');

      const browseBtn = screen.getByRole('button', { name: /Browse Folder\.\.\./i });
      fireEvent.click(browseBtn);

      await waitFor(() => {
        expect((window as any).showDirectoryPicker).toHaveBeenCalledWith({ mode: 'read' });
      });

      // Crucial check: AbortError must NOT trigger fallback input click or state changes
      expect(inputClickSpy).not.toHaveBeenCalled();
      expect(onFolderPathChange).not.toHaveBeenCalled();
      expect(onUploadedFilesChange).not.toHaveBeenCalled();
    });

    it('falls back to file input click when showDirectoryPicker throws non-abort error (e.g., SecurityError)', async () => {
      const onFolderPathChange = vi.fn();
      const onUploadedFilesChange = vi.fn();

      const secError = new Error('Permission denied by browser policy.');
      secError.name = 'SecurityError';

      (window as any).showDirectoryPicker = vi.fn().mockRejectedValue(secError);

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
      const inputClickSpy = vi.spyOn(hiddenInput, 'click');

      const browseBtn = screen.getByRole('button', { name: /Browse Folder\.\.\./i });
      fireEvent.click(browseBtn);

      await waitFor(() => {
        expect((window as any).showDirectoryPicker).toHaveBeenCalled();
        expect(inputClickSpy).toHaveBeenCalled();
      });
    });

    it('filters out non-image files, hidden files, and directories cleanly in showDirectoryPicker', async () => {
      const onFolderPathChange = vi.fn();
      const onUploadedFilesChange = vi.fn();

      const validJpg = new File(['scan1'], 'recipe_card_1.jpg', { type: 'image/jpeg' });
      const validPng = new File(['scan2'], 'recipe_card_2.PNG', { type: 'image/png' });
      const validWebp = new File(['scan3'], 'recipe_card_3.webp', { type: 'image/webp' });
      const validTiff = new File(['scan4'], 'recipe_card_4.TIFF', { type: 'image/tiff' });
      const validBmp = new File(['scan5'], 'recipe_card_5.bmp', { type: 'image/bmp' });
      const validGif = new File(['scan6'], 'recipe_card_6.GIF', { type: 'image/gif' });

      const invalidTxt = new File(['text'], 'notes.txt', { type: 'text/plain' });
      const invalidPdf = new File(['pdf'], 'scans.pdf', { type: 'application/pdf' });
      const invalidDsStore = new File(['meta'], '.DS_Store', { type: 'application/octet-stream' });
      const invalidDocx = new File(['doc'], 'recipe.docx', { type: 'application/octet-stream' });

      const mockDirHandle = {
        name: 'Grandmas_Vintage_Box',
        values: async function* () {
          yield { kind: 'file', name: 'recipe_card_1.jpg', getFile: async () => validJpg };
          yield { kind: 'file', name: 'notes.txt', getFile: async () => invalidTxt };
          yield { kind: 'file', name: 'recipe_card_2.PNG', getFile: async () => validPng };
          yield { kind: 'directory', name: 'subfolder_thumbnails' }; // subdirectory entry
          yield { kind: 'file', name: 'scans.pdf', getFile: async () => invalidPdf };
          yield { kind: 'file', name: 'recipe_card_3.webp', getFile: async () => validWebp };
          yield { kind: 'file', name: '.DS_Store', getFile: async () => invalidDsStore };
          yield { kind: 'file', name: 'recipe_card_4.TIFF', getFile: async () => validTiff };
          yield { kind: 'file', name: 'recipe.docx', getFile: async () => invalidDocx };
          yield { kind: 'file', name: 'recipe_card_5.bmp', getFile: async () => validBmp };
          yield { kind: 'file', name: 'recipe_card_6.GIF', getFile: async () => validGif };
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
        expect(onFolderPathChange).toHaveBeenCalledWith('Grandmas_Vintage_Box');
        expect(onUploadedFilesChange).toHaveBeenCalledTimes(1);
      });

      const passedFiles = onUploadedFilesChange.mock.calls[0][0] as File[];
      expect(passedFiles.length).toBe(6);
      expect(passedFiles.map((f) => f.name)).toEqual([
        'recipe_card_1.jpg',
        'recipe_card_2.PNG',
        'recipe_card_3.webp',
        'recipe_card_4.TIFF',
        'recipe_card_5.bmp',
        'recipe_card_6.GIF',
      ]);
    });

    it('filters out non-image files in webkitdirectory fallback input', () => {
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

      const file1 = new File(['img1'], 'card1.jpg', { type: 'image/jpeg' });
      Object.defineProperty(file1, 'webkitRelativePath', { value: 'Box_A/card1.jpg' });

      const file2 = new File(['text'], 'readme.txt', { type: 'text/plain' });
      Object.defineProperty(file2, 'webkitRelativePath', { value: 'Box_A/readme.txt' });

      const file3 = new File(['img2'], 'card2.png', { type: 'image/png' });
      Object.defineProperty(file3, 'webkitRelativePath', { value: 'Box_A/card2.png' });

      fireEvent.change(hiddenInput, {
        target: { files: [file1, file2, file3] },
      });

      expect(onFolderPathChange).toHaveBeenCalledWith('Box_A');
      expect(onUploadedFilesChange).toHaveBeenCalledWith([file1, file3]);
    });
  });

  // ==========================================================================
  // AREA 2: SSE TELEMETRY RAPID RE-RENDER STRESS & STABILITY
  // ==========================================================================
  describe('Area 2: SSE Telemetry Stress & Re-render Stability', () => {
    it('survives 100+ rapid parent re-renders without thrashing or recreating EventSource', async () => {
      let eventSourceConstructorCount = 0;
      (global as any).EventSource = vi.fn((url: string) => {
        eventSourceConstructorCount++;
        mockEventSourceInstance = new MockEventSource(url);
        return mockEventSourceInstance;
      });

      // Component that triggers continuous state updates / re-renders
      const RapidReRenderHost = () => {
        const [counter, setCounter] = useState(0);
        const [lastStatus, setLastStatus] = useState<any>(null);

        // Hook with dynamic inline callback that creates a new function reference every single render
        const { isConnected } = useSSE({
          sessionId: 'sess_rapid_render_test',
          enabled: true,
          onStateChange: (status) => {
            setLastStatus(status);
          },
          onProgress: (status) => {
            setLastStatus(status);
          },
        });

        return (
          <div>
            <span data-testid="counter">{counter}</span>
            <span data-testid="status">{lastStatus?.status || 'NONE'}</span>
            <span data-testid="connected">{isConnected ? 'YES' : 'NO'}</span>
            <button
              data-testid="re-render-btn"
              onClick={() => setCounter((c) => c + 1)}
            >
              Tick
            </button>
          </div>
        );
      };

      render(<RapidReRenderHost />);

      // Initial EventSource creation
      expect(eventSourceConstructorCount).toBe(1);

      const btn = screen.getByTestId('re-render-btn');

      // Trigger 100 rapid re-renders
      act(() => {
        for (let i = 0; i < 100; i++) {
          fireEvent.click(btn);
        }
      });

      expect(screen.getByTestId('counter')).toHaveTextContent('100');

      // EventSource MUST remain exactly 1 (no reconnect loops or connection thrashing)
      expect(eventSourceConstructorCount).toBe(1);

      // Now emit an event to verify event dispatch still works cleanly after 100 re-renders
      act(() => {
        mockEventSourceInstance?.emit('state_change', {
          session_id: 'sess_rapid_render_test',
          status: PipelineState.CORRELATING,
          progress: { percent_complete: 45.0 },
        });
      });

      expect(screen.getByTestId('status')).toHaveTextContent('CORRELATING');
    });

    it('routes all event types correctly to active callbacks under high throughput', async () => {
      (global as any).EventSource = vi.fn((url: string) => {
        mockEventSourceInstance = new MockEventSource(url);
        return mockEventSourceInstance;
      });

      const onStateChange = vi.fn();
      const onProgress = vi.fn();
      const onHitlReady = vi.fn();
      const onRecipeExtracted = vi.fn();
      const onCompleted = vi.fn();

      renderHook(() =>
        useSSE({
          sessionId: 'sess_burst_test',
          enabled: true,
          onStateChange,
          onProgress,
          onHitlReady,
          onRecipeExtracted,
          onCompleted,
        })
      );

      // Emit high-frequency event burst
      act(() => {
        mockEventSourceInstance?.emit('state_change', {
          session_id: 'sess_burst_test',
          status: PipelineState.FINGERPRINTING,
        });
        mockEventSourceInstance?.emit('progress', {
          session_id: 'sess_burst_test',
          status: PipelineState.FINGERPRINTING,
          progress: { percent_complete: 25.0 },
        });
        mockEventSourceInstance?.emit('hitl_ready', {
          session_id: 'sess_burst_test',
          proposals_count: 3,
        });
        mockEventSourceInstance?.emit('recipe_extracted', {
          recipe_id: 'rec_100',
          title: 'Lemon Meringue Pie',
        });
        mockEventSourceInstance?.emit('completed', {
          session_id: 'sess_burst_test',
          status: PipelineState.COMPLETED,
        });
      });

      expect(onStateChange).toHaveBeenCalledWith(
        expect.objectContaining({ status: PipelineState.FINGERPRINTING })
      );
      expect(onProgress).toHaveBeenCalledWith(
        expect.objectContaining({ progress: { percent_complete: 25.0 } })
      );
      expect(onHitlReady).toHaveBeenCalledWith({
        session_id: 'sess_burst_test',
        proposals_count: 3,
      });
      expect(onRecipeExtracted).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Lemon Meringue Pie' })
      );
      expect(onCompleted).toHaveBeenCalledWith(
        expect.objectContaining({ status: PipelineState.COMPLETED })
      );
    });
  });

  // ==========================================================================
  // AREA 3: BATCH DEFAULT TAGS ADVERSARIAL EDGE CASES
  // ==========================================================================
  describe('Area 3: Batch Default Tags Adversarial Edge Cases', () => {
    it('handles special characters, unicode, emojis, commas, and whitespace trimming in TagManager', () => {
      const onAddTag = vi.fn();
      const onRemoveTag = vi.fn();

      const initialTags = [
        'Grandma\'s "Secret" Box',
        '🍰 Desserts & Sweets',
        '1950s / Vintage #1',
        'Gluten-Free & Organic (100%)',
      ];

      const { rerender } = render(
        <TagManager
          tags={initialTags}
          onAddTag={onAddTag}
          onRemoveTag={onRemoveTag}
        />
      );

      // Verify all special characters and emojis render properly
      expect(screen.getByText('Grandma\'s "Secret" Box')).toBeInTheDocument();
      expect(screen.getByText('🍰 Desserts & Sweets')).toBeInTheDocument();
      expect(screen.getByText('1950s / Vintage #1')).toBeInTheDocument();
      expect(screen.getByText('Gluten-Free & Organic (100%)')).toBeInTheDocument();

      const tagInput = screen.getByPlaceholderText('+ Add tag (press Enter)');

      // Whitespace-only tag -> should NOT trigger onAddTag
      fireEvent.change(tagInput, { target: { value: '    \t   ' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });
      expect(onAddTag).not.toHaveBeenCalled();

      // Comma key press as delimiter
      fireEvent.change(tagInput, { target: { value: '  Quick & Easy  ' } });
      fireEvent.keyDown(tagInput, { key: ',' });
      expect(onAddTag).toHaveBeenCalledWith('Quick & Easy');

      // Add HTML/XSS-like tag
      fireEvent.change(tagInput, { target: { value: '<script>alert("test")</script>' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });
      expect(onAddTag).toHaveBeenCalledWith('<script>alert("test")</script>');
    });

    it('prevents adding duplicate tags in ConfigPanel', () => {
      const onChange = vi.fn();
      render(
        <ConfigPanel
          config={{
            mode: IngestionMode.PARALLEL,
            default_tags: ['Dessert', 'Baking'],
          }}
          onChange={onChange}
        />
      );

      const tagInput = screen.getByPlaceholderText('+ Add tag (press Enter)');

      // Attempt to add existing tag "Dessert"
      fireEvent.change(tagInput, { target: { value: 'Dessert' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });

      // onChange should NOT be called because 'Dessert' is already in default_tags
      expect(onChange).not.toHaveBeenCalled();

      // Add a non-duplicate tag "Holiday"
      fireEvent.change(tagInput, { target: { value: 'Holiday' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ default_tags: ['Dessert', 'Baking', 'Holiday'] })
      );
    });

    it('serializes default_tags with unicode, quotes, and commas cleanly in SessionLauncher launch payloads', async () => {
      const onStarted = vi.fn();
      vi.mocked(api.startPipeline).mockResolvedValueOnce({
        session_id: 'sess_unicode_tags',
        status: 'SCANNING',
        total_scans_found: 5,
        mode: 'parallel',
        message: 'Success',
      });

      render(<SessionLauncher onSessionStarted={onStarted} />);

      const pathInput = screen.getByLabelText(/Local Scans Directory Path/i);
      fireEvent.change(pathInput, { target: { value: '/scans/vintage_box' } });

      const tagInput = screen.getByPlaceholderText('+ Add tag (press Enter)');

      // Add complex tags
      fireEvent.change(tagInput, { target: { value: 'Grand-mère\'s 1950s' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });

      fireEvent.change(tagInput, { target: { value: '🍓 Strawberry & Cream' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });

      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      await waitFor(() => {
        expect(api.startPipeline).toHaveBeenCalledWith(
          expect.objectContaining({
            scans_dir: '/scans/vintage_box',
            default_tags: ['Grand-mère\'s 1950s', '🍓 Strawberry & Cream'],
          })
        );
        expect(onStarted).toHaveBeenCalledWith('sess_unicode_tags');
      });
    });
  });
});
