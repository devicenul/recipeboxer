import React from 'react';
import { HitlWorkspace } from '../components/hitl/HitlWorkspace';
import { GitMerge, Layers } from 'lucide-react';
import { Button } from '../components/common/Button';

import { HitlItem } from '../types/hitl';

export interface HitlReviewPageProps {
  sessionId?: string | null;
  onApprovalsCompleted: () => void;
  onNavigateToIngest: () => void;
  initialBudgetItem?: HitlItem | null;
}

export const HitlReviewPage: React.FC<HitlReviewPageProps> = ({
  sessionId,
  onApprovalsCompleted,
  onNavigateToIngest,
  initialBudgetItem,
}) => {
  if (!sessionId) {
    return (
      <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-12 text-center flex flex-col items-center gap-4">
        <GitMerge className="w-12 h-12 text-slate-400" />
        <div>
          <h3 className="text-base font-bold text-slate-900 dark:text-slate-100">
            No Active Ingestion Session
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-sm">
            Launch a new scan or select an existing session from the Ingestion panel to begin Human-in-the-Loop correlation review.
          </p>
        </div>
        <Button variant="primary" size="md" onClick={onNavigateToIngest} leftIcon={<Layers className="w-4 h-4" />}>
          Go to Ingestion
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <HitlWorkspace
        sessionId={sessionId}
        onApprovalsCompleted={onApprovalsCompleted}
        initialBudgetItem={initialBudgetItem}
      />
    </div>
  );
};
