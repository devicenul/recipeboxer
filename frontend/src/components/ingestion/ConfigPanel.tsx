import React from 'react';
import { Settings2, Cpu, Zap, BrainCircuit, Tag } from 'lucide-react';
import { Slider } from '../common/Slider';
import { IngestionMode, PipelineStartRequest } from '../../types/pipeline';
import { TagManager } from '../recipe/TagManager';

export interface ConfigPanelProps {
  config: PipelineStartRequest;
  onChange: (newConfig: PipelineStartRequest) => void;
  disabled?: boolean;
}

export const ConfigPanel: React.FC<ConfigPanelProps> = ({
  config,
  onChange,
  disabled = false,
}) => {
  const isSlowBatch = config.mode === IngestionMode.SLOW_BATCH || config.mode === 'slow-batch';

  const update = (partial: Partial<PipelineStartRequest>) => {
    onChange({ ...config, ...partial });
  };

  return (
    <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-xs flex flex-col gap-5">
      <div className="flex items-center gap-2 pb-3 border-b border-slate-200 dark:border-slate-800 text-slate-900 dark:text-slate-100 font-semibold text-sm">
        <Settings2 className="w-4 h-4 text-blue-500" />
        <span>Pipeline Engine Configuration</span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        {/* Model Selection */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-slate-700 dark:text-slate-300 flex items-center gap-1.5">
            <Cpu className="w-3.5 h-3.5 text-blue-500" />
            Gemini Multimodal Model
          </label>
          <select
            value={config.model || 'gemini-3.7-flash'}
            disabled={disabled}
            onChange={(e) => update({ model: e.target.value })}
            className="w-full px-3 py-2 text-sm bg-slate-50 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded-lg text-slate-900 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          >
            <option value="gemini-3.7-flash">gemini-3.7-flash (High Throughput, Recommended)</option>
            <option value="gemini-3.7-pro">gemini-3.7-pro (Maximum Reasoning Accuracy)</option>
          </select>
        </div>

        {/* Ingestion Mode */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-slate-700 dark:text-slate-300 flex items-center gap-1.5">
            <Zap className="w-3.5 h-3.5 text-amber-500" />
            Concurrency Execution Mode
          </label>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={disabled}
              onClick={() => update({ mode: IngestionMode.PARALLEL })}
              className={`px-3 py-2 text-xs font-medium rounded-lg border transition-all cursor-pointer ${
                !isSlowBatch
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 font-semibold'
                  : 'border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800'
              }`}
            >
              Parallel Semaphore
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => update({ mode: IngestionMode.SLOW_BATCH })}
              className={`px-3 py-2 text-xs font-medium rounded-lg border transition-all cursor-pointer ${
                isSlowBatch
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 font-semibold'
                  : 'border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800'
              }`}
            >
              Slow-Batch Rate Control
            </button>
          </div>
        </div>
      </div>

      {/* Sliders: Concurrency / Slow Batch Parameters */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5 pt-2">
        <Slider
          label="Max Concurrent Workers (max_concurrency)"
          min={1}
          max={50}
          step={1}
          value={config.max_concurrency ?? 5}
          disabled={disabled}
          onChange={(val) => update({ max_concurrency: val })}
        />

        {isSlowBatch && (
          <>
            <Slider
              label="Slow-Batch Size (slow_batch_size)"
              min={1}
              max={20}
              step={1}
              value={config.slow_batch_size ?? 4}
              disabled={disabled}
              onChange={(val) => update({ slow_batch_size: val })}
            />
            <Slider
              label="Slow-Batch Interval (slow_batch_interval_seconds)"
              min={0.0}
              max={30.0}
              step={0.5}
              unit="s"
              value={config.slow_batch_interval_seconds ?? 2.0}
              disabled={disabled}
              onChange={(val) => update({ slow_batch_interval_seconds: val })}
            />
          </>
        )}
      </div>

      {/* Thinking Mode Controls */}
      <div className="pt-3 border-t border-slate-200 dark:border-slate-800 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <label
            htmlFor="thinking_mode_toggle"
            className="text-xs font-medium text-slate-700 dark:text-slate-300 flex items-center gap-1.5 cursor-pointer"
          >
            <BrainCircuit className="w-3.5 h-3.5 text-purple-500" />
            Enable Gemini Thinking Mode
          </label>
          <input
            id="thinking_mode_toggle"
            type="checkbox"
            checked={!!config.thinking_mode}
            disabled={disabled}
            onChange={(e) => update({ thinking_mode: e.target.checked })}
            className="w-4 h-4 text-blue-600 rounded cursor-pointer accent-purple-600"
          />
        </div>

        {config.thinking_mode && (
          <Slider
            label="Thinking Token Budget (thinking_budget_tokens)"
            min={0}
            max={8192}
            step={256}
            unit=" tokens"
            value={config.thinking_budget_tokens ?? 1024}
            disabled={disabled}
            onChange={(val) => update({ thinking_budget_tokens: val })}
          />
        )}
      </div>

      {/* Batch Default Tags */}
      <div className="pt-3 border-t border-slate-200 dark:border-slate-800 flex flex-col gap-2">
        <TagManager
          tags={config.default_tags || []}
          onAddTag={(tag) => {
            const current = config.default_tags || [];
            if (!current.includes(tag)) {
              update({ default_tags: [...current, tag] });
            }
          }}
          onRemoveTag={(tag) => {
            const current = config.default_tags || [];
            update({ default_tags: current.filter((t) => t !== tag) });
          }}
          disabled={disabled}
        />
        <p className="text-xs text-slate-500 dark:text-slate-400">
          Initial batch default tags automatically applied to all recipe cards scanned and extracted in this session.
        </p>
      </div>
    </div>
  );
};
