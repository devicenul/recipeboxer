import React, { useState } from 'react';
import {
  ZoomIn,
  ZoomOut,
  Maximize2,
  RotateCw,
  RotateCcw,
  SlidersHorizontal,
  Link,
  Unlink,
  RefreshCw,
} from 'lucide-react';
import { FilterControls } from './FilterControls';
import { FilterSettings } from '../../types/viewer';

export interface VisualToolbarProps {
  linked: boolean;
  onToggleLinked: () => void;
  onZoomIn: (target?: 'front' | 'back' | 'both') => void;
  onZoomOut: (target?: 'front' | 'back' | 'both') => void;
  onRotateCw: (target: 'front' | 'back') => void;
  onRotateCcw: (target: 'front' | 'back') => void;
  onResetTransform: (target?: 'front' | 'back' | 'both') => void;
  filters: FilterSettings;
  onChangeBrightness: (val: number) => void;
  onChangeContrast: (val: number) => void;
  onToggleInvert: () => void;
  onResetFilters: () => void;
  currentScale?: number;
  showRotationTarget?: 'front' | 'back' | 'both';
}

export const VisualToolbar: React.FC<VisualToolbarProps> = ({
  linked,
  onToggleLinked,
  onZoomIn,
  onZoomOut,
  onRotateCw,
  onRotateCcw,
  onResetTransform,
  filters,
  onChangeBrightness,
  onChangeContrast,
  onToggleInvert,
  onResetFilters,
  currentScale = 1.0,
  showRotationTarget = 'both',
}) => {
  const [showFilters, setShowFilters] = useState(false);

  return (
    <div className="relative flex items-center justify-between gap-2 p-2 bg-white/90 dark:bg-slate-900/90 backdrop-blur-md rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm text-xs select-none">
      {/* Zoom Controls */}
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onZoomOut()}
          className="p-1.5 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg cursor-pointer"
          title="Zoom Out (-25%)"
        >
          <ZoomOut className="w-4 h-4" />
        </button>
        <span className="font-mono text-slate-600 dark:text-slate-400 min-w-[45px] text-center font-medium">
          {Math.round(currentScale * 100)}%
        </span>
        <button
          type="button"
          onClick={() => onZoomIn()}
          className="p-1.5 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg cursor-pointer"
          title="Zoom In (+25%)"
        >
          <ZoomIn className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={() => onResetTransform()}
          className="p-1.5 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg cursor-pointer"
          title="Reset Zoom & Pan"
        >
          <Maximize2 className="w-4 h-4" />
        </button>
      </div>

      <div className="h-4 w-px bg-slate-200 dark:bg-slate-700" />

      {/* Rotation Controls */}
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onRotateCcw(showRotationTarget === 'back' ? 'back' : 'front')}
          className="p-1.5 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg cursor-pointer"
          title="Rotate Counter-Clockwise (-90°)"
        >
          <RotateCcw className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={() => onRotateCw(showRotationTarget === 'back' ? 'back' : 'front')}
          className="p-1.5 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg cursor-pointer"
          title="Rotate Clockwise (+90°)"
        >
          <RotateCw className="w-4 h-4" />
        </button>
      </div>

      <div className="h-4 w-px bg-slate-200 dark:bg-slate-700" />

      {/* Sync Link / Unlink Toggle */}
      {showRotationTarget === 'both' && (
        <>
          <button
            type="button"
            onClick={onToggleLinked}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border transition-all cursor-pointer ${
              linked
                ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 font-medium'
                : 'border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800'
            }`}
            title={linked ? 'Pan & Zoom synchronized between Front and Back' : 'Independent Viewports'}
          >
            {linked ? <Link className="w-3.5 h-3.5" /> : <Unlink className="w-3.5 h-3.5" />}
            <span className="hidden sm:inline">{linked ? 'Linked' : 'Separate'}</span>
          </button>
          <div className="h-4 w-px bg-slate-200 dark:bg-slate-700" />
        </>
      )}

      {/* Filters Popover Toggle */}
      <div className="relative">
        <button
          type="button"
          onClick={() => setShowFilters(!showFilters)}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg border transition-all cursor-pointer ${
            filters.brightness !== 100 || filters.contrast !== 100 || filters.invert
              ? 'border-purple-500 bg-purple-50 dark:bg-purple-950/50 text-purple-700 dark:text-purple-300 font-medium'
              : 'border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800'
          }`}
          title="Enhance Image Contrast, Brightness & Color Invert"
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Enhance</span>
        </button>

        {showFilters && (
          <div className="absolute right-0 top-full mt-2 z-50 animate-in fade-in zoom-in-95 duration-100">
            <FilterControls
              filters={filters}
              onChangeBrightness={onChangeBrightness}
              onChangeContrast={onChangeContrast}
              onToggleInvert={onToggleInvert}
              onReset={() => {
                onResetFilters();
                setShowFilters(false);
              }}
            />
          </div>
        )}
      </div>

      {/* Reset All */}
      <button
        type="button"
        onClick={() => {
          onResetTransform();
          onResetFilters();
        }}
        className="p-1.5 text-slate-500 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg cursor-pointer"
        title="Reset All Transforms & Filters"
      >
        <RefreshCw className="w-4 h-4" />
      </button>
    </div>
  );
};
