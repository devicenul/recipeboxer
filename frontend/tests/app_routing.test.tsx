import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import App from '../src/App';

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

describe('App Layout & Routing Integration', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
});
