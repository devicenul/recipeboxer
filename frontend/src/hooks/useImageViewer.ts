import { useState, useCallback } from 'react';
import { CanvasTransform, FilterSettings } from '../types/viewer';

const DEFAULT_TRANSFORM: CanvasTransform = {
  scale: 1.0,
  translateX: 0,
  translateY: 0,
  rotation: 0,
};

const DEFAULT_FILTERS: FilterSettings = {
  brightness: 100,
  contrast: 100,
  invert: false,
};

export function useImageViewer() {
  const [linked, setLinked] = useState<boolean>(true);

  const [transformFront, setTransformFront] = useState<CanvasTransform>(DEFAULT_TRANSFORM);
  const [transformBack, setTransformBack] = useState<CanvasTransform>(DEFAULT_TRANSFORM);

  const [filtersFront, setFiltersFront] = useState<FilterSettings>(DEFAULT_FILTERS);
  const [filtersBack, setFiltersBack] = useState<FilterSettings>(DEFAULT_FILTERS);

  // Zoom controls
  const zoomIn = useCallback((target: 'front' | 'back' | 'both' = 'both') => {
    const update = (prev: CanvasTransform) => ({
      ...prev,
      scale: Math.min(5.0, Math.round((prev.scale + 0.25) * 100) / 100),
    });

    if (linked || target === 'both') {
      setTransformFront(update);
      setTransformBack(update);
    } else if (target === 'front') {
      setTransformFront(update);
    } else {
      setTransformBack(update);
    }
  }, [linked]);

  const zoomOut = useCallback((target: 'front' | 'back' | 'both' = 'both') => {
    const update = (prev: CanvasTransform) => ({
      ...prev,
      scale: Math.max(0.25, Math.round((prev.scale - 0.25) * 100) / 100),
    });

    if (linked || target === 'both') {
      setTransformFront(update);
      setTransformBack(update);
    } else if (target === 'front') {
      setTransformFront(update);
    } else {
      setTransformBack(update);
    }
  }, [linked]);

  const setScale = useCallback((scale: number, target: 'front' | 'back' = 'front') => {
    const clamped = Math.min(5.0, Math.max(0.25, scale));
    if (linked) {
      setTransformFront((prev) => ({ ...prev, scale: clamped }));
      setTransformBack((prev) => ({ ...prev, scale: clamped }));
    } else if (target === 'front') {
      setTransformFront((prev) => ({ ...prev, scale: clamped }));
    } else {
      setTransformBack((prev) => ({ ...prev, scale: clamped }));
    }
  }, [linked]);

  // Pan controls
  const pan = useCallback((dx: number, dy: number, target: 'front' | 'back' = 'front') => {
    if (linked) {
      setTransformFront((prev) => ({ ...prev, translateX: prev.translateX + dx, translateY: prev.translateY + dy }));
      setTransformBack((prev) => ({ ...prev, translateX: prev.translateX + dx, translateY: prev.translateY + dy }));
    } else if (target === 'front') {
      setTransformFront((prev) => ({ ...prev, translateX: prev.translateX + dx, translateY: prev.translateY + dy }));
    } else {
      setTransformBack((prev) => ({ ...prev, translateX: prev.translateX + dx, translateY: prev.translateY + dy }));
    }
  }, [linked]);

  // Rotation controls (Independent 90 degree stepped rotation)
  const rotateCw = useCallback((target: 'front' | 'back') => {
    const update = (prev: CanvasTransform) => ({
      ...prev,
      rotation: (prev.rotation + 90) % 360,
    });
    if (target === 'front') {
      setTransformFront(update);
    } else {
      setTransformBack(update);
    }
  }, []);

  const rotateCcw = useCallback((target: 'front' | 'back') => {
    const update = (prev: CanvasTransform) => ({
      ...prev,
      rotation: (prev.rotation - 90 + 360) % 360,
    });
    if (target === 'front') {
      setTransformFront(update);
    } else {
      setTransformBack(update);
    }
  }, []);

  // Reset transform
  const resetTransform = useCallback((target: 'front' | 'back' | 'both' = 'both') => {
    if (linked || target === 'both') {
      setTransformFront(DEFAULT_TRANSFORM);
      setTransformBack(DEFAULT_TRANSFORM);
    } else if (target === 'front') {
      setTransformFront(DEFAULT_TRANSFORM);
    } else {
      setTransformBack(DEFAULT_TRANSFORM);
    }
  }, [linked]);

  // Filter controls
  const setBrightness = useCallback((val: number, target: 'front' | 'back' | 'both' = 'both') => {
    const clamped = Math.min(200, Math.max(0, val));
    if (linked || target === 'both') {
      setFiltersFront((prev) => ({ ...prev, brightness: clamped }));
      setFiltersBack((prev) => ({ ...prev, brightness: clamped }));
    } else if (target === 'front') {
      setFiltersFront((prev) => ({ ...prev, brightness: clamped }));
    } else {
      setFiltersBack((prev) => ({ ...prev, brightness: clamped }));
    }
  }, [linked]);

  const setContrast = useCallback((val: number, target: 'front' | 'back' | 'both' = 'both') => {
    const clamped = Math.min(300, Math.max(0, val));
    if (linked || target === 'both') {
      setFiltersFront((prev) => ({ ...prev, contrast: clamped }));
      setFiltersBack((prev) => ({ ...prev, contrast: clamped }));
    } else if (target === 'front') {
      setFiltersFront((prev) => ({ ...prev, contrast: clamped }));
    } else {
      setFiltersBack((prev) => ({ ...prev, contrast: clamped }));
    }
  }, [linked]);

  const toggleInvert = useCallback((target: 'front' | 'back' | 'both' = 'both') => {
    if (linked || target === 'both') {
      setFiltersFront((prev) => ({ ...prev, invert: !prev.invert }));
      setFiltersBack((prev) => ({ ...prev, invert: !prev.invert }));
    } else if (target === 'front') {
      setFiltersFront((prev) => ({ ...prev, invert: !prev.invert }));
    } else {
      setFiltersBack((prev) => ({ ...prev, invert: !prev.invert }));
    }
  }, [linked]);

  const resetFilters = useCallback((target: 'front' | 'back' | 'both' = 'both') => {
    if (linked || target === 'both') {
      setFiltersFront(DEFAULT_FILTERS);
      setFiltersBack(DEFAULT_FILTERS);
    } else if (target === 'front') {
      setFiltersFront(DEFAULT_FILTERS);
    } else {
      setFiltersBack(DEFAULT_FILTERS);
    }
  }, [linked]);

  /**
   * Helper to build CSS filter string from filter settings
   */
  const getFilterStyle = (filters: FilterSettings): string => {
    const inv = filters.invert ? 'invert(100%)' : 'invert(0%)';
    return `brightness(${filters.brightness}%) contrast(${filters.contrast}%) ${inv}`;
  };

  /**
   * Helper to build CSS transform string from canvas transform
   */
  const getTransformStyle = (t: CanvasTransform): string => {
    return `translate(${t.translateX}px, ${t.translateY}px) scale(${t.scale}) rotate(${t.rotation}deg)`;
  };

  return {
    linked,
    setLinked,
    transformFront,
    transformBack,
    filtersFront,
    filtersBack,
    zoomIn,
    zoomOut,
    setScale,
    pan,
    rotateCw,
    rotateCcw,
    resetTransform,
    setBrightness,
    setContrast,
    toggleInvert,
    resetFilters,
    getFilterStyle,
    getTransformStyle,
  };
}
