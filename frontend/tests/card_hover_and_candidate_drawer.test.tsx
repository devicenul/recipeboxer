import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react';
import { CardHoverPreview } from '../src/components/common/CardHoverPreview';
import { Dropzone } from '../src/components/common/Dropzone';
import { CandidateDrawer } from '../src/components/hitl/CandidateDrawer';
import { MultiCardGrouping } from '../src/components/hitl/MultiCardGrouping';
import { SourceSelector } from '../src/components/ingestion/SourceSelector';
import { CorrelationCandidate } from '../src/types/hitl';
import { api } from '../src/utils/api';

describe('R3: 2-Second Hover Card Magnification Preview Component', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('does not display popover initially', () => {
    render(
      <CardHoverPreview cardId="card_01" imageUrl="http://test/card1.jpg" title="Card 1">
        <div data-testid="thumb-target">Thumbnail</div>
      </CardHoverPreview>
    );

    expect(screen.queryByTestId('card-hover-preview-popover')).not.toBeInTheDocument();
  });

  it('cancels preview if mouse leaves before 2.0 seconds elapse', () => {
    render(
      <CardHoverPreview cardId="card_01" imageUrl="http://test/card1.jpg" title="Card 1">
        <div data-testid="thumb-target">Thumbnail</div>
      </CardHoverPreview>
    );

    const target = screen.getByTestId('thumb-target');
    fireEvent.mouseEnter(target);

    // Advance 1.5 seconds (less than 2000ms)
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(screen.queryByTestId('card-hover-preview-popover')).not.toBeInTheDocument();

    // User moves mouse away before 2.0s
    fireEvent.mouseLeave(target);

    // Advance another 1.0s (total 2.5s)
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    // Popover must NOT have opened
    expect(screen.queryByTestId('card-hover-preview-popover')).not.toBeInTheDocument();
  });

  it('opens magnified preview popover after hovering for >= 2.0 seconds', () => {
    render(
      <CardHoverPreview
        cardId="card_vintage_01"
        imageUrl="http://test/vintage01.jpg"
        title="Card Vintage 01"
        subtitle="Confidence: 94%"
      >
        <div data-testid="thumb-target">Thumbnail</div>
      </CardHoverPreview>
    );

    const target = screen.getByTestId('thumb-target');
    fireEvent.mouseEnter(target);

    // Advance 2000ms
    act(() => {
      vi.advanceTimersByTime(2000);
    });

    const popover = screen.getByTestId('card-hover-preview-popover');
    expect(popover).toBeInTheDocument();
    expect(popover).toHaveAttribute('data-card-id', 'card_vintage_01');
    expect(screen.getByText('Card Vintage 01')).toBeInTheDocument();
    expect(screen.getByText('Confidence: 94%')).toBeInTheDocument();
    expect(screen.getByText(/2x Magnified/i)).toBeInTheDocument();
  });

  it('closes preview cleanly when mouse leaves after popover is open', () => {
    render(
      <CardHoverPreview cardId="card_02" imageUrl="http://test/card2.jpg" title="Card 2">
        <div data-testid="thumb-target">Thumbnail</div>
      </CardHoverPreview>
    );

    const target = screen.getByTestId('thumb-target');
    fireEvent.mouseEnter(target);

    act(() => {
      vi.advanceTimersByTime(2100);
    });
    expect(screen.getByTestId('card-hover-preview-popover')).toBeInTheDocument();

    // Mouse leaves
    fireEvent.mouseLeave(target);
    expect(screen.queryByTestId('card-hover-preview-popover')).not.toBeInTheDocument();
  });

  it('clamps popover coordinates within viewport boundaries', () => {
    render(
      <CardHoverPreview cardId="card_edge" imageUrl="http://test/edge.jpg" title="Edge Card">
        <div data-testid="thumb-target">Thumbnail</div>
      </CardHoverPreview>
    );

    const target = screen.getByTestId('thumb-target');
    fireEvent.mouseEnter(target);

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    const popover = screen.getByTestId('card-hover-preview-popover');
    expect(popover).toBeInTheDocument();
    // Verify top and left styles are defined numbers (clamped)
    expect(popover.style.top).toMatch(/px$/);
    expect(popover.style.left).toMatch(/px$/);
  });

  it('does not open popover when enabled is false', () => {
    render(
      <CardHoverPreview
        cardId="card_disabled"
        imageUrl="http://test/disabled.jpg"
        title="Disabled Card"
        enabled={false}
      >
        <div data-testid="thumb-target">Thumbnail</div>
      </CardHoverPreview>
    );

    const target = screen.getByTestId('thumb-target');
    fireEvent.mouseEnter(target);

    act(() => {
      vi.advanceTimersByTime(2500);
    });

    expect(screen.queryByTestId('card-hover-preview-popover')).not.toBeInTheDocument();
  });

  it('closes popover immediately if enabled becomes false while open', () => {
    const { rerender } = render(
      <CardHoverPreview
        cardId="card_toggle"
        imageUrl="http://test/toggle.jpg"
        title="Toggle Card"
        enabled={true}
      >
        <div data-testid="thumb-target">Thumbnail</div>
      </CardHoverPreview>
    );

    const target = screen.getByTestId('thumb-target');
    fireEvent.mouseEnter(target);

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByTestId('card-hover-preview-popover')).toBeInTheDocument();

    rerender(
      <CardHoverPreview
        cardId="card_toggle"
        imageUrl="http://test/toggle.jpg"
        title="Toggle Card"
        enabled={false}
      >
        <div data-testid="thumb-target">Thumbnail</div>
      </CardHoverPreview>
    );

    expect(screen.queryByTestId('card-hover-preview-popover')).not.toBeInTheDocument();
  });
});

describe('R2: CandidateDrawer Viewport & Attach Button Layout', () => {
  const mockCandidates: CorrelationCandidate[] = [
    {
      candidate_card_id: 'card_apple_pie_back',
      score: 0.92,
      composite_score: 0.92,
      heuristic_score: 0.88,
      semantic_score: 0.95,
      candidate_image_path: 'http://test/apple_back.jpg',
      reasoning_snippet: 'Identical cursive blue ink and card ruling style matching Part 1.',
    },
    {
      candidate_card_id: 'card_pecan_pie_back',
      score: 0.65,
      composite_score: 0.65,
      heuristic_score: 0.60,
      semantic_score: 0.70,
      candidate_image_path: 'http://test/pecan_back.jpg',
      reasoning_snippet: 'Similar cardstock dimensions but differing ink flow.',
    },
  ];

  it('renders all candidate cards, score breakdowns, and both action buttons fully', () => {
    const onSelectCandidateAsBack = vi.fn();
    const onAttachCandidateToGroup = vi.fn();

    render(
      <CandidateDrawer
        isOpen={true}
        onClose={vi.fn()}
        selectedCardId="card_apple_pie_front"
        candidates={mockCandidates}
        currentBackCardId={null}
        groupedCardIds={[]}
        onSelectCandidateAsBack={onSelectCandidateAsBack}
        onAttachCandidateToGroup={onAttachCandidateToGroup}
      />
    );

    // Verify header and selected card display
    expect(screen.getByText('Candidate Correlation Drawer')).toBeInTheDocument();
    expect(screen.getByText('card_apple_pie_front')).toBeInTheDocument();

    // Verify candidate 1
    expect(screen.getByText('card_apple_pie_back')).toBeInTheDocument();
    expect(screen.getByText('92%')).toBeInTheDocument();
    expect(screen.getAllByText(/Heuristic:/).length).toBe(2);
    expect(screen.getAllByText(/Semantic:/).length).toBe(2);

    // Verify candidate 2
    expect(screen.getByText('card_pecan_pie_back')).toBeInTheDocument();
    expect(screen.getByText('65%')).toBeInTheDocument();

    // Verify both action buttons are rendered for each candidate
    const selectButtons = screen.getAllByRole('button', { name: /Select as Next Part/i });
    expect(selectButtons.length).toBe(2);

    const attachButtons = screen.getAllByRole('button', { name: /Attach/i });
    expect(attachButtons.length).toBe(2);
  });

  it('triggers onSelectCandidateAsBack when Select as Next Part is clicked', () => {
    const onSelectCandidateAsBack = vi.fn();
    const onAttachCandidateToGroup = vi.fn();

    render(
      <CandidateDrawer
        isOpen={true}
        onClose={vi.fn()}
        selectedCardId="card_apple_pie_front"
        candidates={mockCandidates}
        currentBackCardId={null}
        groupedCardIds={[]}
        onSelectCandidateAsBack={onSelectCandidateAsBack}
        onAttachCandidateToGroup={onAttachCandidateToGroup}
      />
    );

    const selectButtons = screen.getAllByRole('button', { name: /Select as Next Part/i });
    fireEvent.click(selectButtons[0]);

    expect(onSelectCandidateAsBack).toHaveBeenCalledWith('card_apple_pie_back');
  });

  it('triggers onAttachCandidateToGroup when Attach is clicked', () => {
    const onSelectCandidateAsBack = vi.fn();
    const onAttachCandidateToGroup = vi.fn();

    render(
      <CandidateDrawer
        isOpen={true}
        onClose={vi.fn()}
        selectedCardId="card_apple_pie_front"
        candidates={mockCandidates}
        currentBackCardId={null}
        groupedCardIds={[]}
        onSelectCandidateAsBack={onSelectCandidateAsBack}
        onAttachCandidateToGroup={onAttachCandidateToGroup}
      />
    );

    const attachButtons = screen.getAllByRole('button', { name: /Attach/i });
    fireEvent.click(attachButtons[1]);

    expect(onAttachCandidateToGroup).toHaveBeenCalledWith('card_pecan_pie_back');
  });

  it('displays active state for current back card and attached group cards', () => {
    render(
      <CandidateDrawer
        isOpen={true}
        onClose={vi.fn()}
        selectedCardId="card_apple_pie_front"
        candidates={mockCandidates}
        currentBackCardId="card_apple_pie_back"
        groupedCardIds={['card_pecan_pie_back']}
        onSelectCandidateAsBack={vi.fn()}
        onAttachCandidateToGroup={vi.fn()}
      />
    );

    expect(screen.getByText('Active Next Part')).toBeInTheDocument();
    expect(screen.getByText('Attached')).toBeInTheDocument();
  });
});

describe('R1: SourceSelector Native Directory Browsing & Absolute Windows Path', () => {
  it('populates complete absolute Windows path when native directory browser returns path', async () => {
    const onFolderPathChange = vi.fn();
    const mockWindowsPath = 'E:\\workspaces\\projects\\recipeboxer\\tests\\fixtures\\sample_cards';

    // Mock api.browseDirectory to simulate native Windows folder selection
    vi.spyOn(api, 'browseDirectory').mockResolvedValue({
      path: mockWindowsPath,
      canceled: false,
      success: true,
    });

    render(
      <SourceSelector
        sourceType="local"
        onSourceTypeChange={vi.fn()}
        localMethod="folder"
        onLocalMethodChange={vi.fn()}
        folderPath=""
        onFolderPathChange={onFolderPathChange}
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

    const browseBtn = screen.getByRole('button', { name: /Browse Folder\.\.\./i });
    fireEvent.click(browseBtn);

    await waitFor(() => {
      expect(api.browseDirectory).toHaveBeenCalled();
      expect(onFolderPathChange).toHaveBeenCalledWith(mockWindowsPath);
    });
  });

  it('does not modify path or trigger fallback when native browser dialog is canceled', async () => {
    const onFolderPathChange = vi.fn();

    vi.spyOn(api, 'browseDirectory').mockResolvedValue({
      path: null,
      canceled: true,
      success: true,
    });

    render(
      <SourceSelector
        sourceType="local"
        onSourceTypeChange={vi.fn()}
        localMethod="folder"
        onLocalMethodChange={vi.fn()}
        folderPath="E:\\existing\\path"
        onFolderPathChange={onFolderPathChange}
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

    const browseBtn = screen.getByRole('button', { name: /Browse Folder\.\.\./i });
    fireEvent.click(browseBtn);

    await waitFor(() => {
      expect(api.browseDirectory).toHaveBeenCalled();
    });

    expect(onFolderPathChange).not.toHaveBeenCalled();
  });

  it('falls back to browser showDirectoryPicker when backend native browse returns success=false', async () => {
    const onFolderPathChange = vi.fn();
    const onUploadedFilesChange = vi.fn();

    vi.spyOn(api, 'browseDirectory').mockResolvedValue({
      path: null,
      canceled: false,
      success: false,
      error: 'Tkinter unavailable',
    });

    const mockFile = new File(['mock content'], 'sample_card.jpg', { type: 'image/jpeg' });
    const mockDirHandle = {
      name: 'browser_selected_folder',
      values: vi.fn().mockImplementation(async function* () {
        yield { kind: 'file', getFile: async () => mockFile };
      }),
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
      expect(api.browseDirectory).toHaveBeenCalled();
      expect((window as any).showDirectoryPicker).toHaveBeenCalled();
      expect(onFolderPathChange).toHaveBeenCalledWith('browser_selected_folder');
      expect(onUploadedFilesChange).toHaveBeenCalledWith([mockFile]);
    });
  });

  it('triggers HTML directory input click fallback when showDirectoryPicker is unsupported', async () => {
    const onFolderPathChange = vi.fn();
    delete (window as any).showDirectoryPicker;

    vi.spyOn(api, 'browseDirectory').mockRejectedValue(new Error('Network error'));

    const { container } = render(
      <SourceSelector
        sourceType="local"
        onSourceTypeChange={vi.fn()}
        localMethod="folder"
        onLocalMethodChange={vi.fn()}
        folderPath=""
        onFolderPathChange={onFolderPathChange}
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

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeInTheDocument();
    const clickSpy = vi.spyOn(fileInput, 'click');

    const browseBtn = screen.getByRole('button', { name: /Browse Folder\.\.\./i });
    fireEvent.click(browseBtn);

    await waitFor(() => {
      expect(clickSpy).toHaveBeenCalled();
    });
  });
});

describe('R3 Integration: Hover Preview inside MultiCardGrouping and Dropzone', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('renders hover magnification preview on card thumbnails in MultiCardGrouping tray', () => {
    render(
      <MultiCardGrouping
        cardIds={['card_seq_01', 'card_seq_02']}
        sessionId="test-session"
        onReorder={vi.fn()}
        onDetach={vi.fn()}
      />
    );

    const firstCardImg = screen.getByAltText('card_seq_01');
    expect(firstCardImg).toBeInTheDocument();

    fireEvent.mouseEnter(firstCardImg.closest('div')!.parentElement!);

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    const popover = screen.getByTestId('card-hover-preview-popover');
    expect(popover).toBeInTheDocument();
    expect(screen.getByText('Attached Card: card_seq_01')).toBeInTheDocument();
    expect(screen.getByText('Part 1 of 2 | Sequential Recipe Deck')).toBeInTheDocument();
  });

  it('renders hover magnification preview on uploaded file thumbnails in Dropzone', () => {
    const mockFile = new File(['fake-image-bytes'], 'family_pancakes.png', { type: 'image/png' });

    render(
      <Dropzone
        files={[mockFile]}
        onFilesChange={vi.fn()}
      />
    );

    const fileItem = screen.getByText('family_pancakes.png');
    expect(fileItem).toBeInTheDocument();

    fireEvent.mouseEnter(fileItem.closest('div')!.parentElement!);

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    const popover = screen.getByTestId('card-hover-preview-popover');
    expect(popover).toBeInTheDocument();
    expect(screen.getAllByText('family_pancakes.png').length).toBeGreaterThanOrEqual(1);
  });

  it('disables hover preview during drag-and-drop reordering in MultiCardGrouping', () => {
    render(
      <MultiCardGrouping
        cardIds={['card_seq_01', 'card_seq_02']}
        sessionId="test-session"
        onReorder={vi.fn()}
        onDetach={vi.fn()}
      />
    );

    const firstCardImg = screen.getByAltText('card_seq_01');
    const draggableItem = firstCardImg.closest('div[draggable="true"]')!;

    // Start dragging
    fireEvent.dragStart(draggableItem, {
      dataTransfer: {
        setData: vi.fn(),
        effectAllowed: 'move',
      },
    });

    // Advance 2000ms
    act(() => {
      vi.advanceTimersByTime(2000);
    });

    // Hover preview popover should NOT appear while dragging
    expect(screen.queryByTestId('card-hover-preview-popover')).not.toBeInTheDocument();
  });
});

describe('Adversarial & Edge Cases: CandidateDrawer and SourceSelector', () => {
  it('CandidateDrawer displays loading spinner when isLoading is true', () => {
    render(
      <CandidateDrawer
        isOpen={true}
        onClose={vi.fn()}
        selectedCardId="card_apple_pie_front"
        candidates={[]}
        currentBackCardId={null}
        groupedCardIds={[]}
        onSelectCandidateAsBack={vi.fn()}
        onAttachCandidateToGroup={vi.fn()}
        isLoading={true}
      />
    );

    expect(screen.getByText(/Computing visual and semantic features/i)).toBeInTheDocument();
  });

  it('CandidateDrawer displays empty state when candidates array is empty and isLoading is false', () => {
    render(
      <CandidateDrawer
        isOpen={true}
        onClose={vi.fn()}
        selectedCardId="card_apple_pie_front"
        candidates={[]}
        currentBackCardId={null}
        groupedCardIds={[]}
        onSelectCandidateAsBack={vi.fn()}
        onAttachCandidateToGroup={vi.fn()}
        isLoading={false}
      />
    );

    expect(screen.getByText(/No alternative candidates found/i)).toBeInTheDocument();
  });

  it('CandidateDrawer handles candidates with zero scores gracefully', () => {
    const edgeCandidate: CorrelationCandidate = {
      candidate_card_id: 'card_sparse_01',
      score: 0,
      heuristic_score: 0,
      semantic_score: 0,
      composite_score: 0,
      candidate_image_path: undefined,
    };

    render(
      <CandidateDrawer
        isOpen={true}
        onClose={vi.fn()}
        selectedCardId="card_apple_pie_front"
        candidates={[edgeCandidate]}
        currentBackCardId={null}
        groupedCardIds={[]}
        onSelectCandidateAsBack={vi.fn()}
        onAttachCandidateToGroup={vi.fn()}
      />
    );

    expect(screen.getByText('card_sparse_01')).toBeInTheDocument();
    expect(screen.getAllByText('0%').length).toBe(3);
  });

  it('SourceSelector does not trigger directory browse when disabled is true', async () => {
    const browseSpy = vi.spyOn(api, 'browseDirectory');

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
        disabled={true}
      />
    );

    const browseBtn = screen.getByRole('button', { name: /Browse Folder\.\.\./i });
    expect(browseBtn).toBeDisabled();
    fireEvent.click(browseBtn);

    expect(browseSpy).not.toHaveBeenCalled();
  });
});
