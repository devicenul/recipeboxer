import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import React from 'react';
import { RecipeForm } from '../src/components/recipe/RecipeForm';
import { RecipeEditorView } from '../src/components/recipe/RecipeEditorView';
import { CardViewerCanvas } from '../src/components/viewer/CardViewerCanvas';
import { TagManager } from '../src/components/recipe/TagManager';
import { RecipeSchema } from '../src/types/recipe';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    updateRecipe: vi.fn(),
    getCardImageUrl: vi.fn((id, sid) => (sid ? `/api/cards/${id}/image?session_id=${sid}` : `/api/cards/${id}/image`)),
  },
}));

describe('Milestone 3 Empirical Challenger: Recipe Editor UX & Tag Intelligence', () => {
  const sampleRecipe1: RecipeSchema = {
    recipe_id: 'rec_101',
    title: 'Aunt Clara Apple Crisp [GUESS]',
    description: 'Aunt Clara made this in 1942 for church potlucks in Ohio.',
    yield_servings: '8 servings',
    prep_time: '20 mins',
    cook_time: '45 mins',
    total_time: '1 hr 5 mins',
    ingredients: ['6 cups sliced apples', '1 cup rolled oats', '1/2 cup butter, melted'],
    directions: ['1. Layer sliced apples in 9x13 dish.', '2. Top with oat crumble and bake at 350F.'],
    tags: ['Dessert', 'Baking', 'Autumn'],
    source_notes: "Grandma's 1942 recipe box (Ohio church potluck)",
    card_ids: ['card_101_front.jpg', 'card_101_back.jpg'],
    image_urls: ['/api/cards/card_101_front.jpg/image', '/api/cards/card_101_back.jpg/image'],
  };

  const sampleRecipe2: RecipeSchema = {
    recipe_id: 'rec_102',
    title: 'Mom Butterscotch Fudge',
    description: 'Quick no-bake holiday candy.',
    yield_servings: '24 pieces',
    prep_time: '10 mins',
    cook_time: '5 mins',
    total_time: '15 mins',
    ingredients: ['2 cups butterscotch chips', '1 can sweetened condensed milk'],
    directions: ['1. Melt chips with milk.', '2. Spread into square pan and cool.'],
    tags: ['Candy', 'Holiday', 'Quick'],
    source_notes: 'Handwritten 3x5 index card',
    card_ids: ['card_102_single.jpg'],
    image_urls: ['/api/cards/card_102_single.jpg/image'],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  /* -------------------------------------------------------------------------- */
  /* Area 1: Card image loading errors, rapid switching, recovery, fallbacks    */
  /* -------------------------------------------------------------------------- */
  describe('Area 1: Card Image Loading, Error States & Rapid Switching', () => {
    it('handles image loading error and recovers when imageUrl changes to valid', async () => {
      const transform = { scale: 1, translateX: 0, translateY: 0, rotation: 0 };
      const filters = { brightness: 100, contrast: 100, invert: false };

      const { rerender } = render(
        <CardViewerCanvas
          imageUrl="/api/cards/broken_card.jpg/image"
          cardId="broken_card.jpg"
          transform={transform}
          filters={filters}
        />
      );

      // Initially shows image with loading state
      const img = screen.getByRole('img');
      expect(img).toHaveAttribute('src', '/api/cards/broken_card.jpg/image');

      // Trigger image error
      fireEvent.error(img);

      // Should display error message
      expect(screen.getByText('Failed to load card scan image')).toBeInTheDocument();

      // Rerender with new card image URL -> should clear error and show new image
      rerender(
        <CardViewerCanvas
          imageUrl="/api/cards/valid_card.jpg/image"
          cardId="valid_card.jpg"
          transform={transform}
          filters={filters}
        />
      );

      expect(screen.queryByText('Failed to load card scan image')).not.toBeInTheDocument();
      const newImg = screen.getByRole('img');
      expect(newImg).toHaveAttribute('src', '/api/cards/valid_card.jpg/image');
    });

    it('handles rapid card switching in RecipeEditorView multi-card carousel', async () => {
      render(
        <RecipeEditorView
          recipes={[sampleRecipe1]}
          sessionId="sess_test"
          onRecipeUpdated={vi.fn()}
        />
      );

      // Shows Card 1 and Card 2 tab buttons
      const card1Tab = screen.getByRole('button', { name: /Card 1 \(Front\)/i });
      const card2Tab = screen.getByRole('button', { name: /Card 2 \(Back\)/i });

      expect(card1Tab).toBeInTheDocument();
      expect(card2Tab).toBeInTheDocument();

      // Initial active card is Card 1
      let img = screen.getByRole('img');
      expect(img).toHaveAttribute('src', '/api/cards/card_101_front.jpg/image?session_id=sess_test');

      // Rapidly switch between card tabs
      fireEvent.click(card2Tab);
      img = screen.getByRole('img');
      expect(img).toHaveAttribute('src', '/api/cards/card_101_back.jpg/image?session_id=sess_test');

      fireEvent.click(card1Tab);
      img = screen.getByRole('img');
      expect(img).toHaveAttribute('src', '/api/cards/card_101_front.jpg/image?session_id=sess_test');

      fireEvent.click(card2Tab);
      img = screen.getByRole('img');
      expect(img).toHaveAttribute('src', '/api/cards/card_101_back.jpg/image?session_id=sess_test');
    });

    it('correctly handles recipes with image_urls only and empty card_ids', () => {
      const recipeWithImageOnly: RecipeSchema = {
        ...sampleRecipe2,
        card_ids: [],
        image_urls: ['/uploads/scans/direct_card.png'],
      };

      render(
        <RecipeEditorView
          recipes={[recipeWithImageOnly]}
          sessionId="sess_test"
          onRecipeUpdated={vi.fn()}
        />
      );

      const img = screen.getByRole('img');
      expect(img).toHaveAttribute('src', '/uploads/scans/direct_card.png');
    });

    it('renders empty standalone placeholder state when isStandalonePlaceholder is true', () => {
      const transform = { scale: 1, translateX: 0, translateY: 0, rotation: 0 };
      const filters = { brightness: 100, contrast: 100, invert: false };

      render(
        <CardViewerCanvas
          isStandalonePlaceholder={true}
          transform={transform}
          filters={filters}
        />
      );

      expect(screen.getByText('Standalone Single-Sided Card')).toBeInTheDocument();
      expect(screen.queryByRole('img')).not.toBeInTheDocument();
    });
  });

  /* -------------------------------------------------------------------------- */
  /* Area 2: Workflow Navigation Modal on Last Recipe (Cancel vs Yes)           */
  /* -------------------------------------------------------------------------- */
  describe('Area 2: Workflow Navigation Modal on Last Recipe (Cancel vs Yes)', () => {
    it('does NOT trigger modal on intermediate recipes; triggers only on last recipe', async () => {
      const onNavigateToExport = vi.fn();
      const recipes = [sampleRecipe1, sampleRecipe2];

      render(
        <RecipeEditorView
          recipes={recipes}
          sessionId="sess_test"
          onRecipeUpdated={vi.fn()}
          onNavigateToExport={onNavigateToExport}
        />
      );

      // Recipe 1 of 2: clicking Next advances to Recipe 2 without modal
      expect(screen.getByText('Recipe 1 of 2:')).toBeInTheDocument();
      const nextBtn = screen.getByTitle('Next Recipe');
      fireEvent.click(nextBtn);

      expect(screen.getByText('Recipe 2 of 2:')).toBeInTheDocument();
      expect(screen.queryByText('Go to export?')).not.toBeInTheDocument();
      expect(onNavigateToExport).not.toHaveBeenCalled();

      // Recipe 2 of 2 (last recipe): clicking Next opens modal
      const finishBtn = screen.getByTitle('Finish Review & Go to Export');
      fireEvent.click(finishBtn);

      expect(screen.getByText('Go to export?')).toBeInTheDocument();
      expect(screen.getByText(/You have reached the final recipe in the review queue/i)).toBeInTheDocument();
    });

    it('Cancel keeps user on the recipe editor page and allows subsequent Next click', async () => {
      const onNavigateToExport = vi.fn();
      render(
        <RecipeEditorView
          recipes={[sampleRecipe1]}
          sessionId="sess_test"
          onRecipeUpdated={vi.fn()}
          onNavigateToExport={onNavigateToExport}
        />
      );

      const finishBtn = screen.getByTitle('Finish Review & Go to Export');
      fireEvent.click(finishBtn);

      expect(screen.getByText('Go to export?')).toBeInTheDocument();

      // Click Cancel
      const cancelBtn = screen.getByRole('button', { name: 'Cancel' });
      fireEvent.click(cancelBtn);

      await waitFor(() => {
        expect(screen.queryByText('Go to export?')).not.toBeInTheDocument();
      });
      expect(onNavigateToExport).not.toHaveBeenCalled();

      // Verify user can click Next again
      fireEvent.click(finishBtn);
      expect(screen.getByText('Go to export?')).toBeInTheDocument();

      // Click Yes
      const yesBtn = screen.getByRole('button', { name: 'Yes' });
      fireEvent.click(yesBtn);

      expect(onNavigateToExport).toHaveBeenCalledTimes(1);
    });

    it('renders empty fallback when recipes array is empty', () => {
      render(
        <RecipeEditorView
          recipes={[]}
          sessionId="sess_test"
          onRecipeUpdated={vi.fn()}
        />
      );

      expect(screen.getByText('No Extracted Recipes Found')).toBeInTheDocument();
    });
  });

  /* -------------------------------------------------------------------------- */
  /* Area 3: Inline Tag Editing Edge Cases                                      */
  /* -------------------------------------------------------------------------- */
  describe('Area 3: Inline Tag Editing Edge Cases & State Sync', () => {
    it('handles multi-delimiter input with commas, semicolons, extra whitespace, and empty tokens', () => {
      const onAddTag = vi.fn();
      render(
        <TagManager
          tags={['ExistingTag']}
          onAddTag={onAddTag}
          onRemoveTag={vi.fn()}
        />
      );

      const input = screen.getByPlaceholderText('+ Add tag (press Enter)');
      fireEvent.change(input, { target: { value: '  Gluten-Free , ,  ; Quick Supper ; ; Vintage 1950s  ' } });
      fireEvent.keyDown(input, { key: 'Enter' });

      expect(onAddTag).toHaveBeenCalledTimes(3);
      expect(onAddTag).toHaveBeenNthCalledWith(1, 'Gluten-Free');
      expect(onAddTag).toHaveBeenNthCalledWith(2, 'Quick Supper');
      expect(onAddTag).toHaveBeenNthCalledWith(3, 'Vintage 1950s');
    });

    it('supports special characters and punctuation in tags (quotes, hyphens, ampersands, accents)', () => {
      const specialTags = ["Grandma's Favorite", '10-Minute', 'Sweet & Savory', 'Entrée (Dinner)', '⭐ Top Rated'];
      const onRemoveTag = vi.fn();

      render(
        <TagManager
          tags={specialTags}
          onAddTag={vi.fn()}
          onRemoveTag={onRemoveTag}
        />
      );

      specialTags.forEach((tag) => {
        expect(screen.getByText(tag)).toBeInTheDocument();
      });

      // Remove the tag with quotes
      const removeBtn = screen.getByTitle("Remove tag Grandma's Favorite");
      fireEvent.click(removeBtn);
      expect(onRemoveTag).toHaveBeenCalledWith("Grandma's Favorite");
    });

    it('handles inline tag editing commit on Enter, Check icon, blur, and Escape cancel', () => {
      const onEditTag = vi.fn();
      const onRemoveTag = vi.fn();

      const { rerender } = render(
        <TagManager
          tags={['OldTag', 'StayTag']}
          onAddTag={vi.fn()}
          onRemoveTag={onRemoveTag}
          onEditTag={onEditTag}
        />
      );

      // 1. Edit via Enter
      const oldTagSpan = screen.getByText('OldTag');
      fireEvent.click(oldTagSpan);

      let editInput = screen.getByDisplayValue('OldTag');
      fireEvent.change(editInput, { target: { value: 'NewTag' } });
      fireEvent.keyDown(editInput, { key: 'Enter' });

      expect(onEditTag).toHaveBeenCalledWith('OldTag', 'NewTag');

      // 2. Escape cancels without emitting onEditTag
      rerender(
        <TagManager
          tags={['SecondTag', 'StayTag']}
          onAddTag={vi.fn()}
          onRemoveTag={onRemoveTag}
          onEditTag={onEditTag}
        />
      );

      const secondTagSpan = screen.getByText('SecondTag');
      fireEvent.click(secondTagSpan);

      editInput = screen.getByDisplayValue('SecondTag');
      fireEvent.change(editInput, { target: { value: 'CancelledTag' } });
      fireEvent.keyDown(editInput, { key: 'Escape' });

      expect(onEditTag).not.toHaveBeenCalledWith('SecondTag', 'CancelledTag');
      expect(screen.getByText('SecondTag')).toBeInTheDocument();

      // 3. Editing to empty string removes tag
      fireEvent.click(screen.getByText('SecondTag'));
      editInput = screen.getByDisplayValue('SecondTag');
      fireEvent.change(editInput, { target: { value: '   ' } });
      fireEvent.keyDown(editInput, { key: 'Enter' });

      expect(onRemoveTag).toHaveBeenCalledWith('SecondTag');
    });

    it('RecipeForm full cycle: adds tag, deduplicates case-insensitively, edits tag, and saves dirty state', async () => {
      vi.mocked(api.updateRecipe).mockResolvedValueOnce({
        ...sampleRecipe1,
        tags: ['Dessert', 'Baking', 'Autumn', 'Sunday Dinner'],
      });

      const onSaved = vi.fn();
      render(
        <RecipeForm
          initialRecipe={sampleRecipe1}
          sessionId="sess_test"
          onSaved={onSaved}
        />
      );

      // Verify initial tags
      expect(screen.getByText('Dessert')).toBeInTheDocument();
      expect(screen.getByText('Baking')).toBeInTheDocument();
      expect(screen.getByText('Autumn')).toBeInTheDocument();

      // Attempt duplicate addition in different case: 'dessert'
      const tagInput = screen.getByPlaceholderText('+ Add tag (press Enter)');
      fireEvent.change(tagInput, { target: { value: 'dessert' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });

      // Should still have exactly one Dessert tag
      const dessertTags = screen.getAllByText(/dessert/i);
      expect(dessertTags.length).toBe(1);

      // Add a valid new tag: 'Sunday Dinner'
      fireEvent.change(tagInput, { target: { value: 'Sunday Dinner' } });
      fireEvent.keyDown(tagInput, { key: 'Enter' });
      expect(screen.getByText('Sunday Dinner')).toBeInTheDocument();

      // Verify dirty status indicator
      expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();

      // Save form
      const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
      fireEvent.click(saveBtn);

      await waitFor(() => {
        expect(api.updateRecipe).toHaveBeenCalledWith(
          'rec_101',
          expect.objectContaining({
            tags: ['Dessert', 'Baking', 'Autumn', 'Sunday Dinner'],
          }),
          'sess_test'
        );
        expect(onSaved).toHaveBeenCalled();
        expect(screen.getByText('Saved to SQLite')).toBeInTheDocument();
      });
    });
  });
});
