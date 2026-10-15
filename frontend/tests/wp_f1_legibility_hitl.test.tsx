import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import {
  RecipeSchema,
  IngredientLine,
  Step,
  RecipeWarning,
  displayText,
  formatTimeValue,
  formatYieldValue,
  getAllIngredients,
  getAllSteps,
  parseModelDisagreement,
} from '../src/types/recipe';
import { CorrelationProposal, HitlItem, HitlItemType } from '../src/types/hitl';
import { RecipeForm } from '../src/components/recipe/RecipeForm';
import { RecipeEditorView } from '../src/components/recipe/RecipeEditorView';
import { IngredientListInput } from '../src/components/recipe/IngredientListInput';
import { DirectionListInput } from '../src/components/recipe/DirectionListInput';
import { ConfidenceMeter } from '../src/components/hitl/ConfidenceMeter';
import { RapidQueueCard } from '../src/components/hitl/RapidQueueCard';
import { HitlWorkspace } from '../src/components/hitl/HitlWorkspace';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    updateRecipe: vi.fn(),
    getCardImageUrl: vi.fn((id) => `/api/cards/${id}/image`),
    getHitlPending: vi.fn(),
    getHitlCandidates: vi.fn(),
    approveHitl: vi.fn(),
    resumeSession: vi.fn(),
  },
}));

describe('WP-F1: Legibility UX, Schema v2, & HITL Evidence', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // --------------------------------------------------------------------------
  // 1. Schema v2 Types and Exported Helper Functions
  // --------------------------------------------------------------------------
  describe('Schema v2 Helpers', () => {
    it('displayText prefers normalized_text then text then original_text', () => {
      const lineWithNorm: IngredientLine = {
        original_text: '1 1/2 c. fl.',
        normalized_text: '1.5 cups flour',
        item: 'flour',
        confidence: 0.95,
        source_face: 0,
        legibility: 'CLEAR',
      };
      expect(displayText(lineWithNorm)).toBe('1.5 cups flour');

      const lineWithoutNorm: IngredientLine = {
        original_text: '2 pinches sea salt',
        item: 'sea salt',
        confidence: 0.9,
        source_face: 0,
        legibility: 'CLEAR',
      };
      expect(displayText(lineWithoutNorm)).toBe('2 pinches sea salt');

      const stepWithText: Step = {
        original_text: 'bake @ 350 for 20m',
        text: 'Bake at 350°F for 20 minutes.',
        confidence: 0.9,
        source_face: 0,
        legibility: 'CLEAR',
      };
      expect(displayText(stepWithText)).toBe('Bake at 350°F for 20 minutes.');
    });

    it('formatTimeValue formats TimeValue objects and strings', () => {
      expect(formatTimeValue('15 mins')).toBe('15 mins');
      expect(formatTimeValue({ original_text: '20 minutes' })).toBe('20 minutes');
      expect(formatTimeValue({ original_text: '', minutes: 45 })).toBe('45 mins');
      expect(formatTimeValue({ original_text: '', normalized_minutes: 60 })).toBe('60 mins');
      expect(formatTimeValue({ original_text: '', normalized_iso: 'PT30M' })).toBe('PT30M');
      expect(formatTimeValue(null)).toBe('');
    });

    it('formatYieldValue formats YieldValue objects and strings', () => {
      expect(formatYieldValue('12 muffins')).toBe('12 muffins');
      expect(formatYieldValue({ original_text: '8 servings' })).toBe('8 servings');
      expect(formatYieldValue({ original_text: '', normalized_servings: 24, unit: 'cookies' })).toBe('24 cookies');
      expect(formatYieldValue(null)).toBe('');
    });

    it('getAllIngredients and getAllSteps extract from sections or flat fallback', () => {
      const sectionedRecipe: RecipeSchema = {
        recipe_id: 'rec_sec_01',
        title: 'Fictitious Test Pastry',
        tags: [],
        sections: [
          {
            name: 'Filling',
            ingredients: [
              {
                original_text: '1 cup sugar',
                item: 'sugar',
                confidence: 1.0,
                source_face: 0,
                legibility: 'CLEAR',
              },
            ],
            steps: [
              {
                original_text: 'Whisk together',
                text: 'Whisk together',
                confidence: 1.0,
                source_face: 0,
                legibility: 'CLEAR',
              },
            ],
          },
        ],
      };

      const ings = getAllIngredients(sectionedRecipe);
      expect(ings.length).toBe(1);
      expect(ings[0].original_text).toBe('1 cup sugar');

      const steps = getAllSteps(sectionedRecipe);
      expect(steps.length).toBe(1);
      expect(steps[0].original_text).toBe('Whisk together');

      const flatRecipe: RecipeSchema = {
        recipe_id: 'rec_flat_01',
        title: 'Flat Test Soup',
        tags: [],
        ingredients: ['1 tsp pepper'],
        directions: ['Stir well'],
      };
      expect(getAllIngredients(flatRecipe)[0].item).toBe('1 tsp pepper');
      expect(getAllSteps(flatRecipe)[0].text).toBe('Stir well');
    });

    it('parseModelDisagreement extracts reading 1 and reading 2 from warning messages', () => {
      const warn1: RecipeWarning = {
        code: 'MODEL_DISAGREEMENT',
        message: "Title disagreement: reading 1 has 'Moonlight Scones', reading 2 has 'Midnight Scones'",
        field_path: 'title',
      };
      const parsed1 = parseModelDisagreement(warn1);
      expect(parsed1).not.toBeNull();
      expect(parsed1?.reading1).toBe('Moonlight Scones');
      expect(parsed1?.reading2).toBe('Midnight Scones');
      expect(parsed1?.field).toBe('title');

      const warn2: RecipeWarning = {
        code: 'MODEL_DISAGREEMENT',
        message: "OCR quantity disagreement for 'vanilla': recipe has '1 tsp', OCR has '2 tsp' in 'vanilla line'",
        field_path: 'sections.ingredients[0].quantity',
      };
      const parsed2 = parseModelDisagreement(warn2);
      expect(parsed2?.reading1).toBe('1 tsp');
      expect(parsed2?.reading2).toBe('2 tsp');
    });
  });

  // --------------------------------------------------------------------------
  // 2. Legibility UX in RecipeForm and Line Inputs
  // --------------------------------------------------------------------------
  describe('Legibility UX', () => {
    const fictitiousRecipe: RecipeSchema = {
      recipe_id: 'rec_fictitious_99',
      title: 'Starlight Lavender Tea Cookies',
      title_is_inferred: true,
      description: 'Invented test cookie for legibility evaluation',
      yield_value: { original_text: '18 cookies' },
      prep_time: { original_text: '15 mins', minutes: 15 },
      cook_time: { original_text: '12 mins', minutes: 12 },
      total_time: { original_text: '27 mins', minutes: 27 },
      tags: ['Baking', 'Tea'],
      card_ids: ['card_fictitious_a.png', 'card_fictitious_b.png'],
      warnings: [
        {
          code: 'GROUNDING_UNSUPPORTED',
          message: "Step 3 mentions 'chilling for 1 hr' which does not appear on card face",
          field_path: 'sections.steps[2]',
        },
        {
          code: 'MODEL_DISAGREEMENT',
          message: "Title disagreement: reading 1 has 'Starlight Lavender Tea Cookies', reading 2 has 'Starlight Vanilla Cookies'",
          field_path: 'title',
        },
      ],
      sections: [
        {
          name: 'Main',
          ingredients: [
            {
              original_text: '1/2 c. softened butter',
              normalized_text: '1/2 cup softened butter',
              quantity: '1/2',
              unit: 'cup',
              item: 'softened butter',
              confidence: 0.95,
              source_face: 0,
              legibility: 'CLEAR',
            },
            {
              original_text: 'dried lavender buds [faded]',
              normalized_text: '1 tbsp dried lavender buds',
              quantity: null, // Uncertain quantity
              unit: null,
              item: 'dried lavender buds',
              confidence: 0.72,
              source_face: 0,
              legibility: 'UNCERTAIN',
              illegible_spans: [{ reason: 'faded', text_guess: '1 tbsp' }],
            },
            {
              original_text: '[illegible: stain] extract',
              normalized_text: '[illegible: stain] extract',
              quantity: null,
              item: 'extract',
              confidence: 0.35,
              source_face: 1,
              legibility: 'ILLEGIBLE',
              illegible_spans: [{ reason: 'stain', text_guess: 'vanilla' }],
            },
            {
              original_text: '[crossed out: 1 cup margarine] 1/2 cup coconut oil',
              normalized_text: '1/2 cup coconut oil',
              quantity: '1/2',
              unit: 'cup',
              item: 'coconut oil',
              confidence: 0.88,
              source_face: 1,
              legibility: 'CLEAR',
            },
          ],
          steps: [
            {
              original_text: 'Cream butter and sugar until light.',
              text: 'Cream butter and sugar until light.',
              normalized_text: 'Cream butter and sugar until light.',
              confidence: 0.95,
              source_face: 0,
              legibility: 'CLEAR',
            },
            {
              original_text: 'Bake at 350F [torn: 10-12 mins]',
              text: 'Bake at 350F for 10-12 mins.',
              normalized_text: 'Bake at 350°F for 10-12 minutes.',
              confidence: 0.65,
              source_face: 1,
              legibility: 'UNCERTAIN',
              illegible_spans: [{ reason: 'torn', text_guess: '10-12 mins' }],
            },
          ],
        },
      ],
    };

    it('visually marks low legibility with UNCERTAIN and ILLEGIBLE badges and accessible aria-labels', () => {
      render(<RecipeForm initialRecipe={fictitiousRecipe} sessionId="sess_f1" />);

      // Uncertain badge
      const uncertainBadges = screen.getAllByRole('status', { name: /Uncertain legibility/i });
      expect(uncertainBadges.length).toBeGreaterThanOrEqual(1);

      // Illegible badge
      const illegibleBadges = screen.getAllByRole('status', { name: /Illegible text/i });
      expect(illegibleBadges.length).toBeGreaterThanOrEqual(1);

      // Illegible spans with reasons and text guesses
      expect(screen.getByRole('status', { name: /Illegible span: faded, guess: 1 tbsp/i })).toBeInTheDocument();
      expect(screen.getByRole('status', { name: /Illegible span: stain, guess: vanilla/i })).toBeInTheDocument();
      expect(screen.getByRole('status', { name: /Illegible span: torn, guess: 10-12 mins/i })).toBeInTheDocument();
    });

    it('marks uncertain/null quantities clearly as [Qty: null] without inventing values', () => {
      render(<RecipeForm initialRecipe={fictitiousRecipe} sessionId="sess_f1" />);

      const nullQtyBadges = screen.getAllByRole('status', { name: /Uncertain quantity: null/i });
      expect(nullQtyBadges.length).toBeGreaterThanOrEqual(1);
    });

    it('marks crossed-out text segments visually and accessibly', () => {
      render(<RecipeForm initialRecipe={fictitiousRecipe} sessionId="sess_f1" />);

      const crossedOutBadges = screen.getAllByRole('status', { name: /Crossed out text/i });
      expect(crossedOutBadges.length).toBeGreaterThanOrEqual(1);
    });

    it('visually marks inferred titles with Inferred Title badge and tooltip', () => {
      render(<RecipeForm initialRecipe={fictitiousRecipe} sessionId="sess_f1" />);

      const inferredTitleBadge = screen.getByRole('status', { name: /Inferred title/i });
      expect(inferredTitleBadge).toBeInTheDocument();
      expect(inferredTitleBadge).toHaveTextContent('Inferred Title');
    });

    it('toggles between verbatim original transcription and normalized view', () => {
      render(<RecipeForm initialRecipe={fictitiousRecipe} sessionId="sess_f1" />);

      // Initially in normalized view
      expect(screen.getByDisplayValue('1/2 cup softened butter')).toBeInTheDocument();

      // Click toggle
      const toggleBtn = screen.getByRole('switch');
      fireEvent.click(toggleBtn);

      // Now in verbatim original view
      expect(screen.getByDisplayValue('1/2 c. softened butter')).toBeInTheDocument();

      // Toggle back to normalized
      fireEvent.click(toggleBtn);
      expect(screen.getByDisplayValue('1/2 cup softened butter')).toBeInTheDocument();
    });

    it('clicking an ingredient line or step triggers onSelectFace with source face index', () => {
      const onSelectFace = vi.fn();
      render(
        <RecipeForm
          initialRecipe={fictitiousRecipe}
          sessionId="sess_f1"
          onSelectFace={onSelectFace}
        />
      );

      // Click Face 2 button for the illegible extract on face 1
      const face2Btns = screen.getAllByRole('button', { name: /View source card face 2/i });
      fireEvent.click(face2Btns[0]);
      expect(onSelectFace).toHaveBeenCalledWith(1);

      // Click ingredient input on face 0
      const face1Input = screen.getByDisplayValue('1/2 cup softened butter');
      fireEvent.focus(face1Input);
      expect(onSelectFace).toHaveBeenCalledWith(0);
    });

    it('displays MODEL_DISAGREEMENT warnings showing both readings and lets user choose or edit', () => {
      render(<RecipeForm initialRecipe={fictitiousRecipe} sessionId="sess_f1" />);

      // Disagreement region is rendered
      expect(screen.getByRole('region', { name: /Model disagreement resolution/i })).toBeInTheDocument();

      // Both readings are presented as explicit buttons (never auto-picked)
      const r1Btn = screen.getByRole('button', { name: /Use Reading 1: Starlight Lavender Tea Cookies/i });
      const r2Btn = screen.getByRole('button', { name: /Use Reading 2: Starlight Vanilla Cookies/i });
      expect(r1Btn).toBeInTheDocument();
      expect(r2Btn).toBeInTheDocument();

      // Clicking Reading 2 updates the title
      fireEvent.click(r2Btn);
      expect(screen.getByDisplayValue('Starlight Vanilla Cookies')).toBeInTheDocument();
    });

    it('dismissible grounding warning banner can be dismissed by user', () => {
      render(<RecipeForm initialRecipe={fictitiousRecipe} sessionId="sess_f1" />);

      expect(screen.getByText(/Step 3 mentions 'chilling for 1 hr'/i)).toBeInTheDocument();

      const dismissBtn = screen.getByRole('button', { name: /Dismiss GROUNDING_UNSUPPORTED warning/i });
      fireEvent.click(dismissBtn);

      expect(screen.queryByText(/Step 3 mentions 'chilling for 1 hr'/i)).not.toBeInTheDocument();
    });
  });

  // --------------------------------------------------------------------------
  // 3. RecipeEditorView Filtering
  // --------------------------------------------------------------------------
  describe('RecipeEditorView Filtering', () => {
    const cleanRecipe: RecipeSchema = {
      recipe_id: 'rec_clean_01',
      title: 'Simple Lemon Iced Tea',
      tags: ['Beverage'],
      confidence: 0.99,
      warnings: [],
      sections: [
        {
          ingredients: [
            {
              original_text: '2 cups brewed black tea',
              item: 'brewed black tea',
              confidence: 1.0,
              source_face: 0,
              legibility: 'CLEAR',
            },
          ],
          steps: [],
        },
      ],
    };

    const reviewRecipe: RecipeSchema = {
      recipe_id: 'rec_flagged_02',
      title: 'Grandma Unknown Loaf',
      title_is_inferred: true,
      tags: ['Baking'],
      confidence: 0.65,
      warnings: [{ code: 'LOW_CONFIDENCE', message: 'Faded ink throughout' }],
    };

    it('toggling Needing Review filter restricts displayed recipes', () => {
      const onRecipeUpdated = vi.fn();
      render(
        <RecipeEditorView
          recipes={[cleanRecipe, reviewRecipe]}
          onRecipeUpdated={onRecipeUpdated}
        />
      );

      // Initially shows Recipe 1 of 2
      expect(screen.getByText(/Recipe 1 of 2:/i)).toBeInTheDocument();

      // Click filter
      const filterBtn = screen.getByRole('switch', { name: /Needing Review/i });
      fireEvent.click(filterBtn);

      // Now filtered to 1 recipe needing review
      expect(screen.getByText(/Recipe 1 of 1:/i)).toBeInTheDocument();
      expect(screen.getByRole('textbox', { name: /Recipe Title/i })).toHaveValue('Grandma Unknown Loaf');
    });
  });

  // --------------------------------------------------------------------------
  // 4. HITL Evidence Display & Budget Item Kind
  // --------------------------------------------------------------------------
  describe('HITL Evidence & Budget Handling', () => {
    const verifiedProposal: CorrelationProposal = {
      proposal_id: 'prop_test_01',
      ordered_card_ids: ['card_front.png', 'card_back.png'],
      heuristic_score: 0.85,
      semantic_score: 0.92,
      confidence: 0.89,
      p_link: 0.91, // calibrated confidence
      is_standalone: false,
      semantic_verified: true,
      continuity_explanation: 'Front card ends with "fold in beaten whites" and back continues step 4.',
      recommended_action: 'SEQUENCE',
      evidence_quotes: [
        'Front bottom: fold in beaten whites',
        'Back top: 4. Pour batter into greased pan',
      ],
    };

    const unverifiedLowConfidenceProposal: CorrelationProposal = {
      proposal_id: 'prop_test_02',
      ordered_card_ids: ['card_alpha.png', 'card_beta.png'],
      heuristic_score: 0.45,
      semantic_score: 0.55,
      confidence: 0.52,
      p_link: 0.50, // below 70% threshold
      is_standalone: false,
      semantic_verified: false,
      degraded_reason: 'NO_MODEL',
      continuity_explanation: 'Heuristic substrate match only; offline run.',
      recommended_action: 'UNCERTAIN',
      flags: ['UNVERIFIED_QUOTE: fabricated quote text'],
      evidence_quotes: ['fabricated quote text'],
    };

    it('displays calibrated confidence and Verified (Set Verification) badge', () => {
      render(
        <ConfidenceMeter
          confidence={verifiedProposal.confidence}
          pLink={verifiedProposal.p_link}
          heuristicScore={verifiedProposal.heuristic_score}
          semanticScore={verifiedProposal.semantic_score}
          reasoningSnippet={verifiedProposal.continuity_explanation}
          semanticVerified={verifiedProposal.semantic_verified}
          evidenceQuotes={verifiedProposal.evidence_quotes}
        />
      );

      // Calibrated match percentage
      expect(screen.getByText('91% Match')).toBeInTheDocument();

      // Verified badge
      expect(screen.getByRole('status', { name: /Verified link/i })).toBeInTheDocument();

      // Verbatim evidence quotes cited
      expect(screen.getByText(/Front bottom: fold in beaten whites/i)).toBeInTheDocument();
      expect(screen.getByText(/Back top: 4\. Pour batter into greased pan/i)).toBeInTheDocument();
    });

    it('clearly flags low-confidence links (< 70%) and displays Unverified / Degraded status', () => {
      render(
        <ConfidenceMeter
          confidence={unverifiedLowConfidenceProposal.confidence}
          pLink={unverifiedLowConfidenceProposal.p_link}
          heuristicScore={unverifiedLowConfidenceProposal.heuristic_score}
          semanticScore={unverifiedLowConfidenceProposal.semantic_score}
          reasoningSnippet={unverifiedLowConfidenceProposal.continuity_explanation}
          semanticVerified={unverifiedLowConfidenceProposal.semantic_verified}
          degradedReason={unverifiedLowConfidenceProposal.degraded_reason}
          evidenceQuotes={unverifiedLowConfidenceProposal.evidence_quotes}
          flags={unverifiedLowConfidenceProposal.flags}
        />
      );

      // Low confidence warning
      expect(screen.getByRole('alert', { name: /Low-confidence correlation link/i })).toBeInTheDocument();

      // Unverified / Degraded badge
      expect(screen.getByRole('status', { name: /Unverified or degraded link/i })).toBeInTheDocument();

      // Unverified quote alert
      expect(screen.getByText(/Unverified quote: Model cited text/i)).toBeInTheDocument();
    });

    it('renders Budget HITL item kind when pipeline paused due to spending cap', async () => {
      const budgetItem: HitlItem = {
        item_id: 'hitl_budget_test_01',
        item_type: HitlItemType.BUDGET,
        kind: 'budget',
        stage: 'EXTRACTION',
        message: 'Spending budget cap of $1.00 reached. Current spend: $1.05.',
      };

      vi.mocked(api.getHitlPending).mockResolvedValueOnce({
        session_id: 'sess_budget_01',
        status: 'PAUSED',
        proposals: [verifiedProposal],
        unassigned_cards: [],
        total_proposals: 1,
        total_unassigned: 0,
        items: [budgetItem],
      });

      const onCompleted = vi.fn();
      render(
        <HitlWorkspace
          sessionId="sess_budget_01"
          onApprovalsCompleted={onCompleted}
          initialBudgetItem={budgetItem}
        />
      );

      // Budget alert banner
      await waitFor(() => {
        expect(screen.getByRole('alert', { name: /Budget cap reached/i })).toBeInTheDocument();
        expect(screen.getByText(/Spending budget cap of \$1\.00 reached/i)).toBeInTheDocument();
      });

      // Raise Cap & Resume action button
      const resumeBtn = screen.getByRole('button', { name: /Raise budget cap and resume/i });
      expect(resumeBtn).toBeInTheDocument();

      fireEvent.click(resumeBtn);
      await waitFor(() => {
        expect(api.resumeSession).toHaveBeenCalledWith('sess_budget_01');
      });
    });
  });
});
