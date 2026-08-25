import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { HitlWorkspace } from '../src/components/hitl/HitlWorkspace';
import { RapidQueueCard } from '../src/components/hitl/RapidQueueCard';
import { CandidateDrawer } from '../src/components/hitl/CandidateDrawer';
import { MultiCardGrouping } from '../src/components/hitl/MultiCardGrouping';
import { ConfidenceMeter } from '../src/components/hitl/ConfidenceMeter';
import { CorrelationProposal } from '../src/types/hitl';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    getHitlPending: vi.fn(),
    getHitlCandidates: vi.fn(),
    approveHitl: vi.fn(),
    recordDecision: vi.fn(),
    getCardImageUrl: vi.fn((id) => `/api/cards/${id}/image`),
  },
}));

describe('R3: Rapid HITL Queue & Candidate Drawer', () => {
  const mockProposals: CorrelationProposal[] = [
    {
      proposal_id: 'prop_001',
      front_card_id: '01_cinnamon_rolls_front.jpg',
      back_card_id: '02_cinnamon_rolls_back.jpg',
      heuristic_score: 0.90,
      semantic_score: 0.95,
      confidence: 0.935,
      is_standalone: false,
      continuity_explanation: 'Continuous handwriting and matching paper tone',
      recommended_action: 'PAIR',
    },
    {
      proposal_id: 'prop_002',
      front_card_id: '03_pie_crust_front.jpg',
      back_card_id: null,
      heuristic_score: 0.50,
      semantic_score: 0.10,
      confidence: 0.22,
      is_standalone: true,
      continuity_explanation: 'Single-sided recipe card',
      recommended_action: 'STANDALONE',
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('RapidQueueCard & ConfidenceMeter', () => {
    it('renders front card and proposed back card with confidence scores', () => {
      const onApprove = vi.fn();
      const onStandalone = vi.fn();
      const onReverse = vi.fn();
      const onReject = vi.fn();
      const onToggleDrawer = vi.fn();

      render(
        <RapidQueueCard
          proposal={mockProposals[0]}
          currentIndex={0}
          totalProposals={2}
          onApprove={onApprove}
          onStandalone={onStandalone}
          onReverse={onReverse}
          onReject={onReject}
          onToggleDrawer={onToggleDrawer}
        />
      );

      expect(screen.getByText('Proposal 1 of 2')).toBeInTheDocument();
      expect(screen.getByText('01_cinnamon_rolls_front.jpg')).toBeInTheDocument();
      expect(screen.getByText('02_cinnamon_rolls_back.jpg')).toBeInTheDocument();
      expect(screen.getByText('94% Match')).toBeInTheDocument();
      expect(screen.getByText(/Continuous handwriting/i)).toBeInTheDocument();
    });

    it('renders Standalone placeholder when back_card_id is null', () => {
      render(
        <RapidQueueCard
          proposal={mockProposals[1]}
          currentIndex={1}
          totalProposals={2}
          onApprove={vi.fn()}
          onStandalone={vi.fn()}
          onReverse={vi.fn()}
          onReject={vi.fn()}
          onToggleDrawer={vi.fn()}
        />
      );

      expect(screen.getByText('Standalone Single-Sided Card')).toBeInTheDocument();
    });
  });

  describe('Keyboard Shortcuts & Input Focus Trapping', () => {
    it('advances queue on Space/Enter (Approve) and S (Standalone)', async () => {
      vi.mocked(api.getHitlPending).mockResolvedValueOnce({
        session_id: 'sess_hitl_test',
        status: 'HITL_PENDING',
        proposed_pairs: mockProposals,
        unassigned_cards: [],
        total_proposals: 2,
        total_unassigned: 0,
      });

      render(<HitlWorkspace sessionId="sess_hitl_test" onApprovalsCompleted={vi.fn()} />);

      await waitFor(() => {
        expect(screen.getByText('Proposal 1 of 2')).toBeInTheDocument();
      });

      // Press Space to Approve Proposal 1
      fireEvent.keyDown(window, { key: ' ' });

      await waitFor(() => {
        // Queue advances to Proposal 2
        expect(screen.getByText('Proposal 2 of 2')).toBeInTheDocument();
      });

      // Press S to mark Proposal 2 as Standalone
      fireEvent.keyDown(window, { key: 's' });

      await waitFor(() => {
        // Queue finishes
        expect(screen.getByText('All Proposals Reviewed!')).toBeInTheDocument();
      });
    });

    it('suppresses keyboard navigation when user is typing inside an input element', async () => {
      vi.mocked(api.getHitlPending).mockResolvedValueOnce({
        session_id: 'sess_hitl_trap',
        status: 'HITL_PENDING',
        proposed_pairs: mockProposals,
        unassigned_cards: [],
        total_proposals: 2,
        total_unassigned: 0,
      });

      const { container } = render(
        <div>
          <input type="text" data-testid="search-notes" />
          <HitlWorkspace sessionId="sess_hitl_test" onApprovalsCompleted={vi.fn()} />
        </div>
      );

      await waitFor(() => {
        expect(screen.getByText('Proposal 1 of 2')).toBeInTheDocument();
      });

      const input = screen.getByTestId('search-notes');
      input.focus();

      // Press Space while typing in input
      fireEvent.keyDown(input, { key: ' ' });
      // Queue MUST NOT advance
      expect(screen.getByText('Proposal 1 of 2')).toBeInTheDocument();

      // Press S while typing in input
      fireEvent.keyDown(input, { key: 's' });
      // Queue MUST NOT advance
      expect(screen.getByText('Proposal 1 of 2')).toBeInTheDocument();
    });
  });

  describe('Candidate Drawer & Multi-Card Grouping', () => {
    it('renders ranked candidate drawer and allows selecting alternate back card', () => {
      const onSelect = vi.fn();
      const onAttach = vi.fn();
      const mockCandidates = [
        {
          candidate_card_id: 'alt_back_01.jpg',
          score: 0.88,
          heuristic_score: 0.85,
          semantic_score: 0.90,
          composite_score: 0.88,
          reasoning_snippet: 'Matching blue ink and aged paper',
        },
      ];

      render(
        <CandidateDrawer
          isOpen={true}
          onClose={vi.fn()}
          frontCardId="01_cinnamon_rolls_front.jpg"
          candidates={mockCandidates}
          onSelectCandidateAsBack={onSelect}
          onAttachCandidateToGroup={onAttach}
        />
      );

      expect(screen.getByText('Candidate Correlation Drawer')).toBeInTheDocument();
      expect(screen.getByText('alt_back_01.jpg')).toBeInTheDocument();
      expect(screen.getByText('88%')).toBeInTheDocument();

      const selectBtn = screen.getByRole('button', { name: /Select as Back/i });
      fireEvent.click(selectBtn);
      expect(onSelect).toHaveBeenCalledWith('alt_back_01.jpg');
    });

    it('multi-card grouping tray supports 3+ cards, reordering and detaching', () => {
      const onReorder = vi.fn();
      const onDetach = vi.fn();

      render(
        <MultiCardGrouping
          cardIds={['card_page1.jpg', 'card_page2.jpg', 'card_page3.jpg']}
          onReorder={onReorder}
          onDetach={onDetach}
        />
      );

      expect(screen.getByText(/3 cards attached/i)).toBeInTheDocument();
      expect(screen.getByText('Front (Page 1)')).toBeInTheDocument();
      expect(screen.getByText('Back (Page 2)')).toBeInTheDocument();
      expect(screen.getByText('Part 3')).toBeInTheDocument();

      // Click detach on page 3
      const detachBtns = screen.getAllByTitle('Detach from group');
      fireEvent.click(detachBtns[0]);
      expect(onDetach).toHaveBeenCalled();
    });
  });
});
