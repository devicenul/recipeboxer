import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, renderHook, act } from '@testing-library/react';
import React from 'react';

// Hooks & Components
import { useKeyboard } from '../src/hooks/useKeyboard';
import { useImageViewer } from '../src/hooks/useImageViewer';
import { useRecipeForm } from '../src/hooks/useRecipeForm';
import { MultiCardGrouping } from '../src/components/hitl/MultiCardGrouping';
import { HitlWorkspace } from '../src/components/hitl/HitlWorkspace';
import { TagManager } from '../src/components/recipe/TagManager';
import { RecipeForm } from '../src/components/recipe/RecipeForm';
import { generatePlanToEatCsv, escapeCsvField } from '../src/utils/exportPlanToEat';
import { generateJsonLd, recipeToSchemaOrg } from '../src/utils/exportJsonLd';
import { generateRawJson } from '../src/utils/exportJson';
import { toISO8601Duration } from '../src/utils/formatters';
import { RecipeSchema } from '../src/types/recipe';
import { CorrelationProposal } from '../src/types/hitl';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    getHitlPending: vi.fn(),
    getHitlCandidates: vi.fn(),
    approveHitl: vi.fn(),
    recordDecision: vi.fn(),
    getCardImageUrl: vi.fn((id: string) => `/api/cards/${id}/image`),
    updateRecipe: vi.fn(),
  },
}));

/**
 * RFC 4180 CSV Parse Oracle to validate quoted multi-line CSV output.
 */
function parseRFC4180Csv(csvText: string): string[][] {
  const rows: string[][] = [];
  let currentRow: string[] = [];
  let currentField = '';
  let insideQuotes = false;

  for (let i = 0; i < csvText.length; i++) {
    const char = csvText[i];
    const nextChar = csvText[i + 1];

    if (insideQuotes) {
      if (char === '"') {
        if (nextChar === '"') {
          currentField += '"';
          i++; // Skip escaped quote
        } else {
          insideQuotes = false;
        }
      } else {
        currentField += char;
      }
    } else {
      if (char === '"') {
        insideQuotes = true;
      } else if (char === ',') {
        currentRow.push(currentField);
        currentField = '';
      } else if (char === '\r' && nextChar === '\n') {
        currentRow.push(currentField);
        rows.push(currentRow);
        currentRow = [];
        currentField = '';
        i++; // Skip \n
      } else if (char === '\n') {
        currentRow.push(currentField);
        rows.push(currentRow);
        currentRow = [];
        currentField = '';
      } else {
        currentField += char;
      }
    }
  }

  if (currentField || currentRow.length > 0) {
    currentRow.push(currentField);
    rows.push(currentRow);
  }

  // Filter trailing empty row from final CRLF
  return rows.filter((r) => r.length > 1 || (r.length === 1 && r[0] !== ''));
}

describe('Empirical Challenger Test Suite — Milestone 2 Frontend React + TypeScript SPA', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // =========================================================================
  // 1. Keyboard Navigation Trapping (Inputs vs Global Queue)
  // =========================================================================
  describe('Area 1: Keyboard Navigation Trapping & Global Shortcuts', () => {
    it('executes global hotkeys (Space, Enter, s, S, b, B, x, X, m, M, Tab, Escape) when no input is focused', () => {
      const handlers = {
        onApprove: vi.fn(),
        onStandalone: vi.fn(),
        onReverse: vi.fn(),
        onReject: vi.fn(),
        onToggleCandidates: vi.fn(),
        onEscape: vi.fn(),
      };

      const { unmount } = renderHook(() => useKeyboard({ ...handlers, enabled: true }));

      // Space -> onApprove
      fireEvent.keyDown(window, { key: ' ' });
      expect(handlers.onApprove).toHaveBeenCalledTimes(1);

      // Enter -> onApprove
      fireEvent.keyDown(window, { key: 'Enter' });
      expect(handlers.onApprove).toHaveBeenCalledTimes(2);

      // 's' and 'S' -> onStandalone
      fireEvent.keyDown(window, { key: 's' });
      fireEvent.keyDown(window, { key: 'S' });
      expect(handlers.onStandalone).toHaveBeenCalledTimes(2);

      // 'b' and 'B' -> onReverse
      fireEvent.keyDown(window, { key: 'b' });
      fireEvent.keyDown(window, { key: 'B' });
      expect(handlers.onReverse).toHaveBeenCalledTimes(2);

      // 'x' and 'X' -> onReject
      fireEvent.keyDown(window, { key: 'x' });
      fireEvent.keyDown(window, { key: 'X' });
      expect(handlers.onReject).toHaveBeenCalledTimes(2);

      // 'm' and 'M' -> onToggleCandidates
      fireEvent.keyDown(window, { key: 'm' });
      fireEvent.keyDown(window, { key: 'M' });
      expect(handlers.onToggleCandidates).toHaveBeenCalledTimes(2);

      // Tab -> onToggleCandidates
      fireEvent.keyDown(window, { key: 'Tab' });
      expect(handlers.onToggleCandidates).toHaveBeenCalledTimes(3);

      // Escape -> onEscape
      fireEvent.keyDown(window, { key: 'Escape' });
      expect(handlers.onEscape).toHaveBeenCalledTimes(1);

      unmount();
    });

    it('traps all single-letter hotkeys inside <input>, <textarea>, and <select> elements', () => {
      const handlers = {
        onApprove: vi.fn(),
        onStandalone: vi.fn(),
        onReverse: vi.fn(),
        onReject: vi.fn(),
        onToggleCandidates: vi.fn(),
        onEscape: vi.fn(),
      };

      const Harness = () => {
        useKeyboard({ ...handlers, enabled: true });
        return (
          <div>
            <input data-testid="test-input" type="text" defaultValue="recipe name" />
            <textarea data-testid="test-textarea" defaultValue="recipe directions" />
            <select data-testid="test-select">
              <option value="1">Option 1</option>
            </select>
          </div>
        );
      };

      render(<Harness />);

      const input = screen.getByTestId('test-input');
      const textarea = screen.getByTestId('test-textarea');
      const select = screen.getByTestId('test-select');

      // Test Input element trapping
      input.focus();
      [' ', 'Enter', 's', 'S', 'b', 'B', 'x', 'X', 'm', 'M', 'Tab'].forEach((k) => {
        fireEvent.keyDown(input, { key: k });
      });
      expect(handlers.onApprove).not.toHaveBeenCalled();
      expect(handlers.onStandalone).not.toHaveBeenCalled();
      expect(handlers.onReverse).not.toHaveBeenCalled();
      expect(handlers.onReject).not.toHaveBeenCalled();
      expect(handlers.onToggleCandidates).not.toHaveBeenCalled();

      // Test Textarea element trapping
      textarea.focus();
      [' ', 'Enter', 's', 'b', 'x', 'm'].forEach((k) => {
        fireEvent.keyDown(textarea, { key: k });
      });
      expect(handlers.onApprove).not.toHaveBeenCalled();
      expect(handlers.onStandalone).not.toHaveBeenCalled();

      // Test Select element trapping
      select.focus();
      ['s', 'b', 'x', 'm'].forEach((k) => {
        fireEvent.keyDown(select, { key: k });
      });
      expect(handlers.onReverse).not.toHaveBeenCalled();
      expect(handlers.onReject).not.toHaveBeenCalled();

      // Test Escape in input blurs input and triggers onEscape
      input.focus();
      fireEvent.keyDown(input, { key: 'Escape' });
      expect(handlers.onEscape).toHaveBeenCalledTimes(1);

      // Once input is blurred, pressing Space works again
      input.blur();
      fireEvent.keyDown(window, { key: ' ' });
      expect(handlers.onApprove).toHaveBeenCalledTimes(1);
    });

    it('ignores all keys when enabled is false', () => {
      const handlers = {
        onApprove: vi.fn(),
        onStandalone: vi.fn(),
        onReverse: vi.fn(),
        onReject: vi.fn(),
      };

      renderHook(() => useKeyboard({ ...handlers, enabled: false }));

      fireEvent.keyDown(window, { key: ' ' });
      fireEvent.keyDown(window, { key: 's' });
      fireEvent.keyDown(window, { key: 'b' });
      fireEvent.keyDown(window, { key: 'x' });

      expect(handlers.onApprove).not.toHaveBeenCalled();
      expect(handlers.onStandalone).not.toHaveBeenCalled();
      expect(handlers.onReverse).not.toHaveBeenCalled();
      expect(handlers.onReject).not.toHaveBeenCalled();
    });
  });

  // =========================================================================
  // 2. 3+ Multi-Card Grouping State Mutations
  // =========================================================================
  describe('Area 2: 3+ Multi-Card Grouping State Mutations', () => {
    it('MultiCardGrouping component renders correctly for 3, 4, 5+ attached cards and returns null for <= 1', () => {
      const onReorder = vi.fn();
      const onDetach = vi.fn();

      // <= 1 card should return null
      const { rerender } = render(
        <MultiCardGrouping cardIds={['card1.jpg']} onReorder={onReorder} onDetach={onDetach} />
      );
      expect(screen.queryByText(/attached/i)).not.toBeInTheDocument();

      // 4 cards
      rerender(
        <MultiCardGrouping
          cardIds={['card1.jpg', 'card2.jpg', 'card3.jpg', 'card4.jpg']}
          onReorder={onReorder}
          onDetach={onDetach}
        />
      );

      expect(screen.getByText(/4 cards attached/i)).toBeInTheDocument();
      expect(screen.getByText('Part 1 of 4')).toBeInTheDocument();
      expect(screen.getByText('Part 2 of 4')).toBeInTheDocument();
      expect(screen.getByText('Part 3 of 4')).toBeInTheDocument();
      expect(screen.getByText('Part 4 of 4')).toBeInTheDocument();
      expect(screen.getByText('#1')).toBeInTheDocument();
      expect(screen.getByText('#2')).toBeInTheDocument();
      expect(screen.getByText('#3')).toBeInTheDocument();
      expect(screen.getByText('#4')).toBeInTheDocument();
    });


    it('handles card reordering boundaries (left at 0 disabled, right at N-1 disabled) and valid swaps', () => {
      const onReorder = vi.fn();
      const onDetach = vi.fn();
      const cards = ['card_A.jpg', 'card_B.jpg', 'card_C.jpg', 'card_D.jpg'];

      render(<MultiCardGrouping cardIds={cards} onReorder={onReorder} onDetach={onDetach} />);

      const moveLeftBtns = screen.getAllByTitle('Move earlier');
      const moveRightBtns = screen.getAllByTitle('Move later');

      expect(moveLeftBtns.length).toBe(4);
      expect(moveRightBtns.length).toBe(4);

      // Boundary: Left button on first card is disabled
      expect(moveLeftBtns[0]).toBeDisabled();
      // Boundary: Right button on last card is disabled
      expect(moveRightBtns[3]).toBeDisabled();

      // Move card_C (index 2) left -> should swap index 1 and 2
      fireEvent.click(moveLeftBtns[2]);
      expect(onReorder).toHaveBeenCalledWith(['card_A.jpg', 'card_C.jpg', 'card_B.jpg', 'card_D.jpg']);

      // Move card_A (index 0) right -> should swap index 0 and 1
      fireEvent.click(moveRightBtns[0]);
      expect(onReorder).toHaveBeenCalledWith(['card_B.jpg', 'card_A.jpg', 'card_C.jpg', 'card_D.jpg']);
    });

    it('prevents detaching the front card (index 0) while permitting detaching cards 2..N', () => {
      const onDetach = vi.fn();
      const cards = ['front_01.jpg', 'back_02.jpg', 'part_03.jpg'];

      render(<MultiCardGrouping cardIds={cards} onReorder={vi.fn()} onDetach={onDetach} />);

      const detachBtns = screen.getAllByTitle('Detach from group');
      expect(detachBtns.length).toBe(3);

      // Detach part_03 (the third detach button)
      fireEvent.click(detachBtns[2]);
      expect(onDetach).toHaveBeenCalledWith('part_03.jpg');
    });

    it('HitlWorkspace full workflow: attaches candidates to 3+ card group, reverses roles, and approves multi-card payload', async () => {
      const mockProposal: CorrelationProposal = {
        proposal_id: 'prop_multi_01',
        ordered_card_ids: ['card_1_front.jpg', 'card_2_back.jpg'],
        heuristic_score: 0.92,
        semantic_score: 0.88,
        confidence: 0.90,
        is_standalone: false,
        continuity_explanation: 'Matching script',
        recommended_action: 'PAIR',
      };

      vi.mocked(api.getHitlPending).mockResolvedValueOnce({
        session_id: 'sess_multi_test',
        status: 'HITL_PENDING',
        proposed_pairs: [mockProposal],
        unassigned_cards: [],
        total_proposals: 1,
        total_unassigned: 0,
      });

      vi.mocked(api.getHitlCandidates).mockResolvedValueOnce({
        session_id: 'sess_multi_test',
        front_card_id: 'card_1_front.jpg',
        total_candidates: 2,
        candidates: [
          {
            candidate_card_id: 'card_3_part.jpg',
            score: 0.85,
            heuristic_score: 0.8,
            semantic_score: 0.9,
            composite_score: 0.85,
            reasoning_snippet: 'Page 3 instructions',
          },
          {
            candidate_card_id: 'card_4_part.jpg',
            score: 0.78,
            heuristic_score: 0.75,
            semantic_score: 0.8,
            composite_score: 0.78,
            reasoning_snippet: 'Page 4 notes',
          },
        ],
      });

      vi.mocked(api.approveHitl).mockResolvedValueOnce({
        session_id: 'sess_multi_test',
        status: 'SUCCESS',
        approved_count: 1,
        rejected_count: 0,
        message: 'approved',
      });

      const onCompleted = vi.fn();
      render(<HitlWorkspace sessionId="sess_multi_test" onApprovalsCompleted={onCompleted} />);

      await waitFor(() => {
        expect(screen.getByText('Proposal 1 of 1')).toBeInTheDocument();
      });

      // Open Candidate Drawer
      const drawerBtn = screen.getByRole('button', { name: /Candidates/i });
      fireEvent.click(drawerBtn);

      await waitFor(() => {
        expect(screen.getByText('Candidate Correlation Drawer')).toBeInTheDocument();
        expect(screen.getByText('card_3_part.jpg')).toBeInTheDocument();
        expect(screen.getByText('card_4_part.jpg')).toBeInTheDocument();
      });

      // Attach card_3_part.jpg and card_4_part.jpg
      const attachBtns = screen.getAllByRole('button', { name: /Attach/i });
      fireEvent.click(attachBtns[0]); // Attach card_3_part.jpg
      fireEvent.click(attachBtns[1]); // Attach card_4_part.jpg

      // Close drawer
      const closeDrawerBtn = screen.getByTitle('Close drawer [Esc]');
      fireEvent.click(closeDrawerBtn);

      // Verify Multi-Card Grouping shows 4 cards attached
      await waitFor(() => {
        expect(screen.getByText(/4 cards attached/i)).toBeInTheDocument();
      });

      // Click Reverse Roles (swaps front and back)
      const reverseBtn = screen.getByRole('button', { name: /Reverse/i });
      fireEvent.click(reverseBtn);

      // Verify front and back cards exist in DOM
      expect(screen.getAllByText('card_2_back.jpg').length).toBeGreaterThanOrEqual(1);

      // Approve Match
      const approveBtn = screen.getByRole('button', { name: /Approve/i });
      fireEvent.click(approveBtn);

      // Submit Approvals
      await waitFor(() => {
        expect(screen.getByText('All Proposals Reviewed!')).toBeInTheDocument();
      });

      const submitBtn = screen.getByRole('button', { name: /Submit & Start Transcription/i });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(api.approveHitl).toHaveBeenCalledWith({
          session_id: 'sess_multi_test',
          approvals: [
            expect.objectContaining({
              proposal_id: 'prop_multi_01',
              ordered_card_ids: ['card_4_part.jpg', 'card_3_part.jpg', 'card_2_back.jpg', 'card_1_front.jpg'],
              is_standalone: false,
              is_rejected: false,
            }),
          ],
          rejected_card_ids: [],
          continue_pipeline: true,
        });
        expect(onCompleted).toHaveBeenCalled();
      });
    });


  });

  // =========================================================================
  // 3. Image Transformations (Pan/Zoom Boundaries, Rotation, Filters)
  // =========================================================================
  describe('Area 3: Image Transformations & CSS Filter Pipelines', () => {
    it('enforces zoom boundaries [0.25, 5.0] under extreme zoom-in and zoom-out iterations', () => {
      const { result } = renderHook(() => useImageViewer());

      expect(result.current.transformFront.scale).toBe(1.0);

      // Extreme zoom in (30 times, 30 * 0.25 = +7.5) -> should clamp strictly at 5.0
      act(() => {
        for (let i = 0; i < 30; i++) {
          result.current.zoomIn();
        }
      });
      expect(result.current.transformFront.scale).toBe(5.0);
      expect(result.current.transformBack.scale).toBe(5.0);

      // Extreme zoom out (40 times) -> should clamp strictly at 0.25
      act(() => {
        for (let i = 0; i < 40; i++) {
          result.current.zoomOut();
        }
      });
      expect(result.current.transformFront.scale).toBe(0.25);
      expect(result.current.transformBack.scale).toBe(0.25);

      // setScale clamps arbitrary floats
      act(() => {
        result.current.setScale(-100);
      });
      expect(result.current.transformFront.scale).toBe(0.25);

      act(() => {
        result.current.setScale(999);
      });
      expect(result.current.transformFront.scale).toBe(5.0);
    });

    it('performs exact 90° -> 180° -> 270° -> 360° (0°) clockwise and counter-clockwise cycle rotations', () => {
      const { result } = renderHook(() => useImageViewer());

      expect(result.current.transformFront.rotation).toBe(0);

      // CW: 0 -> 90 -> 180 -> 270 -> 0
      act(() => result.current.rotateCw('front'));
      expect(result.current.transformFront.rotation).toBe(90);

      act(() => result.current.rotateCw('front'));
      expect(result.current.transformFront.rotation).toBe(180);

      act(() => result.current.rotateCw('front'));
      expect(result.current.transformFront.rotation).toBe(270);

      act(() => result.current.rotateCw('front'));
      expect(result.current.transformFront.rotation).toBe(0);

      // CCW: 0 -> 270 -> 180 -> 90 -> 0
      act(() => result.current.rotateCcw('front'));
      expect(result.current.transformFront.rotation).toBe(270);

      act(() => result.current.rotateCcw('front'));
      expect(result.current.transformFront.rotation).toBe(180);

      act(() => result.current.rotateCcw('front'));
      expect(result.current.transformFront.rotation).toBe(90);

      act(() => result.current.rotateCcw('front'));
      expect(result.current.transformFront.rotation).toBe(0);

      // 8 consecutive CW rotations return to 0
      act(() => {
        for (let i = 0; i < 8; i++) {
          result.current.rotateCw('front');
        }
      });
      expect(result.current.transformFront.rotation).toBe(0);
    });

    it('isolates independent front and back transformations when linked is disabled', () => {
      const { result } = renderHook(() => useImageViewer());

      act(() => {
        result.current.setLinked(false);
      });
      expect(result.current.linked).toBe(false);

      act(() => {
        result.current.zoomIn('front');
        result.current.pan(50, -30, 'front');
        result.current.rotateCw('front');
      });

      expect(result.current.transformFront.scale).toBe(1.25);
      expect(result.current.transformFront.translateX).toBe(50);
      expect(result.current.transformFront.translateY).toBe(-30);
      expect(result.current.transformFront.rotation).toBe(90);

      // Back transform remains pristine
      expect(result.current.transformBack.scale).toBe(1.0);
      expect(result.current.transformBack.translateX).toBe(0);
      expect(result.current.transformBack.translateY).toBe(0);
      expect(result.current.transformBack.rotation).toBe(0);
    });

    it('validates brightness, contrast clamping, invert toggle, reset, and CSS style generation', () => {
      const { result } = renderHook(() => useImageViewer());

      // Brightness clamp [0, 200]
      act(() => result.current.setBrightness(-50));
      expect(result.current.filtersFront.brightness).toBe(0);

      act(() => result.current.setBrightness(350));
      expect(result.current.filtersFront.brightness).toBe(200);

      // Contrast clamp [0, 300]
      act(() => result.current.setContrast(-10));
      expect(result.current.filtersFront.contrast).toBe(0);

      act(() => result.current.setContrast(500));
      expect(result.current.filtersFront.contrast).toBe(300);

      // Invert toggle
      expect(result.current.filtersFront.invert).toBe(false);
      act(() => result.current.toggleInvert());
      expect(result.current.filtersFront.invert).toBe(true);

      // Verify CSS strings
      const filterCss = result.current.getFilterStyle(result.current.filtersFront);
      expect(filterCss).toBe('brightness(200%) contrast(300%) invert(100%)');

      const transformCss = result.current.getTransformStyle({
        scale: 2.5,
        translateX: 120,
        translateY: -80,
        rotation: 180,
      });
      expect(transformCss).toBe('translate(120px, -80px) scale(2.5) rotate(180deg)');

      // Reset
      act(() => {
        result.current.resetFilters();
        result.current.resetTransform();
      });

      expect(result.current.filtersFront.brightness).toBe(100);
      expect(result.current.filtersFront.contrast).toBe(100);
      expect(result.current.filtersFront.invert).toBe(false);
      expect(result.current.transformFront.scale).toBe(1.0);
      expect(result.current.transformFront.rotation).toBe(0);
    });
  });

  // =========================================================================
  // 4. Recipe Editor Validation Edge Cases
  // =========================================================================
  describe('Area 4: Recipe Editor Validation Edge Cases', () => {
    it('rejects validation on empty title, all-whitespace ingredients, or all-whitespace directions', () => {
      const edgeRecipe: RecipeSchema = {
        recipe_id: 'rec_edge_01',
        title: '   \t  ',
        ingredients: ['   ', ''],
        directions: ['  \n  '],
        tags: [],
      };

      const { result } = renderHook(() =>
        useRecipeForm({
          initialRecipe: edgeRecipe,
        })
      );

      let isValid = false;
      act(() => {
        isValid = result.current.validate();
      });

      expect(isValid).toBe(false);
      expect(result.current.errors.title).toBe('Title is required');
      expect(result.current.errors.ingredients).toBe('At least one ingredient is required');
      expect(result.current.errors.directions).toBe('At least one direction step is required');
    });

    it('safely handles ingredient removal, keeping at least 1 row, and safely handles out-of-bound moves', () => {
      const ingRecipe: RecipeSchema = {
        recipe_id: 'rec_ing_01',
        title: 'Test Pie',
        ingredients: ['1 cup flour'],
        directions: ['1. Bake it.'],
        tags: [],
      };

      const { result } = renderHook(() =>
        useRecipeForm({
          initialRecipe: ingRecipe,
        })
      );

      // Remove the only ingredient -> array should reset to [''] to maintain an input field
      act(() => {
        result.current.removeIngredient(0);
      });
      expect(result.current.recipe.ingredients).toEqual(['']);

      // Add two ingredients
      act(() => {
        result.current.setIngredient(0, 'Flour');
        result.current.addIngredient();
        result.current.setIngredient(1, 'Sugar');
      });
      expect(result.current.recipe.ingredients).toEqual(['Flour', 'Sugar']);

      // Out of bounds move (-1 or index >= length) should not throw or corrupt array
      act(() => {
        result.current.moveIngredient(0, -1);
        result.current.moveIngredient(1, 99);
      });
      expect(result.current.recipe.ingredients).toEqual(['Flour', 'Sugar']);

      // Valid move
      act(() => {
        result.current.moveIngredient(0, 1);
      });
      expect(result.current.recipe.ingredients).toEqual(['Sugar', 'Flour']);
    });

    it('sanitizes and renumbers direction steps sequentially on save through RecipeForm component', async () => {
      const messyRecipe: RecipeSchema = {
        recipe_id: 'rec_dir_01',
        title: 'OCR Messy Recipe',
        description: 'Testing OCR sanitization',
        ingredients: ['2 eggs', '1 cup milk'],
        directions: [
          '1. First, beat eggs thoroughly.',
          'Step 2: Add warm milk slowly.',
          '3) Whisk vigorously.',
          '4. Pour into buttered skillet.',
          '5.  Cook over medium heat for 5 minutes.',
        ],
        tags: ['Breakfast'],
      };

      const onSaved = vi.fn();
      vi.mocked(api.updateRecipe).mockImplementation(async (_id: string, payload: any) => payload as RecipeSchema);
      render(<RecipeForm initialRecipe={messyRecipe} sessionId="sess_test" onSaved={onSaved} />);

      // Modify title to mark the form dirty
      const titleInput = screen.getByDisplayValue('OCR Messy Recipe');
      fireEvent.change(titleInput, { target: { value: 'OCR Messy Recipe (Edited)' } });

      const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
      expect(saveBtn).not.toBeDisabled();
      fireEvent.click(saveBtn);

      await waitFor(() => {
        expect(api.updateRecipe).toHaveBeenCalledWith(
          'rec_dir_01',
          expect.objectContaining({
            title: 'OCR Messy Recipe (Edited)',
            directions: [
              '1. First, beat eggs thoroughly.',
              '2. Add warm milk slowly.',
              '3. Whisk vigorously.',
              '4. Pour into buttered skillet.',
              '5. Cook over medium heat for 5 minutes.',
            ],
          }),
          'sess_test'
        );
        expect(onSaved).toHaveBeenCalled();
      });
    });

    it('TagManager supports unicode tags (emojis, accents, CJK), trims whitespace, and deduplicates case-insensitively', () => {
      const onAddTag = vi.fn();
      const onRemoveTag = vi.fn();

      const unicodeTags = ['🍰 Dessert', 'Crème Brûlée', 'Münchener Schnitzel', '🌶️ Hot', '抹茶 (Matcha)'];

      render(
        <TagManager
          tags={unicodeTags}
          onAddTag={onAddTag}
          onRemoveTag={onRemoveTag}
        />
      );

      // Verify unicode tags render
      expect(screen.getByText('🍰 Dessert')).toBeInTheDocument();
      expect(screen.getByText('Crème Brûlée')).toBeInTheDocument();
      expect(screen.getByText('Münchener Schnitzel')).toBeInTheDocument();
      expect(screen.getByText('🌶️ Hot')).toBeInTheDocument();
      expect(screen.getByText('抹茶 (Matcha)')).toBeInTheDocument();

      const tagInput = screen.getByPlaceholderText('+ Add tag (press Enter)');

      // Add tag with leading/trailing spaces -> should trim
      fireEvent.change(tagInput, { target: { value: '   Quick Dinner   ' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });
      expect(onAddTag).toHaveBeenCalledWith('Quick Dinner');

      // Add tag with comma delimiter -> should trigger add
      fireEvent.change(tagInput, { target: { value: 'Gluten-Free' } });
      fireEvent.keyDown(tagInput, { key: ',' });
      expect(onAddTag).toHaveBeenCalledWith('Gluten-Free');

      // Empty tag should not trigger onAddTag
      fireEvent.change(tagInput, { target: { value: '   ' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });
      expect(onAddTag).toHaveBeenCalledTimes(2);

      // Remove unicode tag
      const removeBtn = screen.getByTitle('Remove tag 🌶️ Hot');
      fireEvent.click(removeBtn);
      expect(onRemoveTag).toHaveBeenCalledWith('🌶️ Hot');
    });
  });

  // =========================================================================
  // 5. Export Generation with Edge-Case Characters
  // =========================================================================
  describe('Area 5: Multi-Format Export Generation with Edge-Case Characters', () => {
    const complexRecipes: RecipeSchema[] = [
      {
        recipe_id: 'rec_complex_01',
        title: 'Grandma\'s "Award-Winning" Crème Brûlée, & 🍰 Tart',
        description: 'Line 1 with comma, and quotes: "Best ever"\nLine 2 with multiple newlines.\nLine 3: 100% authentic.',
        yield_servings: '6 ramekins (approx. 4-6 servings)',
        prep_time: '1 hr 15 mins',
        cook_time: '40 mins',
        total_time: '1 hr 55 mins',
        ingredients: [
          '2 cups heavy cream (40% fat, chilled)',
          '1 vanilla bean, split & scraped',
          '5 large egg yolks (room temp)',
          '1/2 cup "superfine" sugar, plus extra for torching',
          'Pinch of kosher salt',
        ],
        directions: [
          '1. Heat cream and vanilla to 180°F.',
          'Step 2: Whisk yolks and sugar until pale yellow, ~3 mins.',
          '3) Slowly temper hot cream into yolks, stirring constantly.',
          '4 - Bake at 325°F in water bath for 35-40 min until set with gentle wobble.',
          '5. Chill 4+ hrs. Sprinkle with sugar; torch until amber/caramelized ("crackly").',
        ],
        tags: ['Dessert', 'French Cuisine', 'Crème', 'Gluten-Free', '🍰 Gourmet', 'Holiday Special'],
        source_notes: "Grandma's 1952 Hand-written Book, Page 44 & Box #3",
      },
      {
        recipe_id: 'rec_complex_02',
        title: 'Spicy Sichuan Mapo Tofu (麻婆豆腐) 🌶️',
        description: 'Authentic Sichuan dish with doubanjiang (豆瓣酱) and numbing Sichuan peppercorns.',
        yield_servings: '4 servings',
        prep_time: '15 mins',
        cook_time: '15 mins',
        total_time: '30 mins',
        ingredients: [
          '1 block (400g) soft/medium tofu, cut into 1.5cm cubes',
          '100g ground beef or pork',
          '2 tbsp Pixian doubanjiang (豆瓣酱)',
          '1 tbsp fermented black beans (douchi), rinsed & minced',
          '1 tsp ground Sichuan pepper (花椒粉)',
          '2 scallions, thinly sliced',
        ],
        directions: [
          '1. Simmer tofu cubes in salted boiling water for 1 min; drain gently.',
          '2. Brown minced meat in wok until crispy.',
          '3. Add doubanjiang and douchi; stir-fry until oil turns rich red.',
          '4. Pour in stock, add tofu, simmer for 3 mins.',
          '5. Swirl in cornstarch slurry in 3 batches until sauce thickens glossy.',
          '6. Garnish with ground Sichuan pepper and scallions.',
        ],
        tags: ['Chinese', 'Sichuan', 'Spicy', 'Tofu', '麻婆豆腐', 'Dinner'],
        source_notes: 'Chengdu Chef Masterclass Notes',
      },
    ];

    it('generates 100% RFC 4180 compliant Plan To Eat CSV with proper quote escaping, embedded newlines, and CRLF', () => {
      const csv = generatePlanToEatCsv(complexRecipes);

      // Verify CRLF line terminators
      expect(csv.endsWith('\r\n')).toBe(true);

      // Parse with strict RFC 4180 Oracle
      const parsedRows = parseRFC4180Csv(csv);
      // Header row + 2 data rows = 3 rows
      expect(parsedRows.length).toBe(3);

      // Verify 9 columns for each row
      parsedRows.forEach((row) => {
        expect(row.length).toBe(9);
      });

      // Check header row
      expect(parsedRows[0]).toEqual([
        'Title',
        'Description',
        'Ingredients',
        'Directions',
        'Prep Time',
        'Total Time',
        'Yield',
        'Tags',
        'Source',
      ]);

      // Check Row 1 values (Crème Brûlée)
      const row1 = parsedRows[1];
      expect(row1[0]).toBe('Grandma\'s "Award-Winning" Crème Brûlée, & 🍰 Tart');
      expect(row1[1]).toContain('"Best ever"');
      expect(row1[2]).toContain('1/2 cup "superfine" sugar, plus extra for torching');
      expect(row1[3]).toContain('1. Heat cream and vanilla to 180°F.');
      expect(row1[3]).toContain('5. Chill 4+ hrs. Sprinkle with sugar; torch until amber/caramelized ("crackly").');
      expect(row1[7]).toBe('Dessert, French Cuisine, Crème, Gluten-Free, 🍰 Gourmet, Holiday Special');
      expect(row1[8]).toBe("Grandma's 1952 Hand-written Book, Page 44 & Box #3");

      // Check Row 2 values (Mapo Tofu with CJK characters)
      const row2 = parsedRows[2];
      expect(row2[0]).toBe('Spicy Sichuan Mapo Tofu (麻婆豆腐) 🌶️');
      expect(row2[2]).toContain('2 tbsp Pixian doubanjiang (豆瓣酱)');
      expect(row2[2]).toContain('1 tsp ground Sichuan pepper (花椒粉)');
      expect(row2[7]).toBe('Chinese, Sichuan, Spicy, Tofu, 麻婆豆腐, Dinner');
    });

    it('handles empty/undefined fields gracefully without outputting "null" or "undefined"', () => {
      const minimalRecipe: RecipeSchema[] = [
        {
          recipe_id: 'min_01',
          title: 'Simple Bread',
          ingredients: ['Flour', 'Water', 'Yeast'],
          directions: ['Mix and bake.'],
          tags: [],
        },
      ];

      const csv = generatePlanToEatCsv(minimalRecipe);
      expect(csv).not.toContain('undefined');
      expect(csv).not.toContain('null');
      expect(csv).toContain('Simple Bread');
      expect(csv).toContain('RecipeBoxer OCR'); // Default fallback source notes
    });

    it('generates valid Schema.org Recipe JSON-LD with ISO 8601 durations and structured HowToSteps', () => {
      const jsonLdStr = generateJsonLd(complexRecipes);

      // Must be valid parseable JSON
      const parsed = JSON.parse(jsonLdStr);
      expect(Array.isArray(parsed)).toBe(true);
      expect(parsed.length).toBe(2);

      const cremeBrulee = parsed[0];
      expect(cremeBrulee['@context']).toBe('https://schema.org');
      expect(cremeBrulee['@type']).toBe('Recipe');
      expect(cremeBrulee.name).toBe('Grandma\'s "Award-Winning" Crème Brûlée, & 🍰 Tart');
      expect(cremeBrulee.prepTime).toBe('PT1H15M');
      expect(cremeBrulee.cookTime).toBe('PT40M');
      expect(cremeBrulee.totalTime).toBe('PT1H55M');

      // Steps verification
      expect(cremeBrulee.recipeInstructions.length).toBe(5);
      expect(cremeBrulee.recipeInstructions[0]).toEqual({
        '@type': 'HowToStep',
        position: 1,
        text: 'Heat cream and vanilla to 180°F.',
      });
      expect(cremeBrulee.recipeInstructions[1]).toEqual({
        '@type': 'HowToStep',
        position: 2,
        text: 'Whisk yolks and sugar until pale yellow, ~3 mins.',
      });

      // CJK Recipe verification
      const mapoTofu = parsed[1];
      expect(mapoTofu.name).toBe('Spicy Sichuan Mapo Tofu (麻婆豆腐) 🌶️');
      expect(mapoTofu.prepTime).toBe('PT15M');
      expect(mapoTofu.totalTime).toBe('PT30M');
      expect(mapoTofu.keywords).toContain('麻婆豆腐');
    });

    it('converts diverse human-readable durations to standard ISO 8601 duration strings', () => {
      expect(toISO8601Duration('45 mins')).toBe('PT45M');
      expect(toISO8601Duration('15 minutes')).toBe('PT15M');
      expect(toISO8601Duration('2 hours')).toBe('PT2H');
      expect(toISO8601Duration('1 hr 30 mins')).toBe('PT1H30M');
      expect(toISO8601Duration('3 hrs 5 minutes')).toBe('PT3H5M');
      expect(toISO8601Duration('30')).toBe('PT30M');
      expect(toISO8601Duration('PT45M')).toBe('PT45M');
      expect(toISO8601Duration('')).toBe('PT0M');
      expect(toISO8601Duration('Quick')).toBe('PT0M');
    });

    it('Raw JSON serializer performs 100% loss-less roundtrip serialization', () => {
      const rawJson = generateRawJson(complexRecipes);
      const deserialized: RecipeSchema[] = JSON.parse(rawJson);

      expect(deserialized.length).toBe(2);
      expect(deserialized[0].title).toBe(complexRecipes[0].title);
      expect(deserialized[0].ingredients).toEqual(complexRecipes[0].ingredients);
      expect(deserialized[1].title).toBe(complexRecipes[1].title);
      expect(deserialized[1].ingredients).toEqual(complexRecipes[1].ingredients);
    });
  });
});
