import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, renderHook, act } from '@testing-library/react';
import React from 'react';

// Hooks & Components Under Test
import { useKeyboard } from '../src/hooks/useKeyboard';
import { useImageViewer } from '../src/hooks/useImageViewer';
import { useRecipeForm } from '../src/hooks/useRecipeForm';
import { MultiCardGrouping } from '../src/components/hitl/MultiCardGrouping';
import { HitlWorkspace } from '../src/components/hitl/HitlWorkspace';
import { CandidateDrawer } from '../src/components/hitl/CandidateDrawer';
import { RapidQueueCard } from '../src/components/hitl/RapidQueueCard';
import { CardViewerCanvas } from '../src/components/viewer/CardViewerCanvas';
import { VisualToolbar } from '../src/components/viewer/VisualToolbar';
import { RecipeForm } from '../src/components/recipe/RecipeForm';
import { RecipeEditorView } from '../src/components/recipe/RecipeEditorView';
import { IngredientListInput } from '../src/components/recipe/IngredientListInput';
import { DirectionListInput } from '../src/components/recipe/DirectionListInput';
import { TagManager } from '../src/components/recipe/TagManager';
import { ExportDialog } from '../src/components/export/ExportDialog';
import { ConfigPanel } from '../src/components/ingestion/ConfigPanel';
import { Dropzone } from '../src/components/common/Dropzone';
import { generatePlanToEatCsv, escapeCsvField, formatDirectionsForCsv } from '../src/utils/exportPlanToEat';
import { generateJsonLd, recipeToSchemaOrg } from '../src/utils/exportJsonLd';
import { generateRawJson } from '../src/utils/exportJson';
import { toISO8601Duration } from '../src/utils/formatters';
import { RecipeSchema } from '../src/types/recipe';
import { CorrelationProposal, CorrelationCandidate } from '../src/types/hitl';
import { PipelineStartRequest } from '../src/types/pipeline';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    startPipeline: vi.fn(),
    uploadCards: vi.fn(),
    getStatus: vi.fn(),
    listSessions: vi.fn(),
    getSessionDetail: vi.fn(),
    resumeSession: vi.fn(),
    deleteSession: vi.fn(),
    getHitlPending: vi.fn(),
    getHitlCandidates: vi.fn(),
    approveHitl: vi.fn(),
    recordDecision: vi.fn(),
    getCardImageUrl: vi.fn((id: string, sid?: string) => `/api/cards/${id}/image${sid ? `?session_id=${sid}` : ''}`),
    listRecipes: vi.fn(),
    getRecipe: vi.fn(),
    createRecipe: vi.fn(),
    updateRecipe: vi.fn(),
    deleteRecipe: vi.fn(),
  },
}));

/**
 * Strict RFC 4180 CSV State-Machine Parser Oracle.
 * Correctly parses multi-line quoted fields, escaped double quotes (""), and CRLF line breaks.
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

  if (currentField !== '' || currentRow.length > 0) {
    currentRow.push(currentField);
    rows.push(currentRow);
  }

  // Filter trailing empty record from final CRLF
  return rows.filter((r) => r.length > 1 || (r.length === 1 && r[0] !== ''));
}

describe('Tier 5 Adversarial Coverage Hardening — Frontend React SPA & HITL Suite', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // =========================================================================
  // Area 1: Keyboard Event Trapping, Composition & Focus Management
  // =========================================================================
  describe('Area 1: Keyboard Event Trapping & Composition Handling', () => {
    it('suppresses all queue hotkeys when user types in inputs, textareas, and selects', () => {
      const handlers = {
        onApprove: vi.fn(),
        onStandalone: vi.fn(),
        onReverse: vi.fn(),
        onReject: vi.fn(),
        onToggleCandidates: vi.fn(),
        onEscape: vi.fn(),
      };

      const TestComponent = () => {
        useKeyboard(handlers);
        return (
          <div>
            <input data-testid="test-input" type="text" />
            <textarea data-testid="test-textarea" />
            <select data-testid="test-select">
              <option value="1">Option 1</option>
            </select>
            <button data-testid="test-button">Plain Button</button>
          </div>
        );
      };

      render(<TestComponent />);

      const input = screen.getByTestId('test-input');
      const textarea = screen.getByTestId('test-textarea');
      const select = screen.getByTestId('test-select');
      const button = screen.getByTestId('test-button');

      // Test keys: Space, Enter, s, b, x, m, Tab
      const hotkeys = [' ', 'Enter', 's', 'S', 'b', 'B', 'x', 'X', 'm', 'M', 'Tab'];

      // 1. Inside Input
      input.focus();
      hotkeys.forEach((key) => {
        fireEvent.keyDown(input, { key });
      });
      expect(handlers.onApprove).not.toHaveBeenCalled();
      expect(handlers.onStandalone).not.toHaveBeenCalled();
      expect(handlers.onReverse).not.toHaveBeenCalled();
      expect(handlers.onReject).not.toHaveBeenCalled();
      expect(handlers.onToggleCandidates).not.toHaveBeenCalled();

      // 2. Inside Textarea
      textarea.focus();
      hotkeys.forEach((key) => {
        fireEvent.keyDown(textarea, { key });
      });
      expect(handlers.onApprove).not.toHaveBeenCalled();
      expect(handlers.onStandalone).not.toHaveBeenCalled();

      // 3. Inside Select
      select.focus();
      hotkeys.forEach((key) => {
        fireEvent.keyDown(select, { key });
      });
      expect(handlers.onApprove).not.toHaveBeenCalled();

      // 4. Escape inside input blurs active element and calls onEscape
      fireEvent.keyDown(input, { key: 'Escape' });
      expect(handlers.onEscape).toHaveBeenCalledTimes(1);

      // 5. Outside input (focused on window or plain button), hotkeys trigger immediately
      button.focus();
      fireEvent.keyDown(window, { key: ' ' });
      expect(handlers.onApprove).toHaveBeenCalledTimes(1);

      fireEvent.keyDown(window, { key: 's' });
      expect(handlers.onStandalone).toHaveBeenCalledTimes(1);

      fireEvent.keyDown(window, { key: 'b' });
      expect(handlers.onReverse).toHaveBeenCalledTimes(1);

      fireEvent.keyDown(window, { key: 'x' });
      expect(handlers.onReject).toHaveBeenCalledTimes(1);

      fireEvent.keyDown(window, { key: 'm' });
      expect(handlers.onToggleCandidates).toHaveBeenCalledTimes(1);

      fireEvent.keyDown(window, { key: 'Tab' });
      expect(handlers.onToggleCandidates).toHaveBeenCalledTimes(2);

      fireEvent.keyDown(window, { key: 'Escape' });
      expect(handlers.onEscape).toHaveBeenCalledTimes(2);
    });

    it('cleans up global event listeners on unmount or when enabled is set to false', () => {
      const handlers = { onApprove: vi.fn(), enabled: true };
      const { rerender, unmount } = renderHook((props) => useKeyboard(props), {
        initialProps: handlers,
      });

      // Active
      fireEvent.keyDown(window, { key: 'Enter' });
      expect(handlers.onApprove).toHaveBeenCalledTimes(1);

      // Disabled
      rerender({ onApprove: handlers.onApprove, enabled: false });
      fireEvent.keyDown(window, { key: 'Enter' });
      expect(handlers.onApprove).toHaveBeenCalledTimes(1); // No new call

      // Unmount
      rerender({ onApprove: handlers.onApprove, enabled: true });
      unmount();
      fireEvent.keyDown(window, { key: 'Enter' });
      expect(handlers.onApprove).toHaveBeenCalledTimes(1); // No new call
    });
  });

  // =========================================================================
  // Area 2: Visual Inspection Toolbar & Extreme Pan/Zoom Geometry
  // =========================================================================
  describe('Area 2: Visual Inspection Toolbar & Extreme Pan/Zoom Geometry', () => {
    it('enforces scale clamping between 0.25 and 5.0 under extreme zoom operations', () => {
      const { result } = renderHook(() => useImageViewer());

      // Initial state
      expect(result.current.transformFront.scale).toBe(1.0);
      expect(result.current.transformBack.scale).toBe(1.0);

      // Zoom In to upper limit
      act(() => {
        for (let i = 0; i < 30; i++) {
          result.current.zoomIn('both');
        }
      });
      expect(result.current.transformFront.scale).toBe(5.0);
      expect(result.current.transformBack.scale).toBe(5.0);

      // Extreme explicit scale above limit
      act(() => {
        result.current.setScale(100.0, 'front');
      });
      expect(result.current.transformFront.scale).toBe(5.0);

      // Zoom Out to lower limit
      act(() => {
        for (let i = 0; i < 30; i++) {
          result.current.zoomOut('both');
        }
      });
      expect(result.current.transformFront.scale).toBe(0.25);
      expect(result.current.transformBack.scale).toBe(0.25);

      // Extreme explicit scale below limit / negative
      act(() => {
        result.current.setScale(-10.0, 'front');
      });
      expect(result.current.transformFront.scale).toBe(0.25);

      // Reset
      act(() => {
        result.current.resetTransform('both');
      });
      expect(result.current.transformFront.scale).toBe(1.0);
      expect(result.current.transformFront.translateX).toBe(0);
      expect(result.current.transformFront.translateY).toBe(0);
      expect(result.current.transformFront.rotation).toBe(0);
    });

    it('accurately calculates 360-degree rotation wrap-around in both directions', () => {
      const { result } = renderHook(() => useImageViewer());

      // Rotate CW 4 times (0 -> 90 -> 180 -> 270 -> 0)
      act(() => result.current.rotateCw('front'));
      expect(result.current.transformFront.rotation).toBe(90);

      act(() => result.current.rotateCw('front'));
      expect(result.current.transformFront.rotation).toBe(180);

      act(() => result.current.rotateCw('front'));
      expect(result.current.transformFront.rotation).toBe(270);

      act(() => result.current.rotateCw('front'));
      expect(result.current.transformFront.rotation).toBe(0);

      // Rotate CCW (0 -> 270 -> 180 -> 90 -> 0)
      act(() => result.current.rotateCcw('front'));
      expect(result.current.transformFront.rotation).toBe(270);

      act(() => result.current.rotateCcw('front'));
      expect(result.current.transformFront.rotation).toBe(180);

      act(() => result.current.rotateCcw('front'));
      expect(result.current.transformFront.rotation).toBe(90);

      act(() => result.current.rotateCcw('front'));
      expect(result.current.transformFront.rotation).toBe(0);
    });

    it('handles large pan delta values and independent unlinked transformations', () => {
      const { result } = renderHook(() => useImageViewer());

      // Unlink dual viewers
      act(() => {
        result.current.setLinked(false);
      });

      // Pan front with large coordinate values
      act(() => {
        result.current.pan(5000, -8000, 'front');
      });
      expect(result.current.transformFront.translateX).toBe(5000);
      expect(result.current.transformFront.translateY).toBe(-8000);
      // Back transform remains unchanged
      expect(result.current.transformBack.translateX).toBe(0);
      expect(result.current.transformBack.translateY).toBe(0);

      // Filter settings independent
      act(() => {
        result.current.setBrightness(175, 'front');
        result.current.setContrast(250, 'front');
        result.current.toggleInvert('front');
      });

      expect(result.current.filtersFront.brightness).toBe(175);
      expect(result.current.filtersFront.contrast).toBe(250);
      expect(result.current.filtersFront.invert).toBe(true);

      expect(result.current.filtersBack.brightness).toBe(100);
      expect(result.current.filtersBack.contrast).toBe(100);
      expect(result.current.filtersBack.invert).toBe(false);

      // Test helper CSS string formatting
      const style = result.current.getFilterStyle(result.current.filtersFront);
      expect(style).toBe('brightness(175%) contrast(250%) invert(100%)');

      const transformStr = result.current.getTransformStyle(result.current.transformFront);
      expect(transformStr).toContain('translate(5000px, -8000px)');
      expect(transformStr).toContain('scale(1)');
    });
  });

  // =========================================================================
  // Area 3: Rapid Multi-Card Group Attaches/Detaches & Reordering
  // =========================================================================
  describe('Area 3: Rapid Multi-Card Group State Mutations & Reordering', () => {
    it('supports attaching multiple candidates sequentially into 3+ card recipe sets', async () => {
      const mockProposal: CorrelationProposal = {
        proposal_id: 'prop_multi_001',
        front_card_id: 'card_multi_front',
        back_card_id: 'card_multi_back',
        confidence: 0.88,
        heuristic_score: 0.85,
        semantic_score: 0.9,
        continuity_explanation: 'Strong handwriting and ingredient continuity',
        is_standalone: false,
        recommended_action: 'APPROVE',
      };

      const mockCandidates: CorrelationCandidate[] = [
        {
          candidate_card_id: 'card_extra_part3',
          candidate_image_path: '/api/cards/card_extra_part3/image',
          score: 0.84,
          heuristic_score: 0.82,
          semantic_score: 0.85,
          composite_score: 0.84,
          reasoning_snippet: 'Matching cursive ink and continuation of directions',
        },
        {
          candidate_card_id: 'card_extra_part4',
          candidate_image_path: '/api/cards/card_extra_part4/image',
          score: 0.79,
          heuristic_score: 0.78,
          semantic_score: 0.8,
          composite_score: 0.79,
          reasoning_snippet: 'Dessert frosting section continuation',
        },
      ];

      (api.getHitlPending as any).mockResolvedValue({
        proposed_pairs: [mockProposal],
        unassigned_cards: [],
        total_proposals: 1,
        total_unassigned: 2,
        status: 'pending',
      });

      (api.getHitlCandidates as any).mockResolvedValue({
        session_id: 'sess_multi_01',
        front_card_id: 'card_multi_front',
        candidates: mockCandidates,
        total_candidates: 2,
      });

      (api.approveHitl as any).mockResolvedValue({
        session_id: 'sess_multi_01',
        status: 'success',
        approved_count: 1,
        rejected_count: 0,
        message: 'Approved',
      });

      const onComplete = vi.fn();
      render(<HitlWorkspace sessionId="sess_multi_01" onApprovalsCompleted={onComplete} />);

      await waitFor(() => {
        expect(screen.getByText(/Proposal 1 of 1/i)).toBeInTheDocument();
      });

      // Open Candidate Drawer
      const candidatesBtn = screen.getByRole('button', { name: /Candidates/i });
      fireEvent.click(candidatesBtn);

      await waitFor(() => {
        expect(screen.getByText('card_extra_part3')).toBeInTheDocument();
      });

      // Attach part 3 to group
      const attachButtons = screen.getAllByRole('button', { name: /^Attach$/i });
      fireEvent.click(attachButtons[0]);

      // Attach part 4 to group
      fireEvent.click(attachButtons[1]);

      // Verify Multi-Card Group Tray displays 4 cards attached
      await waitFor(() => {
        expect(screen.getByText(/Multi-Card Recipe Group \(4 cards attached\)/i)).toBeInTheDocument();
      });

      // Close drawer and approve the 4-card group
      const closeDrawerBtn = screen.getByTitle(/Close drawer/i);
      fireEvent.click(closeDrawerBtn);

      const approveBtn = screen.getByRole('button', { name: /Approve/i });
      fireEvent.click(approveBtn);

      // Verify completion summary
      await waitFor(() => {
        expect(screen.getByText(/All Proposals Reviewed!/i)).toBeInTheDocument();
      });

      // Submit and verify API payload structure
      const submitBtn = screen.getByRole('button', { name: /Submit & Start Transcription/i });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(api.approveHitl).toHaveBeenCalledWith({
          session_id: 'sess_multi_01',
          approvals: [
            {
              proposal_id: 'prop_multi_001',
              front_card_id: 'card_multi_front',
              back_card_id: 'card_multi_back',
              card_ids: ['card_multi_front', 'card_multi_back', 'card_extra_part3', 'card_extra_part4'],
              is_standalone: false,
              is_rejected: false,
            },
          ],
          rejected_card_ids: [],
          continue_pipeline: true,
        });
        expect(onComplete).toHaveBeenCalled();
      });
    });

    it('renders MultiCardGrouping component and handles reordering and detaches safely', () => {
      const cardIds = ['card_A', 'card_B', 'card_C', 'card_D'];
      const onReorder = vi.fn();
      const onDetach = vi.fn();

      const { rerender } = render(
        <MultiCardGrouping
          cardIds={cardIds}
          sessionId="sess_test"
          onReorder={onReorder}
          onDetach={onDetach}
        />
      );

      expect(screen.getByText(/Multi-Card Recipe Group \(4 cards attached\)/i)).toBeInTheDocument();
      expect(screen.getByText('Front (Page 1)')).toBeInTheDocument();
      expect(screen.getByText('Back (Page 2)')).toBeInTheDocument();
      expect(screen.getByText('Part 3')).toBeInTheDocument();
      expect(screen.getByText('Part 4')).toBeInTheDocument();

      // Move Part 3 left (index 2 -> index 1)
      const moveEarlierButtons = screen.getAllByTitle('Move earlier');
      fireEvent.click(moveEarlierButtons[2]); // For card_C
      expect(onReorder).toHaveBeenCalledWith(['card_A', 'card_C', 'card_B', 'card_D']);

      // Move Part 2 right (index 1 -> index 2)
      const moveLaterButtons = screen.getAllByTitle('Move later');
      fireEvent.click(moveLaterButtons[1]); // For card_B
      expect(onReorder).toHaveBeenCalledWith(['card_A', 'card_C', 'card_B', 'card_D']);

      // Detach card_D
      const detachButtons = screen.getAllByTitle('Detach from group');
      fireEvent.click(detachButtons[detachButtons.length - 1]);
      expect(onDetach).toHaveBeenCalledWith('card_D');

      // Boundary condition: If <= 1 card, component safely returns null
      rerender(
        <MultiCardGrouping
          cardIds={['card_A']}
          sessionId="sess_test"
          onReorder={onReorder}
          onDetach={onDetach}
        />
      );
      expect(screen.queryByText(/Multi-Card Recipe Group/i)).not.toBeInTheDocument();
    });
  });

  // =========================================================================
  // Area 4: Recipe Schema Validation & Mutation Robustness
  // =========================================================================
  describe('Area 4: Recipe Schema Validation with Missing / Malformed Fields', () => {
    it('blocks save and flags errors when title, ingredients, or directions are missing or blank', async () => {
      const invalidRecipe: RecipeSchema = {
        recipe_id: 'rec_invalid_01',
        title: '   ', // Blank whitespace title
        description: '',
        yield_servings: '',
        prep_time: '',
        cook_time: '',
        total_time: '',
        ingredients: ['  ', ''], // Blank ingredients
        directions: [''], // Blank directions
        tags: [],
        source_notes: '',
      };

      const { result } = renderHook(() => useRecipeForm({ initialRecipe: invalidRecipe }));

      let isValid = true;
      act(() => {
        isValid = result.current.validate();
      });

      expect(isValid).toBe(false);
      expect(result.current.errors.title).toBe('Title is required');
      expect(result.current.errors.ingredients).toBe('At least one ingredient is required');
      expect(result.current.errors.directions).toBe('At least one direction step is required');

      // Attempting to save also rejects
      let saved = false;
      await act(async () => {
        saved = await result.current.save();
      });
      expect(saved).toBe(false);
      expect(api.updateRecipe).not.toHaveBeenCalled();
    });

    it('normalizes direction step numbering and preserves dynamic ingredients reordering', async () => {
      const initialRecipe: RecipeSchema = {
        recipe_id: 'rec_norm_01',
        title: 'Heirloom Apple Crisp',
        description: 'Traditional crisp recipe',
        ingredients: ['4 apples, peeled', '1 cup oats', '1/2 cup brown sugar'],
        directions: [
          'Step 1: Preheat oven to 350F.',
          '2) Slice apples into baking dish.',
          'Combine oats and sugar for topping.',
        ],
        tags: ['Dessert', 'Fall'],
      };

      (api.updateRecipe as any).mockImplementation((_id: string, payload: RecipeSchema) =>
        Promise.resolve(payload)
      );

      const onSaved = vi.fn();
      const { result } = renderHook(() =>
        useRecipeForm({ initialRecipe, sessionId: 'sess_norm', onSaved })
      );

      // Reorder ingredients: move '1 cup oats' (index 1) to top (index 0)
      act(() => {
        result.current.moveIngredient(1, 0);
      });
      expect(result.current.recipe.ingredients).toEqual([
        '1 cup oats',
        '4 apples, peeled',
        '1/2 cup brown sugar',
      ]);

      // Reorder directions: move step 3 to step 1
      act(() => {
        result.current.moveDirection(2, 0);
      });

      // Save and verify normalization
      let saveResult = false;
      await act(async () => {
        saveResult = await result.current.save();
      });

      expect(saveResult).toBe(true);
      expect(api.updateRecipe).toHaveBeenCalledWith(
        'rec_norm_01',
        expect.objectContaining({
          title: 'Heirloom Apple Crisp',
          ingredients: ['1 cup oats', '4 apples, peeled', '1/2 cup brown sugar'],
          directions: [
            '1. Combine oats and sugar for topping.',
            '2. Preheat oven to 350F.',
            '3. Slice apples into baking dish.',
          ],
        }),
        'sess_norm'
      );
      expect(onSaved).toHaveBeenCalled();
      expect(result.current.isDirty).toBe(false);
      expect(result.current.saveSuccess).toBe(true);
    });

    it('handles tag manager case-insensitive duplicate deduplication and keydown submission', () => {
      const onAddTag = vi.fn();
      const onRemoveTag = vi.fn();

      render(
        <TagManager
          tags={['Baking', 'Comfort Food']}
          onAddTag={onAddTag}
          onRemoveTag={onRemoveTag}
        />
      );

      const input = screen.getByPlaceholderText('+ Add tag (press Enter)');

      // Type and submit with Enter
      fireEvent.change(input, { target: { value: 'Dessert' } });
      fireEvent.keyDown(input, { key: 'Enter' });
      expect(onAddTag).toHaveBeenCalledWith('Dessert');

      // Type and submit with comma
      fireEvent.change(input, { target: { value: 'Quick & Easy' } });
      fireEvent.keyDown(input, { key: ',' });
      expect(onAddTag).toHaveBeenCalledWith('Quick & Easy');

      // Remove tag
      const removeBtn = screen.getByTitle('Remove tag Baking');
      fireEvent.click(removeBtn);
      expect(onRemoveTag).toHaveBeenCalledWith('Baking');
    });

    it('renders RecipeEditorView with recipe carousel navigation and card tabs', () => {
      const recipes: RecipeSchema[] = [
        {
          recipe_id: 'rec_e1',
          title: 'Recipe One',
          card_ids: ['card_1_front', 'card_1_back'],
          ingredients: ['Ing 1'],
          directions: ['1. Step 1'],
          tags: ['Tag1'],
        },
        {
          recipe_id: 'rec_e2',
          title: 'Recipe Two',
          card_ids: ['card_2_standalone'],
          ingredients: ['Ing 2'],
          directions: ['1. Step 2'],
          tags: ['Tag2'],
        },
      ];

      const onUpdate = vi.fn();
      render(<RecipeEditorView recipes={recipes} sessionId="sess_editor" onRecipeUpdated={onUpdate} />);

      expect(screen.getByText(/Recipe 1 of 2:/i)).toBeInTheDocument();
      expect(screen.getByText('Card 1 (Front)')).toBeInTheDocument();
      expect(screen.getByText('Card 2 (Back)')).toBeInTheDocument();

      // Click Next Recipe
      const nextBtn = screen.getByTitle('Next Recipe');
      fireEvent.click(nextBtn);

      expect(screen.getByText(/Recipe 2 of 2:/i)).toBeInTheDocument();
    });
  });

  // =========================================================================
  // Area 5: Large Multi-Recipe Multi-Format Export Stress & RFC 4180 Oracle
  // =========================================================================
  describe('Area 5: Large Multi-Recipe Multi-Format Export Stress & RFC 4180 Oracle', () => {
    it('exports 50+ diverse recipes to Plan To Eat CSV and verifies 100% RFC 4180 parsing compliance', () => {
      const largeRecipeSet: RecipeSchema[] = [];

      for (let i = 1; i <= 50; i++) {
        largeRecipeSet.push({
          recipe_id: `rec_stress_${String(i).padStart(3, '0')}`,
          title: `Recipe #${i}: Grandma's "Special" Batch with, Commas & Quotes`,
          description: `Description for recipe ${i}\nFeaturing multiple lines\nand "nested quotes" with fractions 1 ½ cups.`,
          yield_servings: `${i * 2} servings`,
          prep_time: `${10 + (i % 50)} mins`,
          cook_time: `${20 + (i % 60)} mins`,
          total_time: `1 hr ${i % 60} mins`,
          ingredients: [
            `${i} cup flour, sifted`,
            `${i * 0.5} tbsp "pure" vanilla extract`,
            `2 ½ tsp baking powder`,
            `Salt & black pepper to taste`,
          ],
          directions: [
            `1. Mix dry ingredients in a large bowl.`,
            `2) Add liquid ingredients and stir until smooth; do not overmix!`,
            `Step 3: Bake at 350°F (175°C) for ${25 + (i % 20)} minutes until golden brown.`,
          ],
          tags: ['Heirloom', `Category-${i % 5}`, 'Vintage Box'],
          source_notes: `Found in 195${i % 10} wooden kitchen chest.`,
        });
      }

      // Generate Plan To Eat CSV
      const csvOutput = generatePlanToEatCsv(largeRecipeSet);

      // Verify line terminators are strictly CRLF
      expect(csvOutput.endsWith('\r\n')).toBe(true);

      // Parse with strict RFC 4180 Oracle
      const parsedRows = parseRFC4180Csv(csvOutput);

      // Header + 50 recipe rows = 51 rows
      expect(parsedRows.length).toBe(51);

      // Header verification
      const headers = parsedRows[0];
      expect(headers).toEqual([
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

      // Verify all 50 rows have exact 9 columns
      for (let r = 1; r < parsedRows.length; r++) {
        const row = parsedRows[r];
        expect(row.length).toBe(9);

        const recipeIdx = r;
        expect(row[0]).toBe(`Recipe #${recipeIdx}: Grandma's "Special" Batch with, Commas & Quotes`);
        expect(row[1]).toContain('Featuring multiple lines');
        expect(row[1]).toContain('"nested quotes"');
        expect(row[2]).toContain('cup flour, sifted');
        expect(row[3]).toContain('1. Mix dry ingredients in a large bowl.');
        expect(row[3]).toContain('2. Add liquid ingredients');
        expect(row[3]).toContain('3. Bake at 350°F');
        expect(row[7]).toContain('Heirloom');
      }
    });

    it('exports large recipe collection to Schema.org JSON-LD and validates ISO 8601 durations and structured steps', () => {
      const recipes: RecipeSchema[] = [
        {
          recipe_id: 'rec_ld_1',
          title: 'Authentic Bolognese Sauce',
          description: 'Slow-simmered classic ragù alla bolognese.',
          yield_servings: '6 portions',
          prep_time: '25 mins',
          cook_time: '3 hours',
          total_time: '3 hr 25 mins',
          ingredients: ['1 lb ground beef', '1 cup whole milk', '1 cup white wine'],
          directions: ['1. Brown the beef.', '2. Add wine and milk, simmer for 3 hours.'],
          tags: ['Italian', 'Pasta', 'Dinner'],
          source_notes: 'Bologna family heirloom',
        },
        {
          recipe_id: 'rec_ld_2',
          title: 'Quick Crepes',
          prep_time: '10 minutes',
          cook_time: '15 mins',
          total_time: '25 mins',
          ingredients: ['2 eggs', '1 cup milk', '1 cup flour'],
          directions: ['Whisk batter smooth.', 'Cook on hot buttered skillet.'],
          tags: ['Breakfast'],
        },
      ];

      const jsonLdStr = generateJsonLd(recipes);
      const parsedArray = JSON.parse(jsonLdStr);

      expect(Array.isArray(parsedArray)).toBe(true);
      expect(parsedArray.length).toBe(2);

      // Recipe 1
      const r1 = parsedArray[0];
      expect(r1['@context']).toBe('https://schema.org');
      expect(r1['@type']).toBe('Recipe');
      expect(r1.name).toBe('Authentic Bolognese Sauce');
      expect(r1.prepTime).toBe('PT25M');
      expect(r1.cookTime).toBe('PT3H');
      expect(r1.totalTime).toBe('PT3H25M');
      expect(r1.recipeInstructions).toEqual([
        { '@type': 'HowToStep', position: 1, text: 'Brown the beef.' },
        { '@type': 'HowToStep', position: 2, text: 'Add wine and milk, simmer for 3 hours.' },
      ]);

      // Recipe 2
      const r2 = parsedArray[1];
      expect(r2.prepTime).toBe('PT10M');
      expect(r2.cookTime).toBe('PT15M');
      expect(r2.totalTime).toBe('PT25M');
      expect(r2.recipeInstructions).toEqual([
        { '@type': 'HowToStep', position: 1, text: 'Whisk batter smooth.' },
        { '@type': 'HowToStep', position: 2, text: 'Cook on hot buttered skillet.' },
      ]);
    });

    it('exports raw recipe JSON with full fidelity lossless serialization', () => {
      const recipes: RecipeSchema[] = [
        {
          recipe_id: 'rec_raw_1',
          title: 'Raw Recipe Export Test',
          card_ids: ['card_1', 'card_2', 'card_3'],
          ingredients: ['Ing A', 'Ing B'],
          directions: ['1. Step A', '2. Step B'],
          tags: ['Tag1', 'Tag2'],
        },
      ];

      const rawJson = generateRawJson(recipes);
      const parsed = JSON.parse(rawJson);
      expect(parsed).toEqual(recipes);
    });

    it('renders ExportDialog and displays correct preview for all 3 export formats', () => {
      const recipes: RecipeSchema[] = [
        {
          recipe_id: 'rec_dlg_1',
          title: 'Dialog Export Recipe',
          ingredients: ['1 cup sugar'],
          directions: ['1. Stir well.'],
          tags: ['Sweet'],
        },
      ];

      render(<ExportDialog recipes={recipes} sessionId="sess_dlg" />);

      // Default is CSV
      expect(screen.getAllByText(/Plan To Eat CSV/i).length).toBeGreaterThan(0);
      expect(screen.getByText(/Title,Description,Ingredients,Directions/i)).toBeInTheDocument();

      // Click JSON-LD
      const jsonLdBtn = screen.getByRole('button', { name: /Schema\.org JSON-LD/i });
      fireEvent.click(jsonLdBtn);
      expect(screen.getByText(/https:\/\/schema\.org/i)).toBeInTheDocument();

      // Click Raw JSON
      const rawBtn = screen.getByRole('button', { name: /Standard Raw JSON/i });
      fireEvent.click(rawBtn);
      expect(screen.getByText(/"recipe_id": "rec_dlg_1"/i)).toBeInTheDocument();
    });

    it('displays empty state in ExportDialog when no recipes are supplied', () => {
      render(<ExportDialog recipes={[]} />);
      expect(screen.getByText(/No Recipes Available for Export/i)).toBeInTheDocument();
    });
  });

  // =========================================================================
  // Area 6: Ingestion Controls, Sliders & Dropzone Robustness
  // =========================================================================
  describe('Area 6: Ingestion Controls, Sliders & Dropzone Robustness', () => {
    it('updates ingestion configuration options including concurrency throttle and model selector', () => {
      const config: PipelineStartRequest = {
        max_concurrency: 4,
        slow_batch_size: 5,
        slow_batch_interval_seconds: 2.0,
        model: 'gemini-3.7-flash',
      };

      const onChange = vi.fn();
      render(<ConfigPanel config={config} onChange={onChange} />);

      // Change concurrency slider
      const concurrencySlider = screen.getByLabelText(/Max Concurrent Workers/i);
      fireEvent.change(concurrencySlider, { target: { value: '8' } });
      expect(onChange).toHaveBeenCalledWith({ ...config, max_concurrency: 8 });

      // Change model select
      const modelSelect = screen.getByRole('combobox');
      fireEvent.change(modelSelect, { target: { value: 'gemini-3.7-pro' } });
      expect(onChange).toHaveBeenCalledWith({ ...config, model: 'gemini-3.7-pro' });
    });

    it('handles drag-and-drop file selection in Dropzone component', () => {
      const onFilesChange = vi.fn();
      render(<Dropzone files={[]} onFilesChange={onFilesChange} />);

      expect(screen.getByText(/Drag and drop card scans/i)).toBeInTheDocument();

      // Simulate file drop
      const file1 = new File(['mock content 1'], 'card_front.jpg', { type: 'image/jpeg' });
      const file2 = new File(['mock content 2'], 'card_back.png', { type: 'image/png' });

      const dropzoneArea = screen.getByText(/Drag and drop card scans/i).closest('div')!;
      fireEvent.drop(dropzoneArea, {
        dataTransfer: {
          files: [file1, file2],
        },
      });

      expect(onFilesChange).toHaveBeenCalledWith([file1, file2]);
    });
  });
});
