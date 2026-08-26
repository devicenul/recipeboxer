import React, { useState, useEffect, useCallback } from 'react';
import { Header } from './components/layout/Header';
import { StatusBar } from './components/layout/StatusBar';
import { NavTab } from './components/layout/Navigation';
import { IngestionPage } from './pages/IngestionPage';
import { HitlReviewPage } from './pages/HitlReviewPage';
import { RecipeReviewPage } from './pages/RecipeReviewPage';
import { ExportPage } from './pages/ExportPage';
import { TutorialOverlay, TUTORIAL_STEPS, TUTORIAL_STORAGE_KEY } from './components/tutorial';
import { PipelineStatus, PipelineState, IngestionMode } from './types/pipeline';
import { RecipeSchema } from './types/recipe';
import { useSSE } from './hooks/useSSE';
import { api } from './utils/api';
import { ThemeProvider } from './context/ThemeContext';

// Module-level fallback for environments where localStorage is blocked (e.g., sandbox iframes)
let inMemoryTutorialCompleted = false;

export function resetTutorialSessionState(): void {
  inMemoryTutorialCompleted = false;
}

export const AppContent: React.FC = () => {
  const [activeTab, setActiveTab] = useState<NavTab>('ingest');
  const [sessionId, setSessionId] = useState<string | null>(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const sid = params.get('session_id');
      if (sid) return sid;
      const path = window.location.pathname;
      const match = path.match(/\/sessions\/([^\/]+)/);
      if (match) return match[1];
    }
    return null;
  });

  const [isTutorialOpen, setIsTutorialOpen] = useState<boolean>(() => {
    if (typeof window !== 'undefined') {
      try {
        const completed = localStorage.getItem(TUTORIAL_STORAGE_KEY);
        if (completed !== null) {
          return completed !== 'true';
        }
        return !inMemoryTutorialCompleted;
      } catch {
        return !inMemoryTutorialCompleted;
      }
    }
    return false;
  });
  const [currentTutorialStep, setCurrentTutorialStep] = useState<number>(0);

  const [currentStatus, setCurrentStatus] = useState<PipelineStatus>({
    session_id: sessionId || 'none',
    status: PipelineState.IDLE,
    mode: IngestionMode.PARALLEL,
    progress: {
      total_scans: 0,
      fingerprinted: 0,
      proposals_generated: 0,
      approved_pairs: 0,
      recipes_extracted: 0,
      current_stage: 'Idle',
      percent_complete: 0,
    },
    token_usage: {
      prompt_tokens: 0,
      candidates_tokens: 0,
      thinking_tokens: 0,
      total_tokens: 0,
    },
    elapsed_seconds: 0,
  });

  const [recipes, setRecipes] = useState<RecipeSchema[]>([]);
  const [pendingHitlCount, setPendingHitlCount] = useState<number>(0);

  // Sync recipes when session changes or completes
  const fetchRecipes = useCallback(async (sid?: string | null) => {
    const target = sid || sessionId;
    if (!target) return;
    try {
      const list = await api.listRecipes(target);
      setRecipes(list || []);
    } catch {
      // Ignored
    }
  }, [sessionId]);

  const fetchStatus = useCallback(async (sid?: string | null) => {
    const target = sid || sessionId;
    if (!target) return;
    try {
      const st = await api.getStatus(target);
      setCurrentStatus(st);
      if (st.status === PipelineState.HITL_PENDING) {
        setPendingHitlCount(st.progress.proposals_generated || 1);
      }
    } catch {
      // Ignored
    }
  }, [sessionId]);

  useEffect(() => {
    if (sessionId) {
      fetchStatus(sessionId);
      fetchRecipes(sessionId);
    }
  }, [sessionId, fetchStatus, fetchRecipes]);

  // Handle SSE Telemetry Stream
  const { isConnected } = useSSE({
    sessionId,
    enabled: !!sessionId,
    onStateChange: (status) => {
      setCurrentStatus(status);
      if (status.status === PipelineState.HITL_PENDING) {
        setPendingHitlCount(status.progress.proposals_generated || 1);
        setActiveTab('hitl');
      } else if (status.status === PipelineState.COMPLETED) {
        setPendingHitlCount(0);
        fetchRecipes(sessionId);
      }
    },
    onProgress: (status) => {
      setCurrentStatus(status);
    },
    onHitlReady: (data) => {
      setPendingHitlCount(data.proposals_count || 1);
      setActiveTab('hitl');
    },
    onRecipeExtracted: (newRecipe) => {
      setRecipes((prev) => {
        const exists = prev.some((r) => r.recipe_id === newRecipe.recipe_id);
        if (exists) {
          return prev.map((r) => (r.recipe_id === newRecipe.recipe_id ? newRecipe : r));
        }
        return [...prev, newRecipe];
      });
    },
    onCompleted: (status) => {
      setCurrentStatus(status);
      fetchRecipes(sessionId);
    },
  });

  const handleSessionSelect = (newSessionId: string) => {
    setSessionId(newSessionId);
    if (typeof window !== 'undefined' && window.history.pushState) {
      const url = new URL(window.location.href);
      url.searchParams.set('session_id', newSessionId);
      window.history.pushState({}, '', url.toString());
    }
  };

  const handleRecipeUpdated = (updated: RecipeSchema) => {
    setRecipes((prev) =>
      prev.map((r) => (r.recipe_id === updated.recipe_id ? updated : r))
    );
  };

  const handleOpenTutorial = () => {
    setCurrentTutorialStep(0);
    setIsTutorialOpen(true);
    setActiveTab(TUTORIAL_STEPS[0].targetTab);
  };

  const handleCloseTutorial = () => {
    setIsTutorialOpen(false);
    inMemoryTutorialCompleted = true;
    if (typeof window !== 'undefined') {
      try {
        localStorage.setItem(TUTORIAL_STORAGE_KEY, 'true');
      } catch {
        // Fallback to in-memory session persistence
      }
    }
  };

  const handleTutorialStepChange = (newIndex: number) => {
    setCurrentTutorialStep(newIndex);
    const step = TUTORIAL_STEPS[newIndex];
    if (step && step.targetTab) {
      setActiveTab(step.targetTab);
    }
  };

  return (
    <div className="flex flex-col min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-50 transition-colors duration-150">
      {/* Header */}
      <Header
        activeTab={activeTab}
        onTabChange={setActiveTab}
        sessionId={sessionId}
        pipelineState={currentStatus.status}
        isConnected={isConnected}
        pendingHitlCount={pendingHitlCount}
        extractedRecipeCount={recipes.length}
        onOpenTutorial={handleOpenTutorial}
      />

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {activeTab === 'ingest' && (
          <IngestionPage
            currentStatus={currentStatus}
            sessionId={sessionId}
            onSessionSelect={handleSessionSelect}
            onNavigateToHitl={() => setActiveTab('hitl')}
            onNavigateToRecipes={() => setActiveTab('recipes')}
            onNavigateToExport={() => setActiveTab('export')}
          />
        )}

        {activeTab === 'hitl' && (
          <HitlReviewPage
            sessionId={sessionId}
            onApprovalsCompleted={() => {
              setPendingHitlCount(0);
              setActiveTab('ingest');
            }}
            onNavigateToIngest={() => setActiveTab('ingest')}
          />
        )}

        {activeTab === 'recipes' && (
          <RecipeReviewPage
            recipes={recipes}
            sessionId={sessionId}
            onRecipeUpdated={handleRecipeUpdated}
            onNavigateToIngest={() => setActiveTab('ingest')}
            onNavigateToExport={() => setActiveTab('export')}
          />
        )}

        {activeTab === 'export' && (
          <ExportPage recipes={recipes} sessionId={sessionId} />
        )}
      </main>

      {/* Status Bar */}
      <StatusBar
        model="gemini-3.7-flash"
        mode={currentStatus.mode || 'parallel'}
        elapsedSeconds={currentStatus.elapsed_seconds}
        tokenUsage={currentStatus.token_usage}
        showHotkeys={activeTab === 'hitl'}
      />

      {/* Interactive Guided UI Tutorial Overlay */}
      <TutorialOverlay
        isOpen={isTutorialOpen}
        currentStepIndex={currentTutorialStep}
        onStepChange={handleTutorialStepChange}
        onClose={handleCloseTutorial}
      />
    </div>
  );
};

export default function App() {
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  );
}

