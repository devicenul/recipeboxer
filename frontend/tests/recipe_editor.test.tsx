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
