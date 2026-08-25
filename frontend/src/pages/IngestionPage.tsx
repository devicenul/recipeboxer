import React, { useEffect, useState, useCallback } from 'react';
import { SessionLauncher } from '../components/ingestion/SessionLauncher';
import { ProgressDashboard } from '../components/ingestion/ProgressDashboard';
import { SessionHistoryTable } from '../components/ingestion/SessionHistoryTable';
import { PipelineStatus, SessionSummary } from '../types/pipeline';
import { api } from '../utils/api';

export interface IngestionPageProps {
  currentStatus: PipelineStatus;
  sessionId?: string | null;
  onSessionSelect: (sessionId: string) => void;
  onNavigateToHitl: () => void;
  onNavigateToRecipes: () => void;
  onNavigateToExport: () => void;
}

export const IngestionPage: React.FC<IngestionPageProps> = ({
  currentStatus,
  sessionId,
  onSessionSelect,
  onNavigateToHitl,
  onNavigateToRecipes,
  onNavigateToExport,
}) => {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loadingSessions, setLoadingSessions] = useState<boolean>(false);

  const fetchSessions = useCallback(async () => {
    setLoadingSessions(true);
    try {
      const list = await api.listSessions();
      setSessions(list || []);
    } catch {
      setSessions([]);
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const handleDeleteSession = async (sid: string) => {
    try {
      await api.deleteSession(sid);
      fetchSessions();
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  };

  const hasActiveSession = !!sessionId && sessionId !== 'none';

  return (
    <div className="flex flex-col gap-8">
      {/* Live Dashboard if session active */}
      {hasActiveSession && (
        <ProgressDashboard
          status={currentStatus}
          onNavigateToHitl={onNavigateToHitl}
          onNavigateToRecipes={onNavigateToRecipes}
          onNavigateToExport={onNavigateToExport}
        />
      )}

      {/* Session Ingestion Setup */}
      <div className="flex flex-col gap-4">
        <h2 className="text-base font-bold text-slate-900 dark:text-slate-100">
          New Scans Ingestion
        </h2>
        <SessionLauncher onSessionStarted={onSessionSelect} />
      </div>

      {/* Historical Sessions Table */}
      <SessionHistoryTable
        sessions={sessions}
        onResumeSession={(sid) => {
          onSessionSelect(sid);
          onNavigateToHitl();
        }}
        onViewRecipes={(sid) => {
          onSessionSelect(sid);
          onNavigateToRecipes();
        }}
        onDeleteSession={handleDeleteSession}
        onRefresh={fetchSessions}
        isLoading={loadingSessions}
      />
    </div>
  );
};
