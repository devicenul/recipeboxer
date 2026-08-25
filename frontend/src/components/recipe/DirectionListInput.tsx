import React from 'react';
import { Plus, Trash2, ArrowUp, ArrowDown } from 'lucide-react';

export interface DirectionListInputProps {
  directions: string[];
  onChangeItem: (index: number, val: string) => void;
  onAddItem: (afterIndex?: number) => void;
  onRemoveItem: (index: number) => void;
  onMoveItem: (from: number, to: number) => void;
  error?: string;
  disabled?: boolean;
}

export const DirectionListInput: React.FC<DirectionListInputProps> = ({
  directions,
  onChangeItem,
  onAddItem,
  onRemoveItem,
  onMoveItem,
  error,
  disabled = false,
}) => {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <label className="text-xs font-semibold text-slate-800 dark:text-slate-200">
          Preparation Steps ({directions.filter((d) => d.trim()).length})
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
        {directions.map((step, idx) => {
          // Clean visual representation for editing
          const cleanText = step.replace(/^\d+[\.\)\-:]\s*/, '').replace(/^Step\s*\d+[\.\)\-:]\s*/i, '');

          return (
            <div key={idx} className="flex items-start gap-2 group">
              <span className="w-6 h-6 rounded-full bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300 font-bold text-xs flex items-center justify-center shrink-0 mt-1 select-none font-mono">
                {idx + 1}
              </span>
              <textarea
                rows={2}
                placeholder={`Step ${idx + 1} instructions...`}
                value={cleanText}
                disabled={disabled}
                onChange={(e) => onChangeItem(idx, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    onAddItem(idx);
                  }
                }}
                className="flex-1 px-3 py-1.5 text-sm bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 resize-y"
              />
              <div className="flex flex-col items-center gap-0.5 opacity-60 group-hover:opacity-100 transition-opacity mt-1">
                <button
                  type="button"
                  onClick={() => onMoveItem(idx, idx - 1)}
                  disabled={disabled || idx === 0}
                  className="p-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-20 cursor-pointer"
                  title="Move up"
                >
                  <ArrowUp className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => onMoveItem(idx, idx + 1)}
                  disabled={disabled || idx === directions.length - 1}
                  className="p-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-20 cursor-pointer"
                  title="Move down"
                >
                  <ArrowDown className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => onRemoveItem(idx)}
                  disabled={disabled || directions.length <= 1}
                  className="p-1 text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 disabled:opacity-20 cursor-pointer"
                  title="Remove step"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {error && <span className="text-xs text-rose-500 font-medium">{error}</span>}
    </div>
  );
};
