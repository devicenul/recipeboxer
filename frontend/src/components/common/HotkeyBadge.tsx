import React from 'react';

export interface HotkeyBadgeProps {
  hotkey: string;
  className?: string;
}

export const HotkeyBadge: React.FC<HotkeyBadgeProps> = ({ hotkey, className = '' }) => {
  return (
    <kbd
      className={`inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-mono font-semibold text-slate-600 dark:text-slate-300 bg-slate-100 dark:bg-slate-800 border border-slate-300 dark:border-slate-700 rounded shadow-xs select-none ${className}`}
    >
      {hotkey}
    </kbd>
  );
};
