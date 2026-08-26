import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import { MultiCardInspectionModal } from '../src/components/hitl/MultiCardInspectionModal';
import { HitlWorkspace } from '../src/components/hitl/HitlWorkspace';
import { RapidQueueCard } from '../src/components/hitl/RapidQueueCard';
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

describe('F2.1 & F2.2: Multi-Card Inspection Modal & Positional Sequence Controls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const sampleCardIds = ['card_01_front.jpg', 'card_02_back.jpg', 'card_03_cont1.jpg', 'card_04_cont2.jpg'];

  it('renders null when isOpen is false or cardIds is empty', () => {
    const { rerender } = render(
      <MultiCardInspectionModal
        isOpen={false}
        onClose={vi.fn()}
        cardIds={sampleCardIds}
        onReorder={vi.fn()}
        onDetach={vi.fn()}
        onApprove={vi.fn()}
      />
    );
    expect(screen.queryByText(/Multi-Card Inspection & Sequencing/i)).not.toBeInTheDocument();

    rerender(
      <MultiCardInspectionModal
        isOpen={true}
        onClose={vi.fn()}
        cardIds={[]}
        onReorder={vi.fn()}
        onDetach={vi.fn()}
        onApprove={vi.fn()}
      />
    );
    expect(screen.queryByText(/Multi-Card Inspection & Sequencing/i)).not.toBeInTheDocument();
  });

  it('renders all 4 card viewports with positional badges, card IDs, and rank selectors', () => {
    render(
      <MultiCardInspectionModal
        isOpen={true}
        onClose={vi.fn()}
        cardIds={sampleCardIds}
        onReorder={vi.fn()}
        onDetach={vi.fn()}
        onApprove={vi.fn()}
      />
    );

    expect(screen.getByText('Multi-Card Inspection & Sequencing (4 Cards)')).toBeInTheDocument();
    expect(screen.getByText(/Full-Resolution Split Canvas Comparison/i)).toBeInTheDocument();

    // Check positional role badges
    expect(screen.getByText('#1 Front (Page 1)')).toBeInTheDocument();
    expect(screen.getByText('#2 Continuation 1 / Back (Page 2)')).toBeInTheDocument();
    expect(screen.getByText('#3 Continuation 2 (Page 3)')).toBeInTheDocument();
    expect(screen.getByText('#4 Continuation 3 (Page 4)')).toBeInTheDocument();

    // Check card IDs rendered
    expect(screen.getAllByText('card_01_front.jpg').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('card_02_back.jpg').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('card_03_cont1.jpg').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('card_04_cont2.jpg').length).toBeGreaterThanOrEqual(1);
  });

  it('allows positional rank dropdown to reorder cards to explicit ranks', () => {
    const onReorder = vi.fn();

    render(
      <MultiCardInspectionModal
        isOpen={true}
        onClose={vi.fn()}
        cardIds={sampleCardIds}
        onReorder={onReorder}
        onDetach={vi.fn()}
        onApprove={vi.fn()}
      />
    );

    // Select rank dropdown for card_04_cont2.jpg (index 3) and change to position 1 (index 0)
    const rankSelects = screen.getAllByLabelText(/Position rank for/i);
    expect(rankSelects.length).toBe(4);

    fireEvent.change(rankSelects[3], { target: { value: '0' } });
    expect(onReorder).toHaveBeenCalledWith([
      'card_04_cont2.jpg',
      'card_01_front.jpg',
      'card_02_back.jpg',
      'card_03_cont1.jpg',
    ]);
  });

  it('allows moving earlier and later with boundary disable checks', () => {
    const onReorder = vi.fn();

    render(
      <MultiCardInspectionModal
        isOpen={true}
        onClose={vi.fn()}
        cardIds={sampleCardIds}
        onReorder={onReorder}
        onDetach={vi.fn()}
        onApprove={vi.fn()}
      />
    );

    const earlierButtons = screen.getAllByTitle('Move earlier');
    const laterButtons = screen.getAllByTitle('Move later');

    // First card cannot move earlier
    expect(earlierButtons[0]).toBeDisabled();
    // Last card cannot move later
    expect(laterButtons[3]).toBeDisabled();

    // Move card 2 (index 1) earlier
    fireEvent.click(earlierButtons[1]);
    expect(onReorder).toHaveBeenCalledWith([
      'card_02_back.jpg',
      'card_01_front.jpg',
      'card_03_cont1.jpg',
      'card_04_cont2.jpg',
    ]);

    // Move card 1 (index 0) later
    fireEvent.click(laterButtons[0]);
    expect(onReorder).toHaveBeenCalledWith([
      'card_02_back.jpg',
      'card_01_front.jpg',
      'card_03_cont1.jpg',
      'card_04_cont2.jpg',
    ]);
  });

  it('allows detaching continuation cards while preserving front card (index 0)', () => {
    const onDetach = vi.fn();

    render(
      <MultiCardInspectionModal
        isOpen={true}
        onClose={vi.fn()}
        cardIds={sampleCardIds}
        onReorder={vi.fn()}
        onDetach={onDetach}
        onApprove={vi.fn()}
      />
    );

    const detachButtons = screen.getAllByTitle('Detach card from group');
    // Front card has no detach button, so only 3 detach buttons exist (indexes 1, 2, 3)
    expect(detachButtons.length).toBe(3);

    // Detach card_03_cont1.jpg (index 2 in list -> index 1 in detachButtons)
    fireEvent.click(detachButtons[1]);
    expect(onDetach).toHaveBeenCalledWith('card_03_cont1.jpg');
  });

  it('reverses order on Reverse Order button click', () => {
    const onReorder = vi.fn();

    render(
      <MultiCardInspectionModal
        isOpen={true}
        onClose={vi.fn()}
        cardIds={sampleCardIds}
        onReorder={onReorder}
        onDetach={vi.fn()}
        onApprove={vi.fn()}
      />
    );

    const reverseButtons = screen.getAllByRole('button', { name: /Reverse Order/i });
    fireEvent.click(reverseButtons[0]);

    expect(onReorder).toHaveBeenCalledWith([
      'card_04_cont2.jpg',
      'card_03_cont1.jpg',
      'card_02_back.jpg',
      'card_01_front.jpg',
    ]);
  });

  it('approves exact multi-card sequence on direct Approve button click', () => {
    const onApprove = vi.fn();

    render(
      <MultiCardInspectionModal
        isOpen={true}
        onClose={vi.fn()}
        cardIds={sampleCardIds}
        onReorder={vi.fn()}
        onDetach={vi.fn()}
        onApprove={onApprove}
      />
    );

    const approveBtn = screen.getByRole('button', { name: /Approve Multi-Card Sequence \(4 Cards\)/i });
    fireEvent.click(approveBtn);

    expect(onApprove).toHaveBeenCalledWith(sampleCardIds);
  });

  it('supports HTML5 drag-and-drop reordering simulation', () => {
    const onReorder = vi.fn();

    const { container } = render(
      <MultiCardInspectionModal
        isOpen={true}
        onClose={vi.fn()}
        cardIds={sampleCardIds}
        onReorder={onReorder}
        onDetach={vi.fn()}
        onApprove={vi.fn()}
      />
    );

    const draggables = container.querySelectorAll('[draggable="true"]');
    expect(draggables.length).toBe(4);

    const dataTransfer = {
      data: {} as Record<string, string>,
      setData: (key: string, val: string) => { dataTransfer.data[key] = val; },
      getData: (key: string) => dataTransfer.data[key] || '',
      effectAllowed: 'move',
      dropEffect: 'move',
    };

    // Drag item 2 (card_03_cont1.jpg) to target 0 (card_01_front.jpg)
    fireEvent.dragStart(draggables[2], { dataTransfer });
    fireEvent.dragOver(draggables[0], { dataTransfer });
    fireEvent.drop(draggables[0], { dataTransfer });

    expect(onReorder).toHaveBeenCalledWith([
      'card_03_cont1.jpg',
      'card_01_front.jpg',
      'card_02_back.jpg',
      'card_04_cont2.jpg',
    ]);
  });

  it('HitlWorkspace opens MultiCardInspectionModal, performs reordering, and submits approved multi-card sequence', async () => {
    const mockProposal: CorrelationProposal = {
      proposal_id: 'prop_multi_e2e',
      front_card_id: 'card_01.jpg',
      back_card_id: 'card_02.jpg',
      card_ids: ['card_01.jpg', 'card_02.jpg', 'card_03.jpg'],
      heuristic_score: 0.91,
      semantic_score: 0.94,
      confidence: 0.93,
      is_standalone: false,
      continuity_explanation: '3-card sequential recipe',
      recommended_action: 'PAIR',
    };

    vi.mocked(api.getHitlPending).mockResolvedValueOnce({
      session_id: 'sess_modal_hitl',
      status: 'HITL_PENDING',
      proposed_pairs: [mockProposal],
      unassigned_cards: [],
      total_proposals: 1,
      total_unassigned: 0,
    });

    vi.mocked(api.approveHitl).mockResolvedValueOnce({
      session_id: 'sess_modal_hitl',
      status: 'EXTRACTING',
      approved_count: 1,
      rejected_count: 0,
      message: 'HITL approval received. Pipeline resumed extraction.',
    });

    const onCompleted = vi.fn();
    render(<HitlWorkspace sessionId="sess_modal_hitl" onApprovalsCompleted={onCompleted} />);

    await waitFor(() => {
      expect(screen.getByText('Proposal 1 of 1')).toBeInTheDocument();
    });

    // Inspect All (3 Cards) button should be visible in RapidQueueCard header
    const inspectBtn = screen.getByRole('button', { name: /Inspect All \(3 Cards\)/i });
    expect(inspectBtn).toBeInTheDocument();
    fireEvent.click(inspectBtn);

    // Modal should now be open
    await waitFor(() => {
      expect(screen.getByText('Multi-Card Inspection & Sequencing (3 Cards)')).toBeInTheDocument();
    });

    // Reorder inside modal using rank select: Move card_03.jpg (index 2) to Rank 0
    const rankSelects = screen.getAllByLabelText(/Position rank for/i);
    fireEvent.change(rankSelects[2], { target: { value: '0' } });

    // Approve from modal
    const approveModalBtn = screen.getByRole('button', { name: /Approve Multi-Card Sequence/i });
    fireEvent.click(approveModalBtn);

    // Queue completes
    await waitFor(() => {
      expect(screen.getByText('All Proposals Reviewed!')).toBeInTheDocument();
    });

    // Click submit transcription
    const submitBtn = screen.getByRole('button', { name: /Submit & Start Transcription/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(api.approveHitl).toHaveBeenCalledWith({
        session_id: 'sess_modal_hitl',
        approvals: [
          expect.objectContaining({
            proposal_id: 'prop_multi_e2e',
            front_card_id: 'card_03.jpg',
            back_card_id: 'card_01.jpg',
            card_ids: ['card_03.jpg', 'card_01.jpg', 'card_02.jpg'],
          }),
        ],
        rejected_card_ids: [],
        continue_pipeline: true,
      });
      expect(onCompleted).toHaveBeenCalled();
    });
  });
});
