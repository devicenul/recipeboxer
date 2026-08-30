import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { TutorialOverlay } from '../src/components/tutorial/TutorialOverlay';
import { TUTORIAL_STEPS, TUTORIAL_STORAGE_KEY } from '../src/components/tutorial/tutorialSteps';
import { AppContent, resetTutorialSessionState } from '../src/App';
import { ThemeProvider } from '../src/context/ThemeContext';

vi.mock('../src/utils/api', () => ({
  api: {
    getStatus: vi.fn().mockResolvedValue({
      session_id: 'sess_tutorial_01',
      status: 'IDLE',
      mode: 'parallel',
      progress: {
        total_scans: 0,
        fingerprinted: 0,
        proposals_generated: 0,
        approved_pairs: 0,
        recipes_extracted: 0,
        current_stage: 'Idle',
        percent_complete: 0,
      },
      token_usage: { prompt_tokens: 0, candidates_tokens: 0, thinking_tokens: 0, total_tokens: 0 },
      elapsed_seconds: 0,
    }),
    listSessions: vi.fn().mockResolvedValue([]),
    listRecipes: vi.fn().mockResolvedValue([]),
    getHitlPending: vi.fn().mockResolvedValue({
      session_id: 'sess_tutorial_01',
      status: 'HITL_PENDING',
      proposed_pairs: [],
      unassigned_cards: [],
      total_proposals: 0,
      total_unassigned: 0,
    }),
    getCardImageUrl: vi.fn((id) => `/api/cards/${id}/image`),
  },
}));

describe('R1: "First Run" Interactive Guided UI Tutorial Subsystem', () => {
  beforeEach(() => {
    localStorage.clear();
    resetTutorialSessionState();
    vi.clearAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
    resetTutorialSessionState();
  });

  describe('TutorialOverlay Component Unit Tests', () => {
    it('renders Step 1 with accessible dialog semantics and progress bar when open', () => {
      const handleClose = vi.fn();
      const handleStepChange = vi.fn();

      render(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={0}
          onStepChange={handleStepChange}
          onClose={handleClose}
        />
      );

      // Verify dialog semantics
      const dialog = screen.getByRole('dialog');
      expect(dialog).toBeInTheDocument();
      expect(dialog).toHaveAttribute('aria-modal', 'true');
      expect(dialog).toHaveAttribute('aria-labelledby', 'tutorial-step-title');
      expect(dialog).toHaveAttribute('aria-describedby', 'tutorial-step-description');

      // Verify Step 1 title and content
      expect(screen.getByText('Step 1 of 6: Ingestion')).toBeInTheDocument();
      expect(screen.getByText('Ingestion & Pluggable Sources')).toBeInTheDocument();
      expect(screen.getByText(/Import recipe card collections from multiple sources/)).toBeInTheDocument();

      // Progress bar should be ~16.66% width
      const progressBar = screen.getByTestId('tutorial-progress-bar');
      expect(progressBar).toHaveStyle({ width: `${(1 / 6) * 100}%` });

      // Check buttons
      expect(screen.getByTestId('tutorial-skip-btn')).toBeInTheDocument();
      expect(screen.getByTestId('tutorial-next-btn')).toHaveTextContent('Next');
      expect(screen.queryByTestId('tutorial-back-btn')).not.toBeInTheDocument();
    });

    it('renders Step 2 with rapid queue hotkey badges', () => {
      const handleClose = vi.fn();
      const handleStepChange = vi.fn();

      render(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={1}
          onStepChange={handleStepChange}
          onClose={handleClose}
        />
      );

      expect(screen.getByText('Step 2 of 6: Sequence Queue')).toBeInTheDocument();
      expect(screen.getByText('Rapid HITL Queue & Hotkeys')).toBeInTheDocument();

      // Hotkey badges
      expect(screen.getByText('Space')).toBeInTheDocument();
      expect(screen.getByText('Approve Sequence')).toBeInTheDocument();
      expect(screen.getByText('S')).toBeInTheDocument();
      expect(screen.getByText('Mark as Standalone')).toBeInTheDocument();
      expect(screen.getByText('B')).toBeInTheDocument();
      expect(screen.getByText('Reverse Sequence')).toBeInTheDocument();
      expect(screen.getByText('X')).toBeInTheDocument();
      expect(screen.getByText('Reject Scan')).toBeInTheDocument();
      expect(screen.getByText('Tab')).toBeInTheDocument();


      // Back button should be visible on Step 2
      expect(screen.getByTestId('tutorial-back-btn')).toBeInTheDocument();
    });

    it('renders Step 3 (Candidate Drawer) and Step 4 (Image Inspection Tools) correctly', () => {
      const { rerender } = render(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={2}
          onStepChange={vi.fn()}
          onClose={vi.fn()}
        />
      );

      expect(screen.getByText('Step 3 of 6: Multi-Card Sets')).toBeInTheDocument();
      expect(screen.getByText('Candidate Drawer & Multi-Card Sets')).toBeInTheDocument();

      rerender(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={3}
          onStepChange={vi.fn()}
          onClose={vi.fn()}
        />
      );

      expect(screen.getByText('Step 4 of 6: Visual Tools')).toBeInTheDocument();
      expect(screen.getByText('Image Inspection Tools')).toBeInTheDocument();
      expect(screen.getByText(/Synchronized or independent pan and zoom/)).toBeInTheDocument();
    });

    it('renders Step 5 (Recipe Editor) and Step 6 (Multi-Format Export) with Finish button', () => {
      const { rerender } = render(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={4}
          onStepChange={vi.fn()}
          onClose={vi.fn()}
        />
      );

      expect(screen.getByText('Step 5 of 6: Recipe Editor')).toBeInTheDocument();
      expect(screen.getByText('Split-Screen Recipe Editor')).toBeInTheDocument();

      rerender(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={5}
          onStepChange={vi.fn()}
          onClose={vi.fn()}
        />
      );

      expect(screen.getByText('Step 6 of 6: Export Matrix')).toBeInTheDocument();
      expect(screen.getByText('Multi-Format Export Matrix')).toBeInTheDocument();
      expect(screen.getByTestId('tutorial-next-btn')).toHaveTextContent('Finish Tour');
    });

    it('navigates forward via Next button and backward via Back button', () => {
      const handleStepChange = vi.fn();
      const handleClose = vi.fn();

      const { rerender } = render(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={0}
          onStepChange={handleStepChange}
          onClose={handleClose}
        />
      );

      // Click Next on Step 0
      fireEvent.click(screen.getByTestId('tutorial-next-btn'));
      expect(handleStepChange).toHaveBeenCalledWith(1);

      // Re-render at Step 1 and click Back
      rerender(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={1}
          onStepChange={handleStepChange}
          onClose={handleClose}
        />
      );

      fireEvent.click(screen.getByTestId('tutorial-back-btn'));
      expect(handleStepChange).toHaveBeenCalledWith(0);
    });

    it('jumps to a step directly when clicking step dot indicators', () => {
      const handleStepChange = vi.fn();

      render(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={0}
          onStepChange={handleStepChange}
          onClose={vi.fn()}
        />
      );

      const dot4 = screen.getByTestId('tutorial-step-dot-3');
      fireEvent.click(dot4);
      expect(handleStepChange).toHaveBeenCalledWith(3);
    });

    it('calls onClose when clicking Skip Tour, Close (X), or clicking Finish on last step', () => {
      const handleClose = vi.fn();

      const { rerender } = render(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={0}
          onStepChange={vi.fn()}
          onClose={handleClose}
        />
      );

      // Click Skip Tour
      fireEvent.click(screen.getByTestId('tutorial-skip-btn'));
      expect(handleClose).toHaveBeenCalledTimes(1);

      // Click X close button
      fireEvent.click(screen.getByTestId('tutorial-close-btn'));
      expect(handleClose).toHaveBeenCalledTimes(2);

      // Finish on last step
      rerender(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={5}
          onStepChange={vi.fn()}
          onClose={handleClose}
        />
      );

      fireEvent.click(screen.getByTestId('tutorial-next-btn'));
      expect(handleClose).toHaveBeenCalledTimes(3);
    });

    it('handles keyboard navigation with Escape, ArrowRight, and ArrowLeft', () => {
      const handleClose = vi.fn();
      const handleStepChange = vi.fn();

      render(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={2}
          onStepChange={handleStepChange}
          onClose={handleClose}
        />
      );

      // ArrowRight advances step
      fireEvent.keyDown(window, { key: 'ArrowRight' });
      expect(handleStepChange).toHaveBeenCalledWith(3);

      // ArrowLeft goes back step
      fireEvent.keyDown(window, { key: 'ArrowLeft' });
      expect(handleStepChange).toHaveBeenCalledWith(1);

      // Escape closes dialog
      fireEvent.keyDown(window, { key: 'Escape' });
      expect(handleClose).toHaveBeenCalled();
    });

    it('does not capture keyboard shortcuts when typing in input or textarea elements', () => {
      const handleClose = vi.fn();
      const handleStepChange = vi.fn();

      render(
        <div>
          <input data-testid="sample-input" />
          <TutorialOverlay
            isOpen={true}
            currentStepIndex={1}
            onStepChange={handleStepChange}
            onClose={handleClose}
          />
        </div>
      );

      const input = screen.getByTestId('sample-input');
      input.focus();

      // Dispatching arrow event from inside input should not trigger step changes
      fireEvent.keyDown(input, { key: 'ArrowRight' });
      expect(handleStepChange).not.toHaveBeenCalled();

      fireEvent.keyDown(input, { key: 'Escape' });
      expect(handleClose).not.toHaveBeenCalled();
    });

    it('applies tour-target-highlight CSS class to target elements in the DOM and cleans up', () => {
      // Create mock target elements in DOM
      const el1 = document.createElement('div');
      el1.setAttribute('data-tour', 'ingestion-sources');
      el1.scrollIntoView = vi.fn();
      document.body.appendChild(el1);

      const el2 = document.createElement('div');
      el2.setAttribute('data-tour', 'rapid-queue');
      el2.scrollIntoView = vi.fn();
      document.body.appendChild(el2);

      const { rerender, unmount } = render(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={0}
          onStepChange={vi.fn()}
          onClose={vi.fn()}
        />
      );

      expect(el1.classList.contains('tour-target-highlight')).toBe(true);
      expect(el1.scrollIntoView).toHaveBeenCalled();

      // Step 2
      rerender(
        <TutorialOverlay
          isOpen={true}
          currentStepIndex={1}
          onStepChange={vi.fn()}
          onClose={vi.fn()}
        />
      );

      expect(el1.classList.contains('tour-target-highlight')).toBe(false);
      expect(el2.classList.contains('tour-target-highlight')).toBe(true);

      // Unmount
      unmount();
      expect(el2.classList.contains('tour-target-highlight')).toBe(false);

      // Clean up DOM
      document.body.removeChild(el1);
      document.body.removeChild(el2);
    });

    it('does not render anything when isOpen is false', () => {
      render(
        <TutorialOverlay
          isOpen={false}
          currentStepIndex={0}
          onStepChange={vi.fn()}
          onClose={vi.fn()}
        />
      );

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(screen.queryByTestId('tutorial-overlay')).not.toBeInTheDocument();
    });
  });

  describe('Full App Integration & LocalStorage Persistence Tests', () => {
    it('automatically launches tutorial for first-time visitors when localStorage is empty', async () => {
      await act(async () => {
        render(
          <ThemeProvider>
            <AppContent />
          </ThemeProvider>
        );
      });

      // Should automatically render tutorial overlay
      expect(screen.getByTestId('tutorial-overlay')).toBeInTheDocument();
      expect(screen.getByText('Ingestion & Pluggable Sources')).toBeInTheDocument();
    });

    it('does not show tutorial automatically if already marked completed in localStorage', async () => {
      localStorage.setItem(TUTORIAL_STORAGE_KEY, 'true');

      await act(async () => {
        render(
          <ThemeProvider>
            <AppContent />
          </ThemeProvider>
        );
      });

      // Should NOT render tutorial overlay
      expect(screen.queryByTestId('tutorial-overlay')).not.toBeInTheDocument();
    });

    it('saves completion to localStorage and closes overlay when Skip Tour is clicked', async () => {
      await act(async () => {
        render(
          <ThemeProvider>
            <AppContent />
          </ThemeProvider>
        );
      });

      expect(screen.getByTestId('tutorial-overlay')).toBeInTheDocument();
      const skipBtn = screen.getByTestId('tutorial-skip-btn');

      await act(async () => {
        fireEvent.click(skipBtn);
      });

      expect(localStorage.getItem(TUTORIAL_STORAGE_KEY)).toBe('true');
      expect(screen.queryByTestId('tutorial-overlay')).not.toBeInTheDocument();
    });

    it('allows restarting the tour via the header Tour button anytime', async () => {
      localStorage.setItem(TUTORIAL_STORAGE_KEY, 'true');

      await act(async () => {
        render(
          <ThemeProvider>
            <AppContent />
          </ThemeProvider>
        );
      });

      expect(screen.queryByTestId('tutorial-overlay')).not.toBeInTheDocument();

      // Click persistent Tour restart button in Header
      const restartBtn = screen.getAllByTestId('tour-restart-button')[0];
      expect(restartBtn).toBeInTheDocument();

      await act(async () => {
        fireEvent.click(restartBtn);
      });

      // Tour should now open at Step 1
      expect(screen.getByTestId('tutorial-overlay')).toBeInTheDocument();
      expect(screen.getByText('Step 1 of 6: Ingestion')).toBeInTheDocument();
    });

    it('synchronizes active navigation tab as user steps through the tutorial', async () => {
      await act(async () => {
        render(
          <ThemeProvider>
            <AppContent />
          </ThemeProvider>
        );
      });

      expect(screen.getByTestId('tutorial-overlay')).toBeInTheDocument();

      // Step 1 -> Ingestion tab is active
      expect(screen.getByText('Step 1 of 6: Ingestion')).toBeInTheDocument();

      // Step 1 to Step 2 (HITL tab)
      await act(async () => {
        fireEvent.click(screen.getByTestId('tutorial-next-btn'));
      });
      expect(screen.getByText('Step 2 of 6: Sequence Queue')).toBeInTheDocument();


      // Step 2 to Step 3 (HITL tab)
      await act(async () => {
        fireEvent.click(screen.getByTestId('tutorial-next-btn'));
      });
      expect(screen.getByText('Step 3 of 6: Multi-Card Sets')).toBeInTheDocument();

      // Step 3 to Step 4 (Image tools / HITL tab)
      await act(async () => {
        fireEvent.click(screen.getByTestId('tutorial-next-btn'));
      });
      expect(screen.getByText('Step 4 of 6: Visual Tools')).toBeInTheDocument();

      // Step 4 to Step 5 (Recipe Editor tab)
      await act(async () => {
        fireEvent.click(screen.getByTestId('tutorial-next-btn'));
      });
      expect(screen.getByText('Step 5 of 6: Recipe Editor')).toBeInTheDocument();

      // Step 5 to Step 6 (Export tab)
      await act(async () => {
        fireEvent.click(screen.getByTestId('tutorial-next-btn'));
      });
      expect(screen.getByText('Step 6 of 6: Export Matrix')).toBeInTheDocument();

      // Finish tour
      await act(async () => {
        fireEvent.click(screen.getByTestId('tutorial-next-btn'));
      });
      expect(screen.queryByTestId('tutorial-overlay')).not.toBeInTheDocument();
      expect(localStorage.getItem(TUTORIAL_STORAGE_KEY)).toBe('true');
    });

    it('gracefully handles localStorage exceptions in strict sandbox iframes without crashing', async () => {
      const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
        throw new Error('SecurityError: The operation is insecure.');
      });
      const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new Error('QuotaExceededError: Storage quota exceeded.');
      });

      await act(async () => {
        render(
          <ThemeProvider>
            <AppContent />
          </ThemeProvider>
        );
      });

      // Overlay opens via in-memory session fallback without throwing
      expect(screen.getByTestId('tutorial-overlay')).toBeInTheDocument();

      const skipBtn = screen.getByTestId('tutorial-skip-btn');
      await act(async () => {
        fireEvent.click(skipBtn);
      });

      expect(screen.queryByTestId('tutorial-overlay')).not.toBeInTheDocument();

      getItemSpy.mockRestore();
      setItemSpy.mockRestore();
    });
  });
});
