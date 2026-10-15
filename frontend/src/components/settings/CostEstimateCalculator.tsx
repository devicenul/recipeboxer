import React, { useState, useEffect, useCallback } from 'react';
import { Calculator, HelpCircle, Loader2 } from 'lucide-react';
import { CostEstimateResponse } from '../../types/pipeline';
import { api } from '../../utils/api';

export interface CostEstimateCalculatorProps {
  currentProfile: string;
  cardCountDefault?: number;
}

export const CostEstimateCalculator: React.FC<CostEstimateCalculatorProps> = ({
  currentProfile,
  cardCountDefault = 10,
}) => {
  const [nCards, setNCards] = useState<number>(cardCountDefault);
  const [profile, setProfile] = useState<string>(currentProfile || 'balanced');
  const [estimate, setEstimate] = useState<CostEstimateResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (currentProfile) {
      setProfile(currentProfile);
    }
  }, [currentProfile]);

  const fetchEstimate = useCallback(async (cards: number, prof: string) => {
    if (cards < 1) return;
    setIsLoading(true);
    setError(null);
    try {
      const resp = await api.getCostEstimate(cards, prof);
      setEstimate(resp);
    } catch (err: any) {
      setError(err?.message || 'Failed to calculate cost estimate');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchEstimate(nCards, profile);
  }, [nCards, profile, fetchEstimate]);

  const formatCostValue = (val: number | null | undefined): string => {
    if (val === null || val === undefined) {
      return 'unknown - model not priced';
    }
    return `$${val.toFixed(4)}`;
  };

  const handleCardCountChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const parsed = parseInt(e.target.value, 10);
    if (!isNaN(parsed) && parsed >= 1) {
      setNCards(parsed);
    } else if (e.target.value === '') {
      setNCards(1);
    }
  };

  const QUICK_CARD_PRESETS = [5, 10, 25, 50, 100];

  return (
    <div className="space-y-4 p-4 bg-slate-50 dark:bg-slate-900/40 border border-slate-200 dark:border-slate-800 rounded-xl">
      <div>
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-2">
          <Calculator className="w-4 h-4 text-blue-600 dark:text-blue-400" />
          Pre-Run Pipeline Cost Estimator
        </h3>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
          Simulate expected Gemini API costs across vision fingerprinting, correlation, and transcription.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        {/* Card Count Input */}
        <div>
          <label htmlFor="card-count-input" className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
            Number of Cards
          </label>
          <div className="flex items-center gap-2">
            <input
              id="card-count-input"
              type="number"
              min="1"
              max="5000"
              value={nCards}
              onChange={handleCardCountChange}
              className="w-24 px-3 py-1 text-xs font-mono rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-blue-500"
              data-testid="estimate-card-count-input"
            />
            <div className="flex items-center gap-1">
              {QUICK_CARD_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  onClick={() => setNCards(preset)}
                  className={`px-2 py-0.5 text-[11px] rounded font-mono transition-colors cursor-pointer ${
                    nCards === preset
                      ? 'bg-blue-600 text-white dark:bg-blue-500'
                      : 'bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-300 border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-700'
                  }`}
                >
                  {preset}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Profile Selector */}
        <div>
          <label htmlFor="estimate-profile-select" className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
            Profile
          </label>
          <select
            id="estimate-profile-select"
            value={profile}
            onChange={(e) => setProfile(e.target.value)}
            className="px-3 py-1 text-xs rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-blue-500 cursor-pointer"
            data-testid="estimate-profile-select"
          >
            <option value="economy">Economy</option>
            <option value="balanced">Balanced</option>
            <option value="best">Best</option>
          </select>
        </div>
      </div>

      {/* Estimate Cards */}
      {isLoading ? (
        <div className="flex items-center gap-2 py-6 text-xs text-slate-500 dark:text-slate-400">
          <Loader2 className="w-4 h-4 animate-spin text-blue-500" />
          <span>Calculating cost model...</span>
        </div>
      ) : error ? (
        <div className="p-3 bg-rose-50 text-rose-800 dark:bg-rose-950/40 dark:text-rose-300 border border-rose-200 dark:border-rose-800 rounded-lg text-xs">
          {error}
        </div>
      ) : estimate ? (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2">
          {/* Low Bound */}
          <div className="p-3 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg">
            <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400">Low Estimate</span>
            <div
              data-testid="estimate-low"
              className="text-base font-bold text-slate-900 dark:text-slate-100 mt-0.5"
            >
              {formatCostValue(estimate.low)}
            </div>
            <span className="text-[10px] text-slate-400">Minimal retries, clean text</span>
          </div>

          {/* Expected Mean */}
          <div className="p-3 bg-blue-50/70 dark:bg-blue-950/40 border border-blue-200 dark:border-blue-900/60 rounded-lg">
            <span className="text-[11px] font-semibold text-blue-800 dark:text-blue-300">Expected (Mean)</span>
            <div
              data-testid="estimate-expected"
              className="text-base font-bold text-blue-900 dark:text-blue-100 mt-0.5"
            >
              {formatCostValue(estimate.expected)}
            </div>
            <span className="text-[10px] text-blue-600 dark:text-blue-400">Standard complexity</span>
          </div>

          {/* High Bound */}
          <div className="p-3 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-lg">
            <span className="text-[11px] font-medium text-slate-500 dark:text-slate-400">High Estimate</span>
            <div
              data-testid="estimate-high"
              className="text-base font-bold text-slate-900 dark:text-slate-100 mt-0.5"
            >
              {formatCostValue(estimate.high)}
            </div>
            <span className="text-[10px] text-slate-400">Hard handwriting, repair passes</span>
          </div>
        </div>
      ) : null}

      <div className="flex items-start gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
        <HelpCircle className="w-3.5 h-3.5 shrink-0 mt-0.5 text-slate-400" />
        <span>
          Estimates are based on live token pricing from <code>pricing.toml</code>. If an unpriced model is utilized in the profile, the estimate explicitly surfaces &quot;unknown - model not priced&quot; rather than incorrectly assuming $0.00.
        </span>
      </div>
    </div>
  );
};
