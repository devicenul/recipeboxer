import React, { useState, useRef, useEffect, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { Sparkles, ImageOff } from 'lucide-react';

export interface CardHoverPreviewProps {
  cardId?: string;
  imageUrl?: string | null;
  file?: File | null;
  title?: string;
  subtitle?: string;
  delayMs?: number; // default 2000ms (2.0s)
  children: React.ReactNode;
  className?: string;
  enabled?: boolean;
}

export const CardHoverPreview: React.FC<CardHoverPreviewProps> = ({
  cardId,
  imageUrl,
  file,
  title,
  subtitle,
  delayMs = 2000,
  children,
  className = '',
  enabled = true,
}) => {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [coords, setCoords] = useState<{ top: number; left: number }>({ top: 0, left: 0 });
  const [resolvedUrl, setResolvedUrl] = useState<string | null>(imageUrl || null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const triggerRef = useRef<HTMLDivElement | null>(null);

  // Manage File object URL creation & revocation
  useEffect(() => {
    if (file) {
      try {
        const objectUrl = URL.createObjectURL(file);
        setResolvedUrl(objectUrl);
        return () => {
          URL.revokeObjectURL(objectUrl);
        };
      } catch {
        setResolvedUrl(null);
      }
    } else {
      setResolvedUrl(imageUrl || null);
    }
  }, [file, imageUrl]);

  // Clean up timer on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, []);

  // React to enabled state changes
  useEffect(() => {
    if (!enabled) {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      setIsOpen(false);
    }
  }, [enabled]);

  const computeClampedPosition = useCallback(() => {
    if (!triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const viewportWidth = typeof window !== 'undefined' ? window.innerWidth : 1024;
    const viewportHeight = typeof window !== 'undefined' ? window.innerHeight : 768;

    const popoverWidth = 320;
    const popoverHeight = 380;
    const margin = 16;

    // Prefer placing to the left of the trigger (great for right-side candidate drawers)
    let left = rect.left - popoverWidth - 12;
    let top = rect.top + rect.height / 2 - popoverHeight / 2;

    // If overflowing left edge, try placing to the right
    if (left < margin) {
      if (rect.right + popoverWidth + 12 <= viewportWidth - margin) {
        left = rect.right + 12;
      } else {
        // Center horizontally above or below
        left = rect.left + rect.width / 2 - popoverWidth / 2;
        top = rect.top - popoverHeight - 12;
        if (top < margin) {
          top = rect.bottom + 12;
        }
      }
    }

    // Clamp horizontal position strictly within viewport
    if (left < margin) {
      left = margin;
    } else if (left + popoverWidth > viewportWidth - margin) {
      left = Math.max(margin, viewportWidth - popoverWidth - margin);
    }

    // Clamp vertical position strictly within viewport
    if (top < margin) {
      top = margin;
    } else if (top + popoverHeight > viewportHeight - margin) {
      top = Math.max(margin, viewportHeight - popoverHeight - margin);
    }

    setCoords({ top, left });
  }, []);

  const handleMouseEnter = () => {
    if (!enabled) return;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
    }
    timerRef.current = setTimeout(() => {
      computeClampedPosition();
      setIsOpen(true);
    }, delayMs);
  };

  const handleMouseLeave = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setIsOpen(false);
  };

  const displayTitle = title || cardId || 'Card Scan Preview';

  return (
    <div
      ref={triggerRef}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      className={`relative inline-block ${className}`}
    >
      {children}

      {isOpen && typeof document !== 'undefined' &&
        createPortal(
          <div
            data-testid="card-hover-preview-popover"
            data-card-id={cardId || ''}
            className="fixed z-[9999] pointer-events-none w-80 max-w-[calc(100vw-32px)] bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150 flex flex-col"
            style={{
              top: `${coords.top}px`,
              left: `${coords.left}px`,
            }}
          >
            {/* Header */}
            <div className="px-3.5 py-2.5 bg-slate-50 dark:bg-slate-800/80 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between gap-2">
              <div className="flex items-center gap-1.5 min-w-0">
                <Sparkles className="w-3.5 h-3.5 text-blue-500 shrink-0" />
                <span className="text-xs font-bold text-slate-900 dark:text-slate-100 truncate font-mono">
                  {displayTitle}
                </span>
              </div>
              <span className="text-[10px] uppercase font-semibold text-blue-600 dark:text-blue-400 tracking-wider shrink-0 bg-blue-50 dark:bg-blue-950/60 px-1.5 py-0.5 rounded">
                2x Magnified
              </span>
            </div>

            {/* High-Resolution Scan Preview Image */}
            <div className="w-full h-64 bg-slate-950 flex items-center justify-center overflow-hidden p-1 relative">
              {resolvedUrl ? (
                <img
                  src={resolvedUrl}
                  alt={displayTitle}
                  className="w-full h-full object-contain rounded"
                  onError={(e) => {
                    (e.target as HTMLElement).style.display = 'none';
                  }}
                />
              ) : (
                <div className="flex flex-col items-center justify-center gap-1 text-slate-500">
                  <ImageOff className="w-8 h-8 text-slate-600" />
                  <span className="text-xs">Preview unavailable</span>
                </div>
              )}
            </div>

            {/* Footer / Subtitle Details */}
            {subtitle && (
              <div className="px-3.5 py-2 bg-slate-50 dark:bg-slate-800/80 border-t border-slate-200 dark:border-slate-800 text-[11px] text-slate-600 dark:text-slate-400 truncate">
                {subtitle}
              </div>
            )}
          </div>,
          document.body
        )}
    </div>
  );
};
