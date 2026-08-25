import React, { useState } from 'react';
import { ChefHat, ChevronLeft, ChevronRight, Image as ImageIcon } from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';
import { RecipeForm } from './RecipeForm';
import { CardViewerCanvas } from '../viewer/CardViewerCanvas';
import { VisualToolbar } from '../viewer/VisualToolbar';
import { useImageViewer } from '../../hooks/useImageViewer';
import { api } from '../../utils/api';

export interface RecipeEditorViewProps {
  recipes: RecipeSchema[];
  sessionId?: string;
  onRecipeUpdated: (updated: RecipeSchema) => void;
}

export const RecipeEditorView: React.FC<RecipeEditorViewProps> = ({
  recipes,
  sessionId,
  onRecipeUpdated,
}) => {
  const [selectedRecipeIndex, setSelectedRecipeIndex] = useState<number>(0);
  const [selectedCardIndex, setSelectedCardIndex] = useState<number>(0);

  const viewer = useImageViewer();

  if (!recipes || recipes.length === 0) {
    return (
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-12 text-center flex flex-col items-center gap-3">
        <ChefHat className="w-12 h-12 text-slate-400" />
        <h3 className="text-base font-semibold text-slate-800 dark:text-slate-200">
          No Extracted Recipes Found
        </h3>
        <p className="text-xs text-slate-500 dark:text-slate-400 max-w-sm">
          Run an ingestion session and approve correlation pairings in the HITL Queue to extract recipes.
        </p>
      </div>
    );
  }

  const currentRecipe = recipes[selectedRecipeIndex] || recipes[0];
  const cardIds = currentRecipe.card_ids || [];
  const activeCardId = cardIds[selectedCardIndex] || cardIds[0] || null;
  const activeImageUrl = activeCardId ? api.getCardImageUrl(activeCardId, sessionId) : null;

  return (
    <div className="flex flex-col gap-4">
      {/* Recipe Navigation Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-xs">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => {
              setSelectedRecipeIndex((prev) => Math.max(0, prev - 1));
              setSelectedCardIndex(0);
            }}
            disabled={selectedRecipeIndex === 0}
            className="p-1.5 rounded-lg border border-slate-300 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-30 cursor-pointer"
            title="Previous Recipe"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>

          <div className="flex items-center gap-2">
            <span className="text-xs font-bold text-blue-600 dark:text-blue-400 font-mono">
              Recipe {selectedRecipeIndex + 1} of {recipes.length}:
            </span>
            <select
              value={selectedRecipeIndex}
              onChange={(e) => {
                setSelectedRecipeIndex(parseInt(e.target.value, 10));
                setSelectedCardIndex(0);
              }}
              className="text-xs font-semibold bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg px-2.5 py-1.5 text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 max-w-[240px] truncate"
            >
              {recipes.map((r, i) => (
                <option key={r.recipe_id || i} value={i}>
                  {r.title || `Recipe #${i + 1}`}
                </option>
              ))}
            </select>
          </div>

          <button
            type="button"
            onClick={() => {
              setSelectedRecipeIndex((prev) => Math.min(recipes.length - 1, prev + 1));
              setSelectedCardIndex(0);
            }}
            disabled={selectedRecipeIndex === recipes.length - 1}
            className="p-1.5 rounded-lg border border-slate-300 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 disabled:opacity-30 cursor-pointer"
            title="Next Recipe"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>

        {/* Visual Toolbar for Inspector */}
        <VisualToolbar
          linked={false}
          onToggleLinked={() => {}}
          onZoomIn={() => viewer.zoomIn('front')}
          onZoomOut={() => viewer.zoomOut('front')}
          onRotateCw={() => viewer.rotateCw('front')}
          onRotateCcw={() => viewer.rotateCcw('front')}
          onResetTransform={() => viewer.resetTransform('front')}
          filters={viewer.filtersFront}
          onChangeBrightness={(val) => viewer.setBrightness(val, 'front')}
          onChangeContrast={(val) => viewer.setContrast(val, 'front')}
          onToggleInvert={() => viewer.toggleInvert('front')}
          onResetFilters={() => viewer.resetFilters('front')}
          currentScale={viewer.transformFront.scale}
          showRotationTarget="front"
        />
      </div>

      {/* Split-Screen Workspace (50% Left Viewer, 50% Right Form) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        {/* Left Pane: Scanned Source Images Carousel */}
        <div className="flex flex-col gap-3 sticky top-20">
          {/* Card Tabs Carousel if multiple cards */}
          {cardIds.length > 1 && (
            <div className="flex items-center gap-1 p-1 bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl overflow-x-auto">
              {cardIds.map((cid, idx) => (
                <button
                  key={cid}
                  type="button"
                  onClick={() => setSelectedCardIndex(idx)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer whitespace-nowrap ${
                    selectedCardIndex === idx
                      ? 'bg-white dark:bg-slate-800 text-blue-600 dark:text-blue-400 shadow-xs'
                      : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100'
                  }`}
                >
                  <ImageIcon className="w-3.5 h-3.5" />
                  <span>Card {idx + 1} ({idx === 0 ? 'Front' : idx === 1 ? 'Back' : `Part ${idx + 1}`})</span>
                </button>
              ))}
            </div>
          )}

          <div className="h-[600px]">
            <CardViewerCanvas
              imageUrl={activeImageUrl}
              cardId={activeCardId}
              label={
                cardIds.length > 1
                  ? `Card ${selectedCardIndex + 1} of ${cardIds.length}`
                  : 'Original Scanned Card'
              }
              roleBadge={selectedCardIndex === 0 ? 'Front Scan' : selectedCardIndex === 1 ? 'Back Scan' : undefined}
              transform={viewer.transformFront}
              filters={viewer.filtersFront}
              onPan={(dx, dy) => viewer.pan(dx, dy, 'front')}
              onWheelZoom={(delta) => (delta > 0 ? viewer.zoomIn('front') : viewer.zoomOut('front'))}
            />
          </div>
        </div>

        {/* Right Pane: Rich Structured Recipe Form */}
        <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-6 shadow-xs">
          <RecipeForm
            key={currentRecipe.recipe_id}
            initialRecipe={currentRecipe}
            sessionId={sessionId}
            onSaved={onRecipeUpdated}
          />
        </div>
      </div>
    </div>
  );
};
