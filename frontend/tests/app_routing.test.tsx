import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import React from 'react';
import App from '../src/App';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    getStatus: vi.fn().mockResolvedValue({
      session_id: 'sess_app_01',
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
      session_id: 'sess_app_01',
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

describe('App Layout & Routing Integration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastSSEOptions = null;
  });

  it('renders Header, Navigation tabs, and switches views cleanly', async () => {
    render(<App />);

    // Branding
    expect(screen.getByText('RecipeBoxer')).toBeInTheDocument();

    // Default tab: Ingestion
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();

    // Switch to HITL Queue tab
    const hitlTabs = screen.getAllByRole('button', { name: /2\. HITL Queue/i });
    fireEvent.click(hitlTabs[0]);
    expect(screen.getByText(/No Active Ingestion Session/i)).toBeInTheDocument();

    // Switch to Recipe Editor tab
    const recipesTabs = screen.getAllByRole('button', { name: /3\. Recipe Editor/i });
    fireEvent.click(recipesTabs[0]);
    expect(screen.getByText(/No Extracted Recipes Yet/i)).toBeInTheDocument();

    // Switch to Export tab
    const exportTabs = screen.getAllByRole('button', { name: /4\. Export/i });
    fireEvent.click(exportTabs[0]);
    expect(screen.getByText(/No Recipes Available for Export/i)).toBeInTheDocument();
  });

  it('automatically transitions from Ingestion to Recipe Editor tab when pipeline reaches COMPLETED state via onCompleted', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_auto_01',
        title: 'Freshly Extracted Cinnamon Cake',
        ingredients: ['2 cups flour', '1 tsp cinnamon'],
        directions: ['Mix and bake.'],
        tags: ['Dessert'],
        card_ids: ['card_1.jpg'],
        image_urls: ['/api/cards/card_1.jpg/image'],
      },
    ]);

    render(<App />);

    // Starts on Ingestion tab
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();

    // Trigger SSE onCompleted
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: {
          total_scans: 2,
          fingerprinted: 2,
          proposals_generated: 1,
          approved_recipes: 1,
          recipes_extracted: 1,
          current_stage: 'Completed',
          percent_complete: 100,
        },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 12,
      });
    });

    // Verify auto-transitioned to Recipe Editor
    await waitFor(() => {
      expect(api.listRecipes).toHaveBeenCalled();
      expect(screen.getByText('Freshly Extracted Cinnamon Cake')).toBeInTheDocument();
    });
  });

  it('automatically transitions to Recipe Editor when onStateChange receives COMPLETED status', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([]);

    render(<App />);
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();

    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: {
          total_scans: 4,
          fingerprinted: 4,
          proposals_generated: 2,
          approved_recipes: 2,
          recipes_extracted: 2,
          current_stage: 'Completed',
          percent_complete: 100,
        },
        token_usage: { prompt_tokens: 200, candidates_tokens: 100, thinking_tokens: 0, total_tokens: 300 },
        elapsed_seconds: 20,
      });
    });

    await waitFor(() => {
      expect(screen.getByText(/No Extracted Recipes Yet/i)).toBeInTheDocument();
    });
  });

  it('automatically transitions to Recipe Editor when onProgress receives COMPLETED status', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([]);

    render(<App />);
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();

    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: {
          total_scans: 1,
          fingerprinted: 1,
          proposals_generated: 1,
          approved_recipes: 1,
          recipes_extracted: 1,
          current_stage: 'Completed',
          percent_complete: 100,
        },
        token_usage: { prompt_tokens: 50, candidates_tokens: 25, thinking_tokens: 0, total_tokens: 75 },
        elapsed_seconds: 5,
      });
    });

    await waitFor(() => {
      expect(screen.getByText(/No Extracted Recipes Yet/i)).toBeInTheDocument();
    });
  });
});
