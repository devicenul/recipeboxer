import React from 'react';
import { AlertTriangle, Key, ArrowRight } from 'lucide-react';

export interface OnboardingBannerProps {
  analysisBlockedReason?: string | null;
  onboardingHint?: string | null;
  onNavigateToSettings: () => void;
}

export const OnboardingBanner: React.FC<OnboardingBannerProps> = ({
  analysisBlockedReason,
  onboardingHint,
  onNavigateToSettings,
}) => {
  if (analysisBlockedReason !== 'NO_API_KEY') {
    return null;
  }

  return (
    <div
      data-testid="onboarding-no-api-key-banner"
      className="mb-6 p-4 bg-amber-50 dark:bg-amber-950/50 border-2 border-amber-300 dark:border-amber-700/80 rounded-xl shadow-xs text-amber-900 dark:text-amber-200 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 transition-all"
    >
      <div className="flex items-start gap-3">
        <div className="p-2 bg-amber-100 dark:bg-amber-900/60 rounded-lg text-amber-700 dark:text-amber-300 shrink-0">
          <AlertTriangle className="w-5 h-5" />
        </div>
        <div>
          <h4 className="text-sm font-bold text-amber-900 dark:text-amber-100">
            API Key Required for AI Transcription & Analysis
          </h4>
          <p className="text-xs text-amber-800/90 dark:text-amber-300/90 mt-0.5 leading-relaxed">
            Card scanning, thumbnail extraction, and physical feature screening work completely offline. However, AI multimodal transcription, recipe structuring, and correlation require a Google Gemini API key.
          </p>
          {onboardingHint && (
            <p className="text-[11px] font-mono text-amber-700 dark:text-amber-400 mt-1">
              Hint: {onboardingHint}
            </p>
          )}
        </div>
      </div>

      <button
        type="button"
        onClick={onNavigateToSettings}
        className="shrink-0 flex items-center gap-1.5 px-3.5 py-2 text-xs font-bold text-white bg-amber-700 hover:bg-amber-800 dark:bg-amber-600 dark:hover:bg-amber-500 rounded-lg shadow-xs transition-colors cursor-pointer"
        data-testid="onboarding-settings-link"
      >
        <Key className="w-3.5 h-3.5" />
        <span>Configure API Key</span>
        <ArrowRight className="w-3.5 h-3.5 ml-0.5" />
      </button>
    </div>
  );
};
