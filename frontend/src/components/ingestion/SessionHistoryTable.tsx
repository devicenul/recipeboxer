import React from 'react';
import { History, Play, BookOpen, Trash2, RefreshCw } from 'lucide-react';
import { SessionSummary, PipelineState } from '../../types/pipeline';
import { Badge } from '../common/Badge';
import { formatDate } from '../../utils/formatters';

export interface SessionHistoryTableProps {
  sessions: SessionSummary[];
  onResumeSession: (sessionId: string) => void;
  onViewRecipes: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onRefresh: () => void;
  isLoading?: boolean;
}

export const SessionHistoryTable: React.FC<SessionHistoryTableProps> = ({
  sessions,
  onResumeSession,
  onViewRecipes,
  onDeleteSession,
  onRefresh,
  isLoading = false,
}) => {
  const getStatusBadge = (status: PipelineState | string) => {
    switch (status) {
      case PipelineState.COMPLETED:
      case 'COMPLETED':
        return <Badge variant="success">Completed</Badge>;
      case PipelineState.HITL_PENDING:
      case 'HITL_PENDING':
        return <Badge variant="warning">HITL Pending</Badge>;
      case PipelineState.SCANNING:
      case PipelineState.FINGERPRINTING:
      case PipelineState.CORRELATING:
      case PipelineState.EXTRACTING:
      case 'SCANNING':
      case 'FINGERPRINTING':
      case 'CORRELATING':
      case 'EXTRACTING':
        return <Badge variant="primary">Processing</Badge>;
      case PipelineState.FAILED:
      case 'FAILED':
        return <Badge variant="error">Failed</Badge>;
      default:
        return <Badge variant="neutral">{status}</Badge>;
    }
  };

  return (
    <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-xs flex flex-col gap-4">
      <div className="flex items-center justify-between pb-3 border-b border-slate-200 dark:border-slate-800">
        <div className="flex items-center gap-2 font-semibold text-sm text-slate-900 dark:text-slate-100">
          <History className="w-4 h-4 text-blue-500" />
          <span>Historical &amp; Active Sessions</span>
        </div>
        <button
          onClick={onRefresh}
          disabled={isLoading}
          className="flex items-center gap-1.5 px-2.5 py-1 text-xs text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {sessions.length === 0 ? (
        <div className="py-8 text-center text-xs text-slate-500 dark:text-slate-400">
          No previous sessions found in SQLite database. Start a new scan above!
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs text-slate-600 dark:text-slate-300">
            <thead className="bg-slate-50 dark:bg-slate-850/50 text-slate-700 dark:text-slate-300 uppercase tracking-wider font-semibold border-b border-slate-200 dark:border-slate-800">
              <tr>
                <th className="px-3.5 py-2.5">Session ID</th>
                <th className="px-3.5 py-2.5">Status</th>
                <th className="px-3.5 py-2.5">Cards</th>
                <th className="px-3.5 py-2.5">Recipes</th>
                <th className="px-3.5 py-2.5">Created</th>
                <th className="px-3.5 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {sessions.map((sess) => {
                const isHitl = sess.status === PipelineState.HITL_PENDING || sess.status === 'HITL_PENDING';
                const isDone = sess.status === PipelineState.COMPLETED || sess.status === 'COMPLETED';

                return (
                  <tr
                    key={sess.session_id}
                    className="hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors"
                  >
                    <td className="px-3.5 py-3 font-mono font-medium text-slate-900 dark:text-slate-100">
                      {sess.session_id}
                    </td>
                    <td className="px-3.5 py-3">{getStatusBadge(sess.status)}</td>
                    <td className="px-3.5 py-3 font-mono">{sess.card_count}</td>
                    <td className="px-3.5 py-3 font-mono">{sess.recipe_count}</td>
                    <td className="px-3.5 py-3">{formatDate(sess.created_at)}</td>
                    <td className="px-3.5 py-3 text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        {isHitl && (
                          <button
                            onClick={() => onResumeSession(sess.session_id)}
                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-amber-100 text-amber-800 dark:bg-amber-950/80 dark:text-amber-300 hover:bg-amber-200 text-xs font-semibold cursor-pointer"
                            title="Resume HITL Review"
                          >
                            <Play className="w-3 h-3 fill-current" />
                            Resume
                          </button>
                        )}
                        {isDone && (
                          <button
                            onClick={() => onViewRecipes(sess.session_id)}
                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-blue-100 text-blue-800 dark:bg-blue-950/80 dark:text-blue-300 hover:bg-blue-200 text-xs font-semibold cursor-pointer"
                            title="View Recipes"
                          >
                            <BookOpen className="w-3 h-3" />
                            Recipes
                          </button>
                        )}
                        <button
                          onClick={() => onDeleteSession(sess.session_id)}
                          className="p-1 text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 rounded hover:bg-slate-100 dark:hover:bg-slate-800 cursor-pointer"
                          title="Delete Session"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
