import React from 'react';
import { Plus, Trash2, ArrowUp, ArrowDown, AlertTriangle, Eye } from 'lucide-react';
import { Step, displayText } from '../../types/recipe';

export interface DirectionListInputProps {
  directions: Array<Step | string>;
  onChangeItem: (index: number, val: string) => void;
  onAddItem: (afterIndex?: number) => void;
  onRemoveItem: (index: number) => void;
  onMoveItem: (from: number, to: number) => void;
  error?: string;
  disabled?: boolean;
  showOriginal?: boolean;
  onSelectFace?: (faceIndex: number) => void;
  activeFaceIndex?: number;
}

export const DirectionListInput: React.FC<DirectionListInputProps> = ({
  directions,
  onChangeItem,
  onAddItem,
  onRemoveItem,
  onMoveItem,
  error,
  disabled = false,
  showOriginal = false,
  onSelectFace,
  activeFaceIndex,
}) => {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <label className="text-xs font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-1.5">
          <span>Preparation Steps ({directions.filter((d) => (typeof d === 'string' ? d.trim() : displayText(d).trim())).length})</span>
          {showOriginal && (
            <span className="px-1.5 py-0.5 text-[10px] font-mono rounded bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-300">
              Verbatim Original
            </span>
          )}
        </label>
        <button
          type="button"
          onClick={() => onAddItem()}
          disabled={disabled}
          className="flex items-center gap-1 text-xs font-semibold text-blue-600 dark:text-blue-400 hover:underline cursor-pointer disabled:opacity-50"
        >
          <Plus className="w-3.5 h-3.5" />
          Add Step
        </button>
      </div>

      <div className="flex flex-col gap-2.5">
        {directions.map((rawStep, idx) => {
          const isStepObj = typeof rawStep === 'object' && rawStep !== null;
          const step: Step | null = isStepObj ? (rawStep as Step) : null;
          const rawText = isStepObj
            ? showOriginal
              ? step?.original_text || displayText(step)
              : displayText(step)
            : (rawStep as string);

          // Clean visual representation for editing
          const cleanText = rawText.replace(/^\d+[\.\)\-:]\s*/, '').replace(/^Step\s*\d+[\.\)\-:]\s*/i, '');

          const legibility = step?.legibility?.toUpperCase() || 'CLEAR';
          const isUncertain = legibility === 'UNCERTAIN' || (step?.confidence !== undefined && step.confidence < 0.8);
          const isIllegible = legibility === 'ILLEGIBLE';
          const illegibleSpans = step?.illegible_spans || [];
          const sourceFace = step?.source_face ?? 0;
          const isFaceActive = activeFaceIndex !== undefined && activeFaceIndex === sourceFace;

          // Check for crossed-out or illegible cues in step text
          const hasCrossedOutCue = /\[crossed[- ]out/i.test(rawText);
          const hasIllegibleCue = /\[illegible/i.test(rawText);

          return (
            <div
              key={idx}
              className={`flex flex-col gap-1 p-2 rounded-lg border transition-all ${
                isFaceActive
                  ? 'border-blue-400 dark:border-blue-600 bg-blue-50/40 dark:bg-blue-950/20'
                  : 'border-slate-200 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/50'
              }`}
            >
              <div className="flex items-start gap-2 group">
                <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300 font-bold text-xs flex items-center justify-center shrink-0 mt-1 select-none font-mono">
                  {idx + 1}
                </span>

                <div className="relative flex-1">
                  <textarea
                    rows={2}
                    placeholder={`Step ${idx + 1} instructions...`}
                    value={cleanText}
                    disabled={disabled}
                    onFocus={() => onSelectFace?.(sourceFace)}
                    onClick={() => onSelectFace?.(sourceFace)}
                    onChange={(e) => onChangeItem(idx, e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        onAddItem(idx);
                      }
                    }}
                    aria-label={`Step ${idx + 1}`}
                    className={`w-full px-3 py-1.5 text-sm bg-white dark:bg-slate-800 border rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 resize-y ${
                      hasCrossedOutCue ? 'line-through decoration-rose-500 text-slate-500 dark:text-slate-400' : ''
                    } ${
                      isIllegible
                        ? 'border-rose-400 dark:border-rose-600 border-b-2 bg-rose-50/30 dark:bg-rose-950/20'
                        : isUncertain
                        ? 'border-amber-400 dark:border-amber-600 border-b-2 bg-amber-50/30 dark:bg-amber-950/20'
                        : 'border-slate-300 dark:border-slate-700'
                    }`}
                  />
                </div>

                <div className="flex flex-col items-center gap-0.5 opacity-60 group-hover:opacity-100 transition-opacity mt-1">
                  <button
                    type="button"
                    onClick={() => onMoveItem(idx, idx - 1)}
                    disabled={disabled || idx === 0}
                    className="p-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-20 cursor-pointer"
                    title="Move up"
                    aria-label={`Move step ${idx + 1} up`}
                  >
                    <ArrowUp className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onMoveItem(idx, idx + 1)}
                    disabled={disabled || idx === directions.length - 1}
                    className="p-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-20 cursor-pointer"
                    title="Move down"
                    aria-label={`Move step ${idx + 1} down`}
                  >
                    <ArrowDown className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemoveItem(idx)}
                    disabled={disabled || directions.length <= 1}
                    className="p-1 text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 disabled:opacity-20 cursor-pointer"
                    title="Remove step"
                    aria-label={`Remove step ${idx + 1}`}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              {/* Legibility & Source Face Metadata */}
              {(isUncertain || isIllegible || isStepObj || hasCrossedOutCue || hasIllegibleCue) && (
                <div className="flex flex-wrap items-center gap-1.5 pl-8 text-[11px]">
                  {/* Source Face Tag */}
                  {isStepObj && (
                    <button
                      type="button"
                      onClick={() => onSelectFace?.(sourceFace)}
                      aria-label={`View source card face ${sourceFace + 1}`}
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold transition-colors cursor-pointer ${
                        isFaceActive
                          ? 'bg-blue-600 text-white'
                          : 'bg-blue-50 text-blue-700 hover:bg-blue-100 dark:bg-blue-950/60 dark:text-blue-300 dark:hover:bg-blue-900 border border-blue-200 dark:border-blue-800'
                      }`}
                      title={`Extracted from card face ${sourceFace + 1} (click to highlight in viewer)`}
                    >
                      <Eye className="w-3 h-3" />
                      Face {sourceFace + 1}
                    </button>
                  )}

                  {/* Uncertain Legibility Amber Badge */}
                  {isUncertain && (
                    <span
                      role="status"
                      aria-label="Uncertain legibility"
                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 border border-amber-300 dark:border-amber-800"
                      title={step?.confidence ? `Low confidence: ${Math.round(step.confidence * 100)}%` : 'Hard to decipher text'}
                    >
                      <AlertTriangle className="w-3 h-3 text-amber-600 dark:text-amber-400" />
                      Uncertain
                    </span>
                  )}

                  {/* Illegible Red Badge */}
                  {isIllegible && (
                    <span
                      role="status"
                      aria-label="Illegible text"
                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300 border border-rose-300 dark:border-rose-800"
                    >
                      [illegible]
                    </span>
                  )}

                  {/* Illegible Spans */}
                  {illegibleSpans.map((span, sIdx) => (
                    <span
                      key={sIdx}
                      role="status"
                      aria-label={`Illegible span: ${span.reason}${span.text_guess ? `, guess: ${span.text_guess}` : ''}`}
                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-rose-50 text-rose-700 dark:bg-rose-950/60 dark:text-rose-300 border border-rose-200 dark:border-rose-800"
                      title={span.text_guess ? `Best guess: ${span.text_guess}` : `Reason: ${span.reason}`}
                    >
                      [{span.reason || 'illegible'}{span.text_guess ? `: ${span.text_guess}` : ''}]
                    </span>
                  ))}

                  {/* Crossed-out Chip */}
                  {hasCrossedOutCue && (
                    <span
                      role="status"
                      aria-label="Crossed out text"
                      className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-600 line-through"
                    >
                      [crossed out]
                    </span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {error && <span className="text-xs text-rose-500 font-medium">{error}</span>}
    </div>
  );
};
