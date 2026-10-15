import React, { useState, useEffect, useCallback } from 'react';
import { CheckCircle2, AlertCircle, AlertTriangle, Loader2, ArrowRight, RefreshCw, Layers } from 'lucide-react';
import { CorrelationProposal, HitlApproval, CardPairApproval, CorrelationCandidate, HitlItem } from '../../types/hitl';
import { RapidQueueCard } from './RapidQueueCard';
import { CandidateDrawer } from './CandidateDrawer';
import { MultiCardInspectionModal } from './MultiCardInspectionModal';
import { Button } from '../common/Button';
import { useKeyboard } from '../../hooks/useKeyboard';
import { api } from '../../utils/api';

export interface HitlWorkspaceProps {
  sessionId: string;
  onApprovalsCompleted: () => void;
  initialBudgetItem?: HitlItem | null;
}

export const HitlWorkspace: React.FC<HitlWorkspaceProps> = ({
  sessionId,
  onApprovalsCompleted,
  initialBudgetItem,
}) => {
  const [budgetItem, setBudgetItem] = useState<HitlItem | null>(() => initialBudgetItem || null);
  const [proposals, setProposals] = useState<CorrelationProposal[]>([]);
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [approvals, setApprovals] = useState<HitlApproval[]>([]);
  const [rejectedCardIds, setRejectedCardIds] = useState<string[]>([]);
  const [multiCardGroups, setMultiCardGroups] = useState<Record<string, string[]>>({}); // proposal_id -> string[]

  const [isDrawerOpen, setIsDrawerOpen] = useState<boolean>(false);
  const [isInspectionModalOpen, setIsInspectionModalOpen] = useState<boolean>(false);
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
      const rawProposals = res.proposals || res.proposed_pairs || [];
      const rawItems: HitlItem[] = (res as any).items || [];
      const foundBudgetItem = rawItems.find((it) => it.kind === 'budget' || it.item_type === 'BUDGET');
      if (foundBudgetItem) {
        setBudgetItem(foundBudgetItem);
      }
      const normalizedProposals: CorrelationProposal[] = rawProposals.map((p) => {
        let orderedIds: string[] = [];
        if (p.ordered_card_ids && p.ordered_card_ids.length > 0) {
          orderedIds = [...p.ordered_card_ids];
        } else if (p.card_ids && p.card_ids.length > 0) {
          orderedIds = [...p.card_ids];
        } else {
          orderedIds = [
            p.front_card_id,
            ...(p.back_card_id ? [p.back_card_id] : []),
          ].filter(Boolean) as string[];
        }
        return {
          ...p,
          ordered_card_ids: orderedIds,
        };
      });

      setProposals(normalizedProposals);
      setCurrentIndex(0);
      setApprovals([]);
      setRejectedCardIds([]);

      // Initialize multi-card groups
      const initialGroups: Record<string, string[]> = {};
      normalizedProposals.forEach((p) => {
        initialGroups[p.proposal_id] = p.ordered_card_ids;
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
  const currentGroup = currentProposal
    ? multiCardGroups[currentProposal.proposal_id] || currentProposal.ordered_card_ids || []
    : [];

  // 2. Fetch candidate drawer cards when drawer opened
  const fetchCandidates = useCallback(async (cardId: string) => {
    setLoadingCandidates(true);
    try {
      const res = await api.getHitlCandidates(cardId, sessionId);
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
      const primaryCardId = currentGroup[0] || (currentProposal.ordered_card_ids && currentProposal.ordered_card_ids[0]) || '';
      fetchCandidates(primaryCardId);
    }
    setIsDrawerOpen((prev) => !prev);
  }, [currentProposal, currentGroup, isDrawerOpen, fetchCandidates]);

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
    const group = multiCardGroups[currentProposal.proposal_id] || currentProposal.ordered_card_ids || [];

    const approval: HitlApproval = {
      proposal_id: currentProposal.proposal_id,
      ordered_card_ids: group,
      front_card_id: group[0],
      back_card_id: group[1] || null,
      card_ids: group,
      is_standalone: group.length === 1 || currentProposal.is_standalone,
      is_rejected: false,
    };

    setApprovals((prev) => [
      ...prev.filter((a) => a.proposal_id !== approval.proposal_id),
      approval,
    ]);
    advanceQueue();
  }, [currentProposal, multiCardGroups, advanceQueue]);

  const handleStandalone = useCallback(() => {
    if (!currentProposal) return;
    const group = multiCardGroups[currentProposal.proposal_id] || currentProposal.ordered_card_ids || [];
    const standaloneId = group[0] || (currentProposal.ordered_card_ids && currentProposal.ordered_card_ids[0]) || '';
    const approval: HitlApproval = {
      proposal_id: currentProposal.proposal_id,
      ordered_card_ids: [standaloneId],
      front_card_id: standaloneId,
      back_card_id: null,
      card_ids: [standaloneId],
      is_standalone: true,
      is_rejected: false,
    };

    setApprovals((prev) => [
      ...prev.filter((a) => a.proposal_id !== approval.proposal_id),
      approval,
    ]);
    advanceQueue();
  }, [currentProposal, multiCardGroups, advanceQueue]);

  const handleReverse = useCallback(() => {
    if (!currentProposal) return;
    const group = multiCardGroups[currentProposal.proposal_id] || currentProposal.ordered_card_ids || [];
    if (group.length <= 1) return;
    const reversedGroup = [...group].reverse();

    setProposals((prev) => {
      const copy = [...prev];
      copy[currentIndex] = {
        ...copy[currentIndex],
        ordered_card_ids: reversedGroup,
        front_card_id: reversedGroup[0],
        back_card_id: reversedGroup[1] || null,
      };
      return copy;
    });

    setMultiCardGroups((prev) => ({
      ...prev,
      [currentProposal.proposal_id]: reversedGroup,
    }));
  }, [currentProposal, currentIndex, multiCardGroups]);

  const handleReject = useCallback(() => {
    if (!currentProposal) return;
    const group = multiCardGroups[currentProposal.proposal_id] || currentProposal.ordered_card_ids || [];
    setRejectedCardIds((prev) => Array.from(new Set([...prev, ...group])));
    advanceQueue();
  }, [currentProposal, multiCardGroups, advanceQueue]);

  // Drawer actions
  const handleSelectCandidateAsBack = useCallback((candId: string) => {
    if (!currentProposal) return;
    setMultiCardGroups((prev) => {
      const group = prev[currentProposal.proposal_id] || currentProposal.ordered_card_ids || [];
      const newGroup = group.length > 0
        ? [group[0], candId, ...group.slice(2).filter((id) => id !== candId)]
        : [candId];
      return {
        ...prev,
        [currentProposal.proposal_id]: newGroup,
      };
    });

    setProposals((prev) => {
      const copy = [...prev];
      const group = copy[currentIndex].ordered_card_ids || [];
      const newGroup = group.length > 0
        ? [group[0], candId, ...group.slice(2).filter((id) => id !== candId)]
        : [candId];
      copy[currentIndex] = {
        ...copy[currentIndex],
        ordered_card_ids: newGroup,
        back_card_id: candId,
        is_standalone: false,
      };
      return copy;
    });
  }, [currentProposal, currentIndex]);

  const handleAttachCandidateToGroup = useCallback((candId: string) => {
    if (!currentProposal) return;
    setMultiCardGroups((prev) => {
      const group = prev[currentProposal.proposal_id] || currentProposal.ordered_card_ids || [];
      if (group.includes(candId)) return prev;
      return {
        ...prev,
        [currentProposal.proposal_id]: [...group, candId],
      };
    });

    setProposals((prev) => {
      const copy = [...prev];
      const group = copy[currentIndex].ordered_card_ids || [];
      if (!group.includes(candId)) {
        copy[currentIndex] = {
          ...copy[currentIndex],
          ordered_card_ids: [...group, candId],
        };
      }
      return copy;
    });
  }, [currentProposal, currentIndex]);

  const handleReorderGroup = useCallback((newOrder: string[]) => {
    if (!currentProposal) return;
    setMultiCardGroups((prev) => ({
      ...prev,
      [currentProposal.proposal_id]: newOrder,
    }));
    setProposals((prev) => {
      const copy = [...prev];
      copy[currentIndex] = {
        ...copy[currentIndex],
        ordered_card_ids: newOrder,
      };
      return copy;
    });
  }, [currentProposal, currentIndex]);

  const handleDetachCard = useCallback((cardId: string) => {
    if (!currentProposal) return;
    setMultiCardGroups((prev) => {
      const group = prev[currentProposal.proposal_id] || currentProposal.ordered_card_ids || [];
      const updated = group.filter((id) => id !== cardId);
      return {
        ...prev,
        [currentProposal.proposal_id]: updated,
      };
    });
    setProposals((prev) => {
      const copy = [...prev];
      const group = copy[currentIndex].ordered_card_ids || [];
      copy[currentIndex] = {
        ...copy[currentIndex],
        ordered_card_ids: group.filter((id) => id !== cardId),
      };
      return copy;
    });
  }, [currentProposal, currentIndex]);

  // Keyboard shortcut integration
  useKeyboard({
    enabled: !!currentProposal && !isDrawerOpen && !isInspectionModalOpen && !isSubmitting,
    onApprove: handleApprove,
    onStandalone: handleStandalone,
    onReverse: handleReverse,
    onReject: handleReject,
    onToggleCandidates: handleToggleDrawer,
    onEscape: () => {
      setIsDrawerOpen(false);
      setIsInspectionModalOpen(false);
    },
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

  return (
    <div className="flex flex-col gap-6" data-tour="rapid-queue">
      {/* Budget Cap HITL Item Banner */}
      {budgetItem && (
        <div
          role="alert"
          aria-label="Budget cap reached"
          className="p-5 bg-amber-50 dark:bg-amber-950/60 border border-amber-300 dark:border-amber-700 rounded-xl flex flex-col gap-3 shadow-xs"
        >
          <div className="flex items-center gap-2 text-amber-900 dark:text-amber-100 font-bold text-sm">
            <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400" />
            <span>Budget Cap Reached &mdash; Pipeline Paused</span>
          </div>
          <p className="text-xs text-amber-800 dark:text-amber-200 leading-relaxed">
            {budgetItem.message ||
              'The pipeline has paused execution because the spending budget cap was reached. You can raise the cap to resume processing or stop the run.'}
          </p>
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Button
              variant="primary"
              size="sm"
              onClick={async () => {
                try {
                  await api.resumeSession(sessionId);
                  setBudgetItem(null);
                  fetchProposals();
                } catch (e: any) {
                  setError(e.message || 'Failed to resume session');
                }
              }}
              aria-label="Raise budget cap and resume processing"
            >
              Raise Cap &amp; Resume
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setBudgetItem(null);
                onApprovalsCompleted();
              }}
              aria-label="Stop processing"
            >
              Stop Pipeline
            </Button>
          </div>
        </div>
      )}

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
          onOpenInspectionModal={() => setIsInspectionModalOpen(true)}
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
          frontCardId={currentGroup[0] || currentProposal.ordered_card_ids?.[0] || ''}
          selectedCardId={currentGroup[0] || currentProposal.ordered_card_ids?.[0] || ''}
          candidates={candidates}
          currentBackCardId={currentGroup[1] || null}
          groupedCardIds={currentGroup}
          onSelectCandidateAsBack={handleSelectCandidateAsBack}
          onAttachCandidateToGroup={handleAttachCandidateToGroup}
          isLoading={loadingCandidates}
        />
      )}

      {/* Dedicated Multi-Card Full-Size Inspection Modal */}
      {currentProposal && (
        <MultiCardInspectionModal
          isOpen={isInspectionModalOpen}
          onClose={() => setIsInspectionModalOpen(false)}
          cardIds={currentGroup}
          sessionId={sessionId}
          proposalId={currentProposal.proposal_id}
          onReorder={handleReorderGroup}
          onDetach={handleDetachCard}
          onApprove={(orderedIds) => {
            handleReorderGroup(orderedIds);
            const approval: HitlApproval = {
              proposal_id: currentProposal.proposal_id,
              ordered_card_ids: orderedIds,
              front_card_id: orderedIds[0],
              back_card_id: orderedIds[1] || null,
              card_ids: orderedIds,
              is_standalone: orderedIds.length === 1 || currentProposal.is_standalone,
              is_rejected: false,
            };
            setApprovals((prev) => [
              ...prev.filter((a) => a.proposal_id !== approval.proposal_id),
              approval,
            ]);
            setIsInspectionModalOpen(false);
            advanceQueue();
          }}
        />
      )}
    </div>
  );
};

