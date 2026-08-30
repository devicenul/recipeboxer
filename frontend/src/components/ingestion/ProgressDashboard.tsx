import React from 'react';
import {
  CheckCircle2,
  Clock,
  Sparkles,
  Layers,
  Fingerprint,
  GitMerge,
  FileCheck,
  BookOpen,
  Download,
} from 'lucide-react';
import { PipelineStatus, PipelineState } from '../../types/pipeline';
import { formatDuration } from '../../utils/formatters';

export interface ProgressDashboardProps {
  status: PipelineStatus;
  onNavigateToHitl?: () => void;
  onNavigateToRecipes?: () => void;
  onNavigateToExport?: () => void;
}

export const ProgressDashboard: React.FC<ProgressDashboardProps> = ({
  status,
  onNavigateToHitl,
  onNavigateToRecipes,
  onNavigateToExport,
}) => {
  const { progress, token_usage, elapsed_seconds, status: state } = status;

  const stages = [
    { key: PipelineState.SCANNING, label: 'Scanning', icon: Layers },
    { key: PipelineState.FINGERPRINTING, label: 'Fingerprinting', icon: Fingerprint },
    { key: PipelineState.CORRELATING, label: 'Correlating', icon: GitMerge },
    { key: PipelineState.HITL_PENDING, label: 'HITL Review', icon: FileCheck },
    { key: PipelineState.EXTRACTING, label: 'Extracting', icon: BookOpen },
    { key: PipelineState.COMPLETED, label: 'Completed', icon: Download },
  ];

  const getStageIndex = (s: PipelineState | string) => {
    switch (s) {
      case PipelineState.SCANNING:
        return 0;
      case PipelineState.FINGERPRINTING:
        return 1;
      case PipelineState.CORRELATING:
        return 2;
      case PipelineState.HITL_PENDING:
        return 3;
      case PipelineState.EXTRACTING:
        return 4;
      case PipelineState.EXPORTING:
      case PipelineState.COMPLETED:
        return 5;
      default:
        return -1;
    }
  };

  const currentStageIdx = getStageIndex(state);
  const percent = Math.min(100, Math.max(0, Math.round(progress.percent_complete || 0)));

  return (
    <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-6 shadow-xs flex flex-col gap-6">
      {/* Header & Overall Percent */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-slate-200 dark:border-slate-800">
        <div>
          <h2 className="text-base font-bold text-slate-900 dark:text-slate-100 flex items-center gap-2">
            <span>Pipeline Telemetry &amp; Execution</span>
          </h2>
          <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
            Session: <span className="font-mono text-slate-700 dark:text-slate-300">{status.session_id}</span>
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            <Clock className="w-4 h-4 text-blue-500" />
            <span className="font-mono">{formatDuration(elapsed_seconds)}</span>
          </div>
          <div className="text-right">
            <span className="text-2xl font-black text-blue-600 dark:text-blue-400 font-mono">
              {percent}%
            </span>
          </div>
        </div>
      </div>

      {/* Progress Bar */}
      <div className="w-full bg-slate-100 dark:bg-slate-800 h-3 rounded-full overflow-hidden p-0.5 border border-slate-200 dark:border-slate-700">
        <div
          className="h-full bg-gradient-to-r from-blue-600 to-indigo-500 rounded-full transition-all duration-300 shadow-sm"
          style={{ width: `${percent}%` }}
        />
      </div>

      {/* Multi-Stage Stepper */}
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2">
        {stages.map((stg, idx) => {
          const isDone = currentStageIdx > idx || state === PipelineState.COMPLETED;
          const isCurrent = currentStageIdx === idx && state !== PipelineState.COMPLETED;
          const Icon = stg.icon;

          return (
            <div
              key={stg.key}
              className={`flex flex-col items-center justify-center p-3 rounded-xl border text-center transition-all ${
                isDone
                  ? 'border-emerald-200 dark:border-emerald-800/80 bg-emerald-50/50 dark:bg-emerald-950/30 text-emerald-800 dark:text-emerald-300'
                  : isCurrent
                  ? 'border-blue-500 bg-blue-50/80 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 ring-2 ring-blue-500/20 animate-pulse'
                  : 'border-slate-200 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-900/30 text-slate-400 dark:text-slate-600'
              }`}
            >
              {isDone ? (
                <CheckCircle2 className="w-5 h-5 text-emerald-600 dark:text-emerald-400 mb-1" />
              ) : (
                <Icon className={`w-5 h-5 mb-1 ${isCurrent ? 'text-blue-600 dark:text-blue-400' : ''}`} />
              )}
              <span className="text-[11px] font-semibold tracking-tight">{stg.label}</span>
            </div>
          );
        })}
      </div>

      {/* Metrics Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 pt-2">
        <div className="p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 flex flex-col">
          <span className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Scans Ingested</span>
          <span className="text-xl font-bold text-slate-900 dark:text-slate-100 font-mono mt-1">
            {progress.total_scans}
          </span>
        </div>

        <div className="p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 flex flex-col">
          <span className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Fingerprinted</span>
          <span className="text-xl font-bold text-slate-900 dark:text-slate-100 font-mono mt-1">
            {progress.fingerprinted}
          </span>
        </div>

        <div className="p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 flex flex-col">
          <span className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Proposals</span>
          <span className="text-xl font-bold text-slate-900 dark:text-slate-100 font-mono mt-1">
            {progress.proposals_generated}
          </span>
        </div>

        <div className="p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 flex flex-col">
          <span className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Approved Recipes</span>
          <span className="text-xl font-bold text-emerald-600 dark:text-emerald-400 font-mono mt-1">
            {progress.approved_recipes ?? progress.approved_pairs ?? 0}
          </span>
        </div>


        <div className="p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 flex flex-col">
          <span className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Recipes Extracted</span>
          <span className="text-xl font-bold text-blue-600 dark:text-blue-400 font-mono mt-1">
            {progress.recipes_extracted}
          </span>
        </div>
      </div>

      {/* Token Usage Breakdown */}
      <div className="p-4 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-850/50 flex flex-wrap items-center justify-between gap-4 text-xs">
        <div className="flex items-center gap-2 font-medium text-slate-700 dark:text-slate-300">
          <Sparkles className="w-4 h-4 text-purple-500" />
          <span>Gemini Token Consumption</span>
        </div>
        <div className="flex items-center gap-4 flex-wrap text-slate-600 dark:text-slate-400 font-mono">
          <span>Prompt: <strong className="text-slate-900 dark:text-slate-100">{token_usage.prompt_tokens.toLocaleString()}</strong></span>
          <span>Candidates: <strong className="text-slate-900 dark:text-slate-100">{token_usage.candidates_tokens.toLocaleString()}</strong></span>
          {token_usage.thinking_tokens > 0 && (
            <span>Thinking: <strong className="text-purple-600 dark:text-purple-400">{token_usage.thinking_tokens.toLocaleString()}</strong></span>
          )}
          <span>Total: <strong className="text-blue-600 dark:text-blue-400">{token_usage.total_tokens.toLocaleString()}</strong></span>
        </div>
      </div>

      {/* Call to Actions based on state */}
      {state === PipelineState.HITL_PENDING && onNavigateToHitl && (
        <div className="flex items-center justify-between p-4 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 rounded-xl">
          <div className="text-xs text-amber-800 dark:text-amber-300">
            <strong>Correlation Proposals Ready:</strong> Human review required to approve, reject, or mark standalone cards.
          </div>
          <button
            onClick={onNavigateToHitl}
            className="px-4 py-2 text-xs font-semibold rounded-lg bg-amber-600 hover:bg-amber-700 text-white shadow-sm transition-all cursor-pointer"
          >
            Open Rapid Queue →
          </button>
        </div>
      )}

      {state === PipelineState.COMPLETED && (
        <div className="flex items-center justify-between p-4 bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 rounded-xl">
          <div className="text-xs text-emerald-800 dark:text-emerald-300">
            <strong>Digitization Complete:</strong> All structured recipes extracted and ready for editing and multi-format export.
          </div>
          <div className="flex gap-2">
            {onNavigateToRecipes && (
              <button
                onClick={onNavigateToRecipes}
                className="px-3.5 py-1.5 text-xs font-semibold rounded-lg bg-blue-600 hover:bg-blue-700 text-white shadow-sm transition-all cursor-pointer"
              >
                Review Recipes
              </button>
            )}
            {onNavigateToExport && (
              <button
                onClick={onNavigateToExport}
                className="px-3.5 py-1.5 text-xs font-semibold rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white shadow-sm transition-all cursor-pointer"
              >
                Export CSV / JSON
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
