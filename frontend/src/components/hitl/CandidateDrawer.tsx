import React from 'react';
import { X, Sparkles, Plus, Check, ImageOff, Loader2 } from 'lucide-react';
import { CorrelationCandidate } from '../../types/hitl';
import { formatScorePercent } from '../../utils/formatters';
import { CardHoverPreview } from '../common/CardHoverPreview';

export interface CandidateDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  selectedCardId?: string;
  frontCardId?: string;
  candidates: CorrelationCandidate[];
  currentBackCardId?: string | null;
  groupedCardIds?: string[];
  onSelectCandidateAsBack: (candidateId: string) => void;
  onAttachCandidateToGroup: (candidateId: string) => void;
  isLoading?: boolean;
}

export const CandidateDrawer: React.FC<CandidateDrawerProps> = ({
  isOpen,
  onClose,
  selectedCardId,
  frontCardId,
  candidates,
  currentBackCardId,
  groupedCardIds = [],
  onSelectCandidateAsBack,
  onAttachCandidateToGroup,
  isLoading = false,
}) => {
  if (!isOpen) return null;

  const displayCardId = selectedCardId || frontCardId || '';

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop */}
      <div
        onClick={onClose}
        className="absolute inset-0 bg-black/50 backdrop-blur-xs transition-opacity animate-in fade-in"
      />

      <div className="absolute inset-y-0 right-0 max-w-full flex pl-0 sm:pl-6 md:pl-10">
        <div
          className="w-full sm:w-[480px] md:w-[520px] max-w-full bg-white dark:bg-slate-900 border-l border-slate-200 dark:border-slate-800 shadow-2xl flex flex-col animate-in slide-in-from-right duration-200 overflow-hidden"
          data-tour="candidate-drawer"
        >
          {/* Drawer Header */}
          <div className="p-4 sm:p-5 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between shrink-0">
            <div>
              <h3 className="text-base font-bold text-slate-900 dark:text-slate-100 flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-blue-500 shrink-0" />
                <span>Candidate Correlation Drawer</span>
              </h3>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 truncate max-w-[280px] sm:max-w-sm">
                Selected Card: <strong className="font-mono text-slate-800 dark:text-slate-200">{displayCardId}</strong>
              </p>
            </div>
            <button
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 cursor-pointer shrink-0"
              title="Close drawer [Esc]"
              aria-label="Close drawer"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Drawer Content */}
          <div className="flex-1 overflow-y-auto overflow-x-hidden p-4 sm:p-5 flex flex-col gap-4">
            {isLoading ? (
              <div className="py-16 flex flex-col items-center justify-center gap-3 text-slate-400">
                <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
                <span className="text-xs">Computing visual and semantic features...</span>
              </div>
            ) : candidates.length === 0 ? (
              <div className="py-16 text-center text-xs text-slate-500 dark:text-slate-400 flex flex-col items-center gap-3">
                <ImageOff className="w-8 h-8 text-slate-400" />
                <div>
                  <p className="font-semibold text-slate-700 dark:text-slate-300">
                    No alternative candidates found
                  </p>
                  <p className="mt-1 max-w-xs">
                    All scans are assigned or no other cards match handwriting/tone heuristics.
                  </p>
                </div>
              </div>
            ) : (
              candidates.map((cand, idx) => {
                const isCurrentBack = cand.candidate_card_id === currentBackCardId;
                const isGrouped = groupedCardIds.includes(cand.candidate_card_id);
                const scorePct = Math.round((cand.score || cand.composite_score || 0) * 100);

                const getScoreColor = (pct: number) => {
                  if (pct >= 80) return 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/60 border-emerald-200 dark:border-emerald-800';
                  if (pct >= 50) return 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/60 border-amber-200 dark:border-amber-800';
                  return 'text-rose-600 dark:text-rose-400 bg-rose-50 dark:bg-rose-950/60 border-rose-200 dark:border-rose-800';
                };

                return (
                  <div
                    key={cand.candidate_card_id}
                    className={`p-3.5 sm:p-4 rounded-xl border transition-all flex flex-col gap-3 box-border w-full ${
                      isCurrentBack
                        ? 'border-blue-500 bg-blue-50/40 dark:bg-blue-950/30 ring-1 ring-blue-500'
                        : isGrouped
                        ? 'border-purple-500 bg-purple-50/40 dark:bg-purple-950/30'
                        : 'border-slate-200 dark:border-slate-800 bg-slate-50/60 dark:bg-slate-850/60 hover:border-slate-300 dark:hover:border-slate-700'
                    }`}
                  >
                    {/* Top Row: Thumbnail + Scores */}
                    <div className="flex items-start gap-3 min-w-0">
                      <CardHoverPreview
                        cardId={cand.candidate_card_id}
                        imageUrl={cand.candidate_image_path}
                        title={`Candidate: ${cand.candidate_card_id}`}
                        subtitle={`Confidence: ${scorePct}% | Heuristic: ${formatScorePercent(cand.heuristic_score)} | Semantic: ${formatScorePercent(cand.semantic_score)}`}
                      >
                        <div className="w-16 h-20 bg-slate-200 dark:bg-slate-900 rounded-lg overflow-hidden border border-slate-300 dark:border-slate-700 shrink-0 cursor-pointer">
                          {cand.candidate_image_path ? (
                            <img
                              src={cand.candidate_image_path}
                              alt={cand.candidate_card_id}
                              className="w-full h-full object-cover"
                              onError={(e) => {
                                (e.target as HTMLElement).style.display = 'none';
                              }}
                            />
                          ) : (
                            <div className="w-full h-full flex items-center justify-center text-slate-400">
                              <ImageOff className="w-5 h-5" />
                            </div>
                          )}
                        </div>
                      </CardHoverPreview>

                      <div className="flex-1 min-w-0 flex flex-col justify-between h-20">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-xs font-bold text-slate-900 dark:text-slate-100 truncate" title={cand.candidate_card_id}>
                            {cand.candidate_card_id}
                          </span>
                          <span className={`px-2 py-0.5 text-xs font-bold font-mono rounded-full border shrink-0 ${getScoreColor(scorePct)}`}>
                            {scorePct}%
                          </span>
                        </div>

                        <div className="text-[11px] text-slate-500 dark:text-slate-400 flex flex-col gap-0.5 mt-1 font-mono">
                          <div className="truncate">Heuristic: <strong>{formatScorePercent(cand.heuristic_score)}</strong></div>
                          <div className="truncate">Semantic: <strong>{formatScorePercent(cand.semantic_score)}</strong></div>
                        </div>
                      </div>
                    </div>

                    {/* Reasoning Snippet */}
                    {cand.reasoning_snippet && (
                      <p className="text-xs text-slate-600 dark:text-slate-400 italic bg-white dark:bg-slate-900 p-2 rounded-lg border border-slate-200/60 dark:border-slate-800 leading-relaxed break-words">
                        "{cand.reasoning_snippet}"
                      </p>
                    )}

                    {/* Actions */}
                    <div className="flex items-center gap-2 pt-1 w-full box-border">
                      <button
                        type="button"
                        onClick={() => onSelectCandidateAsBack(cand.candidate_card_id)}
                        disabled={isCurrentBack}
                        className={`flex-1 min-w-0 flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-lg transition-all cursor-pointer truncate ${
                          isCurrentBack
                            ? 'bg-blue-600 text-white cursor-default'
                            : 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300 hover:bg-blue-200 dark:hover:bg-blue-900'
                        }`}
                      >
                        {isCurrentBack ? <Check className="w-3.5 h-3.5 shrink-0" /> : null}
                        <span className="truncate">{isCurrentBack ? 'Active Next Part' : 'Select as Next Part'}</span>
                      </button>

                      <button
                        type="button"
                        onClick={() => onAttachCandidateToGroup(cand.candidate_card_id)}
                        disabled={isGrouped}
                        className={`shrink-0 flex items-center justify-center gap-1 px-3.5 py-2 text-xs font-semibold rounded-lg transition-all cursor-pointer ${
                          isGrouped
                            ? 'bg-purple-600 text-white cursor-default'
                            : 'bg-purple-100 text-purple-800 dark:bg-purple-950 dark:text-purple-300 hover:bg-purple-200 dark:hover:bg-purple-900'
                        }`}
                        title="Attach as Next Part (Part N+1)"
                      >
                        <Plus className="w-3.5 h-3.5 shrink-0" />
                        <span>{isGrouped ? 'Attached' : 'Attach'}</span>
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

