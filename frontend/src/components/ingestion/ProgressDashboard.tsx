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
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-base font-bold text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <span>Pipeline Telemetry &amp; Execution</span>
            </h2>
            {Boolean(progress.skipped_cards && progress.skipped_cards > 0) && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800 flex items-center gap-1">
                <span>{progress.skipped_cards} Duplicates Skipped</span>
              </span>
            )}
            {Boolean(progress.baseline_recipes_count && progress.baseline_recipes_count > 0) && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-100 dark:bg-blue-950/80 text-blue-800 dark:text-blue-300 border border-blue-200 dark:border-blue-800 flex items-center gap-1">
                <span>{progress.baseline_recipes_count} Baseline Inherited</span>
              </span>
            )}
          </div>
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
      <div className={`grid grid-cols-2 sm:grid-cols-3 ${Boolean(progress.skipped_cards && progress.skipped_cards > 0) ? 'lg:grid-cols-6' : 'lg:grid-cols-5'} gap-3 pt-2`}>
        <div className="p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 flex flex-col">
          <span className="text-[11px] text-slate-500 dark:text-slate-400 font-medium">Scans Ingested</span>
          <span className="text-xl font-bold text-slate-900 dark:text-slate-100 font-mono mt-1">
            {progress.total_scans}
          </span>
        </div>

        {Boolean(progress.skipped_cards && progress.skipped_cards > 0) && (
          <div className="p-3.5 rounded-xl border border-emerald-200 dark:border-emerald-800/80 bg-emerald-50/50 dark:bg-emerald-950/30 flex flex-col">
            <span className="text-[11px] text-emerald-700 dark:text-emerald-300 font-medium">Duplicates Skipped</span>
            <span className="text-xl font-bold text-emerald-600 dark:text-emerald-400 font-mono mt-1">
              {progress.skipped_cards}
            </span>
          </div>
        )}

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

      {/* Active Extraction Progress Panel for EXTRACTING state */}
      {state === PipelineState.EXTRACTING && (() => {
        const totalApproved =
          (progress.approved_recipes ?? progress.approved_pairs ?? 0) ||
          (progress.proposals_generated > 0 ? progress.proposals_generated : 0) ||
          (progress.total_scans > 0 ? progress.total_scans : 1);
        const extractedCount = progress.recipes_extracted || 0;
        const currentRecipeNumber = Math.min(extractedCount + 1, Math.max(1, totalApproved));
        const extractionPercent = Math.min(100, Math.round((extractedCount / Math.max(1, totalApproved)) * 100));

        return (
          <div className="p-4 sm:p-5 bg-blue-50/80 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-800/80 rounded-xl flex flex-col gap-3.5 shadow-xs">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <span className="relative flex h-3 w-3">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-blue-600" />
                </span>
                <h3 className="text-sm font-bold text-blue-950 dark:text-blue-100 flex items-center gap-2">
                  Multimodal Recipe Extraction in Progress
                </h3>
              </div>
              <div className="flex items-center gap-2 font-mono text-xs font-bold text-blue-700 dark:text-blue-300 bg-blue-100 dark:bg-blue-900/60 px-2.5 py-1 rounded-md border border-blue-200 dark:border-blue-800">
                <span>Recipe {currentRecipeNumber} of {totalApproved}</span>
              </div>
            </div>

            {/* Dynamic Secondary Extraction Progress Sub-bar */}
            <div className="flex flex-col gap-1.5">
              <div className="w-full bg-blue-100 dark:bg-blue-900/40 h-2.5 rounded-full overflow-hidden p-0.5 border border-blue-200 dark:border-blue-800">
                <div
                  className="h-full bg-gradient-to-r from-blue-500 to-indigo-600 rounded-full transition-all duration-300 shadow-sm"
                  style={{ width: `${extractionPercent}%` }}
                />
              </div>
              <div className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400 font-mono">
                <span>Extraction Stage Progress</span>
                <span>{extractionPercent}%</span>
              </div>
            </div>

            {/* Completed Recipe Count & Auto-Transition Notice */}
            <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-blue-200/60 dark:border-blue-800/60 text-xs text-blue-800 dark:text-blue-300">
              <div className="flex items-center gap-1.5 font-medium">
                <span>Completed recipes:</span>
                <strong className="font-bold font-mono text-blue-950 dark:text-blue-100">
                  {extractedCount}
                </strong>
                <span>of {totalApproved}</span>
              </div>
              <span className="text-[11px] text-blue-600/90 dark:text-blue-400 italic">
                Auto-transitioning to Recipe Editor upon completion...
              </span>
            </div>
          </div>
        );
      })()}

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
