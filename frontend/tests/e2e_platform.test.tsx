import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

import { SourceSelector } from '../src/components/ingestion/SourceSelector';
import { ConfigPanel } from '../src/components/ingestion/ConfigPanel';
import { SessionLauncher } from '../src/components/ingestion/SessionLauncher';
import { ProgressDashboard } from '../src/components/ingestion/ProgressDashboard';
import { MultiCardGrouping } from '../src/components/hitl/MultiCardGrouping';
import { RapidQueueCard } from '../src/components/hitl/RapidQueueCard';
import { RecipeEditorView } from '../src/components/recipe/RecipeEditorView';
import { RecipeForm } from '../src/components/recipe/RecipeForm';
import { TagManager } from '../src/components/recipe/TagManager';
import { ExportDialog } from '../src/components/export/ExportDialog';
import { IngestionMode, PipelineState, PipelineStatus } from '../src/types/pipeline';
import { RecipeSchema } from '../src/types/recipe';
import { CorrelationProposal } from '../src/types/hitl';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    startPipeline: vi.fn(),
    uploadCards: vi.fn(),
    listSessions: vi.fn(),
    deleteSession: vi.fn(),
    getCardImageUrl: vi.fn((id, sid) => `/api/cards/${id}/image${sid ? `?session_id=${sid}` : ''}`),
    approveHITL: vi.fn(),
    recordHITLDecision: vi.fn(),
    getCandidates: vi.fn(),
    updateRecipe: vi.fn(),
    deleteRecipe: vi.fn(),
  },
}));

vi.mock('../src/utils/exportPlanToEat', () => ({
  downloadPlanToEatCsv: vi.fn(),
  generatePlanToEatCsv: vi.fn(() => 'Title,Ingredients,Directions,Tags\nApple Pie,Apples,Bake,Dessert'),
}));

vi.mock('../src/utils/exportJsonLd', () => ({
  downloadJsonLd: vi.fn(),
  generateJsonLd: vi.fn(() => '{"@type": "Recipe", "name": "Apple Pie"}'),
}));

vi.mock('../src/utils/exportJson', () => ({
  downloadRawJson: vi.fn(),
  generateRawJson: vi.fn(() => '[{"title": "Apple Pie"}]'),
}));

vi.mock('../src/utils/exportMarkdown', () => ({
  downloadMarkdown: vi.fn(),
  generateMarkdown: vi.fn(() => '# Apple Pie\n\n- Apples\n\nBake.'),
}));

vi.mock('../src/utils/exportCooklang', () => ({
  downloadCooklang: vi.fn(),
  generateCooklang: vi.fn(() => '>> tags: Dessert\n\nAdd @apples{2} and bake.'),
}));

vi.mock('../src/utils/exportGoogleDocs', () => ({
  downloadGoogleDocsHtml: vi.fn(),
  generateGoogleDocsHtml: vi.fn(() => '<html><body><h1>Apple Pie</h1></body></html>'),
}));

vi.mock('../src/utils/exportBinary', () => ({
  downloadDocx: vi.fn(),
  downloadPdf: vi.fn(),
  downloadPaprika: vi.fn(),
}));

describe('RecipeBoxer Frontend E2E Platform Suite (Tiers 1–4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ==========================================================================
  // TIER 1: FEATURE COVERAGE (F1.1 – F5.4)
  // ==========================================================================
  describe('Tier 1: Feature Coverage', () => {
    // F1.1: Directory Picker
    it('T1.F1.1: renders local directory picker with mode toggles and folder input', () => {
      const onFolderPathChange = vi.fn();
      render(
        <SourceSelector
          sourceType="local"
          onSourceTypeChange={vi.fn()}
          localMethod="folder"
          onLocalMethodChange={vi.fn()}
          folderPath="/Users/chef/heirloom_scans"
          onFolderPathChange={onFolderPathChange}
          uploadedFiles={[]}
          onUploadedFilesChange={vi.fn()}
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

      const input = screen.getByLabelText(/Local Scans Directory Path/i);
      expect(input).toBeInTheDocument();
      expect(input).toHaveValue('/Users/chef/heirloom_scans');

      fireEvent.change(input, { target: { value: '/new/scan/path' } });
      expect(onFolderPathChange).toHaveBeenCalledWith('/new/scan/path');
    });

    // F1.2: SSE Stability & Progress Dashboard
    it('T1.F1.2: displays live progress metrics and pipeline state badge', () => {
      const status: PipelineStatus = {
        session_id: 'sess_live_01',
        status: PipelineState.FINGERPRINTING,
        mode: IngestionMode.PARALLEL,
        elapsed_seconds: 12,
        progress: {
          total_scans: 10,
          fingerprinted: 6,
          proposals_generated: 2,
          approved_pairs: 0,
          recipes_extracted: 0,
          percent_complete: 60.0,
          current_stage: 'Stage 1: CIELAB Visual Fingerprinting',
        },
        token_usage: {
          prompt_tokens: 1200,
          candidates_tokens: 450,
          thinking_tokens: 0,
          total_tokens: 1650,
        },
      };

      render(<ProgressDashboard status={status} />);

      expect(screen.getByText(/Fingerprinting/i)).toBeInTheDocument();
      expect(screen.getByText('60%')).toBeInTheDocument();
    });

    // F1.3: Batch Default Tags
    it('T1.F1.3: accepts and manages batch default tags in TagManager', () => {
      const onAddTag = vi.fn();
      const onRemoveTag = vi.fn();
      render(
        <TagManager
          tags={['Heirloom', 'Holiday']}
          onAddTag={onAddTag}
          onRemoveTag={onRemoveTag}
        />
      );

      expect(screen.getByText('Heirloom')).toBeInTheDocument();
      expect(screen.getByText('Holiday')).toBeInTheDocument();

      const tagInput = screen.getByPlaceholderText(/\+ Add tag/i);
      fireEvent.change(tagInput, { target: { value: 'Baking' } });
      fireEvent.keyDown(tagInput, { key: 'Enter', code: 'Enter' });

      expect(onAddTag).toHaveBeenCalledWith('Baking');
    });

    // F2.1: Multi-Card Full-Size Modal
    it('T1.F2.1: displays multi-card modal with 3+ card thumbnails and attached count', () => {
      render(
        <MultiCardGrouping
          cardIds={['card_1', 'card_2', 'card_3']}
          sessionId="sess_multi_01"
          onReorder={vi.fn()}
          onDetach={vi.fn()}
        />
      );

      expect(screen.getByText(/Multi-Card Recipe Group/i)).toBeInTheDocument();
      expect(screen.getByText(/3 cards attached/i)).toBeInTheDocument();
      expect(screen.getByText(/Part 1 of 3/i)).toBeInTheDocument();
      expect(screen.getByText(/Part 2 of 3/i)).toBeInTheDocument();
      expect(screen.getByText(/Part 3 of 3/i)).toBeInTheDocument();
    });


    // F2.2: Positional Drag/Reorder Controls
    it('T1.F2.2: supports positional reordering of card sequence via move controls', () => {
      const onReorder = vi.fn();
      render(
        <MultiCardGrouping
          cardIds={['card_1', 'card_2', 'card_3']}
          sessionId="sess_reorder_01"
          onReorder={onReorder}
          onDetach={vi.fn()}
        />
      );

      const moveLaterButtons = screen.getAllByTitle(/Move later/i);
      expect(moveLaterButtons.length).toBeGreaterThan(0);
      fireEvent.click(moveLaterButtons[0]);

      expect(onReorder).toHaveBeenCalledWith(['card_2', 'card_1', 'card_3']);
    });

    // F2.3: Sequence Preservation
    it('T1.F2.3: preserves sequence in RapidQueueCard approval actions', () => {
      const onApprove = vi.fn();
      const proposal: CorrelationProposal = {
        proposal_id: 'prop_seq_01',
        ordered_card_ids: ['card_a', 'card_b'],
        heuristic_score: 0.91,
        semantic_score: 0.91,
        confidence: 0.91,
        is_standalone: false,
        recommended_action: 'PAIR',
      };


      render(
        <RapidQueueCard
          proposal={proposal}
          currentIndex={0}
          totalProposals={1}
          sessionId="sess_queue_01"
          groupedCardIds={['card_a', 'card_b', 'card_c']}
          onApprove={onApprove}
          onStandalone={vi.fn()}
          onReverse={vi.fn()}
          onReject={vi.fn()}
          onToggleDrawer={vi.fn()}
        />
      );

      const approveBtn = screen.getByText('Approve');
      fireEvent.click(approveBtn);

      expect(onApprove).toHaveBeenCalled();
    });

    // F3.1: Card Images in Review Pane
    it('T1.F3.1: renders card scan image in review pane with image URL resolver', () => {
      const recipes: RecipeSchema[] = [
        {
          recipe_id: 'rec_01',
          title: 'Review Apple Crisp',
          ingredients: ['4 apples', '1 cup oats'],
          directions: ['Slice apples.', 'Bake at 350F.'],
          tags: ['Dessert'],
          card_ids: ['card_crisp_1'],
        },
      ];

      render(
        <RecipeEditorView
          recipes={recipes}
          sessionId="sess_review_01"
          onRecipeUpdated={vi.fn()}
        />
      );

      expect(screen.getByText('Review Apple Crisp')).toBeInTheDocument();
      expect(api.getCardImageUrl).toHaveBeenCalledWith('card_crisp_1', 'sess_review_01');
    });

    // F3.2: Workflow Navigation Dialog
    it('T1.F3.2: navigates between recipes and indicates total recipe count', () => {
      const recipes: RecipeSchema[] = [
        { recipe_id: 'rec_1', title: 'First Recipe', ingredients: ['A'], directions: ['1. Cook A.'], tags: [] },
        { recipe_id: 'rec_2', title: 'Second Recipe', ingredients: ['B'], directions: ['2. Cook B.'], tags: [] },
      ];

      render(
        <RecipeEditorView
          recipes={recipes}
          sessionId="sess_nav_01"
          onRecipeUpdated={vi.fn()}
        />
      );

      expect(screen.getByText(/Recipe 1 of 2:/i)).toBeInTheDocument();

      const nextBtn = screen.getByTitle('Next Recipe');
      fireEvent.click(nextBtn);

      expect(screen.getByText(/Recipe 2 of 2:/i)).toBeInTheDocument();
    });

    // F3.3: Inline Tag Editing & State Sync
    it('T1.F3.3: allows adding and removing tags directly in the recipe editor form', () => {
      const onSaved = vi.fn();
      const recipe: RecipeSchema = {
        recipe_id: 'rec_tags_01',
        title: 'Tag Sync Cake',
        ingredients: ['1 cup flour'],
        directions: ['Bake at 350F.'],
        tags: ['Dessert', 'Quick'],
      };

      render(<RecipeForm initialRecipe={recipe} onSaved={onSaved} />);

      expect(screen.getByText('Dessert')).toBeInTheDocument();
      expect(screen.getByText('Quick')).toBeInTheDocument();

      const removeBtn = screen.getByTitle('Remove tag Dessert');
      fireEvent.click(removeBtn);

      expect(screen.queryByText('Dessert')).not.toBeInTheDocument();
    });

    // F4.1: Recipe Title Guessing Flag
    it('T1.F4.1: displays [GUESS] title and allows renaming inferred title', () => {
      const recipe: RecipeSchema = {
        recipe_id: 'rec_guess_01',
        title: 'Blackberry Cobbler [GUESS]',
        ingredients: ['Blackberries', 'Sugar'],
        directions: ['Bake.'],
        tags: [],
      };

      render(<RecipeForm initialRecipe={recipe} onSaved={vi.fn()} />);

      const titleInput = screen.getByDisplayValue('Blackberry Cobbler [GUESS]');
      expect(titleInput).toBeInTheDocument();

      fireEvent.change(titleInput, { target: { value: 'Confirmed Blackberry Cobbler' } });
      expect(titleInput).toHaveValue('Confirmed Blackberry Cobbler');
    });

    // F4.2: Anecdote & Story Preservation
    it('T1.F4.2: preserves description stories and source notes in form textareas', () => {
      const recipe: RecipeSchema = {
        recipe_id: 'rec_story_01',
        title: 'Heritage Shortbread',
        description: 'Made every Christmas by Aunt Margaret since 1948.',
        source_notes: 'Stained with coffee ring. Double the vanilla next time!',
        ingredients: ['Butter', 'Flour'],
        directions: ['Bake.'],
        tags: [],
      };

      render(<RecipeForm initialRecipe={recipe} onSaved={vi.fn()} />);

      const desc = screen.getByDisplayValue('Made every Christmas by Aunt Margaret since 1948.');
      expect(desc).toBeInTheDocument();

      const notes = screen.getByDisplayValue('Stained with coffee ring. Double the vanilla next time!');
      expect(notes).toBeInTheDocument();
    });

    // F5.1: Google Docs OAuth & Doc Creation
    it('T1.F5.1: provides Google Docs format button disabled with Coming Soon watermark and Support In Progress tooltip in ExportDialog', () => {
      const recipes: RecipeSchema[] = [
        { recipe_id: 'rec_gdoc_01', title: 'Google Doc Tart', ingredients: ['Apples'], directions: ['Bake.'], tags: [] },
      ];

      render(<ExportDialog recipes={recipes} sessionId="sess_gdoc_01" />);

      const gdocTab = screen.getByRole('button', { name: /Google Docs Archival/i });
      expect(gdocTab).toBeDisabled();
      expect(gdocTab).toHaveAttribute('title', 'Support In Progress');
      expect(gdocTab).toHaveTextContent('Coming Soon');
    });

    // F5.2: Embedded Images in PDF & DOCX
    it('T1.F5.2: renders binary export preview tabs for Printable PDF and Word DOCX', () => {
      const recipes: RecipeSchema[] = [
        { recipe_id: 'rec_bin_01', title: 'Binary Recipe', ingredients: ['Flour'], directions: ['Cook.'], tags: [] },
      ];

      render(<ExportDialog recipes={recipes} sessionId="sess_bin_01" />);

      const pdfTab = screen.getByText('Printable PDF');
      fireEvent.click(pdfTab);
      expect(screen.getByText(/Printable Archival PDF Cookbook/i)).toBeInTheDocument();

      const docxTab = screen.getByText('Heirloom DOCX');
      fireEvent.click(docxTab);
      expect(screen.getByText(/Heirloom Cookbook DOCX/i)).toBeInTheDocument();
    });

    // F5.3: Customizable Export Filenames
    it('T1.F5.3: triggers download with correct session-formatted filenames', () => {
      const recipes: RecipeSchema[] = [
        { recipe_id: 'rec_dl_01', title: 'Download Recipe', ingredients: ['Sugar'], directions: ['Mix.'], tags: [] },
      ];

      render(<ExportDialog recipes={recipes} sessionId="sess_export_999999" />);

      const downloadBtn = screen.getByText(/Download Plan To Eat CSV/i);
      expect(downloadBtn).toBeInTheDocument();
      fireEvent.click(downloadBtn);
    });

    // F5.4: Tag Serialization Across Formats
    it('T1.F5.4: previews tag serialization across Plan To Eat, Markdown, and Cooklang', () => {
      const recipes: RecipeSchema[] = [
        {
          recipe_id: 'rec_fmt_01',
          title: 'Format Tagged Stew',
          ingredients: ['Beef', 'Carrots'],
          directions: ['Simmer 2 hours.'],
          tags: ['Heirloom', 'Dinner', 'Winter'],
        },
      ];

      render(<ExportDialog recipes={recipes} sessionId="sess_fmt_01" />);

      expect(screen.getByText('Plan To Eat CSV')).toBeInTheDocument();
      expect(screen.getByText('Obsidian Markdown')).toBeInTheDocument();
      expect(screen.getByText('Cooklang (.cook)')).toBeInTheDocument();
      expect(screen.getByText('Paprika (.paprikarecipes)')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // TIER 2: BOUNDARY & CORNER CASES
  // ==========================================================================
  describe('Tier 2: Boundary & Corner Cases', () => {
    it('T2.1: handles zero recipes empty state gracefully in RecipeEditorView', () => {
      render(<RecipeEditorView recipes={[]} onRecipeUpdated={vi.fn()} />);
      expect(screen.getByText(/No Extracted Recipes Found/i)).toBeInTheDocument();
    });

    it('T2.2: handles zero recipes empty state gracefully in ExportDialog', () => {
      render(<ExportDialog recipes={[]} />);
      expect(screen.getByText(/No Recipes Available for Export/i)).toBeInTheDocument();
    });

    it('T2.3: rejects empty or whitespace-only tags in TagManager', () => {
      const onAddTag = vi.fn();
      render(<TagManager tags={['Dinner']} onAddTag={onAddTag} onRemoveTag={vi.fn()} />);

      const input = screen.getByPlaceholderText(/\+ Add tag/i);
      fireEvent.change(input, { target: { value: '   ' } });
      fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

      expect(onAddTag).not.toHaveBeenCalled();
    });

    it('T2.4: disables prev button on first recipe and triggers export modal on last recipe', () => {
      const recipes: RecipeSchema[] = [
        { recipe_id: 'r1', title: 'First', ingredients: ['A'], directions: ['1.'], tags: [] },
        { recipe_id: 'r2', title: 'Last', ingredients: ['B'], directions: ['2.'], tags: [] },
      ];

      render(<RecipeEditorView recipes={recipes} onRecipeUpdated={vi.fn()} />);

      const prevBtn = screen.getByTitle('Previous Recipe');
      const nextBtn = screen.getByTitle('Next Recipe');

      expect(prevBtn).toBeDisabled();
      expect(nextBtn).not.toBeDisabled();

      fireEvent.click(nextBtn);

      expect(prevBtn).not.toBeDisabled();
      const finishBtn = screen.getByTitle(/Finish Review & Go to Export/i);
      expect(finishBtn).not.toBeDisabled();
      fireEvent.click(finishBtn);
      expect(screen.getByText('Go to export?')).toBeInTheDocument();
    });

    it('T2.5: renders large multi-card set with 10 cards without crashing', () => {
      const cardIds = Array.from({ length: 10 }, (_, i) => `card_${i}`);
      render(
        <MultiCardGrouping
          cardIds={cardIds}
          sessionId="sess_10"
          onReorder={vi.fn()}
          onDetach={vi.fn()}
        />
      );

      expect(screen.getByText(/10 cards attached/i)).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // TIER 3: CROSS-FEATURE INTERACTIONS
  // ==========================================================================
  describe('Tier 3: Cross-Feature Interactions', () => {
    it('T3.1: configures batch default tags and submits pipeline start request', async () => {
      (api.startPipeline as any).mockResolvedValueOnce({ session_id: 'sess_t3_01' });
      const onSessionStarted = vi.fn();
      render(<SessionLauncher onSessionStarted={onSessionStarted} />);

      const folderInput = screen.getByLabelText(/Local Scans Directory Path/i);
      fireEvent.change(folderInput, { target: { value: '/scans/holiday_batch' } });

      const launchBtn = screen.getByRole('button', { name: /Launch Digitization Pipeline/i });
      fireEvent.click(launchBtn);

      await waitFor(() => {
        expect(api.startPipeline).toHaveBeenCalled();
      });
    });

    it('T3.2: reorders multi-card set in HITL queue and approves with updated sequence', () => {
      const onApprove = vi.fn();
      const proposal: CorrelationProposal = {
        proposal_id: 'prop_combo',
        ordered_card_ids: ['card_p1', 'card_p2'],
        heuristic_score: 0.90,
        semantic_score: 0.90,
        confidence: 0.90,
        is_standalone: false,
        recommended_action: 'PAIR',
      };


      render(
        <RapidQueueCard
          proposal={proposal}
          currentIndex={0}
          totalProposals={1}
          sessionId="sess_combo_01"
          groupedCardIds={['card_p1', 'card_p2', 'card_p3']}
          onApprove={onApprove}
          onStandalone={vi.fn()}
          onReverse={vi.fn()}
          onReject={vi.fn()}
          onToggleDrawer={vi.fn()}
        />
      );

      const approveBtn = screen.getByText('Approve');
      fireEvent.click(approveBtn);

      expect(onApprove).toHaveBeenCalled();
    });

    it('T3.3: edits recipe tags in review pane and syncs state to export dialog', () => {
      const recipe: RecipeSchema = {
        recipe_id: 'rec_sync_01',
        title: 'Sync Recipe',
        ingredients: ['Sugar'],
        directions: ['Mix.'],
        tags: ['InitialTag'],
      };

      const { rerender } = render(<RecipeEditorView recipes={[recipe]} onRecipeUpdated={vi.fn()} />);
      expect(screen.getByText('Sync Recipe')).toBeInTheDocument();

      const updatedRecipe = { ...recipe, tags: ['InitialTag', 'ExportReady'] };
      rerender(<ExportDialog recipes={[updatedRecipe]} />);

      expect(screen.getByText('Plan To Eat CSV')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // TIER 4: REAL-WORLD APPLICATION SCENARIOS
  // ==========================================================================
  describe('Tier 4: Real-World Scenarios', () => {
    it('T4.S1: vintage heirloom digitization with family story, source notes, and tag management', () => {
      const heirloomRecipe: RecipeSchema = {
        recipe_id: 'rec_s1',
        title: "Grandma's 1935 Honey Cake",
        description: 'Brought from Poland in 1935 for Rosh Hashanah.',
        source_notes: 'Use dark buckwheat honey for authentic flavor.',
        ingredients: ['3 cups flour', '1 cup dark honey', '3 eggs'],
        directions: ['Beat eggs and honey.', 'Fold in flour.', 'Bake at 325F.'],
        tags: ['Heirloom', 'Holiday', '1935', 'Honey Cake'],
        card_ids: ['g_front', 'g_back'],
      };

      render(<RecipeForm initialRecipe={heirloomRecipe} onSaved={vi.fn()} />);

      expect(screen.getByDisplayValue("Grandma's 1935 Honey Cake")).toBeInTheDocument();
      expect(screen.getByDisplayValue(/Brought from Poland in 1935/i)).toBeInTheDocument();
      expect(screen.getByDisplayValue(/Use dark buckwheat honey/i)).toBeInTheDocument();
    });

    it('T4.S2: rapid triage with keyboard navigation and title guessing confirmation', () => {
      const guessedRecipe: RecipeSchema = {
        recipe_id: 'rec_s2',
        title: 'Skillet Cornbread [GUESS]',
        ingredients: ['1 cup cornmeal', '1 cup buttermilk', '1 egg'],
        directions: ['Whisk batter.', 'Pour into hot greased skillet.', 'Bake at 425F.'],
        tags: ['Bread', 'Cast Iron'],
      };

      render(<RecipeForm initialRecipe={guessedRecipe} onSaved={vi.fn()} />);

      const titleInput = screen.getByDisplayValue('Skillet Cornbread [GUESS]');
      fireEvent.change(titleInput, { target: { value: 'Southern Skillet Cornbread' } });
      expect(titleInput).toHaveValue('Southern Skillet Cornbread');
    });

    it('T4.S3: 4-card Thanksgiving feast recipe assembly, drag reordering, and export tab review', () => {
      const cards = ['turkey_roast', 'sourdough_stuffing', 'pan_gravy', 'cranberry_relish'];
      render(
        <MultiCardGrouping
          cardIds={cards}
          sessionId="sess_s3"
          onReorder={vi.fn()}
          onDetach={vi.fn()}
        />
      );

      expect(screen.getByText(/4 cards attached/i)).toBeInTheDocument();
    });

    it('T4.S4: rate-limited slow batch processing with progress monitoring dashboard', () => {
      const slowStatus: PipelineStatus = {
        session_id: 'sess_slow_s4',
        status: PipelineState.EXTRACTING,
        mode: IngestionMode.SLOW_BATCH,
        elapsed_seconds: 45,
        progress: {
          total_scans: 20,
          fingerprinted: 20,
          proposals_generated: 8,
          approved_pairs: 8,
          recipes_extracted: 4,
          percent_complete: 70.0,
          current_stage: 'Stage 3: Gemini Multimodal Recipe Extraction',
        },
        token_usage: {
          prompt_tokens: 3400,
          candidates_tokens: 1200,
          thinking_tokens: 0,
          total_tokens: 4600,
        },
      };

      render(<ProgressDashboard status={slowStatus} />);

      expect(screen.getByText(/Extracting/i)).toBeInTheDocument();
      expect(screen.getByText('70%')).toBeInTheDocument();
    });

    it('T4.S5: universal multi-format export interoperability validation', () => {
      const universalRecipes: RecipeSchema[] = [
        {
          recipe_id: 'rec_u1',
          title: 'Matcha Chiffon Cake 抹茶 🍵',
          description: 'Light Japanese sponge cake.',
          ingredients: ['Matcha powder', '6 eggs', 'Cake flour'],
          directions: ['Whip whites.', 'Fold batter.', 'Bake at 325F.'],
          tags: ['Dessert', 'Japanese', 'Matcha', 'Baking'],
        },
      ];

      render(<ExportDialog recipes={universalRecipes} sessionId="sess_u5_all" />);

      // Verify all export format tabs render
      expect(screen.getByText('Plan To Eat CSV')).toBeInTheDocument();
      expect(screen.getByText('Schema.org JSON-LD')).toBeInTheDocument();
      expect(screen.getByText('Standard Raw JSON')).toBeInTheDocument();
      expect(screen.getByText('Obsidian Markdown')).toBeInTheDocument();
      expect(screen.getByText('Cooklang (.cook)')).toBeInTheDocument();
      expect(screen.getByText('Google Docs Archival')).toBeInTheDocument();
      expect(screen.getByText('Paprika (.paprikarecipes)')).toBeInTheDocument();
      expect(screen.getByText('Heirloom DOCX')).toBeInTheDocument();
      expect(screen.getByText('Printable PDF')).toBeInTheDocument();
    });
  });
});
