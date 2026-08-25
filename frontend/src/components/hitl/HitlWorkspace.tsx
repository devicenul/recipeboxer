import React, { useState, useEffect, useCallback } from 'react';
import { CheckCircle2, AlertCircle, Loader2, ArrowRight, RefreshCw, Layers } from 'lucide-react';
import { CorrelationProposal, CardPairApproval, CorrelationCandidate } from '../../types/hitl';
import { RapidQueueCard } from './RapidQueueCard';
import { CandidateDrawer } from './CandidateDrawer';
import { Button } from '../common/Button';
import { useKeyboard } from '../../hooks/useKeyboard';
import { api } from '../../utils/api';

export interface HitlWorkspaceProps {
  sessionId: string;
  onApprovalsCompleted: () => void;
}

export const HitlWorkspace: React.FC<HitlWorkspaceProps> = ({
  sessionId,
  onApprovalsCompleted,
}) => {
  const [proposals, setProposals] = useState<CorrelationProposal[]>([]);
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [approvals, setApprovals] = useState<CardPairApproval[]>([]);
  const [rejectedCardIds, setRejectedCardIds] = useState<string[]>([]);
  const [multiCardGroups, setMultiCardGroups] = useState<Record<string, string[]>>({}); // front_card_id -> string[]

  const [isDrawerOpen, setIsDrawerOpen] = useState<boolean>(false);
  const [candidates, setCandidates] = useState<CorrelationCandidate[]>([]);
  const [loadingCandidates, setLoadingCandidates] = useState<boolean>(false);

  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // 1. Fetch pending HITL proposals
  const fetchProposals = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await api.getHitlPending(sessionId);
      setProposals(res.proposed_pairs || []);
      setCurrentIndex(0);
      setApprovals([]);
      setRejectedCardIds([]);

      // Initialize multi-card groups
      const initialGroups: Record<string, string[]> = {};
      (res.proposed_pairs || []).forEach((p) => {
        const ids = [p.front_card_id];
        if (p.back_card_id) ids.push(p.back_card_id);
        if (p.card_ids && p.card_ids.length > 0) {
          initialGroups[p.front_card_id] = p.card_ids;
        } else {
          initialGroups[p.front_card_id] = ids;
        }
      });
      setMultiCardGroups(initialGroups);
    } catch (err: any) {
      setError(err.message || 'Failed to load correlation proposals for review');
    } finally {
      setIsLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (sessionId) {
      fetchProposals();
    }
  }, [sessionId, fetchProposals]);

  const currentProposal = proposals[currentIndex] || null;

  // 2. Fetch candidate drawer cards when drawer opened
  const fetchCandidates = useCallback(async (frontId: string) => {
    setLoadingCandidates(true);
    try {
      const res = await api.getHitlCandidates(frontId, sessionId);
      setCandidates(res.candidates || []);
    } catch (err: any) {
      setCandidates([]);
    } finally {
      setLoadingCandidates(false);
    }
  }, [sessionId]);

  const handleToggleDrawer = useCallback(() => {
    if (!currentProposal) return;
    if (!isDrawerOpen) {
      fetchCandidates(currentProposal.front_card_id);
    }
    setIsDrawerOpen((prev) => !prev);
  }, [currentProposal, isDrawerOpen, fetchCandidates]);

  // Advance queue helper
  const advanceQueue = useCallback(() => {
    setIsDrawerOpen(false);
    if (currentIndex + 1 < proposals.length) {
      setCurrentIndex((prev) => prev + 1);
    } else {
      // Reached the end of queue
      setCurrentIndex(proposals.length);
    }
  }, [currentIndex, proposals.length]);

  // Actions
  const handleApprove = useCallback(() => {
    if (!currentProposal) return;
    const group = multiCardGroups[currentProposal.front_card_id] || [
      currentProposal.front_card_id,
      ...(currentProposal.back_card_id ? [currentProposal.back_card_id] : []),
    ];

    const approval: CardPairApproval = {
      proposal_id: currentProposal.proposal_id,
      front_card_id: group[0] || currentProposal.front_card_id,
      back_card_id: group[1] || currentProposal.back_card_id || null,
      card_ids: group.length > 2 ? group : undefined,
      is_standalone: group.length === 1 || currentProposal.is_standalone,
      is_rejected: false,
    };

    setApprovals((prev) => [...prev.filter((a) => a.front_card_id !== approval.front_card_id), approval]);
    advanceQueue();
  }, [currentProposal, multiCardGroups, advanceQueue]);

  const handleStandalone = useCallback(() => {
    if (!currentProposal) return;
    const approval: CardPairApproval = {
      proposal_id: currentProposal.proposal_id,
      front_card_id: currentProposal.front_card_id,
      back_card_id: null,
      card_ids: [currentProposal.front_card_id],
      is_standalone: true,
      is_rejected: false,
    };

    setApprovals((prev) => [...prev.filter((a) => a.front_card_id !== approval.front_card_id), approval]);
    advanceQueue();
  }, [currentProposal, advanceQueue]);

  const handleReverse = useCallback(() => {
    if (!currentProposal || !currentProposal.back_card_id) return;
    // Swap front and back in current proposal
    const swappedFront = currentProposal.back_card_id;
    const swappedBack = currentProposal.front_card_id;

    setProposals((prev) => {
      const copy = [...prev];
      copy[currentIndex] = {
        ...copy[currentIndex],
        front_card_id: swappedFront,
        back_card_id: swappedBack,
      };
      return copy;
    });

    setMultiCardGroups((prev) => {
      const oldGroup = prev[currentProposal.front_card_id] || [swappedFront, swappedBack];
      const newGroup = [oldGroup[1], oldGroup[0], ...oldGroup.slice(2)];
      return {
        ...prev,
        [swappedFront]: newGroup,
      };
    });
  }, [currentProposal, currentIndex]);

  const handleReject = useCallback(() => {
    if (!currentProposal) return;
    const idsToReject = [currentProposal.front_card_id];
    if (currentProposal.back_card_id) idsToReject.push(currentProposal.back_card_id);

    setRejectedCardIds((prev) => Array.from(new Set([...prev, ...idsToReject])));
    advanceQueue();
  }, [currentProposal, advanceQueue]);

  // Drawer actions
  const handleSelectCandidateAsBack = useCallback((candId: string) => {
    if (!currentProposal) return;
    setProposals((prev) => {
      const copy = [...prev];
      copy[currentIndex] = {
        ...copy[currentIndex],
        back_card_id: candId,
        is_standalone: false,
      };
      return copy;
    });

    setMultiCardGroups((prev) => {
      const group = prev[currentProposal.front_card_id] || [currentProposal.front_card_id];
      const newGroup = [group[0], candId, ...group.slice(2).filter((id) => id !== candId)];
      return {
        ...prev,
        [currentProposal.front_card_id]: newGroup,
      };
    });
  }, [currentProposal, currentIndex]);

  const handleAttachCandidateToGroup = useCallback((candId: string) => {
    if (!currentProposal) return;
    setMultiCardGroups((prev) => {
      const group = prev[currentProposal.front_card_id] || [
        currentProposal.front_card_id,
        ...(currentProposal.back_card_id ? [currentProposal.back_card_id] : []),
      ];
      if (group.includes(candId)) return prev;
      return {
        ...prev,
        [currentProposal.front_card_id]: [...group, candId],
      };
    });
  }, [currentProposal]);

  const handleReorderGroup = useCallback((newOrder: string[]) => {
    if (!currentProposal) return;
    setMultiCardGroups((prev) => ({
      ...prev,
      [currentProposal.front_card_id]: newOrder,
    }));
  }, [currentProposal]);

  const handleDetachCard = useCallback((cardId: string) => {
    if (!currentProposal) return;
    setMultiCardGroups((prev) => {
      const group = prev[currentProposal.front_card_id] || [];
      const updated = group.filter((id) => id !== cardId);
      return {
        ...prev,
        [currentProposal.front_card_id]: updated,
      };
    });
  }, [currentProposal]);

  // Keyboard shortcut integration
  useKeyboard({
    enabled: !!currentProposal && !isDrawerOpen && !isSubmitting,
    onApprove: handleApprove,
    onStandalone: handleStandalone,
    onReverse: handleReverse,
    onReject: handleReject,
    onToggleCandidates: handleToggleDrawer,
    onEscape: () => setIsDrawerOpen(false),
  });

  // Submit approvals
  const handleSubmitApprovals = async () => {
    setIsSubmitting(true);
    setError(null);
    try {
      await api.approveHitl({
        session_id: sessionId,
        approvals,
        rejected_card_ids: rejectedCardIds,
        continue_pipeline: true,
      });
      onApprovalsCompleted();
    } catch (err: any) {
      setError(err.message || 'Failed to submit HITL approvals');
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="py-24 flex flex-col items-center justify-center gap-3 text-slate-500 dark:text-slate-400">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
        <span className="text-sm font-medium">Loading HITL correlation proposals...</span>
      </div>
    );
  }

  if (error && proposals.length === 0) {
    return (
      <div className="p-8 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 rounded-xl text-center flex flex-col items-center gap-3">
        <AlertCircle className="w-8 h-8 text-rose-500" />
        <h3 className="text-base font-semibold text-rose-800 dark:text-rose-200">Unable to load HITL queue</h3>
        <p className="text-xs text-rose-600 dark:text-rose-400 max-w-md">{error}</p>
        <Button variant="secondary" size="sm" onClick={fetchProposals} leftIcon={<RefreshCw className="w-4 h-4" />}>
          Retry Fetch
        </Button>
      </div>
    );
  }

  const isQueueComplete = currentIndex >= proposals.length;
  const currentGroup = currentProposal ? multiCardGroups[currentProposal.front_card_id] || [] : [];

  return (
    <div className="flex flex-col gap-6">
      {/* Workspace View */}
      {!isQueueComplete && currentProposal ? (
        <RapidQueueCard
          proposal={currentProposal}
          currentIndex={currentIndex}
          totalProposals={proposals.length}
          sessionId={sessionId}
          groupedCardIds={currentGroup}
          onApprove={handleApprove}
          onStandalone={handleStandalone}
          onReverse={handleReverse}
          onReject={handleReject}
          onToggleDrawer={handleToggleDrawer}
          onReorderGroup={handleReorderGroup}
          onDetachCard={handleDetachCard}
        />
      ) : (
        /* Queue Finished Summary */
        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-8 text-center flex flex-col items-center gap-5 shadow-xs">
          <div className="w-16 h-16 rounded-full bg-emerald-100 dark:bg-emerald-950/60 text-emerald-600 dark:text-emerald-400 flex items-center justify-center shadow-inner">
            <CheckCircle2 className="w-10 h-10" />
          </div>
          <div>
            <h3 className="text-lg font-bold text-slate-900 dark:text-slate-100">
              All Proposals Reviewed!
            </h3>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-sm">
              You have completed human review for all {proposals.length} correlation pairings. Ready to resume Gemini recipe transcription.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4 text-xs font-mono text-slate-700 dark:text-slate-300 w-full max-w-xs">
            <div className="p-3 bg-slate-50 dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700">
              <span className="text-[11px] text-slate-500 dark:text-slate-400 block font-sans">Approved Recipes</span>
              <strong className="text-lg font-bold text-emerald-600 dark:text-emerald-400">{approvals.length}</strong>
            </div>
            <div className="p-3 bg-slate-50 dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700">
              <span className="text-[11px] text-slate-500 dark:text-slate-400 block font-sans">Rejected Scans</span>
              <strong className="text-lg font-bold text-rose-600 dark:text-rose-400">{rejectedCardIds.length}</strong>
            </div>
          </div>

          {error && (
            <div className="p-3 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 rounded-lg text-xs border border-rose-200 dark:border-rose-800">
              {error}
            </div>
          )}

          <div className="flex gap-3 mt-2">
            <Button
              variant="secondary"
              size="md"
              onClick={() => {
                setCurrentIndex(0);
                setApprovals([]);
                setRejectedCardIds([]);
              }}
            >
              Restart Review
            </Button>
            <Button
              variant="success"
              size="lg"
              onClick={handleSubmitApprovals}
              isLoading={isSubmitting}
              rightIcon={<ArrowRight className="w-4 h-4" />}
            >
              Submit &amp; Start Transcription
            </Button>
          </div>
        </div>
      )}

      {/* Slide-out Candidate Drawer */}
      {currentProposal && (
        <CandidateDrawer
          isOpen={isDrawerOpen}
          onClose={() => setIsDrawerOpen(false)}
          frontCardId={currentProposal.front_card_id}
          candidates={candidates}
          currentBackCardId={currentProposal.back_card_id}
          groupedCardIds={currentGroup}
          onSelectCandidateAsBack={handleSelectCandidateAsBack}
          onAttachCandidateToGroup={handleAttachCandidateToGroup}
          isLoading={loadingCandidates}
        />
      )}
    </div>
  );
};
