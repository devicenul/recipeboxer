import React from 'react';
import { Check, Clock, Users, Save, RotateCcw, AlertCircle, Sparkles } from 'lucide-react';
import { RecipeSchema } from '../../types/recipe';
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
}

export const RecipeForm: React.FC<RecipeFormProps> = ({
  initialRecipe,
  sessionId,
  onSaved,
  className = '',
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
    save,
    reset,
  } = useRecipeForm({
    initialRecipe,
    sessionId,
    onSaved,
  });

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

  return (
    <form onSubmit={handleSubmit} className={`flex flex-col gap-5 ${className}`}>
      {/* Header with Save Status and Actions */}
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
        </div>

        <div className="flex items-center gap-2">
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

      {/* 1. Recipe Title */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="recipe_title" className="text-xs font-semibold text-slate-800 dark:text-slate-200">
          Recipe Title <span className="text-rose-500">*</span>
        </label>
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
            value={recipe.yield_servings || ''}
            onChange={(e) => updateField('yield_servings', e.target.value)}
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
            value={recipe.prep_time || ''}
            onChange={(e) => updateField('prep_time', e.target.value)}
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
            value={recipe.cook_time || ''}
            onChange={(e) => updateField('cook_time', e.target.value)}
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
            value={recipe.total_time || ''}
            onChange={(e) => updateField('total_time', e.target.value)}
            className="w-full px-2.5 py-1.5 text-xs bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>

      {/* 4. Dynamic Ingredients List */}
      <div className="pt-2 border-t border-slate-200 dark:border-slate-800">
        <IngredientListInput
          ingredients={recipe.ingredients}
          onChangeItem={setIngredient}
          onAddItem={addIngredient}
          onRemoveItem={removeIngredient}
          onMoveItem={moveIngredient}
          error={errors.ingredients}
        />
      </div>

      {/* 5. Numbered Preparation Directions */}
      <div className="pt-2 border-t border-slate-200 dark:border-slate-800">
        <DirectionListInput
          directions={recipe.directions}
          onChangeItem={setDirection}
          onAddItem={addDirection}
          onRemoveItem={removeDirection}
          onMoveItem={moveDirection}
          error={errors.directions}
        />
      </div>

      {/* 6. Tag Manager */}
      <div className="pt-2 border-t border-slate-200 dark:border-slate-800">
        <TagManager
          tags={recipe.tags}
          onAddTag={addTag}
          onRemoveTag={removeTag}
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
