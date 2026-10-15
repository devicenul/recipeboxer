import React from 'react';
import { Sparkles, Info, CheckCircle2, AlertTriangle, Quote } from 'lucide-react';
import { formatScorePercent } from '../../utils/formatters';

export interface ConfidenceMeterProps {
  confidence: number; // 0.0 - 1.0
  pLink?: number | null;
  heuristicScore?: number;
  semanticScore?: number;
  reasoningSnippet?: string;
  className?: string;
  semanticVerified?: boolean;
  degradedReason?: string | null;
  evidenceQuotes?: string[];
  flags?: string[];
  isLowConfidence?: boolean;
}

export const ConfidenceMeter: React.FC<ConfidenceMeterProps> = ({
  confidence,
  pLink,
  heuristicScore = 0,
  semanticScore = 0,
  reasoningSnippet,
  className = '',
  semanticVerified,
  degradedReason,
  evidenceQuotes = [],
  flags = [],
  isLowConfidence,
}) => {
  const effectiveScore = pLink !== undefined && pLink !== null ? pLink : confidence;
  const percent = Math.round(effectiveScore * 100);
  const isBelowThreshold = isLowConfidence ?? (effectiveScore < 0.7);

  // Color scheme based on thresholds: >= 80% Green, 70-79% Amber, < 70% Red
  const getColorClasses = () => {
    if (percent >= 80) {
      return {
        badge: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/80 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800',
        bar: 'bg-emerald-500',
        text: 'text-emerald-600 dark:text-emerald-400',
      };
    } else if (percent >= 70) {
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
    <div className={`flex flex-col gap-3 p-4 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl shadow-xs ${className}`}>
      {/* Top Header: Title, Calibrated Confidence, and Verified Status */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 text-xs font-bold text-slate-800 dark:text-slate-200">
            <Sparkles className="w-3.5 h-3.5 text-blue-500" />
            <span>Calibrated Link Confidence</span>
          </div>

          {/* Verified vs Unverified Badge */}
          {semanticVerified === true && (
            <span
              role="status"
              aria-label="Verified link via multimodal set verification"
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-800"
            >
              <CheckCircle2 className="w-3 h-3 text-emerald-600 dark:text-emerald-400" />
              Verified (Set Verification)
            </span>
          )}
          {semanticVerified === false && (
            <span
              role="status"
              aria-label="Unverified or degraded link"
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300 border border-amber-300 dark:border-amber-800"
              title={degradedReason ? `Degraded reason: ${degradedReason}` : 'Offline or heuristic fallback'}
            >
              <AlertTriangle className="w-3 h-3 text-amber-600 dark:text-amber-400" />
              Unverified / Degraded
            </span>
          )}
        </div>

        <span
          className={`px-2.5 py-0.5 text-xs font-black font-mono rounded-full border ${colors.badge}`}
        >
          {percent}% Match
        </span>
      </div>

      {/* Low Confidence Warning Flag */}
      {isBelowThreshold && (
        <div
          role="alert"
          aria-label="Low-confidence correlation link"
          className="flex items-center gap-2 p-2.5 bg-rose-50 dark:bg-rose-950/40 text-rose-800 dark:text-rose-200 rounded-lg text-xs border border-rose-300 dark:border-rose-800"
        >
          <AlertTriangle className="w-4 h-4 text-rose-600 dark:text-rose-400 shrink-0" />
          <div className="flex-1">
            <strong className="font-bold">Low-Confidence Link (&lt; 70%):</strong> Verification is required before grouping cards into a single multi-card recipe.
          </div>
        </div>
      )}

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
          <span>Physical / Heuristic (30%): </span>
          <strong className="text-slate-900 dark:text-slate-100">{formatScorePercent(heuristicScore)}</strong>
        </div>
        <div className="text-right">
          <span>Semantic Continuity (70%): </span>
          <strong className="text-slate-900 dark:text-slate-100">{formatScorePercent(semanticScore)}</strong>
        </div>
      </div>

      {/* Evidence Summary & Verbatim Quotes */}
      {(evidenceQuotes.length > 0 || reasoningSnippet || flags.length > 0) && (
        <div className="flex flex-col gap-2 p-3 bg-slate-50 dark:bg-slate-850 rounded-lg border border-slate-200 dark:border-slate-800 text-xs">
          <div className="font-bold text-slate-800 dark:text-slate-200 flex items-center gap-1.5">
            <Quote className="w-3.5 h-3.5 text-blue-500" />
            <span>Evidence Summary</span>
          </div>

          {/* Verbatim Visible Text Citations */}
          {evidenceQuotes.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-semibold text-slate-600 dark:text-slate-400">
                Cited Visible Text (Verbatim from Scans):
              </span>
              <div className="flex flex-col gap-1 pl-2.5 border-l-2 border-blue-400 dark:border-blue-600">
                {evidenceQuotes.map((quote, qIdx) => (
                  <blockquote
                    key={qIdx}
                    className="italic text-slate-700 dark:text-slate-300 font-serif leading-relaxed"
                  >
                    &ldquo;{quote}&rdquo;
                  </blockquote>
                ))}
              </div>
            </div>
          )}

          {/* Model Reasoning Snippet */}
          {reasoningSnippet && (
            <div className="flex items-start gap-1.5 pt-1 text-slate-600 dark:text-slate-400">
              <Info className="w-3.5 h-3.5 text-blue-500 shrink-0 mt-0.5" />
              <span className="italic leading-relaxed">{reasoningSnippet}</span>
            </div>
          )}

          {/* Unverified Quote Flag Alert */}
          {flags.some((f) => f.includes('UNVERIFIED_QUOTE')) && (
            <div
              role="alert"
              className="flex items-center gap-1.5 text-[11px] text-amber-700 dark:text-amber-300 pt-1 border-t border-amber-200 dark:border-amber-800/60"
            >
              <AlertTriangle className="w-3.5 h-3.5 text-amber-600 shrink-0" />
              <span>Unverified quote: Model cited text that could not be matched verbatim in OCR corpus.</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
