import React, { useState } from 'react';
import {
  Layers,
  ArrowLeft,
  ArrowRight,
  ArrowLeftRight,
  X,
  Check,
  GripVertical,
} from 'lucide-react';
import { Modal } from '../common/Modal';
import { Button } from '../common/Button';
import { CardViewerCanvas } from '../viewer/CardViewerCanvas';
import { VisualToolbar } from '../viewer/VisualToolbar';
import { useImageViewer } from '../../hooks/useImageViewer';
import { api } from '../../utils/api';

export interface MultiCardInspectionModalProps {
  isOpen: boolean;
  onClose: () => void;
  cardIds: string[];
  sessionId?: string;
  proposalId?: string;
  onReorder: (newOrder: string[]) => void;
  onDetach: (cardId: string) => void;
  onApprove: (orderedCardIds: string[]) => void;
}

export const MultiCardInspectionModal: React.FC<MultiCardInspectionModalProps> = ({
  isOpen,
  onClose,
  cardIds,
  sessionId,
  proposalId,
  onReorder,
  onDetach,
  onApprove,
}) => {
  const viewer = useImageViewer();
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  if (!isOpen || !cardIds || cardIds.length === 0) {
    return null;
  }

  const handleMoveLeft = (index: number) => {
    if (index <= 0) return;
    const next = [...cardIds];
    const temp = next[index - 1];
    next[index - 1] = next[index];
    next[index] = temp;
    onReorder(next);
  };

  const handleMoveRight = (index: number) => {
    if (index >= cardIds.length - 1) return;
    const next = [...cardIds];
    const temp = next[index + 1];
    next[index + 1] = next[index];
    next[index] = temp;
    onReorder(next);
  };

  const handleMoveToRank = (currentIndex: number, targetRank: number) => {
    if (targetRank < 0 || targetRank >= cardIds.length || currentIndex === targetRank) return;
    const next = [...cardIds];
    const [movedItem] = next.splice(currentIndex, 1);
    next.splice(targetRank, 0, movedItem);
    onReorder(next);
  };

  const handleReverseOrder = () => {
    if (cardIds.length <= 1) return;
    const reversed = [...cardIds].reverse();
    onReorder(reversed);
  };

  // Drag and Drop handlers
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
      handleMoveToRank(sourceIndex, targetIndex);
    }
    setDraggedIndex(null);
    setDragOverIndex(null);
  };

  const getRoleBadge = (idx: number, total: number) => {
    return `Part ${idx + 1} of ${total}`;
  };

  // Responsive grid columns based on card count
  const gridColsClass =
    cardIds.length === 2
      ? 'grid-cols-1 md:grid-cols-2'
      : cardIds.length === 3
      ? 'grid-cols-1 md:grid-cols-3'
      : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4';

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Multi-Card Inspection & Sequencing (${cardIds.length} Cards)`}
      maxWidth="full"
      footer={
        <div className="flex items-center justify-between w-full">
          <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <span>
              Sequence: <strong className="font-mono text-slate-800 dark:text-slate-200">{cardIds.join(' \u2192 ')}</strong>
            </span>
          </div>
          <div className="flex items-center gap-2.5">
            <Button
              variant="secondary"
              size="sm"
              onClick={handleReverseOrder}
              leftIcon={<ArrowLeftRight className="w-4 h-4" />}
            >
              Reverse Order
            </Button>
            <Button variant="secondary" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="success"
              size="md"
              onClick={() => onApprove(cardIds)}
              leftIcon={<Check className="w-4 h-4" />}
            >
              Approve Multi-Card Sequence ({cardIds.length} Cards)
            </Button>
          </div>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        {/* Subheader & Controls Bar */}
        <div className="flex flex-wrap items-center justify-between gap-4 p-3 bg-slate-50 dark:bg-slate-800/80 border border-slate-200 dark:border-slate-700 rounded-xl">
          <div className="flex items-center gap-2">
            <Layers className="w-4 h-4 text-purple-500" />
            <span className="text-xs font-semibold text-slate-900 dark:text-slate-100">
              Full-Resolution Split Canvas Comparison
            </span>
            <span className="text-[11px] text-slate-500 dark:text-slate-400">
              (Drag cards, choose rank, or use arrows to adjust transcription order)
            </span>
          </div>

          {/* Visual Toolbar for Filter / Zoom adjustments */}
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

        {/* Multi-Card Canvas Grid */}
        <div className={`grid ${gridColsClass} gap-4 overflow-y-auto max-h-[62vh] p-1`}>
          {cardIds.map((cid, idx) => {
            const roleLabel = getRoleBadge(idx, cardIds.length);
            const imageUrl = api.getCardImageUrl(cid, sessionId);
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
                className={`flex flex-col gap-2 p-3 bg-white dark:bg-slate-900 rounded-xl border transition-all duration-150 ${
                  isDragging
                    ? 'opacity-40 border-dashed border-blue-500 scale-98'
                    : isDragOver
                    ? 'border-blue-500 shadow-md ring-2 ring-blue-400/30'
                    : 'border-slate-200 dark:border-slate-800 shadow-xs'
                }`}
              >
                {/* Card Header: Drag handle, Positional badge, Rank select, Actions */}
                <div className="flex items-center justify-between gap-2 pb-2 border-b border-slate-100 dark:border-slate-800">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <div
                      className="cursor-grab active:cursor-grabbing text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 p-0.5"
                      title="Drag to reorder position"
                    >
                      <GripVertical className="w-4 h-4" />
                    </div>
                    <span className="px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider rounded-md bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300 shrink-0">
                      #{idx + 1} {roleLabel}
                    </span>
                  </div>

                  <div className="flex items-center gap-1.5 shrink-0">
                    {/* Explicit Rank Dropdown */}
                    <label className="text-[10px] text-slate-500 dark:text-slate-400 font-medium flex items-center gap-1">
                      <span className="sr-only">Position</span>
                      <select
                        value={idx}
                        onChange={(e) => handleMoveToRank(idx, parseInt(e.target.value, 10))}
                        className="text-[11px] font-semibold bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-md px-1.5 py-0.5 text-slate-800 dark:text-slate-200 cursor-pointer"
                        aria-label={`Position rank for ${cid}`}
                      >
                        {cardIds.map((_, optIdx) => (
                          <option key={optIdx} value={optIdx}>
                            Part {optIdx + 1} of {cardIds.length}
                          </option>
                        ))}
                      </select>
                    </label>

                    {/* Move Earlier Arrow */}
                    <button
                      type="button"
                      onClick={() => handleMoveLeft(idx)}
                      disabled={idx === 0}
                      className="p-1 rounded-md text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-30 cursor-pointer"
                      title="Move earlier"
                      aria-label={`Move ${cid} earlier`}
                    >
                      <ArrowLeft className="w-3.5 h-3.5" />
                    </button>

                    {/* Move Later Arrow */}
                    <button
                      type="button"
                      onClick={() => handleMoveRight(idx)}
                      disabled={idx === cardIds.length - 1}
                      className="p-1 rounded-md text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-30 cursor-pointer"
                      title="Move later"
                      aria-label={`Move ${cid} later`}
                    >
                      <ArrowRight className="w-3.5 h-3.5" />
                    </button>

                    {/* Detach Card Action */}
                    <button
                      type="button"
                      onClick={() => onDetach(cid)}
                      className="p-1 rounded-md text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/50 cursor-pointer ml-1"
                      title="Detach card from group"
                      aria-label={`Detach ${cid} from group`}
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {/* Card ID */}
                <div className="text-[11px] font-mono text-slate-600 dark:text-slate-400 truncate px-0.5" title={cid}>
                  {cid}
                </div>

                {/* Full-Scale Canvas Viewport */}
                <div className="h-[360px] relative rounded-lg overflow-hidden border border-slate-200 dark:border-slate-800">
                  <CardViewerCanvas
                    imageUrl={imageUrl}
                    cardId={cid}
                    label={`SCAN #${idx + 1}`}
                    roleBadge={roleLabel}
                    transform={viewer.transformFront}
                    filters={viewer.filtersFront}
                    onPan={(dx, dy) => viewer.pan(dx, dy, 'front')}
                    onWheelZoom={(delta) => (delta > 0 ? viewer.zoomIn('front') : viewer.zoomOut('front'))}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </Modal>
  );
};

