import React from 'react';
import { Layers, X, ArrowLeft, ArrowRight } from 'lucide-react';
import { api } from '../../utils/api';

export interface MultiCardGroupingProps {
  cardIds: string[];
  sessionId?: string;
  onReorder: (newOrder: string[]) => void;
  onDetach: (cardId: string) => void;
  className?: string;
}

export const MultiCardGrouping: React.FC<MultiCardGroupingProps> = ({
  cardIds,
  sessionId,
  onReorder,
  onDetach,
  className = '',
}) => {
  if (!cardIds || cardIds.length <= 1) {
    return null;
  }

  const moveLeft = (index: number) => {
    if (index <= 0) return;
    const next = [...cardIds];
    const temp = next[index - 1];
    next[index - 1] = next[index];
    next[index] = temp;
    onReorder(next);
  };

  const moveRight = (index: number) => {
    if (index >= cardIds.length - 1) return;
    const next = [...cardIds];
    const temp = next[index + 1];
    next[index + 1] = next[index];
    next[index] = temp;
    onReorder(next);
  };

  return (
    <div className={`p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-xs flex flex-col gap-3 ${className}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-900 dark:text-slate-100">
          <Layers className="w-4 h-4 text-purple-500" />
          <span>Multi-Card Recipe Group ({cardIds.length} cards attached)</span>
        </div>
        <span className="text-[11px] text-slate-500 dark:text-slate-400">
          Reorder sequential pages for transcription
        </span>
      </div>

      <div className="flex items-center gap-3 overflow-x-auto pb-2 pt-1">
        {cardIds.map((cid, idx) => {
          const isFront = idx === 0;
          const isBack = idx === 1;
          const roleLabel = isFront ? 'Front (Page 1)' : isBack ? 'Back (Page 2)' : `Part ${idx + 1}`;
          const imgUrl = api.getCardImageUrl(cid, sessionId);

          return (
            <div
              key={cid}
              className="flex items-center gap-2 shrink-0 group relative bg-slate-50 dark:bg-slate-800/60 border border-slate-200 dark:border-slate-700 rounded-xl p-2.5 shadow-xs"
            >
              {/* Thumbnail */}
              <div className="w-16 h-20 bg-slate-200 dark:bg-slate-900 rounded-lg overflow-hidden border border-slate-300 dark:border-slate-700 relative">
                <img
                  src={imgUrl}
                  alt={cid}
                  className="w-full h-full object-cover"
                  onError={(e) => {
                    (e.target as HTMLElement).style.display = 'none';
                  }}
                />
                <span className="absolute bottom-1 right-1 px-1 py-0.5 text-[9px] font-bold rounded bg-black/70 text-white">
                  #{idx + 1}
                </span>
              </div>

              {/* Info & Actions */}
              <div className="flex flex-col justify-between h-20 min-w-[90px]">
                <div>
                  <span className="text-[10px] font-bold text-blue-600 dark:text-blue-400 uppercase tracking-wider block">
                    {roleLabel}
                  </span>
                  <span className="text-xs font-mono font-medium text-slate-800 dark:text-slate-200 truncate block max-w-[100px]" title={cid}>
                    {cid}
                  </span>
                </div>

                <div className="flex items-center gap-1 mt-auto">
                  <button
                    type="button"
                    onClick={() => moveLeft(idx)}
                    disabled={idx === 0}
                    className="p-1 rounded text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-30 cursor-pointer"
                    title="Move earlier"
                  >
                    <ArrowLeft className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => moveRight(idx)}
                    disabled={idx === cardIds.length - 1}
                    className="p-1 rounded text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-200 dark:hover:bg-slate-700 disabled:opacity-30 cursor-pointer"
                    title="Move later"
                  >
                    <ArrowRight className="w-3.5 h-3.5" />
                  </button>
                  {!isFront && (
                    <button
                      type="button"
                      onClick={() => onDetach(cid)}
                      className="p-1 rounded text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-slate-200 dark:hover:bg-slate-700 cursor-pointer ml-auto"
                      title="Detach from group"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
