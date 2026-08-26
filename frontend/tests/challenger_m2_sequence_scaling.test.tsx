import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

import { MultiCardInspectionModal } from '../src/components/hitl/MultiCardInspectionModal';
import { MultiCardGrouping } from '../src/components/hitl/MultiCardGrouping';
import { HitlWorkspace } from '../src/components/hitl/HitlWorkspace';
import { CorrelationProposal } from '../src/types/hitl';
import { api } from '../src/utils/api';

vi.mock('../src/utils/api', () => ({
  api: {
    getHitlPending: vi.fn(),
    getHitlCandidates: vi.fn(),
    approveHitl: vi.fn(),
    recordDecision: vi.fn(),
    getCardImageUrl: vi.fn((id: string) => `/api/cards/${id}/image`),
  },
}));

describe('Empirical Challenger: Multi-Card Sequence Scaling & Reordering Stress Suite (3, 4, 5, 8 Cards)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const cards3 = ['c3_1.jpg', 'c3_2.jpg', 'c3_3.jpg'];
  const cards4 = ['c4_1.jpg', 'c4_2.jpg', 'c4_3.jpg', 'c4_4.jpg'];
  const cards5 = ['c5_1.jpg', 'c5_2.jpg', 'c5_3.jpg', 'c5_4.jpg', 'c5_5.jpg'];
  const cards8 = [
    'c8_1.jpg',
    'c8_2.jpg',
    'c8_3.jpg',
    'c8_4.jpg',
    'c8_5.jpg',
    'c8_6.jpg',
    'c8_7.jpg',
    'c8_8.jpg',
  ];

  describe('1. 3-Card Sequence Stress & Permutation', () => {
    it('renders 3-card modal with accurate badges and 3-column grid', () => {
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards3}
          onReorder={vi.fn()}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      expect(screen.getByText('Multi-Card Inspection & Sequencing (3 Cards)')).toBeInTheDocument();
      expect(screen.getByText('#1 Front (Page 1)')).toBeInTheDocument();
      expect(screen.getByText('#2 Continuation 1 / Back (Page 2)')).toBeInTheDocument();
      expect(screen.getByText('#3 Continuation 2 (Page 3)')).toBeInTheDocument();
    });

    it('correctly reverses 3-card sequence to [c3_3, c3_2, c3_1]', () => {
      const onReorder = vi.fn();
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards3}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      fireEvent.click(screen.getByRole('button', { name: /Reverse Order/i }));
      expect(onReorder).toHaveBeenCalledWith(['c3_3.jpg', 'c3_2.jpg', 'c3_1.jpg']);
    });

    it('moves middle card left to become first position [c3_2, c3_1, c3_3]', () => {
      const onReorder = vi.fn();
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards3}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      const moveLeftBtns = screen.getAllByTitle('Move earlier');
      expect(moveLeftBtns[0]).toBeDisabled();
      expect(moveLeftBtns[1]).not.toBeDisabled();

      fireEvent.click(moveLeftBtns[1]);
      expect(onReorder).toHaveBeenCalledWith(['c3_2.jpg', 'c3_1.jpg', 'c3_3.jpg']);
    });

    it('detaches middle card c3_2 and verifies onDetach is triggered with exact cardId', () => {
      const onDetach = vi.fn();
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards3}
          onReorder={vi.fn()}
          onDetach={onDetach}
          onApprove={vi.fn()}
        />
      );

      const detachBtns = screen.getAllByTitle('Detach card from group');
      expect(detachBtns.length).toBe(2); // card 2 and card 3

      fireEvent.click(detachBtns[0]); // detach c3_2
      expect(onDetach).toHaveBeenCalledWith('c3_2.jpg');
    });

    it('approves a 3-card sequence in HitlWorkspace with custom order', async () => {
      const mockProposal: CorrelationProposal = {
        proposal_id: 'prop_3card',
        front_card_id: 'c3_1.jpg',
        back_card_id: 'c3_2.jpg',
        card_ids: cards3,
        heuristic_score: 0.9,
        semantic_score: 0.9,
        confidence: 0.9,
        is_standalone: false,
        continuity_explanation: '3-card recipe',
        recommended_action: 'PAIR',
      };

      vi.mocked(api.getHitlPending).mockResolvedValueOnce({
        session_id: 'sess_3card',
        status: 'HITL_PENDING',
        proposed_pairs: [mockProposal],
        unassigned_cards: [],
        total_proposals: 1,
        total_unassigned: 0,
      });

      vi.mocked(api.approveHitl).mockResolvedValueOnce({
        session_id: 'sess_3card',
        status: 'EXTRACTING',
        approved_count: 1,
        rejected_count: 0,
        message: 'Approvals submitted',
      });

      const onCompleted = vi.fn();
      render(<HitlWorkspace sessionId="sess_3card" onApprovalsCompleted={onCompleted} />);

      await waitFor(() => {
        expect(screen.getByText('Proposal 1 of 1')).toBeInTheDocument();
      });

      // Open inspection modal
      fireEvent.click(screen.getByRole('button', { name: /Inspect All \(3 Cards\)/i }));

      await waitFor(() => {
        expect(screen.getByText('Multi-Card Inspection & Sequencing (3 Cards)')).toBeInTheDocument();
      });

      // Reverse order in modal
      fireEvent.click(screen.getByRole('button', { name: /Reverse Order/i }));

      // Approve sequence
      fireEvent.click(screen.getByRole('button', { name: /Approve Multi-Card Sequence \(3 Cards\)/i }));

      await waitFor(() => {
        expect(screen.getByText('All Proposals Reviewed!')).toBeInTheDocument();
      });

      // Submit
      fireEvent.click(screen.getByRole('button', { name: /Submit & Start Transcription/i }));

      await waitFor(() => {
        expect(api.approveHitl).toHaveBeenCalledWith({
          session_id: 'sess_3card',
          approvals: [
            expect.objectContaining({
              proposal_id: 'prop_3card',
              front_card_id: 'c3_3.jpg',
              back_card_id: 'c3_2.jpg',
              card_ids: ['c3_3.jpg', 'c3_2.jpg', 'c3_1.jpg'],
              is_standalone: false,
              is_rejected: false,
            }),
          ],
          rejected_card_ids: [],
          continue_pipeline: true,
        });
        expect(onCompleted).toHaveBeenCalled();
      });
    });
  });

  describe('2. 4-Card Sequence Stress & Permutation', () => {
    it('renders 4-card modal with 4 continuation badges', () => {
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards4}
          onReorder={vi.fn()}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      expect(screen.getByText('Multi-Card Inspection & Sequencing (4 Cards)')).toBeInTheDocument();
      expect(screen.getByText('#1 Front (Page 1)')).toBeInTheDocument();
      expect(screen.getByText('#2 Continuation 1 / Back (Page 2)')).toBeInTheDocument();
      expect(screen.getByText('#3 Continuation 2 (Page 3)')).toBeInTheDocument();
      expect(screen.getByText('#4 Continuation 3 (Page 4)')).toBeInTheDocument();
    });

    it('reverses 4-card sequence to [c4_4, c4_3, c4_2, c4_1]', () => {
      const onReorder = vi.fn();
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards4}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      fireEvent.click(screen.getByRole('button', { name: /Reverse Order/i }));
      expect(onReorder).toHaveBeenCalledWith(['c4_4.jpg', 'c4_3.jpg', 'c4_2.jpg', 'c4_1.jpg']);
    });

    it('reorders rank jump of card 4 (index 3) to Pos 2 (index 1): [c4_1, c4_4, c4_2, c4_3]', () => {
      const onReorder = vi.fn();
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards4}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      const selects = screen.getAllByLabelText(/Position rank for/i);
      expect(selects.length).toBe(4);

      // Select Pos 2 (value="1") for card index 3
      fireEvent.change(selects[3], { target: { value: '1' } });
      expect(onReorder).toHaveBeenCalledWith(['c4_1.jpg', 'c4_4.jpg', 'c4_2.jpg', 'c4_3.jpg']);
    });

    it('approves a 4-card sequence in HitlWorkspace and verifies API payload', async () => {
      const mockProposal: CorrelationProposal = {
        proposal_id: 'prop_4card',
        front_card_id: 'c4_1.jpg',
        back_card_id: 'c4_2.jpg',
        card_ids: cards4,
        heuristic_score: 0.88,
        semantic_score: 0.92,
        confidence: 0.9,
        is_standalone: false,
        continuity_explanation: '4-card recipe',
        recommended_action: 'PAIR',
      };

      vi.mocked(api.getHitlPending).mockResolvedValueOnce({
        session_id: 'sess_4card',
        status: 'HITL_PENDING',
        proposed_pairs: [mockProposal],
        unassigned_cards: [],
        total_proposals: 1,
        total_unassigned: 0,
      });

      vi.mocked(api.approveHitl).mockResolvedValueOnce({
        session_id: 'sess_4card',
        status: 'EXTRACTING',
        approved_count: 1,
        rejected_count: 0,
        message: 'Approvals submitted',
      });

      const onCompleted = vi.fn();
      render(<HitlWorkspace sessionId="sess_4card" onApprovalsCompleted={onCompleted} />);

      await waitFor(() => {
        expect(screen.getByText('Proposal 1 of 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Inspect All \(4 Cards\)/i }));

      await waitFor(() => {
        expect(screen.getByText('Multi-Card Inspection & Sequencing (4 Cards)')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Reverse Order/i }));
      fireEvent.click(screen.getByRole('button', { name: /Approve Multi-Card Sequence \(4 Cards\)/i }));

      await waitFor(() => {
        expect(screen.getByText('All Proposals Reviewed!')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Submit & Start Transcription/i }));

      await waitFor(() => {
        expect(api.approveHitl).toHaveBeenCalledWith({
          session_id: 'sess_4card',
          approvals: [
            expect.objectContaining({
              proposal_id: 'prop_4card',
              front_card_id: 'c4_4.jpg',
              back_card_id: 'c4_3.jpg',
              card_ids: ['c4_4.jpg', 'c4_3.jpg', 'c4_2.jpg', 'c4_1.jpg'],
              is_standalone: false,
            }),
          ],
          rejected_card_ids: [],
          continue_pipeline: true,
        });
      });
    });
  });

  describe('3. 5-Card Sequence Stress & Permutation', () => {
    it('performs sequence of arrow moves to bubble first card to the end', () => {
      let currentOrder = [...cards5];
      const onReorder = vi.fn((newOrder: string[]) => {
        currentOrder = newOrder;
      });

      const { rerender } = render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={currentOrder}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      // Move index 0 right -> becomes index 1
      const moveRightBtns = screen.getAllByTitle('Move later');
      fireEvent.click(moveRightBtns[0]);
      expect(onReorder).toHaveBeenCalledWith(['c5_2.jpg', 'c5_1.jpg', 'c5_3.jpg', 'c5_4.jpg', 'c5_5.jpg']);

      // Rerender with updated order
      rerender(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={['c5_2.jpg', 'c5_1.jpg', 'c5_3.jpg', 'c5_4.jpg', 'c5_5.jpg']}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      // Move index 1 right -> becomes index 2
      const updatedRightBtns = screen.getAllByTitle('Move later');
      fireEvent.click(updatedRightBtns[1]);
      expect(onReorder).toHaveBeenCalledWith(['c5_2.jpg', 'c5_3.jpg', 'c5_1.jpg', 'c5_4.jpg', 'c5_5.jpg']);
    });

    it('handles drag-and-drop move from index 4 to index 0 on 5-card set', () => {
      const onReorder = vi.fn();
      const { container } = render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards5}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      const draggables = container.querySelectorAll('[draggable="true"]');
      expect(draggables.length).toBe(5);

      const dataTransfer = {
        data: {} as Record<string, string>,
        setData: (key: string, val: string) => { dataTransfer.data[key] = val; },
        getData: (key: string) => dataTransfer.data[key] || '',
        effectAllowed: 'move',
        dropEffect: 'move',
      };

      // Drag card 5 (index 4) to index 0
      fireEvent.dragStart(draggables[4], { dataTransfer });
      fireEvent.dragOver(draggables[0], { dataTransfer });
      fireEvent.drop(draggables[0], { dataTransfer });

      expect(onReorder).toHaveBeenCalledWith([
        'c5_5.jpg',
        'c5_1.jpg',
        'c5_2.jpg',
        'c5_3.jpg',
        'c5_4.jpg',
      ]);
    });
  });

  describe('4. 8-Card Sequence Stress (Large Booklet / Multi-Card Recipe)', () => {
    it('renders 8-card modal with 8 unique position options and correct badges for all 8 cards', () => {
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards8}
          onReorder={vi.fn()}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      expect(screen.getByText('Multi-Card Inspection & Sequencing (8 Cards)')).toBeInTheDocument();
      expect(screen.getByText('#1 Front (Page 1)')).toBeInTheDocument();
      expect(screen.getByText('#2 Continuation 1 / Back (Page 2)')).toBeInTheDocument();
      expect(screen.getByText('#3 Continuation 2 (Page 3)')).toBeInTheDocument();
      expect(screen.getByText('#4 Continuation 3 (Page 4)')).toBeInTheDocument();
      expect(screen.getByText('#5 Continuation 4 (Page 5)')).toBeInTheDocument();
      expect(screen.getByText('#6 Continuation 5 (Page 6)')).toBeInTheDocument();
      expect(screen.getByText('#7 Continuation 6 (Page 7)')).toBeInTheDocument();
      expect(screen.getByText('#8 Continuation 7 (Page 8)')).toBeInTheDocument();

      const selects = screen.getAllByLabelText(/Position rank for/i);
      expect(selects.length).toBe(8);
      // Each select should have 8 options
      const firstSelectOptions = selects[0].querySelectorAll('option');
      expect(firstSelectOptions.length).toBe(8);
      expect(firstSelectOptions[0].textContent).toBe('Pos 1 of 8');
      expect(firstSelectOptions[7].textContent).toBe('Pos 8 of 8');
    });

    it('fully reverses 8-card sequence to [c8_8, c8_7, c8_6, c8_5, c8_4, c8_3, c8_2, c8_1]', () => {
      const onReorder = vi.fn();
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards8}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      fireEvent.click(screen.getByRole('button', { name: /Reverse Order/i }));
      expect(onReorder).toHaveBeenCalledWith([
        'c8_8.jpg',
        'c8_7.jpg',
        'c8_6.jpg',
        'c8_5.jpg',
        'c8_4.jpg',
        'c8_3.jpg',
        'c8_2.jpg',
        'c8_1.jpg',
      ]);
    });

    it('reorders rank jump of card 8 (index 7) to Pos 1 (index 0) in 8-card set', () => {
      const onReorder = vi.fn();
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards8}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      const selects = screen.getAllByLabelText(/Position rank for/i);
      fireEvent.change(selects[7], { target: { value: '0' } });

      expect(onReorder).toHaveBeenCalledWith([
        'c8_8.jpg',
        'c8_1.jpg',
        'c8_2.jpg',
        'c8_3.jpg',
        'c8_4.jpg',
        'c8_5.jpg',
        'c8_6.jpg',
        'c8_7.jpg',
      ]);
    });

    it('enforces boundary constraints on 8-card set: index 0 cannot move earlier, index 7 cannot move later', () => {
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards8}
          onReorder={vi.fn()}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      const moveLeftBtns = screen.getAllByTitle('Move earlier');
      const moveRightBtns = screen.getAllByTitle('Move later');

      expect(moveLeftBtns.length).toBe(8);
      expect(moveRightBtns.length).toBe(8);

      expect(moveLeftBtns[0]).toBeDisabled();
      expect(moveLeftBtns[7]).not.toBeDisabled();

      expect(moveRightBtns[0]).not.toBeDisabled();
      expect(moveRightBtns[7]).toBeDisabled();
    });

    it('renders MultiCardGrouping tray for 8 cards and allows inspection click', () => {
      const onOpenModal = vi.fn();
      render(
        <MultiCardGrouping
          cardIds={cards8}
          onReorder={vi.fn()}
          onDetach={vi.fn()}
          onOpenInspectionModal={onOpenModal}
        />
      );

      expect(screen.getByText('Multi-Card Recipe Group (8 cards attached)')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /Inspect Full-Size Modal/i }));
      expect(onOpenModal).toHaveBeenCalled();
    });

    it('approves an 8-card reversed sequence in HitlWorkspace and verifies full 8-card payload to api.approveHitl', async () => {
      const mockProposal: CorrelationProposal = {
        proposal_id: 'prop_8card',
        front_card_id: 'c8_1.jpg',
        back_card_id: 'c8_2.jpg',
        card_ids: cards8,
        heuristic_score: 0.95,
        semantic_score: 0.95,
        confidence: 0.95,
        is_standalone: false,
        continuity_explanation: '8-card booklet recipe',
        recommended_action: 'PAIR',
      };

      vi.mocked(api.getHitlPending).mockResolvedValueOnce({
        session_id: 'sess_8card',
        status: 'HITL_PENDING',
        proposed_pairs: [mockProposal],
        unassigned_cards: [],
        total_proposals: 1,
        total_unassigned: 0,
      });

      vi.mocked(api.approveHitl).mockResolvedValueOnce({
        session_id: 'sess_8card',
        status: 'EXTRACTING',
        approved_count: 1,
        rejected_count: 0,
        message: 'Approvals submitted',
      });

      const onCompleted = vi.fn();
      render(<HitlWorkspace sessionId="sess_8card" onApprovalsCompleted={onCompleted} />);

      await waitFor(() => {
        expect(screen.getByText('Proposal 1 of 1')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole('button', { name: /Inspect All \(8 Cards\)/i }));

      await waitFor(() => {
        expect(screen.getByText('Multi-Card Inspection & Sequencing (8 Cards)')).toBeInTheDocument();
      });

      // Reverse order of all 8 cards
      fireEvent.click(screen.getByRole('button', { name: /Reverse Order/i }));

      // Approve 8-card sequence
      fireEvent.click(screen.getByRole('button', { name: /Approve Multi-Card Sequence \(8 Cards\)/i }));

      await waitFor(() => {
        expect(screen.getByText('All Proposals Reviewed!')).toBeInTheDocument();
      });

      // Submit
      fireEvent.click(screen.getByRole('button', { name: /Submit & Start Transcription/i }));

      await waitFor(() => {
        expect(api.approveHitl).toHaveBeenCalledWith({
          session_id: 'sess_8card',
          approvals: [
            expect.objectContaining({
              proposal_id: 'prop_8card',
              front_card_id: 'c8_8.jpg',
              back_card_id: 'c8_7.jpg',
              card_ids: [
                'c8_8.jpg',
                'c8_7.jpg',
                'c8_6.jpg',
                'c8_5.jpg',
                'c8_4.jpg',
                'c8_3.jpg',
                'c8_2.jpg',
                'c8_1.jpg',
              ],
              is_standalone: false,
              is_rejected: false,
            }),
          ],
          rejected_card_ids: [],
          continue_pipeline: true,
        });
        expect(onCompleted).toHaveBeenCalled();
      });
    });
  });
});
