import React from 'react';
import { Sun, Contrast, FlipHorizontal2, RotateCcw } from 'lucide-react';
import { Slider } from '../common/Slider';
import { FilterSettings } from '../../types/viewer';

export interface FilterControlsProps {
  filters: FilterSettings;
  onChangeBrightness: (val: number) => void;
  onChangeContrast: (val: number) => void;
  onToggleInvert: () => void;
  onReset: () => void;
  targetLabel?: string;
}

export const FilterControls: React.FC<FilterControlsProps> = ({
  filters,
  onChangeBrightness,
  onChangeContrast,
  onToggleInvert,
  onReset,
  targetLabel,
}) => {
  return (
    <div className="p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-lg flex flex-col gap-3 min-w-[260px] text-xs">
      <div className="flex items-center justify-between pb-2 border-b border-slate-200 dark:border-slate-800">
        <span className="font-semibold text-slate-800 dark:text-slate-200">
          Visual Enhancement {targetLabel ? `(${targetLabel})` : ''}
        </span>
        <button
          onClick={onReset}
          className="flex items-center gap-1 text-[11px] text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
        >
          <RotateCcw className="w-3 h-3" />
          Reset
        </button>
      </div>

      <div className="flex items-center gap-2">
        <Sun className="w-4 h-4 text-amber-500 shrink-0" />
        <div className="flex-1">
          <Slider
            label="Brightness"
            min={0}
            max={200}
            step={5}
            unit="%"
            value={filters.brightness}
            onChange={onChangeBrightness}
          />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Contrast className="w-4 h-4 text-indigo-500 shrink-0" />
        <div className="flex-1">
          <Slider
            label="Contrast Boost"
            min={0}
            max={300}
            step={5}
            unit="%"
            value={filters.contrast}
            onChange={onChangeContrast}
          />
        </div>
      </div>

      <div className="pt-2 border-t border-slate-200 dark:border-slate-800 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-slate-700 dark:text-slate-300">
          <FlipHorizontal2 className="w-4 h-4 text-purple-500" />
          Invert Colors (Faded Pencil)
        </span>
        <button
          type="button"
          onClick={onToggleInvert}
          className={`px-2.5 py-1 text-xs font-semibold rounded-md border transition-all cursor-pointer ${
            filters.invert
              ? 'bg-purple-600 border-purple-600 text-white shadow-xs'
              : 'border-slate-300 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800'
          }`}
        >
          {filters.invert ? 'Inverted' : 'Normal'}
        </button>
      </div>
    </div>
  );
};
