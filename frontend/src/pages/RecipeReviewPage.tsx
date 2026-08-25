import React from 'react';
import { RecipeSchema } from '../types/recipe';
import { RecipeEditorView } from '../components/recipe/RecipeEditorView';
import { BookOpen, Layers } from 'lucide-react';
import { Button } from '../components/common/Button';

export interface RecipeReviewPageProps {
  recipes: RecipeSchema[];
  sessionId?: string | null;
  onRecipeUpdated: (updated: RecipeSchema) => void;
  onNavigateToIngest: () => void;
}

export const RecipeReviewPage: React.FC<RecipeReviewPageProps> = ({
  recipes,
  sessionId,
  onRecipeUpdated,
  onNavigateToIngest,
}) => {
  if (!recipes || recipes.length === 0) {
    return (
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-12 text-center flex flex-col items-center gap-4">
        <BookOpen className="w-12 h-12 text-slate-400" />
        <div>
          <h3 className="text-base font-bold text-slate-900 dark:text-slate-100">
            No Extracted Recipes Yet
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-sm">
            Complete the HITL queue review to extract structured JSON recipes from scanned cards with Gemini.
          </p>
        </div>
        <Button variant="primary" size="md" onClick={onNavigateToIngest} leftIcon={<Layers className="w-4 h-4" />}>
          View Ingestion &amp; Queue
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <RecipeEditorView
        recipes={recipes}
        sessionId={sessionId || undefined}
        onRecipeUpdated={onRecipeUpdated}
      />
    </div>
  );
};
