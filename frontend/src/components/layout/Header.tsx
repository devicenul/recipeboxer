import React from 'react';
import { ChefHat, Radio, Wifi, WifiOff, HelpCircle } from 'lucide-react';
import { ThemeToggle } from '../common/ThemeToggle';
import { Navigation, NavTab } from './Navigation';
import { Badge } from '../common/Badge';
import { PipelineState } from '../../types/pipeline';

export interface HeaderProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  sessionId?: string | null;
  pipelineState?: PipelineState | string;
  isConnected?: boolean;
  pendingHitlCount?: number;
  extractedRecipeCount?: number;
  onOpenTutorial?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  onTabChange,
  sessionId,
  pipelineState = PipelineState.IDLE,
  isConnected = false,
  pendingHitlCount = 0,
  extractedRecipeCount = 0,
  onOpenTutorial,
}) => {
  const getStatusBadge = () => {
    switch (pipelineState) {
      case PipelineState.COMPLETED:
        return <Badge variant="success">Completed</Badge>;
      case PipelineState.HITL_PENDING:
        return <Badge variant="warning">HITL Review Ready</Badge>;
      case PipelineState.SCANNING:
      case PipelineState.FINGERPRINTING:
      case PipelineState.CORRELATING:
      case PipelineState.EXTRACTING:
      case PipelineState.EXPORTING:
        return (
          <Badge variant="primary" className="animate-pulse">
            <Radio className="w-3 h-3 animate-spin" />
            {pipelineState}
          </Badge>
        );
      case PipelineState.FAILED:
        return <Badge variant="error">Failed</Badge>;
      default:
        return <Badge variant="neutral">Idle</Badge>;
    }
  };

  return (
    <header className="sticky top-0 z-40 bg-white/80 dark:bg-slate-900/80 backdrop-blur-md border-b border-slate-200 dark:border-slate-800 transition-colors">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16 gap-4">
          {/* Logo & Brand */}
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center text-white shadow-md">
              <ChefHat className="w-6 h-6" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base sm:text-lg font-bold tracking-tight text-slate-900 dark:text-slate-100">
                  RecipeBoxer
                </h1>
                <span className="text-[10px] uppercase font-bold tracking-wider px-1.5 py-0.5 rounded bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300">
                  AI OCR
                </span>
              </div>
              {sessionId ? (
                <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                  <span className="font-mono truncate max-w-[120px] sm:max-w-[180px]">
                    {sessionId}
                  </span>
                  <span className="inline-block w-1 h-1 rounded-full bg-slate-400"></span>
                  {getStatusBadge()}
                </div>
              ) : (
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Digitization & HITL Correlation
                </p>
              )}
            </div>
          </div>

          {/* Navigation Bar */}
          <div className="hidden md:flex items-center">
            <Navigation
              activeTab={activeTab}
              onTabChange={onTabChange}
              pendingHitlCount={pendingHitlCount}
              extractedRecipeCount={extractedRecipeCount}
            />
          </div>

          {/* Right Controls: SSE Connection + Tour Help + Theme Toggle */}
          <div className="flex items-center gap-2 sm:gap-3">
            <button
              type="button"
              onClick={onOpenTutorial}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-200 bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 dark:hover:bg-slate-700 rounded-lg transition-colors cursor-pointer"
              title="Interactive UI Walkthrough & Tutorial"
              aria-label="Interactive UI Walkthrough & Tutorial"
              data-testid="tour-restart-button"
            >
              <HelpCircle className="w-4 h-4 text-blue-600 dark:text-blue-400" />
              <span className="hidden sm:inline">Tour</span>
            </button>

            <div
              className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400"
              title={isConnected ? 'Connected to live events stream' : 'Disconnected from event stream'}
            >
              {isConnected ? (
                <Wifi className="w-4 h-4 text-emerald-500" />
              ) : (
                <WifiOff className="w-4 h-4 text-slate-400" />
              )}
              <span className="hidden lg:inline">{isConnected ? 'Live' : 'Offline'}</span>
            </div>
            <ThemeToggle />
          </div>
        </div>

        {/* Mobile Navigation */}
        <div className="md:hidden pb-3 pt-1">
          <Navigation
            activeTab={activeTab}
            onTabChange={onTabChange}
            pendingHitlCount={pendingHitlCount}
            extractedRecipeCount={extractedRecipeCount}
          />
        </div>
      </div>
    </header>
  );
};
