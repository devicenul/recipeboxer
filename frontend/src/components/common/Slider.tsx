import React from 'react';

export interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange: (val: number) => void;
  disabled?: boolean;
  className?: string;
  formatValue?: (val: number) => string;
  ariaLabel?: string;
}

export const Slider: React.FC<SliderProps> = ({
  label,
  value,
  min,
  max,
  step = 1,
  unit = '',
  onChange,
  disabled = false,
  className = '',
  formatValue,
  ariaLabel,
}) => {
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <div className="flex justify-between items-center text-xs font-medium text-slate-700 dark:text-slate-300">
        <span>{label}</span>
        <span className="font-mono text-slate-900 dark:text-slate-100 font-semibold">
          {formatValue ? formatValue(value) : `${value}${unit}`}
        </span>
      </div>
      <input
        type="range"
        aria-label={ariaLabel || label}
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-600 dark:accent-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
      />
    </div>
  );
};
