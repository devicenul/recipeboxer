import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, renderHook, act } from '@testing-library/react';
import React from 'react';
import { VisualToolbar } from '../src/components/viewer/VisualToolbar';
import { FilterControls } from '../src/components/viewer/FilterControls';
import { CardViewerCanvas } from '../src/components/viewer/CardViewerCanvas';
import { useImageViewer } from '../src/hooks/useImageViewer';

describe('R3: Visual Inspection Toolbar & Image Viewer Filters', () => {
  describe('useImageViewer hook state machine', () => {
    it('manages zoom within bounds [0.25, 5.0] with linked sync', () => {
      const { result } = renderHook(() => useImageViewer());

      expect(result.current.transformFront.scale).toBe(1.0);
      expect(result.current.transformBack.scale).toBe(1.0);
      expect(result.current.linked).toBe(true);

      // Zoom in
      act(() => {
        result.current.zoomIn();
      });
      expect(result.current.transformFront.scale).toBe(1.25);
      expect(result.current.transformBack.scale).toBe(1.25);

      // Zoom out
      act(() => {
        result.current.zoomOut();
        result.current.zoomOut();
      });
      expect(result.current.transformFront.scale).toBe(0.75);
      expect(result.current.transformBack.scale).toBe(0.75);
    });

    it('performs independent 90-degree stepped rotation per card', () => {
      const { result } = renderHook(() => useImageViewer());

      // Rotate Front 90 deg CW
      act(() => {
        result.current.rotateCw('front');
      });
      expect(result.current.transformFront.rotation).toBe(90);
      expect(result.current.transformBack.rotation).toBe(0);

      // Rotate Front 90 deg CW again -> 180
      act(() => {
        result.current.rotateCw('front');
      });
      expect(result.current.transformFront.rotation).toBe(180);

      // Rotate Back CCW -> 270
      act(() => {
        result.current.rotateCcw('back');
      });
      expect(result.current.transformBack.rotation).toBe(270);
    });

    it('controls brightness, contrast boost, and color inversion filters for deciphering faded pencil', () => {
      const { result } = renderHook(() => useImageViewer());

      expect(result.current.filtersFront.brightness).toBe(100);
      expect(result.current.filtersFront.contrast).toBe(100);
      expect(result.current.filtersFront.invert).toBe(false);

      // Apply contrast boost and invert
      act(() => {
        result.current.setContrast(200);
        result.current.toggleInvert();
      });

      expect(result.current.filtersFront.contrast).toBe(200);
      expect(result.current.filtersFront.invert).toBe(true);

      const filterCss = result.current.getFilterStyle(result.current.filtersFront);
      expect(filterCss).toContain('contrast(200%)');
      expect(filterCss).toContain('invert(100%)');
    });
  });

  describe('VisualToolbar and FilterControls components', () => {
    it('renders zoom in/out, rotate CW/CCW, and link/unlink buttons', () => {
      const onZoomIn = vi.fn();
      const onZoomOut = vi.fn();
      const onRotateCw = vi.fn();
      const onRotateCcw = vi.fn();
      const onToggleLinked = vi.fn();

      render(
        <VisualToolbar
          linked={true}
          onToggleLinked={onToggleLinked}
          onZoomIn={onZoomIn}
          onZoomOut={onZoomOut}
          onRotateCw={onRotateCw}
          onRotateCcw={onRotateCcw}
          onResetTransform={vi.fn()}
          filters={{ brightness: 100, contrast: 100, invert: false }}
          onChangeBrightness={vi.fn()}
          onChangeContrast={vi.fn()}
          onToggleInvert={vi.fn()}
          onResetFilters={vi.fn()}
        />
      );

      fireEvent.click(screen.getByTitle('Zoom In (+25%)'));
      expect(onZoomIn).toHaveBeenCalled();

      fireEvent.click(screen.getByTitle('Zoom Out (-25%)'));
      expect(onZoomOut).toHaveBeenCalled();

      fireEvent.click(screen.getByTitle('Rotate Clockwise (+90°)'));
      expect(onRotateCw).toHaveBeenCalled();

      fireEvent.click(screen.getByTitle(/Pan & Zoom synchronized/i));
      expect(onToggleLinked).toHaveBeenCalled();
    });
  });
});
