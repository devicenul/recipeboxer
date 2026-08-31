import React, { useState } from 'react';
import {
  Layers,
  ArrowUpDown,
  BookMarked,
  FileSpreadsheet,
  Users,
  Tag,
  AlignLeft,
  SlidersHorizontal,
  ChevronUp,
  ChevronDown,
  GripVertical,
  RotateCcw,
  CheckCircle2,
} from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';
import { GroupingPreset, ExportCurationOptions, CuratedCookbook } from '../../types/export';

export interface ExportCurationToolbarProps {
  recipes: RecipeSchema[];
  curatedCookbook: CuratedCookbook;
  curationOptions: ExportCurationOptions;
  onChangeOptions: (newOptions: ExportCurationOptions) => void;
}

export const ExportCurationToolbar: React.FC<ExportCurationToolbarProps> = ({
  recipes,
  curatedCookbook,
  curationOptions,
  onChangeOptions,
}) => {
  const [isReorderOpen, setIsReorderOpen] = useState<boolean>(false);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);

  const presets: { id: GroupingPreset; label: string; icon: React.ReactNode; desc: string }[] = [
    {
      id: 'none',
      label: 'Natural Order',
      icon: <Layers className="w-3.5 h-3.5" />,
      desc: 'Preserves scan and extraction sequence.',
    },
    {
      id: 'course',
      label: 'By Course',
      icon: <BookMarked className="w-3.5 h-3.5" />,
      desc: 'Appetizers, Mains, Sides, Desserts.',
    },
    {
      id: 'contributor',
      label: 'By Contributor',
      icon: <Users className="w-3.5 h-3.5" />,
      desc: 'Family member and kitchen attribution.',
    },
    {
      id: 'tags',
      label: 'By Tag',
      icon: <Tag className="w-3.5 h-3.5" />,
      desc: 'Organized by prominent primary tags.',
    },
    {
      id: 'alphabetical',
      label: 'A to Z',
      icon: <AlignLeft className="w-3.5 h-3.5" />,
      desc: 'Alphabetical sorting (articles stripped).',
    },
    {
      id: 'custom',
      label: 'Custom Order',
      icon: <ArrowUpDown className="w-3.5 h-3.5" />,
      desc: 'Interactive manual sequence.',
    },
  ];

  // Current active recipe order
  const currentOrderedList: RecipeSchema[] = (() => {
    if (!curationOptions.recipe_order || curationOptions.recipe_order.length === 0) {
      return [...recipes];
    }
    const map = new Map(recipes.map((r) => [r.recipe_id, r]));
    const ordered: RecipeSchema[] = [];
    curationOptions.recipe_order.forEach((id) => {
      const r = map.get(id);
      if (r) ordered.push(r);
    });
    recipes.forEach((r) => {
      if (!curationOptions.recipe_order!.includes(r.recipe_id)) {
        ordered.push(r);
      }
    });
    return ordered;
  })();

  const handlePresetSelect = (preset: GroupingPreset) => {
    onChangeOptions({
      ...curationOptions,
      grouping_preset: preset,
    });
    if (preset === 'custom') {
      setIsReorderOpen(true);
    }
  };

  const handleMoveRecipe = (fromIndex: number, toIndex: number) => {
    if (toIndex < 0 || toIndex >= currentOrderedList.length) return;
    const updated = [...currentOrderedList];
    const [moved] = updated.splice(fromIndex, 1);
    updated.splice(toIndex, 0, moved);

    onChangeOptions({
      ...curationOptions,
      recipe_order: updated.map((r) => r.recipe_id),
      grouping_preset: 'custom',
    });
  };

  const handleResetOrder = () => {
    onChangeOptions({
      ...curationOptions,
      recipe_order: recipes.map((r) => r.recipe_id),
      grouping_preset: 'none',
    });
  };

  const handleDragStart = (e: React.DragEvent, index: number) => {
    setDraggedIndex(index);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const handleDrop = (e: React.DragEvent, targetIndex: number) => {
    e.preventDefault();
    if (draggedIndex === null || draggedIndex === targetIndex) {
      setDraggedIndex(null);
      return;
    }
    handleMoveRecipe(draggedIndex, targetIndex);
    setDraggedIndex(null);
  };

  return (
    <div className="bg-slate-50 dark:bg-slate-850/70 border border-slate-200 dark:border-slate-800 rounded-xl p-4 flex flex-col gap-4">
      {/* Title and Badge */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <SlidersHorizontal className="w-4 h-4 text-emerald-500" />
          <h3 className="text-xs font-bold uppercase tracking-wider text-slate-800 dark:text-slate-200">
            Cookbook Curation & Recipe Sequencing (R3)
          </h3>
        </div>

        <div className="flex items-center gap-2 text-[11px] font-medium text-slate-600 dark:text-slate-400">
          <span className="px-2 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-950/60 text-emerald-800 dark:text-emerald-300 font-semibold border border-emerald-200 dark:border-emerald-800">
            {curatedCookbook.sections.length} {curatedCookbook.sections.length === 1 ? 'Section' : 'Sections'}
          </span>
          <span>•</span>
          <span>{curatedCookbook.total_recipe_count} Recipes</span>
        </div>
      </div>

      {/* Preset Selector Pills */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2">
        {presets.map((preset) => {
          const isActive = curationOptions.grouping_preset === preset.id;
          return (
            <button
              key={preset.id}
              type="button"
              onClick={() => handlePresetSelect(preset.id)}
              className={`p-2.5 rounded-lg border text-left flex flex-col gap-1 transition-all cursor-pointer ${
                isActive
                  ? 'border-emerald-500 bg-emerald-50/80 dark:bg-emerald-950/40 ring-2 ring-emerald-500/20 text-emerald-900 dark:text-emerald-200 shadow-2xs'
                  : 'border-slate-200 dark:border-slate-750 bg-white dark:bg-slate-800 hover:border-slate-300 dark:hover:border-slate-700 text-slate-700 dark:text-slate-300'
              }`}
            >
              <div className="flex items-center gap-1.5 font-semibold text-xs">
                {preset.icon}
                <span>{preset.label}</span>
              </div>
              <p className="text-[10px] text-slate-500 dark:text-slate-400 line-clamp-1">
                {preset.desc}
              </p>
            </button>
          );
        })}
      </div>

      {/* Toggles & Options */}
      <div className="flex flex-wrap items-center justify-between gap-4 pt-2 border-t border-slate-200 dark:border-slate-800 text-xs">
        <div className="flex flex-wrap items-center gap-6">
          {/* Include TOC Toggle */}
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={curationOptions.include_toc}
              onChange={(e) =>
                onChangeOptions({
                  ...curationOptions,
                  include_toc: e.target.checked,
                })
              }
              className="rounded-sm border-slate-300 dark:border-slate-700 text-emerald-600 focus:ring-emerald-500 h-4 w-4"
            />
            <span className="font-medium text-slate-800 dark:text-slate-200">
              Include Table of Contents
            </span>
          </label>

          {/* Include Section Dividers Toggle */}
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={curationOptions.include_section_dividers}
              onChange={(e) =>
                onChangeOptions({
                  ...curationOptions,
                  include_section_dividers: e.target.checked,
                })
              }
              className="rounded-sm border-slate-300 dark:border-slate-700 text-emerald-600 focus:ring-emerald-500 h-4 w-4"
            />
            <span className="font-medium text-slate-800 dark:text-slate-200">
              Include Section Dividers
            </span>
          </label>

          {/* TOC Title Field (if TOC enabled) */}
          {curationOptions.include_toc && (
            <div className="flex items-center gap-2">
              <label htmlFor="toc-title-input" className="text-slate-600 dark:text-slate-400">
                TOC Title:
              </label>
              <input
                id="toc-title-input"
                type="text"
                value={curationOptions.toc_title}
                onChange={(e) =>
                  onChangeOptions({
                    ...curationOptions,
                    toc_title: e.target.value,
                  })
                }
                className="px-2.5 py-1 text-xs rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-slate-800 dark:text-slate-200 focus:ring-1 focus:ring-emerald-500 focus:outline-hidden"
                placeholder="Table of Contents"
              />
            </div>
          )}
        </div>

        {/* Toggle Manual Recipe Reorder Panel */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setIsReorderOpen(!isReorderOpen)}
            className="px-3 py-1 text-xs font-semibold rounded-lg bg-slate-200 dark:bg-slate-750 text-slate-700 dark:text-slate-200 hover:bg-slate-300 dark:hover:bg-slate-700 cursor-pointer flex items-center gap-1.5"
          >
            <ArrowUpDown className="w-3.5 h-3.5 text-emerald-500" />
            <span>{isReorderOpen ? 'Hide Recipe Sequence' : 'Custom Sequence...'}</span>
          </button>
        </div>
      </div>

      {/* Manual Drag and Drop Recipe Reorder Drawer */}
      {isReorderOpen && (
        <div className="mt-1 p-3 rounded-lg bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 flex flex-col gap-2">
          <div className="flex items-center justify-between pb-2 border-b border-slate-100 dark:border-slate-800">
            <span className="text-[11px] font-bold text-slate-600 dark:text-slate-400 uppercase tracking-wider">
              Drag & Drop or Use Arrows to Sequence Recipes
            </span>
            <button
              type="button"
              onClick={handleResetOrder}
              className="text-xs text-slate-500 hover:text-slate-700 dark:hover:text-slate-300 cursor-pointer flex items-center gap-1"
              title="Reset order to default extraction sequence"
            >
              <RotateCcw className="w-3 h-3" />
              <span>Reset Order</span>
            </button>
          </div>

          <div className="flex flex-col gap-1.5 max-h-60 overflow-y-auto pr-1">
            {currentOrderedList.map((recipe, index) => (
              <div
                key={recipe.recipe_id}
                draggable
                onDragStart={(e) => handleDragStart(e, index)}
                onDragOver={handleDragOver}
                onDrop={(e) => handleDrop(e, index)}
                className={`flex items-center justify-between p-2 rounded-lg border transition-all ${
                  draggedIndex === index
                    ? 'opacity-50 border-emerald-400 bg-emerald-50 dark:bg-emerald-950'
                    : 'bg-slate-50 dark:bg-slate-850/50 border-slate-200 dark:border-slate-800 hover:border-slate-300 dark:hover:border-slate-700'
                }`}
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="cursor-grab active:cursor-grabbing text-slate-400 hover:text-slate-600">
                    <GripVertical className="w-3.5 h-3.5" />
                  </span>
                  <span className="px-1.5 py-0.5 rounded-sm bg-slate-200 dark:bg-slate-750 text-[10px] font-mono font-bold text-slate-700 dark:text-slate-300 shrink-0">
                    #{index + 1}
                  </span>
                  <span className="text-xs font-semibold text-slate-800 dark:text-slate-200 truncate">
                    {recipe.title || 'Untitled Recipe'}
                  </span>
                  {recipe.source_notes && (
                    <span className="text-[10px] text-slate-400 dark:text-slate-500 italic truncate hidden sm:inline">
                      ({recipe.source_notes})
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-1 shrink-0 ml-2">
                  <button
                    type="button"
                    disabled={index === 0}
                    onClick={() => handleMoveRecipe(index, index - 1)}
                    className="p-1 rounded-sm text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 hover:bg-slate-200 dark:hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                    title="Move recipe up"
                  >
                    <ChevronUp className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    disabled={index === currentOrderedList.length - 1}
                    onClick={() => handleMoveRecipe(index, index + 1)}
                    className="p-1 rounded-sm text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 hover:bg-slate-200 dark:hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
                    title="Move recipe down"
                  >
                    <ChevronDown className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
