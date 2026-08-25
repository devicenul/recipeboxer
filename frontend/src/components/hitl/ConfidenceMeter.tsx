import React from 'react';
import { Sparkles, Info } from 'lucide-react';
import { formatScorePercent } from '../../utils/formatters';

export interface ConfidenceMeterProps {
  confidence: number; // 0.0 - 1.0
  heuristicScore?: number;
  semanticScore?: number;
  reasoningSnippet?: string;
  className?: string;
}

export const ConfidenceMeter: React.FC<ConfidenceMeterProps> = ({
  confidence,
  heuristicScore = 0,
  semanticScore = 0,
  reasoningSnippet,
  className = '',
}) => {
  const percent = Math.round(confidence * 100);

  // Color scheme based on thresholds: >= 80% Green, 50-79% Amber, < 50% Red
  const getColorClasses = () => {
    if (percent >= 80) {
      return {
        badge: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800',
        bar: 'bg-emerald-500',
        text: 'text-emerald-600 dark:text-emerald-400',
      };
    } else if (percent >= 50) {
      return {
        badge: 'bg-amber-100 text-amber-800 dark:bg-amber-950/80 dark:text-amber-300 border-amber-200 dark:border-amber-800',
        bar: 'bg-amber-500',
        text: 'text-amber-600 dark:text-amber-400',
      };
    } else {
      return {
        badge: 'bg-rose-100 text-rose-800 dark:bg-rose-950/80 dark:text-rose-300 border-rose-200 dark:border-rose-800',
        bar: 'bg-rose-500',
        text: 'text-rose-600 dark:text-rose-400',
      };
    }
  };

  const colors = getColorClasses();

  return (
    <div className={`flex flex-col gap-2.5 p-3.5 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-xs ${className}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-800 dark:text-slate-200">
          <Sparkles className="w-3.5 h-3.5 text-blue-500" />
          <span>Match Confidence</span>
        </div>
        <span
          className={`px-2.5 py-0.5 text-xs font-black font-mono rounded-full border ${colors.badge}`}
        >
          {percent}% Match
        </span>
      </div>

      {/* Progress Bar */}
      <div className="w-full bg-slate-100 dark:bg-slate-800 h-2 rounded-full overflow-hidden">
        <div
          className={`h-full ${colors.bar} rounded-full transition-all duration-300`}
          style={{ width: `${percent}%` }}
        />
      </div>

      {/* Score Breakdown (Heuristic 30% / Semantic 70%) */}
      <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-600 dark:text-slate-400 pt-1 border-t border-slate-100 dark:border-slate-800/60 font-mono">
        <div>
          <span>Heuristic (30%): </span>
          <strong className="text-slate-900 dark:text-slate-100">{formatScorePercent(heuristicScore)}</strong>
        </div>
        <div className="text-right">
          <span>Semantic (70%): </span>
          <strong className="text-slate-900 dark:text-slate-100">{formatScorePercent(semanticScore)}</strong>
        </div>
      </div>

      {/* Reasoning Snippet */}
      {reasoningSnippet && (
        <div className="flex items-start gap-1.5 p-2 bg-slate-50 dark:bg-slate-850 rounded-lg text-xs text-slate-600 dark:text-slate-400 border border-slate-200/60 dark:border-slate-800">
          <Info className="w-3.5 h-3.5 text-blue-500 shrink-0 mt-0.5" />
          <span className="italic leading-relaxed">{reasoningSnippet}</span>
        </div>
      )}
    </div>
  );
};
