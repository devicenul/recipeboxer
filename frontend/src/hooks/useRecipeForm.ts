import { useState, useEffect, useCallback } from 'react';
import { RecipeSchema } from '../types/recipe';
import { api } from '../utils/api';

export interface UseRecipeFormOptions {
  initialRecipe?: RecipeSchema | null;
  sessionId?: string;
  onSaved?: (recipe: RecipeSchema) => void;
}

export function useRecipeForm({ initialRecipe, sessionId, onSaved }: UseRecipeFormOptions) {
  const [recipe, setRecipe] = useState<RecipeSchema>(() => {
    return (
      initialRecipe || {
        recipe_id: '',
        title: '',
        description: '',
        yield_servings: '',
        prep_time: '',
        cook_time: '',
        total_time: '',
        ingredients: [''],
        directions: [''],
        tags: [],
        source_notes: '',
      }
    );
  });

  const [isDirty, setIsDirty] = useState<boolean>(false);
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saveSuccess, setSaveSuccess] = useState<boolean>(false);

  useEffect(() => {
    if (initialRecipe) {
      setRecipe({
        ...initialRecipe,
        ingredients: initialRecipe.ingredients?.length ? initialRecipe.ingredients : [''],
        directions: initialRecipe.directions?.length ? initialRecipe.directions : [''],
        tags: initialRecipe.tags || [],
      });
      setIsDirty(false);
      setErrors({});
    }
  }, [initialRecipe]);

  // Field updates
  const updateField = useCallback(<K extends keyof RecipeSchema>(field: K, value: RecipeSchema[K]) => {
    setRecipe((prev) => ({ ...prev, [field]: value }));
    setIsDirty(true);
    setSaveSuccess(false);
    setErrors((prev) => {
      const next = { ...prev };
      delete next[field as string];
      return next;
    });
  }, []);

  // Ingredients operations
  const setIngredient = useCallback((index: number, text: string) => {
    setRecipe((prev) => {
      const list = [...prev.ingredients];
      list[index] = text;
      return { ...prev, ingredients: list };
    });
    setIsDirty(true);
    setSaveSuccess(false);
  }, []);

  const addIngredient = useCallback((afterIndex?: number) => {
    setRecipe((prev) => {
      const list = [...prev.ingredients];
      if (afterIndex !== undefined && afterIndex >= 0) {
        list.splice(afterIndex + 1, 0, '');
      } else {
        list.push('');
      }
      return { ...prev, ingredients: list };
    });
    setIsDirty(true);
  }, []);

  const removeIngredient = useCallback((index: number) => {
    setRecipe((prev) => {
      const list = prev.ingredients.filter((_, i) => i !== index);
      return { ...prev, ingredients: list.length > 0 ? list : [''] };
    });
    setIsDirty(true);
  }, []);

  const moveIngredient = useCallback((fromIndex: number, toIndex: number) => {
    setRecipe((prev) => {
      if (toIndex < 0 || toIndex >= prev.ingredients.length) return prev;
      const list = [...prev.ingredients];
      const [item] = list.splice(fromIndex, 1);
      list.splice(toIndex, 0, item);
      return { ...prev, ingredients: list };
    });
    setIsDirty(true);
  }, []);

  // Directions operations
  const setDirection = useCallback((index: number, text: string) => {
    setRecipe((prev) => {
      const list = [...prev.directions];
      list[index] = text;
      return { ...prev, directions: list };
    });
    setIsDirty(true);
    setSaveSuccess(false);
  }, []);

  const addDirection = useCallback((afterIndex?: number) => {
    setRecipe((prev) => {
      const list = [...prev.directions];
      if (afterIndex !== undefined && afterIndex >= 0) {
        list.splice(afterIndex + 1, 0, '');
      } else {
        list.push('');
      }
      return { ...prev, directions: list };
    });
    setIsDirty(true);
  }, []);

  const removeDirection = useCallback((index: number) => {
    setRecipe((prev) => {
      const list = prev.directions.filter((_, i) => i !== index);
      return { ...prev, directions: list.length > 0 ? list : [''] };
    });
    setIsDirty(true);
  }, []);

  const moveDirection = useCallback((fromIndex: number, toIndex: number) => {
    setRecipe((prev) => {
      if (toIndex < 0 || toIndex >= prev.directions.length) return prev;
      const list = [...prev.directions];
      const [item] = list.splice(fromIndex, 1);
      list.splice(toIndex, 0, item);
      return { ...prev, directions: list };
    });
    setIsDirty(true);
  }, []);

  // Tags operations
  const addTag = useCallback((tag: string) => {
    const clean = tag.trim();
    if (!clean) return;
    setRecipe((prev) => {
      const exists = (prev.tags || []).some((t) => t.toLowerCase() === clean.toLowerCase());
      if (exists) return prev;
      return { ...prev, tags: [...(prev.tags || []), clean] };
    });
    setIsDirty(true);
  }, []);

  const removeTag = useCallback((tagToRemove: string) => {
    setRecipe((prev) => ({
      ...prev,
      tags: (prev.tags || []).filter((t) => t !== tagToRemove),
    }));
    setIsDirty(true);
  }, []);

  // Validation
  const validate = useCallback((): boolean => {
    const errs: Record<string, string> = {};
    if (!recipe.title || !recipe.title.trim()) {
      errs.title = 'Title is required';
    }

    const cleanedIngs = (recipe.ingredients || []).map((i) => i.trim()).filter(Boolean);
    if (cleanedIngs.length === 0) {
      errs.ingredients = 'At least one ingredient is required';
    }

    const cleanedDirs = (recipe.directions || []).map((d) => d.trim()).filter(Boolean);
    if (cleanedDirs.length === 0) {
      errs.directions = 'At least one direction step is required';
    }

    setErrors(errs);
    return Object.keys(errs).length === 0;
  }, [recipe]);

  // Save changes
  const save = useCallback(async (): Promise<boolean> => {
    if (!validate()) return false;
    setIsSaving(true);
    try {
      // Normalize ingredients and directions
      const cleanedIngredients = recipe.ingredients
        .map((i) => i.trim())
        .filter(Boolean);

      const cleanedDirections = recipe.directions
        .map((d, idx) => {
          const raw = d.trim().replace(/^\d+[\.\)\-:]\s*/, '').replace(/^Step\s*\d+[\.\)\-:]\s*/i, '');
          return `${idx + 1}. ${raw}`;
        })
        .filter((d) => d.length > 3);

      const payload: RecipeSchema = {
        ...recipe,
        title: recipe.title.trim(),
        description: recipe.description?.trim() || '',
        ingredients: cleanedIngredients,
        directions: cleanedDirections,
        tags: Array.from(new Set(recipe.tags || [])),
      };

      const updated = await api.updateRecipe(recipe.recipe_id, payload, sessionId);
      setRecipe(updated);
      setIsDirty(false);
      setSaveSuccess(true);
      onSaved?.(updated);
      return true;
    } catch (err: any) {
      setErrors((prev) => ({ ...prev, submit: err.message || 'Failed to save recipe' }));
      return false;
    } finally {
      setIsSaving(false);
    }
  }, [recipe, validate, sessionId, onSaved]);

  const reset = useCallback(() => {
    if (initialRecipe) {
      setRecipe(initialRecipe);
      setIsDirty(false);
      setErrors({});
      setSaveSuccess(false);
    }
  }, [initialRecipe]);

  return {
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
    validate,
    save,
    reset,
  };
}
