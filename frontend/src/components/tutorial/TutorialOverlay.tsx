import React, { useEffect, useRef, useCallback } from 'react';
import {
  Layers,
  GitMerge,
  LayoutGrid,
  SlidersHorizontal,
  BookOpen,
  Download,
  X,
  ChevronRight,
  ChevronLeft,
  CheckCircle2,
  Sparkles,
  HelpCircle,
} from 'lucide-react';
import { TutorialStep, TUTORIAL_STEPS, HotkeyHint } from './tutorialSteps';
import { HotkeyBadge } from '../common/HotkeyBadge';
import { Button } from '../common/Button';

export interface TutorialOverlayProps {
  isOpen: boolean;
  currentStepIndex: number;
  onStepChange: (index: number) => void;
  onClose: () => void;
}

const STEP_ICONS = {
  Layers: Layers,
  GitMerge: GitMerge,
  LayoutGrid: LayoutGrid,
  SlidersHorizontal: SlidersHorizontal,
  BookOpen: BookOpen,
  Download: Download,
};

export const TutorialOverlay: React.FC<TutorialOverlayProps> = ({
  isOpen,
  currentStepIndex,
  onStepChange,
  onClose,
}) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const totalSteps = TUTORIAL_STEPS.length;
  const currentStep: TutorialStep = TUTORIAL_STEPS[currentStepIndex] || TUTORIAL_STEPS[0];
  const StepIcon = STEP_ICONS[currentStep.iconName] || Sparkles;

  const handleNext = useCallback(() => {
    if (currentStepIndex < totalSteps - 1) {
      onStepChange(currentStepIndex + 1);
    } else {
      onClose();
    }
  }, [currentStepIndex, totalSteps, onStepChange, onClose]);

  const handlePrev = useCallback(() => {
    if (currentStepIndex > 0) {
      onStepChange(currentStepIndex - 1);
    }
  }, [currentStepIndex, onStepChange]);

  // Keyboard navigation & accessibility
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't capture when typing in text inputs or textareas
      const target = e.target as HTMLElement;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
        return;
      }

      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        handleNext();
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        handlePrev();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, handleNext, handlePrev, onClose]);

  // Target element highlight & scrolling effect
  useEffect(() => {
    if (!isOpen || !currentStep.targetSelector) return;

    let targetElement: Element | null = null;
    try {
      targetElement = document.querySelector(currentStep.targetSelector);
      if (targetElement) {
        targetElement.classList.add('tour-target-highlight');
        targetElement.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    } catch {
      // Ignored if invalid selector or DOM unavailable
    }

    return () => {
      if (targetElement) {
        targetElement.classList.remove('tour-target-highlight');
      }
    };
  }, [isOpen, currentStepIndex, currentStep.targetSelector]);

  // Focus management: focus dialog on open
  useEffect(() => {
    if (isOpen && dialogRef.current) {
      dialogRef.current.focus();
    }
  }, [isOpen, currentStepIndex]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 bg-slate-950/65 backdrop-blur-xs transition-opacity duration-200"
      data-testid="tutorial-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          onClose();
        }
      }}
    >
      {/* Dialog Window */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tutorial-step-title"
        aria-describedby="tutorial-step-description"
        tabIndex={-1}
        data-testid="tutorial-overlay"
        className="relative w-full max-w-xl mx-2 sm:mx-auto bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-2xl overflow-hidden focus:outline-none animate-in fade-in zoom-in-95 duration-150"
      >
        {/* Top Progress Bar */}
        <div className="w-full bg-slate-100 dark:bg-slate-800 h-1.5 overflow-hidden">
          <div
            className="bg-gradient-to-r from-blue-600 to-indigo-500 h-full transition-all duration-300 ease-out"
            style={{ width: `${((currentStepIndex + 1) / totalSteps) * 100}%` }}
            data-testid="tutorial-progress-bar"
          />
        </div>

        {/* Header Bar */}
        <div className="flex items-center justify-between px-4 sm:px-6 pt-4 sm:pt-5 pb-3 border-b border-slate-100 dark:border-slate-800/80">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-blue-50 dark:bg-blue-950/70 border border-blue-200 dark:border-blue-800 flex items-center justify-center text-blue-600 dark:text-blue-400 shadow-xs shrink-0">
              <StepIcon className="w-4 h-4" />
            </div>
            <div>
              <span className="text-[11px] font-bold uppercase tracking-wider text-blue-600 dark:text-blue-400 font-mono">
                {currentStep.badge}
              </span>
              <h2
                id="tutorial-step-title"
                className="text-sm sm:text-base font-bold text-slate-900 dark:text-slate-100"
              >
                {currentStep.title}
              </h2>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            aria-label="Close tutorial"
            data-testid="tutorial-close-btn"
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="px-4 sm:px-6 py-4 sm:py-5 flex flex-col gap-4 text-sm max-h-[65vh] overflow-y-auto">
          {/* Main Description */}
          <p
            id="tutorial-step-description"
            className="text-slate-600 dark:text-slate-300 leading-relaxed text-xs sm:text-sm"
          >
            {currentStep.description}
          </p>

          {/* Feature Highlights */}
          {currentStep.highlights && currentStep.highlights.length > 0 && (
            <div className="flex flex-col gap-2 p-3 sm:p-3.5 bg-slate-50 dark:bg-slate-850 rounded-xl border border-slate-200/80 dark:border-slate-800">
              <span className="text-[10px] sm:text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                Key Features & Capabilities
              </span>
              <ul className="flex flex-col gap-1.5 sm:gap-2 text-xs text-slate-700 dark:text-slate-300">
                {currentStep.highlights.map((item, idx) => (
                  <li key={idx} className="flex items-start gap-2">
                    <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Hotkey Shortcuts if applicable */}
          {currentStep.hotkeys && currentStep.hotkeys.length > 0 && (
            <div className="flex flex-col gap-2 p-3 sm:p-3.5 bg-blue-50/50 dark:bg-blue-950/30 rounded-xl border border-blue-200/60 dark:border-blue-800/60">
              <span className="text-[10px] sm:text-[11px] font-bold uppercase tracking-wider text-blue-700 dark:text-blue-300">
                Rapid Review Keyboard Shortcuts
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                {currentStep.hotkeys.map((hk: HotkeyHint, idx: number) => (
                  <div key={idx} className="flex items-center gap-2">
                    <HotkeyBadge hotkey={hk.key} className="min-w-[42px] text-center" />
                    <span className="text-slate-700 dark:text-slate-300 font-medium">
                      {hk.label}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Step Indicator Dots */}
          <div className="flex items-center justify-center gap-1.5 pt-2">
            {TUTORIAL_STEPS.map((step, idx) => (
              <button
                key={step.id}
                type="button"
                onClick={() => onStepChange(idx)}
                aria-label={`Jump to Step ${idx + 1}: ${step.title}`}
                data-testid={`tutorial-step-dot-${idx}`}
                className={`h-2 rounded-full transition-all cursor-pointer ${
                  idx === currentStepIndex
                    ? 'w-6 bg-blue-600 dark:bg-blue-500'
                    : 'w-2 bg-slate-300 dark:bg-slate-700 hover:bg-slate-400 dark:hover:bg-slate-600'
                }`}
              />
            ))}
          </div>
        </div>

        {/* Footer Navigation Controls */}
        <div className="flex items-center justify-between px-4 sm:px-6 py-3 sm:py-4 bg-slate-50 dark:bg-slate-900 border-t border-slate-100 dark:border-slate-800">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            data-testid="tutorial-skip-btn"
          >
            Skip Tour
          </Button>

          <div className="flex items-center gap-2">
            {currentStepIndex > 0 && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={handlePrev}
                leftIcon={<ChevronLeft className="w-4 h-4" />}
                data-testid="tutorial-back-btn"
              >
                Back
              </Button>
            )}

            <Button
              type="button"
              variant="primary"
              size="sm"
              onClick={handleNext}
              rightIcon={
                currentStepIndex === totalSteps - 1 ? undefined : (
                  <ChevronRight className="w-4 h-4" />
                )
              }
              data-testid="tutorial-next-btn"
            >
              {currentStepIndex === totalSteps - 1 ? 'Finish Tour' : 'Next'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};
