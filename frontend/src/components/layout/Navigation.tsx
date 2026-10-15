import React from 'react';
import { Layers, GitMerge, BookOpen, Download, Settings } from 'lucide-react';

export type NavTab = 'ingest' | 'hitl' | 'recipes' | 'export' | 'settings';

export interface NavigationProps {
  activeTab: NavTab;
  onTabChange: (tab: NavTab) => void;
  pendingHitlCount?: number;
  extractedRecipeCount?: number;
}

export const Navigation: React.FC<NavigationProps> = ({
  activeTab,
  onTabChange,
  pendingHitlCount = 0,
  extractedRecipeCount = 0,
}) => {
  const tabs: { id: NavTab; label: string; icon: React.ReactNode; badge?: number }[] = [
    { id: 'ingest', label: '1. Ingestion', icon: <Layers className="w-4 h-4" /> },
    {
      id: 'hitl',
      label: '2. HITL Queue',
      icon: <GitMerge className="w-4 h-4" />,
      badge: pendingHitlCount > 0 ? pendingHitlCount : undefined,
    },
    {
      id: 'recipes',
      label: '3. Recipe Editor',
      icon: <BookOpen className="w-4 h-4" />,
      badge: extractedRecipeCount > 0 ? extractedRecipeCount : undefined,
    },
    { id: 'export', label: '4. Export', icon: <Download className="w-4 h-4" /> },
    { id: 'settings', label: '5. Settings', icon: <Settings className="w-4 h-4" /> },
  ];

  return (
    <nav className="flex items-center gap-1 p-1 bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl">
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id;
        return (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`flex items-center gap-2 px-3.5 py-1.5 text-xs sm:text-sm font-medium rounded-lg transition-all cursor-pointer select-none ${
              isActive
                ? 'bg-white dark:bg-slate-800 text-blue-600 dark:text-blue-400 shadow-xs'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 hover:bg-slate-200/50 dark:hover:bg-slate-800/50'
            }`}
          >
            {tab.icon}
            <span>{tab.label}</span>
            {tab.badge !== undefined && (
              <span
                className={`px-1.5 py-0.2 text-[10px] font-bold rounded-full ${
                  isActive
                    ? 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300'
                    : 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300'
                }`}
              >
                {tab.badge}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
};
