import React from 'react';
import { Plus, Trash2, ArrowUp, ArrowDown, AlertTriangle, Eye, HelpCircle } from 'lucide-react';
import { IngredientLine, displayText } from '../../types/recipe';

export interface IngredientListInputProps {
  ingredients: Array<IngredientLine | string>;
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

export const IngredientListInput: React.FC<IngredientListInputProps> = ({
  ingredients,
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
          <span>Ingredients ({ingredients.filter((i) => (typeof i === 'string' ? i.trim() : displayText(i).trim())).length})</span>
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
          Add Ingredient
        </button>
      </div>

      <div className="flex flex-col gap-2">
        {ingredients.map((rawItem, idx) => {
          const isLineObj = typeof rawItem === 'object' && rawItem !== null;
          const line: IngredientLine | null = isLineObj ? (rawItem as IngredientLine) : null;
          const displayVal = isLineObj
            ? showOriginal
              ? line?.original_text || displayText(line)
              : displayText(line)
            : (rawItem as string);

          const legibility = line?.legibility?.toUpperCase() || 'CLEAR';
          const isUncertain = legibility === 'UNCERTAIN' || (line?.confidence !== undefined && line.confidence < 0.8);
          const isIllegible = legibility === 'ILLEGIBLE';
          const illegibleSpans = line?.illegible_spans || [];
          const hasNullQty = isLineObj && (line?.quantity === null || line?.quantity === undefined || line?.quantity === '');
          const sourceFace = line?.source_face ?? 0;
          const isFaceActive = activeFaceIndex !== undefined && activeFaceIndex === sourceFace;

          // Check for inline crossed-out or illegible cues in text
          const rawStr = typeof rawItem === 'string' ? rawItem : (line?.original_text || displayVal);
          const hasCrossedOutCue = /\[crossed[- ]out/i.test(rawStr);
          const hasIllegibleCue = /\[illegible/i.test(rawStr);

          return (
            <div
              key={idx}
              className={`flex flex-col gap-1 p-2 rounded-lg border transition-all ${
                isFaceActive
                  ? 'border-blue-400 dark:border-blue-600 bg-blue-50/40 dark:bg-blue-950/20'
                  : 'border-slate-200 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/50'
              }`}
            >
              <div className="flex items-center gap-2 group">
                <span className="w-5 text-right font-mono text-xs text-slate-400 select-none">
                  {idx + 1}.
                </span>

                <div className="relative flex-1">
                  <input
                    type="text"
                    placeholder="e.g. 2 cups all-purpose flour"
                    value={displayVal}
                    disabled={disabled}
                    onFocus={() => onSelectFace?.(sourceFace)}
                    onClick={() => onSelectFace?.(sourceFace)}
                    onChange={(e) => onChangeItem(idx, e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        onAddItem(idx);
                      }
                    }}
                    aria-label={`Ingredient ${idx + 1}`}
                    className={`w-full px-3 py-1.5 text-sm bg-white dark:bg-slate-800 border rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 ${
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

                <div className="flex items-center gap-0.5 opacity-60 group-hover:opacity-100 transition-opacity">
                  <button
                    type="button"
                    onClick={() => onMoveItem(idx, idx - 1)}
                    disabled={disabled || idx === 0}
                    className="p-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-20 cursor-pointer"
                    title="Move up"
                    aria-label={`Move ingredient ${idx + 1} up`}
                  >
                    <ArrowUp className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onMoveItem(idx, idx + 1)}
                    disabled={disabled || idx === ingredients.length - 1}
                    className="p-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-20 cursor-pointer"
                    title="Move down"
                    aria-label={`Move ingredient ${idx + 1} down`}
                  >
                    <ArrowDown className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemoveItem(idx)}
                    disabled={disabled || ingredients.length <= 1}
                    className="p-1 text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 disabled:opacity-20 cursor-pointer"
                    title="Remove ingredient"
                    aria-label={`Remove ingredient ${idx + 1}`}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              {/* Legibility & Source Metadata Indicators */}
              {(isUncertain || isIllegible || hasNullQty || isLineObj || hasCrossedOutCue || hasIllegibleCue) && (
                <div className="flex flex-wrap items-center gap-1.5 pl-7 text-[11px]">
                  {/* Source Face Tag */}
                  {isLineObj && (
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
                      title={line?.confidence ? `Low confidence: ${Math.round(line.confidence * 100)}%` : 'Hard to decipher text'}
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
                      title="Unreadable handwriting or damaged card portion"
                    >
                      [illegible]
                    </span>
                  )}

                  {/* Illegible Spans (faded, torn, stain, crossed_out) */}
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

                  {/* Uncertain / Null Quantity Badge */}
                  {hasNullQty && (
                    <span
                      role="status"
                      aria-label="Uncertain quantity: null"
                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400 border border-slate-300 dark:border-slate-700"
                      title="Quantity not explicitly stated on card (omitted rather than guessed)"
                    >
                      <HelpCircle className="w-3 h-3 text-slate-400" />
                      Qty: null
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
