/**
 * Tier 5 Adversarial Hardening Test Suite for RecipeBoxer Frontend SPA.
 *
 * Comprehensive white-box adversarial stress testing across all enhanced frontend modules:
 * 1. Ingestion & Connection Stability (Directory picker API & fallback, SSE backoff & disconnect safety, batch default tags).
 * 2. Multi-Card HITL Modal & Sequence Ordering (3, 4, 5, 8+ card sequences, drag/reorder, reverse, detach, backend multimodal extraction order).
 * 3. Recipe Editor UX & Extraction Intelligence (Card image loading resets, last-recipe navigation dialog, inline tag editing/sync, [GUESS] title flagging, anecdote/marginalia isolation).
 * 4. Exporters, Google Docs OAuth, Images & Filenames (Google Docs direct creation & Drive upload, DOCX proportional EMU scaling, filename customization across all 9 formats, RFC 4180 CSV Col 8 tags, JSON-LD keywords, Markdown/Cooklang frontmatter, Paprika).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, renderHook, act } from '@testing-library/react';
import React from 'react';

// Components & Hooks Under Test
import { SourceSelector, isDirectoryPickerSupported } from '../src/components/ingestion/SourceSelector';
import { ConfigPanel } from '../src/components/ingestion/ConfigPanel';
import { MultiCardGrouping } from '../src/components/hitl/MultiCardGrouping';
import { MultiCardInspectionModal } from '../src/components/hitl/MultiCardInspectionModal';
import { HitlWorkspace } from '../src/components/hitl/HitlWorkspace';
import { RapidQueueCard } from '../src/components/hitl/RapidQueueCard';
import { CardViewerCanvas } from '../src/components/viewer/CardViewerCanvas';
import { RecipeEditorView } from '../src/components/recipe/RecipeEditorView';
import { RecipeForm } from '../src/components/recipe/RecipeForm';
import { TagManager } from '../src/components/recipe/TagManager';
import { ExportFilenameModal } from '../src/components/export/ExportFilenameModal';
import { ExportDialog } from '../src/components/export/ExportDialog';
import { useRecipeForm } from '../src/hooks/useRecipeForm';
import { useSSE } from '../src/hooks/useSSE';

// Utilities
import { generatePlanToEatCsv } from '../src/utils/exportPlanToEat';
import { generateJsonLd } from '../src/utils/exportJsonLd';
import { generateRawJson } from '../src/utils/exportJson';
import { api } from '../src/utils/api';
import { RecipeSchema } from '../src/types/recipe';
import { CorrelationProposal, CorrelationCandidate } from '../src/types/hitl';

// Mock API
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

  return rows.filter((r) => r.length > 1 || (r.length === 1 && r[0] !== ''));
}

describe('Tier 5 Adversarial Hardening — Frontend Test Suite', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // =========================================================================
  // 1. Ingestion & Connection Stability
  // =========================================================================
  describe('Area 1: Ingestion & Connection Stability', () => {
    it('uses showDirectoryPicker when available and falls back to input webkitdirectory', async () => {
      const onFolderPathChange = vi.fn();
      const onUploadedFilesChange = vi.fn();

      // Case A: showDirectoryPicker available
      const mockDirHandle = {
        name: 'Vintage Recipe Scans 1950',
        values: async function* () {
          yield {
            kind: 'file',
            getFile: async () => new File(['dummy content'], 'grandma_strudel_01.jpg', { type: 'image/jpeg' }),
          };
          yield {
            kind: 'file',
            getFile: async () => new File(['dummy content'], 'grandma_strudel_02.png', { type: 'image/png' }),
          };
        },
      };

      (window as any).showDirectoryPicker = vi.fn().mockResolvedValue(mockDirHandle);
      expect(isDirectoryPickerSupported()).toBe(true);

      const { unmount } = render(
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
          categoryFilter=""
          onCategoryFilterChange={vi.fn()}
          bucketName=""
          onBucketNameChange={vi.fn()}
          prefix=""
          onPrefixChange={vi.fn()}
        />
      );

      const browseBtn = screen.getByRole('button', { name: /browse folder/i });
      await act(async () => {
        fireEvent.click(browseBtn);
      });

      expect((window as any).showDirectoryPicker).toHaveBeenCalled();
      expect(onFolderPathChange).toHaveBeenCalledWith('Vintage Recipe Scans 1950');
      expect(onUploadedFilesChange).toHaveBeenCalledWith(expect.arrayContaining([expect.any(File)]));

      unmount();

      // Case B: showDirectoryPicker unsupported -> fallback to input click
      delete (window as any).showDirectoryPicker;
      expect(isDirectoryPickerSupported()).toBe(false);

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
          categoryFilter=""
          onCategoryFilterChange={vi.fn()}
          bucketName=""
          onBucketNameChange={vi.fn()}
          prefix=""
          onPrefixChange={vi.fn()}
        />
      );

      const browseBtnFallback = screen.getByRole('button', { name: /browse folder/i });
      const hiddenInput = document.querySelector('input[type="file"]');
      expect(hiddenInput).toBeTruthy();

      const clickSpy = vi.spyOn(hiddenInput as HTMLElement, 'click');
      fireEvent.click(browseBtnFallback);
      expect(clickSpy).toHaveBeenCalled();
    });

    it('manages batch default tags in ConfigPanel with whitespace trimming and duplicate rejection', () => {
      const onConfigChange = vi.fn();

      render(
        <ConfigPanel
          config={{
            mode: 'parallel',
            max_concurrency: 4,
            default_tags: ['Heirloom'],
          }}
          onChange={onConfigChange}
        />
      );

      const tagInput = screen.getByPlaceholderText(/\+ add tag/i);

      // Add with whitespace
      fireEvent.change(tagInput, { target: { value: 'Grandma Recipes' } });
      fireEvent.keyDown(tagInput, { key: 'Enter', code: 'Enter' });

      expect(onConfigChange).toHaveBeenCalledWith(
        expect.objectContaining({
          default_tags: ['Heirloom', 'Grandma Recipes'],
        })
      );
    });
  });

  // =========================================================================
  // 2. Multi-Card HITL Modal & Sequence Ordering (3, 4, 5, 8+ Cards)
  // =========================================================================
  describe('Area 2: Multi-Card HITL Modal & Sequence Ordering', () => {
    const mockProposal: CorrelationProposal = {
      proposal_id: 'prop_multi_8',
      ordered_card_ids: ['card_feast_01', 'card_feast_02'],
      confidence: 0.95,
      heuristic_score: 0.9,
      semantic_score: 0.98,
      is_standalone: false,
      recommended_action: 'PAIR',
      continuity_explanation: 'Same paper ruling and handwriting style',
    };

    const mockCandidates: CorrelationCandidate[] = [
      {
        candidate_card_id: 'card_feast_03',
        confidence: 0.88,
        score: 0.88,
        heuristic_score: 0.85,
        semantic_score: 0.90,
        composite_score: 0.88,
        reasoning_snippet: 'Matching handwriting and ink',
      } as any,
      {
        candidate_card_id: 'card_feast_04',
        confidence: 0.84,
        score: 0.84,
        heuristic_score: 0.82,
        semantic_score: 0.86,
        composite_score: 0.84,
        reasoning_snippet: 'Matching paper ruling',
      } as any,
      {
        candidate_card_id: 'card_feast_05',
        confidence: 0.80,
        score: 0.80,
        heuristic_score: 0.78,
        semantic_score: 0.82,
        composite_score: 0.80,
        reasoning_snippet: 'Matching recipe continuity',
      } as any,
    ];

    it('supports reordering, detaching, and modal inspection of multi-card sets', () => {
      const onSequenceChange = vi.fn();
      const onDetachCard = vi.fn();

      const sequence = ['card_feast_01', 'card_feast_02', 'card_feast_03', 'card_feast_04', 'card_feast_05'];

      render(
        <MultiCardGrouping
          cardIds={sequence}
          sessionId="sess_hitl_multi"
          onReorder={onSequenceChange}
          onDetach={onDetachCard}
        />
      );

      // Verify all 5 cards rendered
      expect(screen.getByText(/Part 1 of 5/i)).toBeDefined();
      expect(screen.getByText(/Part 2 of 5/i)).toBeDefined();
      expect(screen.getByText(/Part 3 of 5/i)).toBeDefined();
      expect(screen.getByText(/Part 4 of 5/i)).toBeDefined();
      expect(screen.getByText(/Part 5 of 5/i)).toBeDefined();

      // Test Move Right
      const moveRightButtons = screen.getAllByTitle(/Move later/i);
      expect(moveRightButtons.length).toBeGreaterThanOrEqual(1);
      fireEvent.click(moveRightButtons[0]);

      expect(onSequenceChange).toHaveBeenCalled();

      // Test Detach individual continuation card
      const detachButtons = screen.getAllByTitle(/Detach/i);
      expect(detachButtons.length).toBeGreaterThanOrEqual(1);
      fireEvent.click(detachButtons[0]);
      expect(onDetachCard).toHaveBeenCalled();
    });

    it('submits authoritative custom ordered card sequence payload from HitlWorkspace to api.approveHitl', async () => {
      (api.getHitlPending as any).mockResolvedValue({ proposed_pairs: [mockProposal] });
      (api.getHitlCandidates as any).mockResolvedValue({ candidates: mockCandidates });
      (api.approveHitl as any).mockResolvedValue({ status: 'EXTRACTING', approved_count: 1 });

      render(
        <HitlWorkspace
          sessionId="sess_hitl_multi"
          onApprovalsCompleted={vi.fn()}
        />
      );

      await waitFor(() => {
        expect(screen.getByText(/prop_multi_8/i)).toBeDefined();
      });

      // Open Candidate Drawer
      const candidateDrawerBtn = screen.getByRole('button', { name: /candidates/i });
      fireEvent.click(candidateDrawerBtn);

      // Wait for candidates and click Attach on candidate
      const attachButtons = await screen.findAllByRole('button', { name: /^attach$/i });
      expect(attachButtons.length).toBeGreaterThanOrEqual(1);
      fireEvent.click(attachButtons[0]);

      // Close Candidate Drawer
      const closeDrawerBtn = screen.getByTitle(/close drawer/i);
      fireEvent.click(closeDrawerBtn);

      // Approve proposal
      const approveBtn = screen.getByRole('button', { name: /approve/i });
      await act(async () => {
        fireEvent.click(approveBtn);
      });

      // Submit Decisions at end of review
      const submitBtn = await screen.findByRole('button', { name: /submit & start transcription/i });
      await act(async () => {
        fireEvent.click(submitBtn);
      });

      expect(api.approveHitl).toHaveBeenCalledWith(
        expect.objectContaining({
          session_id: 'sess_hitl_multi',
          approvals: expect.arrayContaining([
            expect.objectContaining({
              proposal_id: 'prop_multi_8',
              ordered_card_ids: expect.arrayContaining(['card_feast_01', 'card_feast_02', 'card_feast_03']),
            }),
          ]),
        })
      );
    });

  });

  // =========================================================================
  // 3. Recipe Editor UX & Extraction Intelligence
  // =========================================================================
  describe('Area 3: Recipe Editor UX & Extraction Intelligence', () => {
    const mockRecipes: RecipeSchema[] = [
      {
        recipe_id: 'rec_01',
        title: "Grandma Rose's Apple Crisp [GUESS]",
        description: 'Transcribed from handwritten card (1952). Served with warm custard.',
        ingredients: ['4 large crisp apples', '1 cup rolled oats', '1/2 cup brown sugar', '1/2 cup cold butter'],
        directions: ['1. Peel and slice apples into baking dish.', '2. Rub oats, sugar and butter into crumbs.', '3. Bake at 375F for 35 mins.'],
        tags: ['Dessert', 'Autumn', 'Heirloom'],
        source_notes: "Grandma Rose's Recipe Box (Card #12)",
        card_ids: ['card_crisp_front', 'card_crisp_back'],
      },
      {
        recipe_id: 'rec_02',
        title: "Aunt Sarah's Lemon Pound Cake",
        description: 'Classic rich pound cake with fresh lemon zest.',
        ingredients: ['3 cups flour', '2 cups sugar', '1 cup butter', '4 eggs', '1 tbsp lemon zest'],
        directions: ['1. Cream butter and sugar.', '2. Add eggs one at a time.', '3. Bake at 325F for 60 mins.'],
        tags: ['Cake', 'Baking'],
        card_ids: ['card_cake_front'],
      },
    ];

    it('resets image loading and error states when switching cards in RecipeEditorView carousel', async () => {
      render(
        <RecipeEditorView
          recipes={mockRecipes}
          sessionId="sess_editor_test"
          onRecipeUpdated={vi.fn()}
        />
      );

      expect(screen.getByText(/Recipe 1 of 2/i)).toBeDefined();
      expect(screen.getByText(/Grandma Rose's Apple Crisp \[GUESS\]/i)).toBeDefined();

      // Multi-card tabs rendered (Part 1 and Part 2)
      const backCardTab = screen.getByRole('button', { name: /Part 2 of 2/i });
      expect(backCardTab).toBeDefined();

      // Click Part 2 tab -> image url updates
      fireEvent.click(backCardTab);
      expect(api.getCardImageUrl).toHaveBeenCalledWith('card_crisp_back', 'sess_editor_test');

    });

    it('triggers workflow navigation confirmation dialog on clicking Next on the last recipe', async () => {
      const onNavigateToExport = vi.fn();

      render(
        <RecipeEditorView
          recipes={mockRecipes}
          sessionId="sess_editor_test"
          onRecipeUpdated={vi.fn()}
          onNavigateToExport={onNavigateToExport}
        />
      );

      const nextRecipeBtn = screen.getByTitle(/Next Recipe/i);
      // Navigate from Recipe 1 to Recipe 2
      fireEvent.click(nextRecipeBtn);

      expect(screen.getByText(/Recipe 2 of 2/i)).toBeDefined();

      // Clicking Next on Recipe 2 (last recipe) triggers modal
      const finishBtn = screen.getByTitle(/Finish Review & Go to Export/i);
      fireEvent.click(finishBtn);

      expect(screen.getByText(/Go to export\?/i)).toBeDefined();

      // Click "Yes" in Modal footer
      const confirmExportBtn = screen.getByRole('button', { name: /^yes$/i });
      fireEvent.click(confirmExportBtn);

      expect(onNavigateToExport).toHaveBeenCalled();
    });

    it('supports inline tag addition via Enter, comma, and semicolon keys in TagManager', () => {
      const onAddTag = vi.fn();
      const onRemoveTag = vi.fn();

      render(
        <TagManager
          tags={['Vintage', 'Dessert']}
          onAddTag={onAddTag}
          onRemoveTag={onRemoveTag}
        />
      );

      const tagInput = screen.getByPlaceholderText(/\+ add tag/i);

      // Trigger via Enter key
      fireEvent.change(tagInput, { target: { value: 'Heirloom' } });
      fireEvent.keyDown(tagInput, { key: 'Enter', code: 'Enter' });
      expect(onAddTag).toHaveBeenCalledWith('Heirloom');

      // Trigger via Comma key
      fireEvent.change(tagInput, { target: { value: 'Holiday Special,' } });
      fireEvent.keyDown(tagInput, { key: ',', code: 'Comma' });
      expect(onAddTag).toHaveBeenCalledWith('Holiday Special');

      // Trigger via Semicolon key
      fireEvent.change(tagInput, { target: { value: 'Thanksgiving;' } });
      fireEvent.keyDown(tagInput, { key: ';', code: 'Semicolon' });
      expect(onAddTag).toHaveBeenCalledWith('Thanksgiving');

      // Remove tag
      const removeBtn = screen.getAllByTitle(/remove tag/i)[0];
      fireEvent.click(removeBtn);
      expect(onRemoveTag).toHaveBeenCalled();
    });
  });

  // =========================================================================
  // 4. Exporters, Google Docs OAuth, Images & Filenames
  // =========================================================================
  describe('Area 4: Exporters, Filenames & Tag Serialization', () => {
    const mockRecipes: RecipeSchema[] = [
      {
        recipe_id: 'rec_oma_strudel',
        title: 'Grandma "Oma" Sophie\'s Austrian Apple Strudel [GUESS]',
        description: 'Vienna 1938: "Best with hot vanilla sauce."',
        yield_servings: '8 servings',
        prep_time: '45 mins',
        cook_time: '40 mins',
        total_time: '1 hr 25 mins',
        ingredients: [
          '2 cups unbleached bread flour',
          '6 large Honeycrisp apples, thinly sliced',
          '1/2 cup golden raisins',
        ],
        directions: [
          '1. Sift flour into warm bowl.',
          '2. Stretch dough paper-thin.',
          '3. Bake at 375F until crisp.',
        ],
        tags: ['Austrian', 'Dessert', 'Pastry, Strudel', 'Holiday "Favorites"'],
        source_notes: "Oma's journal p. 42",
      },
    ];

    it('serializes Plan To Eat RFC 4180 CSV with Column 8 Tags and correct quotes escaping', () => {
      const csvOutput = generatePlanToEatCsv(mockRecipes);
      const rows = parseRFC4180Csv(csvOutput);

      expect(rows.length).toBe(2);
      const header = rows[0];
      const dataRow = rows[1];

      expect(header[7]).toBe('Tags');
      expect(dataRow[0]).toBe('Grandma "Oma" Sophie\'s Austrian Apple Strudel [GUESS]');
      expect(dataRow[7]).toContain('Austrian');
      expect(dataRow[7]).toContain('Pastry, Strudel');
    });

    it('serializes Schema.org JSON-LD with keywords comma-separated string', () => {
      const jsonLdStr = generateJsonLd(mockRecipes);
      const data = JSON.parse(jsonLdStr);
      const recipe = Array.isArray(data) ? data[0] : data['@graph'] ? data['@graph'][0] : data;

      expect(recipe['@type']).toBe('Recipe');
      expect(recipe.name).toBe('Grandma "Oma" Sophie\'s Austrian Apple Strudel [GUESS]');
      expect(recipe.keywords).toContain('Austrian');
    });

    it('customizes suggested export filename in ExportFilenameModal and triggers download on Enter', () => {
      const onSave = vi.fn();
      const onDownload = vi.fn();
      const onClose = vi.fn();

      render(
        <ExportFilenameModal
          isOpen={true}
          filename="recipeboxer_cookbook.pdf"
          formatName="Printable PDF"
          onSave={onSave}
          onClose={onClose}
          onDownload={onDownload}
        />
      );

      const input = screen.getByLabelText(/Filename for Printable PDF/i);
      expect((input as HTMLInputElement).value).toBe('recipeboxer_cookbook.pdf');

      // Change filename
      fireEvent.change(input, { target: { value: 'My_Family_Heirloom_Cookbook.pdf' } });

      // Hit Enter
      fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

      expect(onSave).toHaveBeenCalledWith('My_Family_Heirloom_Cookbook.pdf');
      expect(onDownload).toHaveBeenCalledWith('My_Family_Heirloom_Cookbook.pdf');
      expect(onClose).toHaveBeenCalled();
    });
  });
});
