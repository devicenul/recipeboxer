import React, { useState } from 'react';
import { Layers, X, ArrowLeft, ArrowRight, Maximize2, GripVertical } from 'lucide-react';
import { api } from '../../utils/api';

export interface MultiCardGroupingProps {
  cardIds: string[];
  sessionId?: string;
  onReorder: (newOrder: string[]) => void;
  onDetach: (cardId: string) => void;
  onOpenInspectionModal?: () => void;
  className?: string;
}

export const MultiCardGrouping: React.FC<MultiCardGroupingProps> = ({
  cardIds,
  sessionId,
  onReorder,
  onDetach,
  onOpenInspectionModal,
  className = '',
}) => {
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

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

  const handleDragStart = (e: React.DragEvent, index: number) => {
    setDraggedIndex(index);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(index));
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    if (dragOverIndex !== index) {
      setDragOverIndex(index);
    }
  };

  const handleDragEnd = () => {
    setDraggedIndex(null);
    setDragOverIndex(null);
  };

  const handleDrop = (e: React.DragEvent, targetIndex: number) => {
    e.preventDefault();
    const sourceIdxStr = e.dataTransfer.getData('text/plain');
    const sourceIndex = sourceIdxStr !== '' ? parseInt(sourceIdxStr, 10) : draggedIndex;

    if (sourceIndex !== null && !isNaN(sourceIndex) && sourceIndex !== targetIndex) {
      const next = [...cardIds];
      const [movedItem] = next.splice(sourceIndex, 1);
      next.splice(targetIndex, 0, movedItem);
      onReorder(next);
    }
    setDraggedIndex(null);
    setDragOverIndex(null);
  };

  return (
    <div className={`p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-xs flex flex-col gap-3 ${className}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-900 dark:text-slate-100">
          <Layers className="w-4 h-4 text-purple-500" />
          <span>Multi-Card Recipe Group ({cardIds.length} cards attached)</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-slate-500 dark:text-slate-400">
            Reorder sequential pages for transcription
          </span>
          {onOpenInspectionModal && (
            <button
              type="button"
              onClick={onOpenInspectionModal}
              className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] font-semibold text-purple-700 hover:text-purple-800 dark:text-purple-300 dark:hover:text-purple-200 bg-purple-50 hover:bg-purple-100 dark:bg-purple-950/60 dark:hover:bg-purple-900/60 border border-purple-200 dark:border-purple-800 rounded-lg cursor-pointer transition-colors"
              title="Inspect all cards in full-size split canvas view"
            >
              <Maximize2 className="w-3.5 h-3.5" />
              <span>Inspect Full-Size Modal</span>
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3 overflow-x-auto pb-2 pt-1">
        {cardIds.map((cid, idx) => {
          const isFront = idx === 0;
          const isBack = idx === 1;
          const roleLabel = isFront ? 'Front (Page 1)' : isBack ? 'Back (Page 2)' : `Part ${idx + 1}`;
          const imgUrl = api.getCardImageUrl(cid, sessionId);
          const isDragging = draggedIndex === idx;
          const isDragOver = dragOverIndex === idx;

          return (
            <div
              key={cid}
              draggable
              onDragStart={(e) => handleDragStart(e, idx)}
              onDragOver={(e) => handleDragOver(e, idx)}
              onDragEnd={handleDragEnd}
              onDrop={(e) => handleDrop(e, idx)}
              className={`flex items-center gap-2 shrink-0 group relative bg-slate-50 dark:bg-slate-800/60 border rounded-xl p-2.5 shadow-xs transition-all duration-150 ${
                isDragging
                  ? 'opacity-40 border-dashed border-blue-500'
                  : isDragOver
                  ? 'border-blue-500 ring-2 ring-blue-400/30'
                  : 'border-slate-200 dark:border-slate-700'
              }`}
            >
              {/* Thumbnail (Click to open full inspection) */}
              <div
                onClick={() => onOpenInspectionModal?.()}
                className={`w-16 h-20 bg-slate-200 dark:bg-slate-900 rounded-lg overflow-hidden border border-slate-300 dark:border-slate-700 relative ${
                  onOpenInspectionModal ? 'cursor-pointer hover:opacity-90 hover:ring-2 hover:ring-purple-400/50' : ''
                }`}
                title={onOpenInspectionModal ? 'Click to inspect in full-size modal' : cid}
              >
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

