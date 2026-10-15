import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Sliders, DollarSign, Gauge, Brain, Layers } from 'lucide-react';

export interface SpendCapsSectionProps {
  spendCapUsd: number | null;
  monthlyCapUsd: number | null;
  rpmLimit: number;
  extraEffort: boolean;
  embeddingsEnabled: boolean;
  stageModels: Record<string, string>;
  onChange: (updates: {
    spend_cap_usd?: number | null;
    monthly_cap_usd?: number | null;
    rpm_limit?: number;
    extra_effort?: boolean;
    embeddings_enabled?: boolean;
    stage_models?: Record<string, string>;
  }) => void;
}

const STAGE_KEYS = [
  { key: 'FINGERPRINT', label: 'Card Fingerprinting (Stage 2)', placeholder: 'e.g. gemini-3.8-flash' },
  { key: 'VERIFY', label: 'Set-Level Verification (Stage 3)', placeholder: 'e.g. gemini-3.8-flash or gemini-3.1-pro-preview' },
  { key: 'TRANSCRIBE', label: 'Verbatim Transcription (Stage 5)', placeholder: 'e.g. gemini-3.8-flash' },
  { key: 'STRUCTURE', label: 'Recipe Structuring (Stage 5)', placeholder: 'e.g. gemini-3.8-flash' },
  { key: 'GROUNDING', label: 'Grounding Verification (Stage 5)', placeholder: 'e.g. gemini-3.8-flash' },
  { key: 'SELF_CONSISTENCY', label: 'Self-Consistency (Stage 5)', placeholder: 'e.g. gemini-3.6-flash' },
];

export const SpendCapsSection: React.FC<SpendCapsSectionProps> = ({
  spendCapUsd,
  monthlyCapUsd,
  rpmLimit,
  extraEffort,
  embeddingsEnabled,
  stageModels,
  onChange,
}) => {
  const [showAdvancedOverrides, setShowAdvancedOverrides] = useState<boolean>(false);

  const handleSpendCapChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value.trim();
    if (raw === '') {
      onChange({ spend_cap_usd: null });
    } else {
      const parsed = parseFloat(raw);
      if (!isNaN(parsed) && parsed >= 0) {
        onChange({ spend_cap_usd: parsed });
      }
    }
  };

  const handleMonthlyCapChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.value.trim();
    if (raw === '') {
      onChange({ monthly_cap_usd: null });
    } else {
      const parsed = parseFloat(raw);
      if (!isNaN(parsed) && parsed >= 0) {
        onChange({ monthly_cap_usd: parsed });
      }
    }
  };

  const handleRpmChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const parsed = parseInt(e.target.value, 10);
    if (!isNaN(parsed) && parsed >= 1) {
      onChange({ rpm_limit: parsed });
    }
  };

  const handleStageModelChange = (stage: string, modelName: string) => {
    const updated = { ...stageModels };
    const clean = modelName.trim();
    if (clean) {
      updated[stage] = clean;
    } else {
      delete updated[stage];
    }
    onChange({ stage_models: updated });
  };

  return (
    <div className="space-y-6">
      {/* Spend Limits & Rate Limits */}
      <div>
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-2">
          <DollarSign className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
          Spend Caps & Rate Limits
        </h3>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
          Guards pipeline execution against unexpected Gemini API token expenses.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-3">
          {/* Spend Cap Per Run */}
          <div>
            <label
              htmlFor="spend-cap-run"
              className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1"
            >
              Per-Run Spend Cap (USD)
            </label>
            <div className="relative rounded-md shadow-xs">
              <span className="absolute inset-y-0 left-0 pl-3 flex items-center text-slate-400 text-xs">
                $
              </span>
              <input
                id="spend-cap-run"
                type="number"
                step="0.25"
                min="0"
                placeholder="No limit"
                value={spendCapUsd !== null && spendCapUsd !== undefined ? spendCapUsd : ''}
                onChange={handleSpendCapChange}
                className="block w-full pl-7 pr-3 py-1.5 text-xs rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
              Pauses and requests approval if exceeded.
            </p>
          </div>

          {/* Monthly Cap */}
          <div>
            <label
              htmlFor="monthly-cap"
              className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1"
            >
              Monthly Spend Cap (USD)
            </label>
            <div className="relative rounded-md shadow-xs">
              <span className="absolute inset-y-0 left-0 pl-3 flex items-center text-slate-400 text-xs">
                $
              </span>
              <input
                id="monthly-cap"
                type="number"
                step="1.00"
                min="0"
                placeholder="No limit"
                value={monthlyCapUsd !== null && monthlyCapUsd !== undefined ? monthlyCapUsd : ''}
                onChange={handleMonthlyCapChange}
                className="block w-full pl-7 pr-3 py-1.5 text-xs rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
              Cumulative monthly safety limit.
            </p>
          </div>

          {/* RPM Limit */}
          <div>
            <label
              htmlFor="rpm-limit"
              className="block text-xs font-medium text-slate-700 dark:text-slate-300 mb-1"
            >
              Requests Per Minute (RPM)
            </label>
            <div className="relative rounded-md shadow-xs">
              <input
                id="rpm-limit"
                type="number"
                min="1"
                max="1000"
                value={rpmLimit || 60}
                onChange={handleRpmChange}
                className="block w-full px-3 py-1.5 text-xs rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">
              Free-tier keys require lower RPM (e.g. 15).
            </p>
          </div>
        </div>
      </div>

      {/* Feature Toggles */}
      <div className="space-y-3 pt-4 border-t border-slate-200 dark:border-slate-800">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-200 flex items-center gap-2">
          <Sliders className="w-4 h-4 text-blue-600 dark:text-blue-400" />
          Inference & Analysis Toggles
        </h3>

        {/* Extra Effort */}
        <label className="flex items-start gap-3 p-3 bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-lg cursor-pointer hover:bg-slate-100/70 dark:hover:bg-slate-800/50 transition-colors">
          <input
            type="checkbox"
            checked={extraEffort}
            onChange={(e) => onChange({ extra_effort: e.target.checked })}
            className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
          />
          <div>
            <div className="text-xs font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-1.5">
              <Brain className="w-3.5 h-3.5 text-purple-600 dark:text-purple-400" />
              <span>Extra effort for hard-to-read cards</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
              Enables Gemini multimodal reasoning thinking budget during transcription. Consumes additional output tokens but substantially improves accuracy on cursive and faded script.
            </p>
          </div>
        </label>

        {/* Embeddings */}
        <label className="flex items-start gap-3 p-3 bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-lg cursor-pointer hover:bg-slate-100/70 dark:hover:bg-slate-800/50 transition-colors">
          <input
            type="checkbox"
            checked={embeddingsEnabled}
            onChange={(e) => onChange({ embeddings_enabled: e.target.checked })}
            className="mt-1 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
          />
          <div>
            <div className="text-xs font-semibold text-slate-900 dark:text-slate-100 flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
              <span>Embeddings-based semantic pairing</span>
            </div>
            <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
              Uses embedding vectors (gemini-embedding-2) for screening card pair candidates in large decks. Recommended when digitizing 50+ cards with mixed homogeneous recipes.
            </p>
          </div>
        </label>
      </div>

      {/* Advanced per-stage model overrides (collapsed accordion) */}
      <div className="pt-4 border-t border-slate-200 dark:border-slate-800">
        <button
          type="button"
          onClick={() => setShowAdvancedOverrides((prev) => !prev)}
          className="flex items-center justify-between w-full text-left text-xs font-semibold text-slate-700 dark:text-slate-300 hover:text-slate-900 dark:hover:text-slate-100 cursor-pointer"
          data-testid="toggle-advanced-overrides"
        >
          <span className="flex items-center gap-2">
            <Gauge className="w-4 h-4 text-slate-500" />
            <span>Advanced Per-Stage Model Overrides</span>
            {Object.keys(stageModels || {}).length > 0 && (
              <span className="text-[10px] bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300 px-1.5 py-0.2 rounded-full font-mono">
                {Object.keys(stageModels).length} active
              </span>
            )}
          </span>
          {showAdvancedOverrides ? (
            <ChevronDown className="w-4 h-4 text-slate-400" />
          ) : (
            <ChevronRight className="w-4 h-4 text-slate-400" />
          )}
        </button>

        {showAdvancedOverrides && (
          <div className="mt-3 p-4 bg-slate-50 dark:bg-slate-900/60 border border-slate-200 dark:border-slate-800 rounded-xl space-y-3">
            <p className="text-[11px] text-slate-500 dark:text-slate-400">
              Override default models selected by the quality profile. Leave blank to inherit the active profile&apos;s model.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {STAGE_KEYS.map((stage) => (
                <div key={stage.key}>
                  <label
                    htmlFor={`stage-override-${stage.key}`}
                    className="block text-[11px] font-medium text-slate-700 dark:text-slate-300 mb-1"
                  >
                    {stage.label}
                  </label>
                  <input
                    id={`stage-override-${stage.key}`}
                    type="text"
                    placeholder={stage.placeholder}
                    value={stageModels[stage.key] || ''}
                    onChange={(e) => handleStageModelChange(stage.key, e.target.value)}
                    className="block w-full px-2.5 py-1 text-xs font-mono rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-slate-100 focus:outline-hidden focus:ring-1 focus:ring-blue-500"
                  />
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
