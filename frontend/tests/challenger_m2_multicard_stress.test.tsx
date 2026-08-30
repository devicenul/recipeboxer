import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

import { MultiCardInspectionModal } from '../src/components/hitl/MultiCardInspectionModal';
import { MultiCardGrouping } from '../src/components/hitl/MultiCardGrouping';
import { RapidQueueCard } from '../src/components/hitl/RapidQueueCard';
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

describe('Empirical Challenger: Milestone 2 Multi-Card UI & Workflow Deep Stress Suite', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const cards5 = ['card_01.jpg', 'card_02.jpg', 'card_03.jpg', 'card_04.jpg', 'card_05.jpg'];

  describe('1. MultiCardInspectionModal Edge Cases & Boundary Behaviors', () => {
    it('handles 2-card sets with correct "Part 1 of 2" and "Part 2 of 2" badges', () => {
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={['front.jpg', 'back.jpg']}
          onReorder={vi.fn()}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      expect(screen.getByText('#1 Part 1 of 2')).toBeInTheDocument();
      expect(screen.getByText('#2 Part 2 of 2')).toBeInTheDocument();
    });

    it('handles 5-card sets with continuation badges up to Page 5', () => {
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards5}
          onReorder={vi.fn()}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      expect(screen.getByText('#1 Part 1 of 5')).toBeInTheDocument();
      expect(screen.getByText('#2 Part 2 of 5')).toBeInTheDocument();
      expect(screen.getByText('#3 Part 3 of 5')).toBeInTheDocument();
      expect(screen.getByText('#4 Part 4 of 5')).toBeInTheDocument();
      expect(screen.getByText('#5 Part 5 of 5')).toBeInTheDocument();
    });


    it('enforces boundary disable checks on arrows: index 0 cannot move left, index 4 cannot move right', () => {
      const onReorder = vi.fn();
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards5}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      const moveLeftBtns = screen.getAllByTitle('Move earlier');
      const moveRightBtns = screen.getAllByTitle('Move later');

      expect(moveLeftBtns[0]).toBeDisabled();
      expect(moveLeftBtns[1]).not.toBeDisabled();
      expect(moveRightBtns[3]).not.toBeDisabled();
      expect(moveRightBtns[4]).toBeDisabled();

      // Click middle card left
      fireEvent.click(moveLeftBtns[2]);
      expect(onReorder).toHaveBeenCalledWith([
        'card_01.jpg',
        'card_03.jpg',
        'card_02.jpg',
        'card_04.jpg',
        'card_05.jpg',
      ]);
    });

    it('reorders arbitrary jump via Pos rank selector (e.g. card 5 to Pos 1)', () => {
      const onReorder = vi.fn();
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards5}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      const selects = screen.getAllByLabelText(/Position rank for/i);
      expect(selects.length).toBe(5);

      // Change rank of card_05 (index 4) to 0
      fireEvent.change(selects[4], { target: { value: '0' } });
      expect(onReorder).toHaveBeenCalledWith([
        'card_05.jpg',
        'card_01.jpg',
        'card_02.jpg',
        'card_03.jpg',
        'card_04.jpg',
      ]);
    });

    it('reverses 5-card order completely on Reverse Order button click', () => {
      const onReorder = vi.fn();

      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards5}
          onReorder={onReorder}
          onDetach={vi.fn()}
          onApprove={vi.fn()}
        />
      );

      const revBtn = screen.getByRole('button', { name: /Reverse Order/i });
      fireEvent.click(revBtn);

      expect(onReorder).toHaveBeenCalledWith([
        'card_05.jpg',
        'card_04.jpg',
        'card_03.jpg',
        'card_02.jpg',
        'card_01.jpg',
      ]);
    });

    it('detaches middle and last cards in 5-card set', () => {
      const onDetach = vi.fn();
      render(
        <MultiCardInspectionModal
          isOpen={true}
          onClose={vi.fn()}
          cardIds={cards5}
          onReorder={vi.fn()}
          onDetach={onDetach}
          onApprove={vi.fn()}
        />
      );

      const detachButtons = screen.getAllByTitle('Detach card from group');
      expect(detachButtons.length).toBe(5);

      // Detach card_03 (index 2 -> detachButtons[2])
      fireEvent.click(detachButtons[2]);
      expect(onDetach).toHaveBeenCalledWith('card_03.jpg');

      // Detach card_05 (index 4 -> detachButtons[4])
      fireEvent.click(detachButtons[4]);
      expect(onDetach).toHaveBeenCalledWith('card_05.jpg');
    });


    it('handles drag-and-drop drop on same target index cleanly without dispatching redundant changes', () => {
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
      const dataTransfer = {
        data: {} as Record<string, string>,
        setData: (key: string, val: string) => { dataTransfer.data[key] = val; },
        getData: (key: string) => dataTransfer.data[key] || '',
        effectAllowed: 'move',
        dropEffect: 'move',
      };

      // Drag index 2 to index 2
      fireEvent.dragStart(draggables[2], { dataTransfer });
      fireEvent.dragOver(draggables[2], { dataTransfer });
      fireEvent.drop(draggables[2], { dataTransfer });

      expect(onReorder).not.toHaveBeenCalled();
    });
  });

  describe('2. MultiCardGrouping Tray Component Interactions', () => {
    it('renders horizontal grouping tray with card count, thumbnails, and reorder controls', () => {
      const onReorder = vi.fn();
      const onDetach = vi.fn();
      const onOpenModal = vi.fn();

      render(
        <MultiCardGrouping
          cardIds={cards5}
          onReorder={onReorder}
          onDetach={onDetach}
          onOpenInspectionModal={onOpenModal}
        />
      );

      expect(screen.getByText('Multi-Card Recipe Group (5 cards attached)')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Inspect Full-Size Modal/i })).toBeInTheDocument();

      // Click inspect button
      fireEvent.click(screen.getByRole('button', { name: /Inspect Full-Size Modal/i }));
      expect(onOpenModal).toHaveBeenCalled();

      // Click card thumbnail
      const thumbs = screen.getAllByRole('img');
      expect(thumbs.length).toBe(5);
      fireEvent.click(thumbs[0]);
      expect(onOpenModal).toHaveBeenCalledTimes(2);
    });

    it('supports drag-and-drop on MultiCardGrouping thumbnails', () => {
      const onReorder = vi.fn();
      const { container } = render(
        <MultiCardGrouping
          cardIds={['card_1.jpg', 'card_2.jpg', 'card_3.jpg']}
          onReorder={onReorder}
          onDetach={vi.fn()}
        />
      );

      const draggables = container.querySelectorAll('[draggable="true"]');
      expect(draggables.length).toBe(3);

      const dataTransfer = {
        data: {} as Record<string, string>,
        setData: (key: string, val: string) => { dataTransfer.data[key] = val; },
        getData: (key: string) => dataTransfer.data[key] || '',
        effectAllowed: 'move',
        dropEffect: 'move',
      };

      // Drag card 3 (index 2) to target 0
      fireEvent.dragStart(draggables[2], { dataTransfer });
      fireEvent.dragOver(draggables[0], { dataTransfer });
      fireEvent.drop(draggables[0], { dataTransfer });

      expect(onReorder).toHaveBeenCalledWith(['card_3.jpg', 'card_1.jpg', 'card_2.jpg']);
    });
  });

  describe('3. HitlWorkspace End-to-End Multi-Card Workflow & Keyboard Shortcut Trapping', () => {
    it('suppresses rapid-queue keyboard hotkeys while inspection modal is open and closes on Escape', async () => {
      const mockProposal: CorrelationProposal = {
        proposal_id: 'prop_multi_5card',
        ordered_card_ids: cards5,
        heuristic_score: 0.95,
        semantic_score: 0.96,
        confidence: 0.95,
        is_standalone: false,
        continuity_explanation: '5-card longform recipe',
        recommended_action: 'PAIR',
      };

      vi.mocked(api.getHitlPending).mockResolvedValueOnce({
        session_id: 'sess_modal_keys',
        status: 'HITL_PENDING',
        proposed_pairs: [mockProposal],
        unassigned_cards: [],
        total_proposals: 1,
        total_unassigned: 0,
      });

      render(<HitlWorkspace sessionId="sess_modal_keys" onApprovalsCompleted={vi.fn()} />);

      await waitFor(() => {
        expect(screen.getByText('Proposal 1 of 1')).toBeInTheDocument();
      });

      // Open inspection modal
      const inspectBtn = screen.getByRole('button', { name: /Inspect All \(5 Cards\)/i });
      fireEvent.click(inspectBtn);

      await waitFor(() => {
        expect(screen.getByText('Multi-Card Inspection & Sequencing (5 Cards)')).toBeInTheDocument();
      });

      // Press Space (which would normally approve and advance the queue)
      fireEvent.keyDown(window, { code: 'Space', key: ' ' });

      // Modal should still be open, queue should NOT have advanced
      expect(screen.getByText('Multi-Card Inspection & Sequencing (5 Cards)')).toBeInTheDocument();
      expect(screen.queryByText('All Proposals Reviewed!')).not.toBeInTheDocument();

      // Press Escape to dismiss modal
      fireEvent.keyDown(window, { code: 'Escape', key: 'Escape' });

      await waitFor(() => {
        expect(screen.queryByText('Multi-Card Inspection & Sequencing (5 Cards)')).not.toBeInTheDocument();
      });
    });

    it('approves a 5-card custom ordered sequence from modal and submits to api.approveHitl', async () => {
      const mockProposal: CorrelationProposal = {
        proposal_id: 'prop_multi_commit',
        ordered_card_ids: cards5,
        heuristic_score: 0.92,
        semantic_score: 0.95,
        confidence: 0.93,
        is_standalone: false,
        continuity_explanation: '5-card recipe',
        recommended_action: 'PAIR',
      };

      vi.mocked(api.getHitlPending).mockResolvedValueOnce({
        session_id: 'sess_multi_commit',
        status: 'HITL_PENDING',
        proposed_pairs: [mockProposal],
        unassigned_cards: [],
        total_proposals: 1,
        total_unassigned: 0,
      });

      vi.mocked(api.approveHitl).mockResolvedValueOnce({
        session_id: 'sess_multi_commit',
        status: 'EXTRACTING',
        approved_count: 1,
        rejected_count: 0,
        message: 'HITL approvals submitted.',
      });

      const onCompleted = vi.fn();
      render(<HitlWorkspace sessionId="sess_multi_commit" onApprovalsCompleted={onCompleted} />);

      await waitFor(() => {
        expect(screen.getByText('Proposal 1 of 1')).toBeInTheDocument();
      });

      // Open inspection modal
      fireEvent.click(screen.getByRole('button', { name: /Inspect All \(5 Cards\)/i }));

      await waitFor(() => {
        expect(screen.getByText('Multi-Card Inspection & Sequencing (5 Cards)')).toBeInTheDocument();
      });

      // Reorder: Reverse Order
      const revBtn = screen.getByRole('button', { name: /Reverse Order/i });
      fireEvent.click(revBtn);

      // Approve multi-card sequence
      const approveBtn = screen.getByRole('button', { name: /Approve Multi-Card Sequence \(5 Cards\)/i });
      fireEvent.click(approveBtn);

      // Modal closes and queue summary appears
      await waitFor(() => {
        expect(screen.getByText('All Proposals Reviewed!')).toBeInTheDocument();
      });

      // Submit approvals
      fireEvent.click(screen.getByRole('button', { name: /Submit & Start Transcription/i }));

      await waitFor(() => {
        expect(api.approveHitl).toHaveBeenCalledWith({
          session_id: 'sess_multi_commit',
          approvals: [
            expect.objectContaining({
              proposal_id: 'prop_multi_commit',
              ordered_card_ids: ['card_05.jpg', 'card_04.jpg', 'card_03.jpg', 'card_02.jpg', 'card_01.jpg'],
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
