import React from 'react';
import { RecipeSchema } from '../types/recipe';
import { ExportDialog } from '../components/export/ExportDialog';

export interface ExportPageProps {
  recipes: RecipeSchema[];
  sessionId?: string | null;
}

export const ExportPage: React.FC<ExportPageProps> = ({ recipes, sessionId }) => {
  return (
    <div className="flex flex-col gap-6">
      <ExportDialog recipes={recipes} sessionId={sessionId || undefined} />
    </div>
  );
};
