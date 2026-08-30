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

  it('retains user navigation to Export tab and does not revert on subsequent SSE completion / heartbeat events', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_auto_02',
        title: 'Heirloom Apple Pie',
        ingredients: ['6 apples', '1 cup sugar'],
        directions: ['Peel and bake.'],
        tags: ['Baking'],
        card_ids: ['card_pie.jpg'],
        image_urls: ['/api/cards/card_pie.jpg/image'],
      },
    ]);

    render(<App />);

    // 1. Trigger initial completion -> transitions to Recipe Editor
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
        elapsed_seconds: 10,
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Heirloom Apple Pie')).toBeInTheDocument();
    });

    // 2. User navigates to Export tab
    const exportTabs = screen.getAllByRole('button', { name: /4\. Export/i });
    fireEvent.click(exportTabs[0]);

    // Verify on Export view
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // 3. Background SSE reconnect / heartbeat events fire with COMPLETED status
    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 15,
      });
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 16,
      });
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 17,
      });
    });

    // 4. Assert active view is STILL Export, not reverted to Recipe Editor
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();
    expect(screen.queryByText('Heirloom Apple Pie')).not.toBeInTheDocument();
  });

  it('retains user navigation to Ingest and HITL tabs without auto-reverting on SSE reconnects', async () => {
    render(<App />);

    // Complete session
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 50, candidates_tokens: 25, thinking_tokens: 0, total_tokens: 75 },
        elapsed_seconds: 5,
      });
    });

    await waitFor(() => {
      expect(screen.getByText(/No Extracted Recipes Yet/i)).toBeInTheDocument();
    });

    // Switch to Ingest tab
    const ingestTabs = screen.getAllByRole('button', { name: /1\. Ingestion/i });
    fireEvent.click(ingestTabs[0]);
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();

    // Fire simulated SSE reconnect / completed events
    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 50, candidates_tokens: 25, thinking_tokens: 0, total_tokens: 75 },
        elapsed_seconds: 8,
      });
    });

    // Remains on Ingest tab
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();

    // Switch to HITL tab
    const hitlTabs = screen.getAllByRole('button', { name: /2\. HITL Queue/i });
    fireEvent.click(hitlTabs[0]);
    await waitFor(() => {
      expect(screen.getByText(/All Proposals Reviewed!/i)).toBeInTheDocument();
    });

    // Fire simulated SSE state change event
    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 50, candidates_tokens: 25, thinking_tokens: 0, total_tokens: 75 },
        elapsed_seconds: 10,
      });
    });

    // Remains on HITL tab
    expect(screen.getByText(/All Proposals Reviewed!/i)).toBeInTheDocument();
  });

  it('proceeds to export from Recipe Editor confirmation modal and stays on export view despite SSE reconnections', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_01',
        title: 'First Recipe',
        ingredients: ['item 1'],
        directions: ['step 1'],
        tags: [],
        card_ids: ['c1.jpg'],
        image_urls: ['/api/cards/c1.jpg/image'],
      },
      {
        recipe_id: 'rec_02',
        title: 'Second Recipe',
        ingredients: ['item 2'],
        directions: ['step 2'],
        tags: [],
        card_ids: ['c2.jpg'],
        image_urls: ['/api/cards/c2.jpg/image'],
      },
    ]);

    render(<App />);

    // Complete session -> transitions to Recipe Editor
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 10,
      });
    });

    await waitFor(() => {
      expect(screen.getByText('First Recipe')).toBeInTheDocument();
    });

    // Navigate to second recipe
    const nextBtn = screen.getByTitle('Next Recipe');
    fireEvent.click(nextBtn);
    expect(screen.getByText('Second Recipe')).toBeInTheDocument();

    // Click next button on final recipe -> opens "Go to export?" confirmation modal
    const finishBtn = screen.getByTitle('Finish Review & Go to Export');
    fireEvent.click(finishBtn);

    expect(screen.getByText('Go to export?')).toBeInTheDocument();

    // Confirm navigation to export
    const proceedBtn = screen.getByRole('button', { name: /Yes/i });
    fireEvent.click(proceedBtn);

    // Verify navigated to Export view
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // Fire simulated SSE reconnect cycle
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 12,
      });
    });

    // View remains on Export view and does not snap back to Recipe 1
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();
    expect(screen.queryByText('First Recipe')).not.toBeInTheDocument();
  });

  it('resets completion tracker when session transitions to a non-completed state and re-triggers single transition on next completion', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_reset_01',
        title: 'Reset Test Recipe',
        ingredients: ['ingredient 1'],
        directions: ['direction 1'],
        tags: [],
        card_ids: ['c1.jpg'],
        image_urls: ['/api/cards/c1.jpg/image'],
      },
    ]);

    render(<App />);

    // 1. Initial completion -> transitions to Recipe Editor
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 50, candidates_tokens: 25, thinking_tokens: 0, total_tokens: 75 },
        elapsed_seconds: 5,
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Reset Test Recipe')).toBeInTheDocument();
    });

    // 2. User switches to Export tab
    const exportTabs = screen.getAllByRole('button', { name: /4\. Export/i });
    fireEvent.click(exportTabs[0]);
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // 3. Session restarts / runs new job (status changes to RUNNING)
    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_app_01',
        status: 'RUNNING',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 20, current_stage: 'Fingerprinting' },
        token_usage: { prompt_tokens: 60, candidates_tokens: 30, thinking_tokens: 0, total_tokens: 90 },
        elapsed_seconds: 15,
      });
    });

    // Still on export while running
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // 4. Session completes again -> auto-transition fires for this new completion run
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 25,
      });
    });

    // Auto-transitioned to Recipe Editor for new completion
    await waitFor(() => {
      expect(screen.getByText('Reset Test Recipe')).toBeInTheDocument();
    });

    // 5. User switches back to Export
    fireEvent.click(exportTabs[0]);
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // 6. Heartbeat / reconnect does not revert again
    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_app_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 28,
      });
    });

    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();
  });

  it('handles multiple distinct sessions cleanly with single-fire completion per session', async () => {
    vi.mocked(api.listRecipes).mockImplementation(async (sid) => {
      if (sid === 'sess_02') {
        return [
          {
            recipe_id: 'rec_sess_02',
            title: 'Session 2 Recipe',
            ingredients: ['s2 ing'],
            directions: ['s2 dir'],
            tags: [],
            card_ids: ['c_s2.jpg'],
            image_urls: ['/api/cards/c_s2.jpg/image'],
          },
        ];
      }
      return [
        {
          recipe_id: 'rec_sess_01',
          title: 'Session 1 Recipe',
          ingredients: ['s1 ing'],
          directions: ['s1 dir'],
          tags: [],
          card_ids: ['c_s1.jpg'],
          image_urls: ['/api/cards/c_s1.jpg/image'],
        },
      ];
    });

    render(<App />);

    // Complete Session 1
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 50, candidates_tokens: 25, thinking_tokens: 0, total_tokens: 75 },
        elapsed_seconds: 5,
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Session 1 Recipe')).toBeInTheDocument();
    });

    // Switch to Export tab
    const exportTabs = screen.getAllByRole('button', { name: /4\. Export/i });
    fireEvent.click(exportTabs[0]);
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // Session 1 heartbeat -> does NOT revert
    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_01',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 50, candidates_tokens: 25, thinking_tokens: 0, total_tokens: 75 },
        elapsed_seconds: 8,
      });
    });
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // Session 2 completes -> auto-transitions for Session 2
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_02',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 50, candidates_tokens: 25, thinking_tokens: 0, total_tokens: 75 },
        elapsed_seconds: 12,
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Session 2 Recipe')).toBeInTheDocument();
    });

    // Switch to Ingestion tab
    const ingestTabs = screen.getAllByRole('button', { name: /1\. Ingestion/i });
    await act(async () => {
      fireEvent.click(ingestTabs[0]);
    });
    await waitFor(() => {
      expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();
    });

    // Session 2 heartbeat -> does NOT revert
    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_02',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 50, candidates_tokens: 25, thinking_tokens: 0, total_tokens: 75 },
        elapsed_seconds: 15,
      });
    });
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();
  });
});

