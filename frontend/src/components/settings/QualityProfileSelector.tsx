import React from 'react';
import { ShieldCheck, Zap, Sparkles, AlertTriangle } from 'lucide-react';

export type QualityProfile = 'economy' | 'balanced' | 'best';

export interface QualityProfileSelectorProps {
  value: QualityProfile | string;
  onChange: (profile: QualityProfile) => void;
  hasApiKey?: boolean;
}

interface ProfileOption {
  id: QualityProfile;
  label: string;
  tagline: string;
  description: string;
  specs: string[];
  icon: React.ReactNode;
  requiresPaidKey?: boolean;
}

const PROFILES: ProfileOption[] = [
  {
    id: 'economy',
    label: 'Economy',
    tagline: 'Lowest cost & fastest',
    description:
      'Fast, cost-conscious processing suited for clear, high-contrast printed recipe cards.',
    specs: [
      'Gemini Flash-Lite for card fingerprinting & grouping',
      'Gemini Flash for transcription & structuring',
      'Lower image resolution (1280px long edge)',
      'Single-pass validation without self-consistency',
    ],
    icon: <Zap className="w-5 h-5 text-emerald-500" />,
  },
  {
    id: 'balanced',
    label: 'Balanced',
    tagline: 'Recommended default',
    description:
      'Optimal balance of transcription accuracy, semantic pairing quality, and affordable API cost.',
    specs: [
      'Gemini Flash with adaptive thinking budget for hard handwriting',
      'Standard image resolution (1600px long edge)',
      'Selective verification passes for uncertain cards',
      'Works with standard or free-tier keys (within quota)',
    ],
    icon: <ShieldCheck className="w-5 h-5 text-blue-500" />,
  },
  {
    id: 'best',
    label: 'Best',
    tagline: 'Maximum accuracy for difficult cards',
    description:
      'Deep multimodal reasoning for faded cursive, margin notes, stains, and complex multi-card recipes.',
    specs: [
      'Gemini Pro models with full thinking effort',
      'High image resolution (2000px long edge)',
      'Dual-model self-consistency & grounding verification',
      'Requires a paid Gemini API key (Pro models unavailable on free tier)',
    ],
    icon: <Sparkles className="w-5 h-5 text-purple-500" />,
    requiresPaidKey: true,
  },
];

export const QualityProfileSelector: React.FC<QualityProfileSelectorProps> = ({
  value,
  onChange,
  hasApiKey = false,
}) => {
  const normalizedValue = (value || 'balanced').toLowerCase();

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-semibold text-slate-800 dark:text-slate-200">
          Quality & Cost Profile
        </label>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
          Choose the speed, accuracy, and model tier trade-off for card digitization.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4" role="radiogroup" aria-label="Quality Profile Selection">
        {PROFILES.map((p) => {
          const isSelected = normalizedValue === p.id;
          return (
            <div
              key={p.id}
              role="radio"
              aria-checked={isSelected}
              tabIndex={0}
              onClick={() => onChange(p.id)}
              onKeyDown={(e) => {
                if (e.key === ' ' || e.key === 'Enter') {
                  e.preventDefault();
                  onChange(p.id);
                }
              }}
              data-testid={`profile-card-${p.id}`}
              className={`relative flex flex-col justify-between p-4 rounded-xl border-2 cursor-pointer transition-all ${
                isSelected
                  ? 'border-blue-600 bg-blue-50/50 dark:bg-blue-950/30 dark:border-blue-400 shadow-sm'
                  : 'border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 hover:border-slate-300 dark:hover:border-slate-700'
              }`}
            >
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    {p.icon}
                    <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">{p.label}</h3>
                  </div>
                  {isSelected && (
                    <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full bg-blue-600 text-white dark:bg-blue-500">
                      Active
                    </span>
                  )}
                </div>

                <p className="text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
                  {p.tagline}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400 mb-3 leading-relaxed">
                  {p.description}
                </p>

                <ul className="text-[11px] text-slate-600 dark:text-slate-400 space-y-1 mb-3">
                  {p.specs.map((spec, idx) => (
                    <li key={idx} className="flex items-start gap-1.5">
                      <span className="text-blue-500 font-bold">•</span>
                      <span>{spec}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {p.requiresPaidKey && (
                <div className="pt-2 border-t border-slate-200 dark:border-slate-800 text-[11px] text-amber-700 dark:text-amber-400 flex items-start gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  <span>Paid key required — Pro models are not available on Google&apos;s free tier.</span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {normalizedValue === 'best' && (
        <div
          data-testid="best-profile-warning"
          className="p-3 bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800/60 rounded-lg text-xs text-amber-800 dark:text-amber-300 flex items-start gap-2.5"
        >
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400" />
          <div>
            <span className="font-semibold">Best Quality Profile Notice:</span> The Best profile orchestrates Gemini Pro models for transcription, verification, and grounding. Pro models are unavailable on Google&apos;s free-tier API quotas. Ensure you have configured a paid Google Cloud Gemini API key with billing enabled.
          </div>
        </div>
      )}
    </div>
  );
};
