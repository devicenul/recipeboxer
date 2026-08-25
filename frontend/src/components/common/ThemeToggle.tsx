import React from 'react';
import { Sun, Moon, Laptop } from 'lucide-react';
import { useTheme, Theme } from '../../hooks/useTheme';

export const ThemeToggle: React.FC<{ className?: string }> = ({ className = '' }) => {
  const { theme, setTheme } = useTheme();

  const themes: { key: Theme; label: string; icon: React.ReactNode }[] = [
    { key: 'light', label: 'Light', icon: <Sun className="w-4 h-4" /> },
    { key: 'dark', label: 'Dark', icon: <Moon className="w-4 h-4" /> },
    { key: 'system', label: 'System', icon: <Laptop className="w-4 h-4" /> },
  ];

  return (
    <div
      className={`inline-flex items-center p-1 bg-slate-200 dark:bg-slate-800 rounded-lg border border-slate-300 dark:border-slate-700 ${className}`}
      role="radiogroup"
      aria-label="Theme selector"
    >
      {themes.map(({ key, label, icon }) => {
        const isActive = theme === key;
        return (
          <button
            key={key}
            onClick={() => setTheme(key)}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md transition-all ${
              isActive
                ? 'bg-white dark:bg-slate-900 text-blue-600 dark:text-blue-400 shadow-xs'
                : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100'
            }`}
            role="radio"
            aria-checked={isActive}
            title={`${label} theme`}
          >
            {icon}
            <span className="hidden sm:inline">{label}</span>
          </button>
        );
      })}
    </div>
  );
};
