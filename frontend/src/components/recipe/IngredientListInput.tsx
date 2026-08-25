import React from 'react';
import { Plus, Trash2, ArrowUp, ArrowDown } from 'lucide-react';

export interface IngredientListInputProps {
  ingredients: string[];
  onChangeItem: (index: number, val: string) => void;
  onAddItem: (afterIndex?: number) => void;
  onRemoveItem: (index: number) => void;
  onMoveItem: (from: number, to: number) => void;
  error?: string;
  disabled?: boolean;
}

export const IngredientListInput: React.FC<IngredientListInputProps> = ({
  ingredients,
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
          Ingredients ({ingredients.filter((i) => i.trim()).length})
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
        {ingredients.map((item, idx) => (
          <div key={idx} className="flex items-center gap-2 group">
            <span className="w-5 text-right font-mono text-xs text-slate-400 select-none">
              {idx + 1}.
            </span>
            <input
              type="text"
              placeholder="e.g. 2 cups all-purpose flour"
              value={item}
              disabled={disabled}
              onChange={(e) => onChangeItem(idx, e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  onAddItem(idx);
                }
              }}
              className="flex-1 px-3 py-1.5 text-sm bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
            />
            <div className="flex items-center gap-0.5 opacity-60 group-hover:opacity-100 transition-opacity">
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
                disabled={disabled || idx === ingredients.length - 1}
                className="p-1 text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 disabled:opacity-20 cursor-pointer"
                title="Move down"
              >
                <ArrowDown className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                onClick={() => onRemoveItem(idx)}
                disabled={disabled || ingredients.length <= 1}
                className="p-1 text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 disabled:opacity-20 cursor-pointer"
                title="Remove ingredient"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        ))}
      </div>

      {error && <span className="text-xs text-rose-500 font-medium">{error}</span>}
    </div>
  );
};
