import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { RecipeForm } from '../src/components/recipe/RecipeForm';
import { RecipeEditorView } from '../src/components/recipe/RecipeEditorView';
import { RecipeSchema } from '../src/types/recipe';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    updateRecipe: vi.fn(),
    getCardImageUrl: vi.fn((id) => `/api/cards/${id}/image`),
  },
}));

describe('R4: Recipe Transcription Review & Inline Editor', () => {
  const sampleRecipe: RecipeSchema = {
    recipe_id: 'rec_001',
    title: "Grandma's Cinnamon Rolls",
    description: 'Holiday family recipe',
    yield_servings: '12 rolls',
    prep_time: '30 mins',
    cook_time: '25 mins',
    total_time: '55 mins',
    ingredients: ['1 cup warm milk', '2 1/4 tsp yeast', '4 cups bread flour'],
    directions: ['1. Dissolve yeast in milk.', '2. Knead in flour.', '3. Bake at 375F.'],
    tags: ['Baking', 'Breakfast', 'Dessert'],
    source_notes: "Grandma's 1964 tin box",
    card_ids: ['card1.jpg', 'card2.jpg'],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('RecipeForm fields and operations', () => {
    it('renders all recipe fields accurately', () => {
      render(<RecipeForm initialRecipe={sampleRecipe} sessionId="sess_test" />);

      expect(screen.getByDisplayValue("Grandma's Cinnamon Rolls")).toBeInTheDocument();
      expect(screen.getByDisplayValue('Holiday family recipe')).toBeInTheDocument();
      expect(screen.getByDisplayValue('12 rolls')).toBeInTheDocument();
      expect(screen.getByDisplayValue('30 mins')).toBeInTheDocument();
      expect(screen.getByDisplayValue('1 cup warm milk')).toBeInTheDocument();
      expect(screen.getByDisplayValue('Dissolve yeast in milk.')).toBeInTheDocument();
      expect(screen.getByText('Baking')).toBeInTheDocument();
    });

    it('adds a new ingredient line on Add Ingredient click', () => {
      render(<RecipeForm initialRecipe={sampleRecipe} sessionId="sess_test" />);

      const addBtn = screen.getByRole('button', { name: /Add Ingredient/i });
      fireEvent.click(addBtn);

      const allIngInputs = screen.getAllByPlaceholderText(/e\.g\. 2 cups all-purpose flour/i);
      expect(allIngInputs.length).toBe(4);
    });

    it('adds a new tag chip and deduplicates case-insensitively', () => {
      render(<RecipeForm initialRecipe={sampleRecipe} sessionId="sess_test" />);

      const tagInput = screen.getByPlaceholderText('+ Add tag (press Enter)');
      fireEvent.change(tagInput, { target: { value: 'Holiday' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });

      expect(screen.getByText('Holiday')).toBeInTheDocument();

      // Duplicate entry attempt: 'holiday'
      fireEvent.change(tagInput, { target: { value: 'holiday' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });

      const holidayTags = screen.getAllByText('Holiday');
      expect(holidayTags.length).toBe(1);
    });

    it('validates required fields before allowing save', async () => {
      render(<RecipeForm initialRecipe={sampleRecipe} sessionId="sess_test" />);

      const titleInput = screen.getByDisplayValue("Grandma's Cinnamon Rolls");
      fireEvent.change(titleInput, { target: { value: '' } });

      const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
      fireEvent.click(saveBtn);

      await waitFor(() => {
        expect(screen.getByText('Title is required')).toBeInTheDocument();
        expect(api.updateRecipe).not.toHaveBeenCalled();
      });
    });

    it('persists recipe updates directly to backend SQLite via PUT /api/recipes/{recipe_id}', async () => {
      vi.mocked(api.updateRecipe).mockResolvedValueOnce({
        ...sampleRecipe,
        title: "Grandma's Ultimate Cinnamon Rolls",
      });

      const onSaved = vi.fn();
      render(<RecipeForm initialRecipe={sampleRecipe} sessionId="sess_test" onSaved={onSaved} />);

      const titleInput = screen.getByDisplayValue("Grandma's Cinnamon Rolls");
      fireEvent.change(titleInput, { target: { value: "Grandma's Ultimate Cinnamon Rolls" } });

      const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
      fireEvent.click(saveBtn);

      await waitFor(() => {
        expect(api.updateRecipe).toHaveBeenCalledWith(
          'rec_001',
          expect.objectContaining({ title: "Grandma's Ultimate Cinnamon Rolls" }),
          'sess_test'
        );
        expect(onSaved).toHaveBeenCalled();
        expect(screen.getByText(/Saved to SQLite/i)).toBeInTheDocument();
      });
    });
  });

  describe('F3.1: CardViewerCanvas Image Lifecycle & Fallback', () => {
    it('displays fallback state when no card image is available', () => {
      render(
        <RecipeEditorView
          recipes={[{ ...sampleRecipe, card_ids: [], image_urls: [] }]}
          sessionId="sess_test"
          onRecipeUpdated={vi.fn()}
        />
      );

      expect(screen.getByText(/No Card Scan Image Available/i)).toBeInTheDocument();
      expect(screen.getByText(/No scanned card image is associated with this recipe/i)).toBeInTheDocument();
    });
  });

  describe('F3.2: Workflow Navigation Dialog on Last Recipe', () => {
    it('clicking Next on the last recipe triggers Go to export confirmation modal', async () => {
      const onNavigateToExport = vi.fn();
      render(
        <RecipeEditorView
          recipes={[sampleRecipe]}
          sessionId="sess_test"
          onRecipeUpdated={vi.fn()}
          onNavigateToExport={onNavigateToExport}
        />
      );

      const nextBtn = screen.getByTitle(/Finish Review & Go to Export/i);
      expect(nextBtn).not.toBeDisabled();
      fireEvent.click(nextBtn);

      expect(screen.getByText('Go to export?')).toBeInTheDocument();
      expect(screen.getByText(/You have reached the final recipe in the review queue/i)).toBeInTheDocument();

      // Click Cancel -> dialog closes and stays in RecipeEditorView
      const cancelBtn = screen.getByRole('button', { name: 'Cancel' });
      fireEvent.click(cancelBtn);

      await waitFor(() => {
        expect(screen.queryByText(/You have reached the final recipe in the review queue/i)).not.toBeInTheDocument();
        expect(onNavigateToExport).not.toHaveBeenCalled();
      });

      // Click Next again, then Yes -> triggers onNavigateToExport
      fireEvent.click(nextBtn);
      const yesBtn = screen.getByRole('button', { name: 'Yes' });
      fireEvent.click(yesBtn);

      expect(onNavigateToExport).toHaveBeenCalledTimes(1);
    });

    it('navigates sequentially between multiple recipes before reaching export modal', () => {
      const recipe2: RecipeSchema = {
        ...sampleRecipe,
        recipe_id: 'rec_002',
        title: 'Luscious Lemon Bars',
      };
      const onNavigateToExport = vi.fn();

      render(
        <RecipeEditorView
          recipes={[sampleRecipe, recipe2]}
          sessionId="sess_test"
          onRecipeUpdated={vi.fn()}
          onNavigateToExport={onNavigateToExport}
        />
      );

      expect(screen.getByText(/Recipe 1 of 2:/i)).toBeInTheDocument();
      const nextBtn = screen.getByTitle('Next Recipe');
      fireEvent.click(nextBtn);

      // Now at Recipe 2
      expect(screen.getByText(/Recipe 2 of 2:/i)).toBeInTheDocument();
      expect(screen.queryByText('Go to export?')).not.toBeInTheDocument();

      // Clicking Next on recipe 2 triggers modal
      const finishBtn = screen.getByTitle(/Finish Review & Go to Export/i);
      fireEvent.click(finishBtn);
      expect(screen.getByText('Go to export?')).toBeInTheDocument();
    });
  });

  describe('F3.3: Inline Tag Editing & State Sync', () => {
    it('supports adding tags via semicolon, comma, blur, and + Add button', () => {
      render(<RecipeForm initialRecipe={sampleRecipe} sessionId="sess_test" />);

      const tagInput = screen.getByPlaceholderText('+ Add tag (press Enter)');

      // Semicolon trigger
      fireEvent.change(tagInput, { target: { value: 'Vintage Treats;' } });
      fireEvent.keyDown(tagInput, { key: ';' });
      expect(screen.getByText('Vintage Treats')).toBeInTheDocument();

      // Comma trigger
      fireEvent.change(tagInput, { target: { value: 'Holiday Baking,' } });
      fireEvent.keyDown(tagInput, { key: ',' });
      expect(screen.getByText('Holiday Baking')).toBeInTheDocument();

      // Multi-tag delimited entry
      fireEvent.change(tagInput, { target: { value: 'Quick, Simple; Family Favorite' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });
      expect(screen.getByText('Quick')).toBeInTheDocument();
      expect(screen.getByText('Simple')).toBeInTheDocument();
      expect(screen.getByText('Family Favorite')).toBeInTheDocument();

      // Blur trigger
      fireEvent.change(tagInput, { target: { value: 'Comfort Food' } });
      fireEvent.blur(tagInput);
      expect(screen.getByText('Comfort Food')).toBeInTheDocument();

      // + Add button trigger
      fireEvent.change(tagInput, { target: { value: 'Sunday Supper' } });
      const addTagBtn = screen.getByTitle('Add tag');
      fireEvent.click(addTagBtn);
      expect(screen.getByText('Sunday Supper')).toBeInTheDocument();
    });

    it('supports inline editing of existing tags', () => {
      render(<RecipeForm initialRecipe={sampleRecipe} sessionId="sess_test" />);

      // Click on the Baking tag to start editing
      const bakingTag = screen.getByText('Baking');
      fireEvent.click(bakingTag);

      // Inline input appears
      const editInput = screen.getByDisplayValue('Baking');
      expect(editInput).toBeInTheDocument();

      // Modify tag to 'Artisan Baking' and press Enter
      fireEvent.change(editInput, { target: { value: 'Artisan Baking' } });
      fireEvent.keyDown(editInput, { key: 'Enter' });

      expect(screen.getByText('Artisan Baking')).toBeInTheDocument();
      expect(screen.queryByText('Baking')).not.toBeInTheDocument();
    });

    it('removes tags when clicking the remove button', () => {
      render(<RecipeForm initialRecipe={sampleRecipe} sessionId="sess_test" />);

      expect(screen.getByText('Dessert')).toBeInTheDocument();
      const removeDessertBtn = screen.getByTitle('Remove tag Dessert');
      fireEvent.click(removeDessertBtn);

      expect(screen.queryByText('Dessert')).not.toBeInTheDocument();
    });
  });

  describe('F4.1 & F4.2: Title Guessing and Story Preservation', () => {
    it('handles [GUESS] flag in recipe title and preserves description and source notes', () => {
      const guessedRecipe: RecipeSchema = {
        ...sampleRecipe,
        title: 'Old-Fashioned Apple Cobbler [GUESS]',
        description: 'Heirloom family recipe made every Thanksgiving by Aunt Clara since 1952.',
        source_notes: 'Grandma Clara handwritten card (stained ruled paper)',
      };

      render(<RecipeForm initialRecipe={guessedRecipe} sessionId="sess_test" />);

      expect(screen.getByDisplayValue('Old-Fashioned Apple Cobbler [GUESS]')).toBeInTheDocument();
      expect(screen.getByDisplayValue(/Heirloom family recipe made every Thanksgiving/i)).toBeInTheDocument();
      expect(screen.getByDisplayValue(/Grandma Clara handwritten card/i)).toBeInTheDocument();
    });
  });

  describe('RecipeEditorView split-screen', () => {
    it('renders card image viewer on left pane and form on right pane', () => {
      render(
        <RecipeEditorView
          recipes={[sampleRecipe]}
          sessionId="sess_test"
          onRecipeUpdated={vi.fn()}
        />
      );

      expect(screen.getByText(/Recipe 1 of 1/i)).toBeInTheDocument();
      expect(screen.getByText('Card 1 (Front)')).toBeInTheDocument();
      expect(screen.getByText('Card 2 (Back)')).toBeInTheDocument();
      expect(screen.getAllByDisplayValue("Grandma's Cinnamon Rolls").length).toBeGreaterThanOrEqual(1);
    });
  });
});
