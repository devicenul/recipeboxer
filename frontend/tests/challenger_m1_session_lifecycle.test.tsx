import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import React from 'react';
import App from '../src/App';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    getStatus: vi.fn().mockResolvedValue({
      session_id: 'sess_init',
      status: 'IDLE',
      mode: 'parallel',
      progress: {
        total_scans: 0,
        fingerprinted: 0,
        proposals_generated: 0,
        approved_recipes: 0,
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
      session_id: 'sess_init',
      status: 'HITL_PENDING',
      proposed_pairs: [],
      unassigned_cards: [],
      total_proposals: 0,
      total_unassigned: 0,
    }),
    getCardImageUrl: vi.fn((id) => `/api/cards/${id}/image`),
    listSourceProviders: vi.fn().mockResolvedValue([]),
    listSourceContainers: vi.fn().mockResolvedValue({ containers: [] }),
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

describe('Empirical Challenger M1: Session Lifecycle & Pipeline Completion Transitions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    lastSSEOptions = null;
  });

  // ==========================================================================
  // SCENARIO 1: MULTI-SESSION CONTEXT SWITCHING & COMPLETION ISOLATION
  // ==========================================================================
  it('handles multi-session switching (completing session A, switching to session B, completing session B) without cross-session reversion', async () => {
    vi.mocked(api.listRecipes).mockImplementation(async (sid) => {
      if (sid === 'sess_alpha') {
        return [
          {
            recipe_id: 'rec_alpha_01',
            title: 'Alpha Grandma Pie',
            ingredients: ['Apples', 'Flour'],
            directions: ['Bake at 350F'],
            tags: ['Baking'],
            card_ids: ['card_alpha.jpg'],
            image_urls: ['/api/cards/card_alpha.jpg/image'],
          },
        ];
      }
      if (sid === 'sess_beta') {
        return [
          {
            recipe_id: 'rec_beta_01',
            title: 'Beta Smoked Brisket',
            ingredients: ['Brisket', 'Rub'],
            directions: ['Smoke 12 hours'],
            tags: ['BBQ'],
            card_ids: ['card_beta.jpg'],
            image_urls: ['/api/cards/card_beta.jpg/image'],
          },
        ];
      }
      return [];
    });

    render(<App />);

    // Initial state: on Ingestion tab
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();

    // 1. Session Alpha completes
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_alpha',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: {
          total_scans: 2,
          fingerprinted: 2,
          proposals_generated: 1,
          approved_recipes: 1,
          approved_pairs: 1,
          recipes_extracted: 1,
          current_stage: 'Completed',
          percent_complete: 100,
        },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 10,
      });
    });

    // Auto-transition to Recipe Editor for sess_alpha
    await waitFor(() => {
      expect(screen.getByText('Alpha Grandma Pie')).toBeInTheDocument();
    });

    // 2. User manually switches to Export tab
    const exportTabs = screen.getAllByRole('button', { name: /4\. Export/i });
    fireEvent.click(exportTabs[0]);
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // 3. Heartbeats from Session Alpha arrive
    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_alpha',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 15,
      });
    });

    // Tab must remain on Export
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // 4. Session Beta starts scanning (user is still on Export)
    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_beta',
        status: 'SCANNING',
        mode: 'parallel',
        progress: { total_scans: 4, percent_complete: 25, current_stage: 'Fingerprinting' },
        token_usage: { prompt_tokens: 50, candidates_tokens: 20, thinking_tokens: 0, total_tokens: 70 },
        elapsed_seconds: 5,
      });
    });

    // User is still on Export view while Beta is running
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // 5. Session Beta finishes and emits COMPLETED
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_beta',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: {
          total_scans: 4,
          fingerprinted: 4,
          proposals_generated: 2,
          approved_recipes: 1,
          approved_pairs: 2,
          recipes_extracted: 1,
          current_stage: 'Completed',
          percent_complete: 100,
        },
        token_usage: { prompt_tokens: 200, candidates_tokens: 100, thinking_tokens: 0, total_tokens: 300 },
        elapsed_seconds: 25,
      });
    });

    // Auto-transition to Recipe Editor for sess_beta!
    await waitFor(() => {
      expect(screen.getByText('Beta Smoked Brisket')).toBeInTheDocument();
    });

    // 6. User navigates to Ingestion tab
    const ingestTabs = screen.getAllByRole('button', { name: /1\. Ingestion/i });
    fireEvent.click(ingestTabs[0]);
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();

    // 7. Background heartbeats from Beta arrive
    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_beta',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 4, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 200, candidates_tokens: 100, thinking_tokens: 0, total_tokens: 300 },
        elapsed_seconds: 30,
      });
    });

    // View remains firmly on Ingestion tab
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();
    expect(screen.queryByText('Beta Smoked Brisket')).not.toBeInTheDocument();
  });

  // ==========================================================================
  // SCENARIO 2: PIPELINE RESTART & MULTI-RUN CYCLE
  // ==========================================================================
  it('supports pipeline restart lifecycle (COMPLETED -> SCANNING -> COMPLETED) with single auto-transition per run', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_restart_01',
        title: 'Restart Test Sourdough',
        ingredients: ['Flour', 'Water', 'Starter'],
        directions: ['Ferment and bake.'],
        tags: ['Bread'],
        card_ids: ['card_bread.jpg'],
        image_urls: ['/api/cards/card_bread.jpg/image'],
      },
    ]);

    render(<App />);

    // 1. Initial Run completes -> auto-transitions to recipes tab
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_restart',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 80, candidates_tokens: 40, thinking_tokens: 0, total_tokens: 120 },
        elapsed_seconds: 8,
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Restart Test Sourdough')).toBeInTheDocument();
    });

    // 2. User navigates to Export tab
    const exportTabs = screen.getAllByRole('button', { name: /4\. Export/i });
    fireEvent.click(exportTabs[0]);
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // 3. User restarts pipeline: SSE transitions to SCANNING
    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_restart',
        status: 'SCANNING',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 10, current_stage: 'Scanning' },
        token_usage: { prompt_tokens: 10, candidates_tokens: 5, thinking_tokens: 0, total_tokens: 15 },
        elapsed_seconds: 1,
      });
    });

    // While running, view stays on Export
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // Intermediate progress update
    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_restart',
        status: 'EXTRACTING',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 80, current_stage: 'Extracting' },
        token_usage: { prompt_tokens: 60, candidates_tokens: 30, thinking_tokens: 0, total_tokens: 90 },
        elapsed_seconds: 10,
      });
    });

    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // 4. Second run completes -> auto-transitions to Recipe Editor again!
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_restart',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 14,
      });
    });

    await waitFor(() => {
      expect(screen.getByText('Restart Test Sourdough')).toBeInTheDocument();
    });

    // 5. User navigates away to HITL Queue
    const hitlTabs = screen.getAllByRole('button', { name: /2\. HITL Queue/i });
    fireEvent.click(hitlTabs[0]);
    await waitFor(() => {
      expect(screen.getByText(/All Proposals Reviewed!/i)).toBeInTheDocument();
    });

    // Reconnection events do not yank user away from HITL
    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_restart',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 16,
      });
    });

    expect(screen.getByText(/All Proposals Reviewed!/i)).toBeInTheDocument();
  });

  // ==========================================================================
  // SCENARIO 3: ERROR / FAILURE STATES TRANSITIONING TO RECOVERY & RETRY
  // ==========================================================================
  it('retains tab stability during ERROR/FAILED states and cleanly transitions upon successful recovery/retry', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_error_01',
        title: 'Recovered Peach Cobbler',
        ingredients: ['Peaches', 'Sugar', 'Butter', 'Flour'],
        directions: ['Layer and bake at 375F.'],
        tags: ['Southern'],
        card_ids: ['card_peach.jpg'],
        image_urls: ['/api/cards/card_peach.jpg/image'],
      },
    ]);

    render(<App />);

    // 1. Session starts running on Ingestion
    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_fail_test',
        status: 'FINGERPRINTING',
        mode: 'parallel',
        progress: { total_scans: 3, percent_complete: 30, current_stage: 'Fingerprinting' },
        token_usage: { prompt_tokens: 20, candidates_tokens: 10, thinking_tokens: 0, total_tokens: 30 },
        elapsed_seconds: 3,
      });
    });

    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();

    // 2. Failure occurs (e.g., OCR engine crash / FAILED state)
    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_fail_test',
        status: 'FAILED',
        mode: 'parallel',
        progress: { total_scans: 3, percent_complete: 30, current_stage: 'Failed' },
        token_usage: { prompt_tokens: 20, candidates_tokens: 10, thinking_tokens: 0, total_tokens: 30 },
        elapsed_seconds: 4,
      });
    });

    // User should remain on current tab (no abrupt switch to recipes on failure)
    expect(screen.getByText(/New Scans Ingestion/i)).toBeInTheDocument();

    // User navigates to Export to inspect previous artifacts
    const exportTabs = screen.getAllByRole('button', { name: /4\. Export/i });
    fireEvent.click(exportTabs[0]);
    expect(screen.getByText(/No Recipes Available for Export/i)).toBeInTheDocument();

    // 3. User or system triggers retry -> transitions back to SCANNING / EXTRACTING
    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_fail_test',
        status: 'EXTRACTING',
        mode: 'parallel',
        progress: { total_scans: 3, percent_complete: 50, current_stage: 'Extracting' },
        token_usage: { prompt_tokens: 80, candidates_tokens: 40, thinking_tokens: 0, total_tokens: 120 },
        elapsed_seconds: 12,
      });
    });

    // Still on Export while retrying
    expect(screen.getByText(/No Recipes Available for Export/i)).toBeInTheDocument();

    // 4. Retry succeeds and reaches COMPLETED
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_fail_test',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 3, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 120, candidates_tokens: 60, thinking_tokens: 0, total_tokens: 180 },
        elapsed_seconds: 18,
      });
    });

    // Auto-transition to Recipe Editor for the recovered session
    await waitFor(() => {
      expect(screen.getByText('Recovered Peach Cobbler')).toBeInTheDocument();
    });

    // 5. Subsequent heartbeats do not revert
    fireEvent.click(exportTabs[0]);
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    act(() => {
      lastSSEOptions?.onProgress?.({
        session_id: 'sess_fail_test',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 3, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 120, candidates_tokens: 60, thinking_tokens: 0, total_tokens: 180 },
        elapsed_seconds: 22,
      });
    });

    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();
  });

  // ==========================================================================
  // SCENARIO 4: HITL QUEUE FLOW INTO PIPELINE COMPLETION
  // ==========================================================================
  it('handles HITL pending state transition and subsequent completion flow cleanly', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_hitl_01',
        title: 'HITL Approved Pecan Bars',
        ingredients: ['Pecans', 'Brown Sugar', 'Butter'],
        directions: ['Press crust and bake.'],
        tags: ['Bars'],
        card_ids: ['card_hitl_1.jpg', 'card_hitl_2.jpg'],
        image_urls: ['/api/cards/card_hitl_1.jpg/image', '/api/cards/card_hitl_2.jpg/image'],
      },
    ]);

    render(<App />);

    // 1. Session hits HITL_PENDING
    act(() => {
      lastSSEOptions?.onStateChange?.({
        session_id: 'sess_hitl_flow',
        status: 'HITL_PENDING',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 50, current_stage: 'HITL Review', proposals_generated: 1 },
        token_usage: { prompt_tokens: 40, candidates_tokens: 20, thinking_tokens: 0, total_tokens: 60 },
        elapsed_seconds: 6,
      });
    });

    // Transitions to HITL tab
    await waitFor(() => {
      expect(screen.getByText(/All Proposals Reviewed!/i)).toBeInTheDocument();
    });

    // 2. Extraction completes
    act(() => {
      lastSSEOptions?.onCompleted?.({
        session_id: 'sess_hitl_flow',
        status: 'COMPLETED',
        mode: 'parallel',
        progress: { total_scans: 2, percent_complete: 100, current_stage: 'Completed' },
        token_usage: { prompt_tokens: 100, candidates_tokens: 50, thinking_tokens: 0, total_tokens: 150 },
        elapsed_seconds: 15,
      });
    });

    // Auto-transitions to Recipe Editor
    await waitFor(() => {
      expect(screen.getByText('HITL Approved Pecan Bars')).toBeInTheDocument();
    });
  });

  // ==========================================================================
  // SCENARIO 5: RAPID BURST EVENT STORM IDEMPOTENCY
  // ==========================================================================
  it('remains resilient and idempotent under rapid burst storm of 50+ completion events across all handlers', async () => {
    vi.mocked(api.listRecipes).mockResolvedValue([
      {
        recipe_id: 'rec_burst_01',
        title: 'Storm Resistant Cake',
        ingredients: ['1 cup sugar'],
        directions: ['Stir.'],
        tags: [],
        card_ids: ['c.jpg'],
        image_urls: ['/api/cards/c.jpg/image'],
      },
    ]);

    render(<App />);

    // Emit 50 rapid completion events across onStateChange, onProgress, onCompleted
    act(() => {
      for (let i = 0; i < 50; i++) {
        const payload = {
          session_id: 'sess_burst_storm',
          status: 'COMPLETED',
          mode: 'parallel',
          progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
          token_usage: { prompt_tokens: 10, candidates_tokens: 5, thinking_tokens: 0, total_tokens: 15 },
          elapsed_seconds: i,
        };
        if (i % 3 === 0) lastSSEOptions?.onStateChange?.(payload);
        else if (i % 3 === 1) lastSSEOptions?.onProgress?.(payload);
        else lastSSEOptions?.onCompleted?.(payload);
      }
    });

    await waitFor(() => {
      expect(screen.getByText('Storm Resistant Cake')).toBeInTheDocument();
    });

    // Record call count before user switches tab
    const callCountAfterCompletion = vi.mocked(api.listRecipes).mock.calls.length;

    // Switch to Export
    const exportTabs = screen.getAllByRole('button', { name: /4\. Export/i });
    fireEvent.click(exportTabs[0]);
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();

    // Another burst of 50 completed events
    act(() => {
      for (let i = 0; i < 50; i++) {
        const payload = {
          session_id: 'sess_burst_storm',
          status: 'COMPLETED',
          mode: 'parallel',
          progress: { total_scans: 1, percent_complete: 100, current_stage: 'Completed' },
          token_usage: { prompt_tokens: 10, candidates_tokens: 5, thinking_tokens: 0, total_tokens: 15 },
          elapsed_seconds: 50 + i,
        };
        lastSSEOptions?.onProgress?.(payload);
      }
    });

    // View remains securely on Export and no duplicate listRecipes calls triggered by duplicate completed events
    expect(screen.getByText(/Multi-Format Recipe Export/i)).toBeInTheDocument();
    expect(vi.mocked(api.listRecipes).mock.calls.length).toBe(callCountAfterCompletion);
  });
});
