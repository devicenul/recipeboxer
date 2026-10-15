import React, { useState, useCallback, useMemo } from 'react';
import {
  Check,
  Clock,
  Users,
  Save,
  RotateCcw,
  AlertCircle,
  AlertTriangle,
  Sparkles,
  FileText,
  X,
  Split,
} from 'lucide-react';
import {
  RecipeSchema,
  RecipeWarning,
  IngredientLine,
  Step,
  formatTimeValue,
  formatYieldValue,
  getAllIngredients,
  getAllSteps,
  parseModelDisagreement,
} from '../../types/recipe';
import { IngredientListInput } from './IngredientListInput';
import { DirectionListInput } from './DirectionListInput';
import { TagManager } from './TagManager';
import { Button } from '../common/Button';
import { useRecipeForm } from '../../hooks/useRecipeForm';

export interface RecipeFormProps {
  initialRecipe?: RecipeSchema | null;
  sessionId?: string;
  onSaved?: (updated: RecipeSchema) => void;
  className?: string;
  onSelectFace?: (faceIndex: number) => void;
  selectedFaceIndex?: number;
}

export const RecipeForm: React.FC<RecipeFormProps> = ({
  initialRecipe,
  sessionId,
  onSaved,
  className = '',
  onSelectFace,
  selectedFaceIndex,
}) => {
  const {
    recipe,
    isDirty,
    isSaving,
    errors,
    saveSuccess,
    updateField,
    setIngredient,
    addIngredient,
    removeIngredient,
    moveIngredient,
    setDirection,
    addDirection,
    removeDirection,
    moveDirection,
    addTag,
    removeTag,
    updateTag,
    save,
    reset,
  } = useRecipeForm({
    initialRecipe,
    sessionId,
    onSaved,
  });

  // Legibility toggle: Original verbatim text vs Normalized clean text
  const [showOriginal, setShowOriginal] = useState<boolean>(false);

  // Local mutable warnings list for dismissing grounding flags or resolving disagreements
  const [warnings, setWarnings] = useState<RecipeWarning[]>(() => initialRecipe?.warnings || []);

  // Compute rich ingredient lines and steps with legibility metadata if sections exist
  const richIngredients = useMemo<Array<IngredientLine | string>>(() => {
    if (initialRecipe && initialRecipe.sections && initialRecipe.sections.length > 0) {
      const all = getAllIngredients(initialRecipe);
      if (all.length > 0) {
        return all.map((line, idx) => {
          // If flat ingredients was updated in form, reflect text change
          const flatText = recipe.ingredients?.[idx];
          if (flatText && flatText.trim() && flatText !== (line.normalized_text || line.original_text)) {
            return { ...line, normalized_text: flatText };
          }
          return line;
        });
      }
    }
    return recipe.ingredients || [''];
  }, [initialRecipe, recipe.ingredients]);

  const richSteps = useMemo<Array<Step | string>>(() => {
    if (initialRecipe && initialRecipe.sections && initialRecipe.sections.length > 0) {
      const all = getAllSteps(initialRecipe);
      if (all.length > 0) {
        return all.map((step, idx) => {
          const flatText = recipe.directions?.[idx];
          if (flatText && flatText.trim() && flatText !== (step.normalized_text || step.text || step.original_text)) {
            return { ...step, normalized_text: flatText, text: flatText };
          }
          return step;
        });
      }
    }
    return recipe.directions || [''];
  }, [initialRecipe, recipe.directions]);

  const dismissWarning = useCallback((warnIndex: number) => {
    setWarnings((prev) => prev.filter((_, i) => i !== warnIndex));
  }, []);

  const resolveDisagreement = useCallback(
    (warning: RecipeWarning, chosenValue: string, warnIndex: number) => {
      const path = warning.field_path || '';
      if (path.includes('title')) {
        updateField('title', chosenValue);
      } else if (path.includes('quantity')) {
        // Find index if specified (e.g. sections.ingredients[0].quantity)
        const match = /ingredients\[(\d+)\]/i.exec(path);
        if (match) {
          const idx = parseInt(match[1], 10);
          const currentIng = recipe.ingredients?.[idx] || '';
          // Replace or prefix quantity
          const updated = currentIng ? `${chosenValue} ${currentIng.replace(/^[\d\s\/\.]+(?:cups?|tsp|tbsp|oz|lbs?)?\s*/i, '')}` : chosenValue;
          setIngredient(idx, updated.trim());
        }
      } else if (path.includes('ingredients')) {
        const match = /ingredients\[(\d+)\]/i.exec(path);
        if (match) {
          setIngredient(parseInt(match[1], 10), chosenValue);
        }
      } else if (path.includes('steps')) {
        const match = /steps\[(\d+)\]/i.exec(path);
        if (match) {
          setDirection(parseInt(match[1], 10), chosenValue);
        }
      }
      dismissWarning(warnIndex);
    },
    [recipe.ingredients, setIngredient, setDirection, updateField, dismissWarning]
  );

  if (!initialRecipe) {
    return (
      <div className="py-20 text-center text-xs text-slate-500 dark:text-slate-400">
        Select a recipe from the list to review and edit structured details.
      </div>
    );
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    save();
  };

  const flaggedCount = warnings.length + (recipe.title_is_inferred ? 1 : 0);

  return (
    <form onSubmit={handleSubmit} className={`flex flex-col gap-5 ${className}`}>
      {/* Header with Save Status, Legibility Toggle, and Actions */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-slate-200 dark:border-slate-800">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono font-bold text-slate-500 dark:text-slate-400">
            {recipe.recipe_id}
          </span>
          {saveSuccess ? (
            <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-950/50 px-2 py-0.5 rounded-full border border-emerald-200 dark:border-emerald-800">
              <Check className="w-3 h-3" /> Saved to SQLite
            </span>
          ) : isDirty ? (
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/50 px-2.5 py-0.5 rounded-full border border-amber-200 dark:border-amber-800">
              <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse"></span>
              Unsaved Changes
            </span>
          ) : (
            <span className="text-xs text-slate-400">Up to date</span>
          )}

          {/* Needs Review Badge */}
          {flaggedCount > 0 && (
            <span
              role="status"
              aria-label={`Needs review: ${flaggedCount} item(s) flagged`}
              className="inline-flex items-center gap-1 text-xs font-bold text-amber-700 dark:text-amber-300 bg-amber-100 dark:bg-amber-950 px-2 py-0.5 rounded-full border border-amber-300 dark:border-amber-800"
            >
              <AlertTriangle className="w-3 h-3 text-amber-600" />
              Needs Review ({flaggedCount})
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Legibility Mode Toggle */}
          <button
            type="button"
            role="switch"
            aria-checked={showOriginal}
            onClick={() => setShowOriginal(!showOriginal)}
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold rounded-lg border transition-all cursor-pointer ${
              showOriginal
                ? 'bg-amber-100 text-amber-900 border-amber-300 dark:bg-amber-950 dark:text-amber-200 dark:border-amber-700 shadow-xs'
                : 'bg-slate-100 text-slate-700 border-slate-300 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700'
            }`}
            title="Toggle between verbatim raw handwriting transcription and normalized recipe text"
          >
            <FileText className="w-3.5 h-3.5" />
            <span>{showOriginal ? 'Verbatim Transcription' : 'Normalized View'}</span>
          </button>

          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={!isDirty || isSaving}
            onClick={reset}
            leftIcon={<RotateCcw className="w-3.5 h-3.5" />}
          >
            Discard
          </Button>
          <Button
            type="submit"
            variant="primary"
            size="sm"
            isLoading={isSaving}
            disabled={!isDirty && !saveSuccess}
            leftIcon={<Save className="w-3.5 h-3.5" />}
          >
            Save Changes
          </Button>
        </div>
      </div>

      {errors.submit && (
        <div className="flex items-center gap-2 p-3 bg-rose-50 dark:bg-rose-950/40 text-rose-700 dark:text-rose-300 rounded-lg text-xs border border-rose-200 dark:border-rose-800">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errors.submit}</span>
        </div>
      )}

      {/* Warnings & Model Disagreement Resolution Stack */}
      {warnings.length > 0 && (
        <div className="flex flex-col gap-2.5 p-3.5 bg-slate-50 dark:bg-slate-850 rounded-xl border border-slate-200 dark:border-slate-800">
          <div className="flex items-center justify-between text-xs font-bold text-slate-700 dark:text-slate-300">
            <span className="flex items-center gap-1.5">
              <AlertTriangle className="w-4 h-4 text-amber-500" />
              Extraction Warnings &amp; Verification Flags ({warnings.length})
            </span>
          </div>

          <div className="flex flex-col gap-2">
            {warnings.map((warn, wIdx) => {
              const isDisagreement = warn.code === 'MODEL_DISAGREEMENT';
              const isGrounding = warn.code === 'GROUNDING_UNSUPPORTED';
              const parsed = isDisagreement ? parseModelDisagreement(warn) : null;

              if (isDisagreement && parsed) {
                return (
                  <div
                    key={wIdx}
                    role="region"
                    aria-label="Model disagreement resolution"
                    className="p-3 bg-purple-50 dark:bg-purple-950/40 border border-purple-200 dark:border-purple-800 rounded-lg text-xs flex flex-col gap-2"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5 font-bold text-purple-900 dark:text-purple-200">
                        <Split className="w-3.5 h-3.5 text-purple-600 dark:text-purple-400" />
                        <span>Model Disagreement: {warn.field_path || 'Field'}</span>
                      </div>
                      <button
                        type="button"
                        onClick={() => dismissWarning(wIdx)}
                        aria-label="Dismiss disagreement flag"
                        className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 p-0.5 cursor-pointer"
                      >
                        <X className="w-3.5 h-3.5" />
                      </button>
                    </div>

                    <p className="text-purple-800 dark:text-purple-300">{warn.message}</p>

                    <div className="flex flex-wrap items-center gap-2 pt-1">
                      <button
                        type="button"
                        aria-label={`Use Reading 1: ${parsed.reading1}`}
                        onClick={() => resolveDisagreement(warn, parsed.reading1, wIdx)}
                        className="px-2.5 py-1 text-xs font-semibold bg-purple-600 hover:bg-purple-700 text-white rounded cursor-pointer transition-colors shadow-xs"
                      >
                        Use Reading 1: "{parsed.reading1}"
                      </button>
                      <button
                        type="button"
                        aria-label={`Use Reading 2: ${parsed.reading2}`}
                        onClick={() => resolveDisagreement(warn, parsed.reading2, wIdx)}
                        className="px-2.5 py-1 text-xs font-semibold bg-purple-700 hover:bg-purple-800 text-white rounded cursor-pointer transition-colors shadow-xs"
                      >
                        Use Reading 2: "{parsed.reading2}"
                      </button>

                      {/* Custom edit option */}
                      <div className="flex items-center gap-1">
                        <input
                          type="text"
                          id={`custom-disagreement-edit-${wIdx}`}
                          placeholder="Or type custom reading..."
                          className="px-2.5 py-1 text-xs bg-white dark:bg-slate-800 border border-purple-300 dark:border-purple-700 rounded text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-1 focus:ring-purple-500"
                        />
                        <button
                          type="button"
                          onClick={() => {
                            const input = document.getElementById(
                              `custom-disagreement-edit-${wIdx}`
                            ) as HTMLInputElement;
                            if (input && input.value.trim()) {
                              resolveDisagreement(warn, input.value.trim(), wIdx);
                            }
                          }}
                          className="px-2.5 py-1 text-xs font-semibold bg-slate-700 hover:bg-slate-800 text-white rounded cursor-pointer transition-colors"
                        >
                          Apply
                        </button>
                      </div>
                    </div>
                  </div>
                );
              }

              return (
                <div
                  key={wIdx}
                  role="alert"
                  className={`flex items-start justify-between p-2.5 rounded-lg text-xs border ${
                    isGrounding
                      ? 'bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-200 border-amber-300 dark:border-amber-800'
                      : 'bg-rose-50 dark:bg-rose-950/40 text-rose-800 dark:text-rose-200 border-rose-200 dark:border-rose-800'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400" />
                    <div>
                      <span className="font-bold mr-1.5">[{warn.code}]:</span>
                      <span>{warn.message}</span>
                      {isGrounding && (
                        <p className="text-[11px] opacity-80 mt-0.5">
                          Extracted content was not substantiated by verbatim card face transcription.
                        </p>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => dismissWarning(wIdx)}
                    aria-label={`Dismiss ${warn.code} warning`}
                    className="p-1 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 rounded cursor-pointer"
                    title="Dismiss warning"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 1. Recipe Title & Inferred Title Marker */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <label htmlFor="recipe_title" className="text-xs font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-1.5">
            <span>Recipe Title</span>
            <span className="text-rose-500">*</span>
          </label>

          {recipe.title_is_inferred && (
            <span
              role="status"
              aria-label="Inferred title"
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 border border-amber-300 dark:border-amber-800"
              title="Title was not explicitly written on card and was inferred from content"
            >
              <Sparkles className="w-3 h-3 text-amber-600 dark:text-amber-400" />
              Inferred Title
            </span>
          )}
        </div>

        <input
          id="recipe_title"
          type="text"
          placeholder="e.g. Grandma's Famous Apple Pie"
          value={recipe.title}
          onChange={(e) => updateField('title', e.target.value)}
          className={`w-full px-3.5 py-2 text-base font-bold bg-slate-50 dark:bg-slate-800 border rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 ${
            errors.title
              ? 'border-rose-500 focus:ring-rose-500'
              : 'border-slate-300 dark:border-slate-700'
          }`}
        />
        {errors.title && <span className="text-xs text-rose-500 font-medium">{errors.title}</span>}
      </div>

      {/* 2. Description / Family Notes */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="recipe_description" className="text-xs font-semibold text-slate-800 dark:text-slate-200">
          Description / History
        </label>
        <textarea
          id="recipe_description"
          rows={2}
          placeholder="Historical background, card origin, or serving notes..."
          value={recipe.description || ''}
          onChange={(e) => updateField('description', e.target.value)}
          className="w-full px-3 py-1.5 text-xs bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
        />
      </div>

      {/* 3. Compact 4-Column Grid for Yield and Times */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1">
            <Users className="w-3 h-3 text-slate-400" />
            Yield / Servings
          </label>
          <input
            type="text"
            placeholder="e.g. 24 cookies"
            value={formatYieldValue(recipe.yield_value) || (typeof recipe.yield_servings === 'string' ? recipe.yield_servings : '')}
            onChange={(e) => {
              updateField('yield_servings', e.target.value);
              updateField('yield_value', { original_text: e.target.value });
            }}
            className="w-full px-2.5 py-1.5 text-xs bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1">
            <Clock className="w-3 h-3 text-blue-400" />
            Prep Time
          </label>
          <input
            type="text"
            placeholder="e.g. 20 mins"
            value={formatTimeValue(recipe.prep_time)}
            onChange={(e) => updateField('prep_time', { original_text: e.target.value })}
            className="w-full px-2.5 py-1.5 text-xs bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1">
            <Clock className="w-3 h-3 text-amber-400" />
            Cook Time
          </label>
          <input
            type="text"
            placeholder="e.g. 45 mins"
            value={formatTimeValue(recipe.cook_time)}
            onChange={(e) => updateField('cook_time', { original_text: e.target.value })}
            className="w-full px-2.5 py-1.5 text-xs bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1">
            <Clock className="w-3 h-3 text-emerald-400" />
            Total Time
          </label>
          <input
            type="text"
            placeholder="e.g. 1 hr 5 mins"
            value={formatTimeValue(recipe.total_time)}
            onChange={(e) => updateField('total_time', { original_text: e.target.value })}
            className="w-full px-2.5 py-1.5 text-xs bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>

      {/* 4. Dynamic Ingredients List with Legibility & Face Highlighting */}
      <div className="pt-2 border-t border-slate-200 dark:border-slate-800">
        <IngredientListInput
          ingredients={richIngredients}
          onChangeItem={setIngredient}
          onAddItem={addIngredient}
          onRemoveItem={removeIngredient}
          onMoveItem={moveIngredient}
          error={errors.ingredients}
          showOriginal={showOriginal}
          onSelectFace={onSelectFace}
          activeFaceIndex={selectedFaceIndex}
        />
      </div>

      {/* 5. Numbered Preparation Directions with Legibility & Face Highlighting */}
      <div className="pt-2 border-t border-slate-200 dark:border-slate-800">
        <DirectionListInput
          directions={richSteps}
          onChangeItem={setDirection}
          onAddItem={addDirection}
          onRemoveItem={removeDirection}
          onMoveItem={moveDirection}
          error={errors.directions}
          showOriginal={showOriginal}
          onSelectFace={onSelectFace}
          activeFaceIndex={selectedFaceIndex}
        />
      </div>

      {/* 6. Tag Manager */}
      <div className="pt-2 border-t border-slate-200 dark:border-slate-800">
        <TagManager
          tags={recipe.tags}
          onAddTag={addTag}
          onRemoveTag={removeTag}
          onEditTag={updateTag}
        />
      </div>

      {/* 7. Source Notes */}
      <div className="pt-2 border-t border-slate-200 dark:border-slate-800 flex flex-col gap-1.5">
        <label className="text-xs font-semibold text-slate-800 dark:text-slate-200">
          Source Notes &amp; Provenance
        </label>
        <input
          type="text"
          placeholder="e.g. Grandma Helen's 1964 tin box (handwritten index card)"
          value={recipe.source_notes || ''}
          onChange={(e) => updateField('source_notes', e.target.value)}
          className="w-full px-3 py-1.5 text-xs bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
    </form>
  );
};
