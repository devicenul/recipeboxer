import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSSE } from '../src/hooks/useSSE';
import { PipelineState } from '../src/types/pipeline';

// Mock EventSource
class MockEventSource {
  url: string;
  onopen: (() => void) | null = null;
  onerror: ((err: any) => void) | null = null;
  listeners: Record<string, ((e: any) => void)[]> = {};
  private openTimer: any = null;

  constructor(url: string) {
    this.url = url;
    this.openTimer = setTimeout(() => {
      this.onopen?.();
    }, 10);
  }

  addEventListener(type: string, listener: (e: any) => void) {
    if (!this.listeners[type]) {
      this.listeners[type] = [];
    }
    this.listeners[type].push(listener);
  }

  removeEventListener(type: string, listener: (e: any) => void) {
    if (this.listeners[type]) {
      this.listeners[type] = this.listeners[type].filter((l) => l !== listener);
    }
  }

  emit(type: string, data: any) {
    const event = { data: typeof data === 'string' ? data : JSON.stringify(data) };
    if (this.listeners[type]) {
      this.listeners[type].forEach((l) => l(event));
    }
  }

  triggerError(err: any) {
    this.onerror?.(err);
  }

  close() {
    if (this.openTimer) {
      clearTimeout(this.openTimer);
      this.openTimer = null;
    }
    this.listeners = {};
  }
}

describe('R1: Server-Sent Events (SSE) Live Stream Hook', () => {
  let mockEventSourceInstance: MockEventSource | null = null;

  beforeEach(() => {
    (global as any).EventSource = vi.fn((url: string) => {
      mockEventSourceInstance = new MockEventSource(url);
      return mockEventSourceInstance;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('connects to /api/events with target session_id query param', () => {
    const { result } = renderHook(() =>
      useSSE({
        sessionId: 'sess_123',
        enabled: true,
      })
    );

    expect((global as any).EventSource).toHaveBeenCalledWith(
      '/api/events?session_id=sess_123'
    );
  });

  it('dispatches state_change and progress callbacks on receiving SSE events', async () => {
    const onStateChange = vi.fn();
    const onProgress = vi.fn();
    const onHitlReady = vi.fn();

    renderHook(() =>
      useSSE({
        sessionId: 'sess_123',
        onStateChange,
        onProgress,
        onHitlReady,
      })
    );

    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });

    // Simulate state_change event
    act(() => {
      mockEventSourceInstance?.emit('state_change', {
        session_id: 'sess_123',
        status: PipelineState.CORRELATING,
        progress: { percent_complete: 40.0 },
      });
    });

    expect(onStateChange).toHaveBeenCalledWith(
      expect.objectContaining({ status: PipelineState.CORRELATING })
    );

    // Simulate hitl_ready event
    act(() => {
      mockEventSourceInstance?.emit('hitl_ready', {
        session_id: 'sess_123',
        proposals_count: 5,
      });
    });

    expect(onHitlReady).toHaveBeenCalledWith({
      session_id: 'sess_123',
      proposals_count: 5,
    });
  });

  it('stabilizes callbacks using ref without reconnecting on inline function changes', async () => {
    let callCount = 0;
    (global as any).EventSource = vi.fn((url: string) => {
      callCount++;
      mockEventSourceInstance = new MockEventSource(url);
      return mockEventSourceInstance;
    });

    const onStateChange1 = vi.fn();
    const { rerender } = renderHook(
      ({ cb }) =>
        useSSE({
          sessionId: 'sess_stable_1',
          onStateChange: cb,
        }),
      {
        initialProps: { cb: onStateChange1 },
      }
    );

    expect(callCount).toBe(1);

    // Re-render with brand new function reference
    const onStateChange2 = vi.fn();
    rerender({ cb: onStateChange2 });

    // EventSource constructor should NOT have been called again
    expect(callCount).toBe(1);

    // Emitting event should invoke the latest callback reference (onStateChange2)
    act(() => {
      mockEventSourceInstance?.emit('state_change', {
        session_id: 'sess_stable_1',
        status: PipelineState.EXTRACTING,
        progress: { percent_complete: 80.0 },
      });
    });

    expect(onStateChange1).not.toHaveBeenCalled();
    expect(onStateChange2).toHaveBeenCalledWith(
      expect.objectContaining({ status: PipelineState.EXTRACTING })
    );
  });

  it('handles connection error with backoff and error notification', async () => {
    vi.useFakeTimers();
    let callCount = 0;
    (global as any).EventSource = vi.fn((url: string) => {
      callCount++;
      mockEventSourceInstance = new MockEventSource(url);
      return mockEventSourceInstance;
    });

    const onError = vi.fn();
    const { result } = renderHook(() =>
      useSSE({
        sessionId: 'sess_err_test',
        onError,
      })
    );

    expect(callCount).toBe(1);

    // Trigger error on active EventSource
    act(() => {
      mockEventSourceInstance?.triggerError(new Error('Network disconnected'));
    });

    expect(onError).toHaveBeenCalled();
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isReconnecting).toBe(true);

    // Fast-forward backoff delay (1500ms)
    act(() => {
      vi.advanceTimersByTime(1600);
    });

    // Should have reconnected
    expect(callCount).toBe(2);
    vi.useRealTimers();
  });
});
