import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import React from 'react';
import App from '../src/App';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    getStatus: vi.fn().mockResolvedValue({
      session_id: 'sess_adv_01',
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
      session_id: 'sess_adv_01',
      status: 'HITL_PENDING',
      proposed_pairs: [],
      unassigned_cards: [],
      total_proposals: 0,
      total_unassigned: 0,
    }),
    getCardImageUrl: vi.fn((id) => `/api/cards/${id}/image`),
  },
}));

let lastSSEOptions: any = null;

vi.mock('../src/hooks/useSSE', () => ({
  useSSE: (opts: any) => {
    lastSSEOptions = opts;
    return {
      isConnected: true,
      isReconnecting: false,
      reconnectCount: 0,
      lastEvent: null,
      reconnect: vi.fn(),
    };
  },
}));

describe('Milestone M1 Adversarial: Tab Navigation Stability & Reversion Prevention', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastSSEOptions = null;
  });

  it('maintains tab stability through rapid sequential tab switching (Ingest -> Review -> Export -> Ingest -> Export) under high-frequency SSE bombardment', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_stress_01',
        title: 'Adversarial Test Pie',
        ingredients: ['1 cup flour'],
        directions: ['Bake at 350F'],
        tags: ['Dessert'],
        card_ids: ['card_1.jpg'],
        image_urls: ['/api/cards/card_1.jpg/image'],
      },
    ]);

    render(<App />);

    // Initial completion triggers auto-transition to recipes
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_adv_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 10, candidates_tokens: 10, thinking_tokens: 0, total_tokens: 20 },
        elapsed_seconds: 5,
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Adversarial Test Pie')).toBeInTheDocument();
    });

    const getTabButtons = () => {
      return {
        ingest: screen.getAllByRole('button', { name: /1\. Ingestion/i })[0],
        hitl: screen.getAllByRole('button', { name: /2\. HITL Queue/i })[0],
        recipes: screen.getAllByRole('button', { name: /3\. Recipe Editor/i })[0],
        export: screen.getAllByRole('button', { name: /4\. Export/i })[0],
      };
    };

    // Helper to fire high frequency SSE events
    const fireSSEBurst = (count = 10) => {
      for (let i = 0; i < count; i++) {
        lastSSEOptions?.onProgress?.({
          session_id: 'sess_adv_01',
          status: 'COMPLETED',
          mode: 'parallel',
          progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
          token_usage: { prompt_tokens: 10 + i, candidates_tokens: 10, thinking_tokens: 0, total_tokens: 20 + i },
          elapsed_seconds: 5 + i * 0.1,
        });
        lastSSEOptions?.onStateChange?.({
          session_id: 'sess_adv_01',
          status: 'COMPLETED',
          mode: 'parallel',
          progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
          token_usage: { prompt_tokens: 10 + i, candidates_tokens: 10, thinking_tokens: 0, total_tokens: 20 + i },
          elapsed_seconds: 5 + i * 0.1,
        });
        lastSSEOptions?.onCompleted?.({
          session_id: 'sess_adv_01',
          status: 'COMPLETED',
          mode: 'parallel',
          progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
          token_usage: { prompt_tokens: 10 + i, candidates_tokens: 10, thinking_tokens: 0, total_tokens: 20 + i },
          elapsed_seconds: 5 + i * 0.1,
        });
      }
    };

    // Step 1: Switch to Ingest tab while SSE burst fires
    act(() => {
      fireEvent.click(getTabButtons().ingest);
      fireSSEBurst(5);
    });
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();
    expect(screen.queryByText('Adversarial Test Pie')).not.toBeInTheDocument();

    // Step 2: Switch to Review (HITL Queue) tab while SSE burst fires
    await act(async () => {
      fireEvent.click(getTabButtons().hitl);
      fireSSEBurst(5);
    });
    await waitFor(() => {
      expect(screen.getByText(/All Proposals Reviewed!/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Adversarial Test Pie')).not.toBeInTheDocument();

    // Step 3: Switch to Export tab while SSE burst fires
    act(() => {
      fireEvent.click(getTabButtons().export);
      fireSSEBurst(10);
    });
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();
    expect(screen.queryByText('Adversarial Test Pie')).not.toBeInTheDocument();

    // Step 4: Switch to Ingest tab while SSE burst fires
    act(() => {
      fireEvent.click(getTabButtons().ingest);
      fireSSEBurst(10);
    });
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();
    expect(screen.queryByText(/Multi-Format Recipe Export/i)).not.toBeInTheDocument();

    // Step 5: Switch back to Export tab while heavy 50-event SSE burst fires
    act(() => {
      fireEvent.click(getTabButtons().export);
      fireSSEBurst(50);
    });
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();
    expect(screen.queryByText('Adversarial Test Pie')).not.toBeInTheDocument();
    expect(screen.queryByText(/New Scans Ingestion/i)).not.toBeInTheDocument();
  });

  it('preserves selected recipe index in Recipe Editor when background SSE updates arrive', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_01',
        title: 'Recipe One (Appetizer)',
        ingredients: ['Appetizer ingredients'],
        directions: ['Appetizer directions'],
        tags: ['Appetizer'],
        card_ids: ['card_1.jpg'],
        image_urls: ['/api/cards/card_1.jpg/image'],
      },
      {
        recipe_id: 'rec_02',
        title: 'Recipe Two (Main Course)',
        ingredients: ['Main ingredients'],
        directions: ['Main directions'],
        tags: ['Dinner'],
        card_ids: ['card_2.jpg'],
        image_urls: ['/api/cards/card_2.jpg/image'],
      },
      {
        recipe_id: 'rec_03',
        title: 'Recipe Three (Dessert)',
        ingredients: ['Dessert ingredients'],
        directions: ['Dessert directions'],
        tags: ['Dessert'],
        card_ids: ['card_3.jpg'],
        image_urls: ['/api/cards/card_3.jpg/image'],
      },
    ]);

    render(<App />);

    // Complete session
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_adv_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 3, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 50, candidates_tokens: 25, thinking_tokens: 0, total_tokens: 75 },
        elapsed_seconds: 5,
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Recipe One (Appetizer)')).toBeInTheDocument();
    });

    // Navigate to Recipe 2
    const nextBtn = screen.getByTitle('Next Recipe');
    fireEvent.click(nextBtn);
    expect(screen.getByText('Recipe Two (Main Course)')).toBeInTheDocument();

    // Navigate to Recipe 3
    fireEvent.click(nextBtn);
    expect(screen.getByText('Recipe Three (Dessert)')).toBeInTheDocument();

    const recipeSelect = screen.getByRole('combobox') as HTMLSelectElement;
    expect(recipeSelect.value).toBe('2');

    // Background SSE events fire with COMPLETED status and recipe extraction updates
    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_adv_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 3, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 60, candidates_tokens: 30, thinking_tokens: 0, total_tokens: 90 },
        elapsed_seconds: 8,
      });
      lastSSEOptions?.onRecipeExtracted?.({
        recipe_id: 'rec_02',
        title: 'Recipe Two (Updated Main Course)',
        ingredients: ['Updated ingredients'],
        directions: ['Updated directions'],
        tags: ['Dinner', 'Special'],
        card_ids: ['card_2.jpg'],
        image_urls: ['/api/cards/card_2.jpg/image'],
      });
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_adv_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 3, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 70, candidates_tokens: 35, thinking_tokens: 0, total_tokens: 105 },
        elapsed_seconds: 10,
      });
    });

    // Recipe index must still be on Recipe Three (Dessert), NOT reset to Recipe One (index 0)
    expect(recipeSelect.value).toBe('2');
    expect(screen.getByText('Recipe 3 of 3:')).toBeInTheDocument();
  });

  it('handles edge-case SSE payloads with null, none, or empty session_id gracefully', async () => {
    render(<App />);

    // Null session_id in payload
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: null,
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 0, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 0, candidates_tokens: 0, thinking_tokens: 0, total_tokens: 0 },
        elapsed_seconds: 1,
      });
    });

    // Should not throw and should handle gracefully
    await waitFor(() => {
      expect(screen.getByText(/No Extracted Recipes Yet/i)).toBeInTheDocument();
    });

    // Switch to Export tab
    const exportTabs = screen.getAllByRole('button', { name: /4\. Export/i });
    fireEvent.click(exportTabs[0]);
    expect(screen.getByText(/No Recipes Available for Export/i)).toBeInTheDocument();

    // Payload with session_id: "none"
    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'none',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 0, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 0, candidates_tokens: 0, thinking_tokens: 0, total_tokens: 0 },
        elapsed_seconds: 2,
      });
    });

    // Remains on Export tab
    expect(screen.getByText(/No Recipes Available for Export/i)).toBeInTheDocument();
  });

  it('correctly handles cancelling from the Proceed to Export modal without reverting active view or selected recipe', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_01',
        title: 'Chocolate Chip Cookies',
        ingredients: ['Flour', 'Butter', 'Chocolate Chips'],
        directions: ['Mix and bake.'],
        tags: ['Baking'],
        card_ids: ['cookie_card.jpg'],
        image_urls: ['/api/cards/cookie_card.jpg/image'],
      },
    ]);

    render(<App />);

    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_adv_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 10, candidates_tokens: 5, thinking_tokens: 0, total_tokens: 15 },
        elapsed_seconds: 2,
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Chocolate Chip Cookies')).toBeInTheDocument();
    });

    // Click finish review button on single recipe
    const finishBtn = screen.getByTitle('Finish Review & Go to Export');
    fireEvent.click(finishBtn);

    expect(screen.getByText('Go to export?')).toBeInTheDocument();

    // Click Cancel in modal
    const cancelBtn = screen.getByRole('button', { name: /Cancel/i });
    fireEvent.click(cancelBtn);

    // Modal closes and remains on Recipe Editor with the current recipe
    expect(screen.queryByText('Go to export?')).not.toBeInTheDocument();
    expect(screen.getByText('Chocolate Chip Cookies')).toBeInTheDocument();

    // Re-firing SSE progress does not cause instability
    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_adv_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 10, candidates_tokens: 5, thinking_tokens: 0, total_tokens: 15 },
        elapsed_seconds: 4,
      });
    });

    expect(screen.getByText('Chocolate Chip Cookies')).toBeInTheDocument();
  });
});
