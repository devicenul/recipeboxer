import React, { useEffect, useRef, useState } from 'react';
import { ImageOff, Loader2 } from 'lucide-react';
import { CanvasTransform, FilterSettings } from '../../types/viewer';

export interface CardViewerCanvasProps {
  imageUrl?: string | null;
  cardId?: string | null;
  label?: string;
  roleBadge?: string;
  transform: CanvasTransform;
  filters: FilterSettings;
  onPan?: (dx: number, dy: number) => void;
  onWheelZoom?: (delta: number) => void;
  isStandalonePlaceholder?: boolean;
}

export const CardViewerCanvas: React.FC<CardViewerCanvasProps> = ({
  imageUrl,
  cardId,
  label = 'Card Scan',
  roleBadge,
  transform,
  filters,
  onPan,
  onWheelZoom,
  isStandalonePlaceholder = false,
}) => {
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    setIsLoading(true);
    setHasError(false);
  }, [imageUrl, cardId]);

  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = (e: React.MouseEvent) => {
    if (isStandalonePlaceholder || !imageUrl) return;
    setIsDragging(true);
    setDragStart({ x: e.clientX, y: e.clientY });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging) return;
    const dx = e.clientX - dragStart.x;
    const dy = e.clientY - dragStart.y;
    setDragStart({ x: e.clientX, y: e.clientY });
    onPan?.(dx, dy);
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const handleWheel = (e: React.WheelEvent) => {
    if (isStandalonePlaceholder || !imageUrl) return;
    e.preventDefault();
    const zoomDelta = e.deltaY < 0 ? 0.15 : -0.15;
    onWheelZoom?.(zoomDelta);
  };

  // Build filter style string
  const filterStyle = `brightness(${filters.brightness}%) contrast(${filters.contrast}%) ${
    filters.invert ? 'invert(100%)' : 'invert(0%)'
  }`;

  // Build transform style string
  const transformStyle = `translate(${transform.translateX}px, ${transform.translateY}px) scale(${transform.scale}) rotate(${transform.rotation}deg)`;

  return (
    <div
      ref={containerRef}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onWheel={handleWheel}
      className={`relative w-full h-full min-h-[380px] bg-slate-100 dark:bg-slate-950 rounded-xl overflow-hidden border border-slate-200 dark:border-slate-800 flex items-center justify-center select-none ${
        isStandalonePlaceholder || !imageUrl ? 'cursor-default' : isDragging ? 'cursor-grabbing' : 'cursor-grab'
      }`}
    >
      {/* Label and Badge Overlay */}
      <div className="absolute top-3 left-3 z-10 flex items-center gap-2 pointer-events-none">
        <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-slate-900/80 text-white backdrop-blur-md shadow-xs">
          {label}
        </span>
        {cardId && (
          <span className="px-2 py-0.5 text-[11px] font-mono text-slate-300 bg-black/60 backdrop-blur-md rounded">
            {cardId}
          </span>
        )}
        {roleBadge && (
          <span className="px-2 py-0.5 text-[10px] font-semibold text-blue-300 bg-blue-950/80 backdrop-blur-md rounded border border-blue-800/60">
            {roleBadge}
          </span>
        )}
      </div>

      {/* Standalone Placeholder State */}
      {isStandalonePlaceholder ? (
        <div className="flex flex-col items-center justify-center p-8 text-center gap-3 text-slate-400 dark:text-slate-500">
          <div className="p-4 bg-slate-200/60 dark:bg-slate-800/60 rounded-full">
            <ImageOff className="w-8 h-8" />
          </div>
          <div>
            <h4 className="text-sm font-semibold text-slate-700 dark:text-slate-300">
              Standalone Single-Sided Card
            </h4>
            <p className="text-xs max-w-xs mt-1 text-slate-500 dark:text-slate-400">
              No back card is paired with this recipe. Press <strong className="text-blue-500">[Space]</strong> to confirm as single-sided.
            </p>
          </div>
        </div>
      ) : imageUrl ? (
        <>
          {isLoading && !hasError && (
            <div className="absolute inset-0 flex items-center justify-center bg-slate-100/60 dark:bg-slate-950/60 z-10">
              <Loader2 className="w-6 h-6 text-blue-500 animate-spin" />
            </div>
          )}

          {hasError ? (
            <div className="flex flex-col items-center justify-center p-6 text-center text-rose-500 gap-2">
              <ImageOff className="w-8 h-8" />
              <span className="text-xs font-semibold">Failed to load card scan image</span>
            </div>
          ) : (
            <div
              className="origin-center transition-transform duration-75 will-change-transform max-w-full max-h-full flex items-center justify-center"
              style={{
                transform: transformStyle,
                filter: filterStyle,
              }}
            >
              <img
                src={imageUrl}
                alt={cardId || label}
                onLoad={() => setIsLoading(false)}
                onError={() => {
                  setIsLoading(false);
                  setHasError(true);
                }}
                className="max-h-[85%] max-w-[85%] object-contain rounded shadow-lg pointer-events-none"
                draggable={false}
              />
            </div>
          )}
        </>
      ) : (
        <div className="flex flex-col items-center justify-center p-8 text-center gap-3 text-slate-400 dark:text-slate-500">
          <div className="p-4 bg-slate-200/60 dark:bg-slate-800/60 rounded-full">
            <ImageOff className="w-8 h-8" />
          </div>
          <div>
            <h4 className="text-sm font-semibold text-slate-700 dark:text-slate-300">
              No Card Scan Image Available
            </h4>
            <p className="text-xs max-w-xs mt-1 text-slate-500 dark:text-slate-400">
              No scanned card image is associated with this recipe or card ID.
            </p>
          </div>
        </div>
      )}
    </div>
  );
};
