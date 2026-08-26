import React from 'react';
import { Check, UserX, ArrowLeftRight, X, Sparkles, SlidersHorizontal, Maximize2 } from 'lucide-react';
import { CorrelationProposal } from '../../types/hitl';
import { CardViewerCanvas } from '../viewer/CardViewerCanvas';
import { VisualToolbar } from '../viewer/VisualToolbar';
import { ConfidenceMeter } from './ConfidenceMeter';
import { MultiCardGrouping } from './MultiCardGrouping';
import { useImageViewer } from '../../hooks/useImageViewer';
import { HotkeyBadge } from '../common/HotkeyBadge';
import { api } from '../../utils/api';

export interface RapidQueueCardProps {
  proposal: CorrelationProposal;
  currentIndex: number;
  totalProposals: number;
  sessionId?: string;
  groupedCardIds?: string[];
  onApprove: () => void;
  onStandalone: () => void;
  onReverse: () => void;
  onReject: () => void;
  onToggleDrawer: () => void;
  onReorderGroup?: (order: string[]) => void;
  onDetachCard?: (cardId: string) => void;
  onOpenInspectionModal?: () => void;
}

export const RapidQueueCard: React.FC<RapidQueueCardProps> = ({
  proposal,
  currentIndex,
  totalProposals,
  sessionId,
  groupedCardIds = [],
  onApprove,
  onStandalone,
  onReverse,
  onReject,
  onToggleDrawer,
  onReorderGroup,
  onDetachCard,
  onOpenInspectionModal,
}) => {
  const viewer = useImageViewer();

  const frontImageUrl = api.getCardImageUrl(proposal.front_card_id, sessionId);
  const backImageUrl = proposal.back_card_id
    ? api.getCardImageUrl(proposal.back_card_id, sessionId)
    : null;

  return (
    <div className="flex flex-col gap-4">
      {/* Header Bar: Telemetry, Index, Confidence */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-xs">
        <div className="flex items-center gap-3">
          <span className="px-3 py-1 text-xs font-bold font-mono rounded-lg bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
            Proposal {currentIndex + 1} of {totalProposals}
          </span>
          <span className="text-xs text-slate-500 dark:text-slate-400">
            ID: <strong className="font-mono text-slate-700 dark:text-slate-300">{proposal.proposal_id}</strong>
          </span>
          {groupedCardIds.length > 2 && onOpenInspectionModal && (
            <button
              type="button"
              onClick={onOpenInspectionModal}
              className="flex items-center gap-1.5 px-3 py-1 text-xs font-semibold rounded-lg bg-purple-100 text-purple-800 hover:bg-purple-200 dark:bg-purple-950 dark:text-purple-300 dark:hover:bg-purple-900 transition-colors cursor-pointer"
              title="Inspect all cards in full-size split view modal"
            >
              <Maximize2 className="w-3.5 h-3.5" />
              <span>Inspect All ({groupedCardIds.length} Cards)</span>
            </button>
          )}
        </div>

        {/* Global Toolbar for Dual Canvas Viewers */}
        <VisualToolbar
          linked={viewer.linked}
          onToggleLinked={() => viewer.setLinked(!viewer.linked)}
          onZoomIn={(target) => viewer.zoomIn(target)}
          onZoomOut={(target) => viewer.zoomOut(target)}
          onRotateCw={(target) => viewer.rotateCw(target)}
          onRotateCcw={(target) => viewer.rotateCcw(target)}
          onResetTransform={(target) => viewer.resetTransform(target)}
          filters={viewer.filtersFront}
          onChangeBrightness={(val) => viewer.setBrightness(val, 'both')}
          onChangeContrast={(val) => viewer.setContrast(val, 'both')}
          onToggleInvert={() => viewer.toggleInvert('both')}
          onResetFilters={() => viewer.resetFilters('both')}
          currentScale={viewer.transformFront.scale}
          showRotationTarget="both"
        />
      </div>

      {/* Dual Card Comparison Viewport */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 min-h-[420px]">
        {/* Left: Front Card */}
        <div className="flex flex-col gap-2">
          <CardViewerCanvas
            imageUrl={frontImageUrl}
            cardId={proposal.front_card_id}
            label="FRONT SCAN"
            roleBadge="Page 1 / Front"
            transform={viewer.transformFront}
            filters={viewer.filtersFront}
            onPan={(dx, dy) => viewer.pan(dx, dy, 'front')}
            onWheelZoom={(delta) => (delta > 0 ? viewer.zoomIn('front') : viewer.zoomOut('front'))}
          />
        </div>

        {/* Right: Proposed Back Card / Standalone */}
        <div className="flex flex-col gap-2">
          <CardViewerCanvas
            imageUrl={backImageUrl}
            cardId={proposal.back_card_id}
            label="PROPOSED BACK SCAN"
            roleBadge={proposal.back_card_id ? 'Page 2 / Back' : undefined}
            transform={viewer.transformBack}
            filters={viewer.filtersBack}
            onPan={(dx, dy) => viewer.pan(dx, dy, 'back')}
            onWheelZoom={(delta) => (delta > 0 ? viewer.zoomIn('back') : viewer.zoomOut('back'))}
            isStandalonePlaceholder={!proposal.back_card_id || proposal.is_standalone}
          />
        </div>
      </div>

      {/* Multi-Card Grouping Tray (if 3+ cards attached) */}
      {groupedCardIds.length > 2 && onReorderGroup && onDetachCard && (
        <MultiCardGrouping
          cardIds={groupedCardIds}
          sessionId={sessionId}
          onReorder={onReorderGroup}
          onDetach={onDetachCard}
          onOpenInspectionModal={onOpenInspectionModal}
        />
      )}

      {/* Confidence & Continuity Breakdown */}
      <ConfidenceMeter
        confidence={proposal.confidence}
        heuristicScore={proposal.heuristic_score}
        semanticScore={proposal.semantic_score}
        reasoningSnippet={proposal.continuity_explanation}
      />

      {/* Action Buttons with Keyboard Hotkeys */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5 pt-2">
        {/* 1. Approve */}
        <button
          type="button"
          onClick={onApprove}
          className="flex items-center justify-center gap-2 p-3 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-xs rounded-xl shadow-md transition-all cursor-pointer"
        >
          <Check className="w-4 h-4" />
          <span>Approve</span>
          <HotkeyBadge hotkey="Space" className="bg-emerald-800 text-white border-emerald-700" />
        </button>

        {/* 2. Standalone */}
        <button
          type="button"
          onClick={onStandalone}
          className="flex items-center justify-center gap-2 p-3 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs rounded-xl shadow-md transition-all cursor-pointer"
        >
          <UserX className="w-4 h-4" />
          <span>Standalone</span>
          <HotkeyBadge hotkey="S" className="bg-blue-800 text-white border-blue-700" />
        </button>

        {/* 3. Reverse Roles */}
        <button
          type="button"
          onClick={onReverse}
          disabled={!proposal.back_card_id}
          className="flex items-center justify-center gap-2 p-3 bg-purple-600 hover:bg-purple-700 text-white font-semibold text-xs rounded-xl shadow-md transition-all cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <ArrowLeftRight className="w-4 h-4" />
          <span>Reverse</span>
          <HotkeyBadge hotkey="B" className="bg-purple-800 text-white border-purple-700" />
        </button>

        {/* 4. Reject */}
        <button
          type="button"
          onClick={onReject}
          className="flex items-center justify-center gap-2 p-3 bg-rose-600 hover:bg-rose-700 text-white font-semibold text-xs rounded-xl shadow-md transition-all cursor-pointer"
        >
          <X className="w-4 h-4" />
          <span>Reject</span>
          <HotkeyBadge hotkey="X" className="bg-rose-800 text-white border-rose-700" />
        </button>

        {/* 5. Candidate Drawer */}
        <button
          type="button"
          onClick={onToggleDrawer}
          className="col-span-2 sm:col-span-1 flex items-center justify-center gap-2 p-3 bg-slate-700 hover:bg-slate-800 text-white font-semibold text-xs rounded-xl shadow-md transition-all cursor-pointer"
        >
          <Sparkles className="w-4 h-4 text-amber-400" />
          <span>Candidates</span>
          <HotkeyBadge hotkey="M" className="bg-slate-900 text-white border-slate-700" />
        </button>
      </div>
    </div>
  );
};
