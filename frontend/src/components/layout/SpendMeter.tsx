import React from 'react';
import { DollarSign, AlertOctagon, PhoneCall } from 'lucide-react';

export interface SpendMeterProps {
  spendUsd: number;
  spendCapUsd?: number | null;
  totalCalls?: number;
  isBudgetExceeded?: boolean;
  onNavigateToSettings?: () => void;
  className?: string;
}

export const SpendMeter: React.FC<SpendMeterProps> = ({
  spendUsd = 0.0,
  spendCapUsd = null,
  totalCalls = 0,
  isBudgetExceeded = false,
  onNavigateToSettings,
  className = '',
}) => {
  const hasCap = spendCapUsd !== null && spendCapUsd !== undefined && spendCapUsd > 0;
  const progressPercent = hasCap ? Math.min(100, (spendUsd / spendCapUsd) * 100) : 0;
  const isNearCap = hasCap && progressPercent >= 85 && !isBudgetExceeded;

  return (
    <div
      data-testid="spend-meter"
      className={`flex items-center gap-2.5 px-2.5 py-1 rounded-lg border text-xs transition-all ${
        isBudgetExceeded
          ? 'bg-rose-50 dark:bg-rose-950/60 border-rose-300 dark:border-rose-800 text-rose-900 dark:text-rose-200'
          : isNearCap
          ? 'bg-amber-50 dark:bg-amber-950/50 border-amber-300 dark:border-amber-800 text-amber-900 dark:text-amber-200'
          : 'bg-slate-50 dark:bg-slate-900/60 border-slate-200 dark:border-slate-800 text-slate-700 dark:text-slate-300'
      } ${className}`}
    >
      {/* Icon + USD Spend */}
      <div className="flex items-center gap-1 font-semibold" title="Live Gemini API Spend">
        <DollarSign
          className={`w-3.5 h-3.5 ${
            isBudgetExceeded
              ? 'text-rose-600 dark:text-rose-400 animate-pulse'
              : 'text-emerald-600 dark:text-emerald-400'
          }`}
        />
        <span className="font-mono text-xs">
          ${spendUsd.toFixed(4)}
        </span>
      </div>

      {/* Spend Cap Comparison */}
      {hasCap && (
        <div className="flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
          <span>/</span>
          <span className="font-mono">${spendCapUsd.toFixed(2)}</span>
          <div
            className="w-14 h-1.5 bg-slate-200 dark:bg-slate-800 rounded-full overflow-hidden"
            role="progressbar"
            aria-valuenow={Math.round(progressPercent)}
            aria-valuemin={0}
            aria-valuemax={100}
            title={`${Math.round(progressPercent)}% of run budget cap`}
          >
            <div
              className={`h-full transition-all duration-300 ${
                isBudgetExceeded
                  ? 'bg-rose-600 dark:bg-rose-500'
                  : isNearCap
                  ? 'bg-amber-500'
                  : 'bg-emerald-500'
              }`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>
          <span className="font-mono text-[10px]">
            {Math.round(progressPercent)}%
          </span>
        </div>
      )}

      {/* Calls Counter */}
      {totalCalls > 0 && (
        <div className="hidden sm:flex items-center gap-0.5 text-[10px] text-slate-500 dark:text-slate-400 font-mono">
          <PhoneCall className="w-2.5 h-2.5 text-slate-400" />
          <span>{totalCalls} calls</span>
        </div>
      )}

      {/* Budget Exceeded State Pill */}
      {isBudgetExceeded && (
        <div
          data-testid="budget-exceeded-badge"
          className="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-rose-600 text-white dark:bg-rose-500"
        >
          <AlertOctagon className="w-3 h-3" />
          <span>Budget Exceeded — Paused</span>
        </div>
      )}

      {/* Adjust Cap button if exceeded */}
      {isBudgetExceeded && onNavigateToSettings && (
        <button
          type="button"
          onClick={onNavigateToSettings}
          className="text-[10px] underline font-semibold text-rose-700 dark:text-rose-300 hover:text-rose-900 cursor-pointer"
        >
          Adjust Cap
        </button>
      )}
    </div>
  );
};
