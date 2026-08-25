import { useEffect } from 'react';

export interface KeyboardShortcutHandlers {
  onApprove?: () => void;
  onStandalone?: () => void;
  onReverse?: () => void;
  onReject?: () => void;
  onToggleCandidates?: () => void;
  onEscape?: () => void;
  enabled?: boolean;
}

/**
 * Custom hook to register global keyboard shortcuts with input focus trapping.
 */
export function useKeyboard({
  onApprove,
  onStandalone,
  onReverse,
  onReject,
  onToggleCandidates,
  onEscape,
  enabled = true,
}: KeyboardShortcutHandlers) {
  useEffect(() => {
    if (!enabled || typeof window === 'undefined') return;

    const handleKeyDown = (e: KeyboardEvent) => {
      // 1. Input trapping: Check if active element is an input, textarea, select, or editable
      const target = e.target as HTMLElement | null;
      if (target) {
        const tagName = target.tagName?.toUpperCase();
        const isEditable =
          tagName === 'INPUT' ||
          tagName === 'TEXTAREA' ||
          tagName === 'SELECT' ||
          target.isContentEditable;

        if (isEditable) {
          // Allow Escape to blur the input
          if (e.key === 'Escape') {
            target.blur();
            onEscape?.();
          }
          return; // Suppress all queue hotkeys while typing
        }
      }

      // 2. Global Hotkey Routing
      switch (e.key) {
        case ' ':
        case 'Enter':
          e.preventDefault();
          onApprove?.();
          break;

        case 's':
        case 'S':
          e.preventDefault();
          onStandalone?.();
          break;

        case 'b':
        case 'B':
          e.preventDefault();
          onReverse?.();
          break;

        case 'x':
        case 'X':
          e.preventDefault();
          onReject?.();
          break;

        case 'm':
        case 'M':
          e.preventDefault();
          onToggleCandidates?.();
          break;

        case 'Tab':
          // Optional candidate drawer toggle on Tab if focused in queue
          e.preventDefault();
          onToggleCandidates?.();
          break;

        case 'Escape':
          e.preventDefault();
          onEscape?.();
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [enabled, onApprove, onStandalone, onReverse, onReject, onToggleCandidates, onEscape]);
}
