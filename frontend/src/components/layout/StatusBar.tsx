import React from 'react';
import { Cpu, Clock, Coins, Sparkles } from 'lucide-react';
import { TokenUsage } from '../../types/pipeline';
import { formatDuration } from '../../utils/formatters';
import { HotkeyBadge } from '../common/HotkeyBadge';

export interface StatusBarProps {
  model?: string;
  mode?: string;
  elapsedSeconds?: number;
  tokenUsage?: TokenUsage;
  showHotkeys?: boolean;
}

export const StatusBar: React.FC<StatusBarProps> = ({
  model = 'gemini-3.7-flash',
  mode = 'parallel',
  elapsedSeconds = 0,
  tokenUsage = { prompt_tokens: 0, candidates_tokens: 0, thinking_tokens: 0, total_tokens: 0 },
  showHotkeys = true,
}) => {
  return (
    <footer className="bg-white dark:bg-slate-900 border-t border-slate-200 dark:border-slate-800 text-xs text-slate-500 dark:text-slate-400 py-2.5 px-4 transition-colors">
      <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-3">
        {/* Left: Active Engine Telemetry */}
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-1.5" title="Active Model & Ingestion Mode">
            <Cpu className="w-3.5 h-3.5 text-blue-500" />
            <span className="font-medium text-slate-700 dark:text-slate-300">{model}</span>
            <span className="text-slate-400 font-mono">({mode})</span>
          </div>

          <div className="flex items-center gap-1.5" title="Total Tokens Used">
            <Coins className="w-3.5 h-3.5 text-amber-500" />
            <span className="font-mono">{tokenUsage.total_tokens.toLocaleString()} tokens</span>
            {tokenUsage.thinking_tokens > 0 && (
              <span className="text-[10px] text-purple-600 dark:text-purple-400 font-mono flex items-center gap-0.5">
                <Sparkles className="w-2.5 h-2.5" />
                {tokenUsage.thinking_tokens.toLocaleString()} thought
              </span>
            )}
          </div>

          <div className="flex items-center gap-1.5" title="Elapsed Pipeline Time">
            <Clock className="w-3.5 h-3.5 text-slate-400" />
            <span className="font-mono">{formatDuration(elapsedSeconds)}</span>
          </div>
        </div>

        {/* Right: Rapid HITL Hotkey Cheat Sheet */}
        {showHotkeys && (
          <div className="hidden sm:flex items-center gap-3 text-slate-600 dark:text-slate-300">
            <div className="flex items-center gap-1">
              <HotkeyBadge hotkey="Space" />
              <span>Approve</span>
            </div>
            <div className="flex items-center gap-1">
              <HotkeyBadge hotkey="S" />
              <span>Standalone</span>
            </div>
            <div className="flex items-center gap-1">
              <HotkeyBadge hotkey="B" />
              <span>Reverse</span>
            </div>
            <div className="flex items-center gap-1">
              <HotkeyBadge hotkey="X" />
              <span>Reject</span>
            </div>
            <div className="flex items-center gap-1">
              <HotkeyBadge hotkey="M" />
              <span>Candidates</span>
            </div>
          </div>
        )}
      </div>
    </footer>
  );
};
