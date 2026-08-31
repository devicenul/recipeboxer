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
  openTimer: any = null;

  constructor(url: string) {
    this.url = url;
    this.openTimer = setTimeout(() => {
      this.onopen?.();
    }, 10);
  }

  cancelAutoOpen() {
    if (this.openTimer) {
      clearTimeout(this.openTimer);
      this.openTimer = null;
    }
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
    if (this.openTimer) {
      clearTimeout(this.openTimer);
      this.openTimer = null;
    }
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

  it('caps repeated reconnection attempts and stops reconnect loop', async () => {
    vi.useFakeTimers();
    let callCount = 0;
    (global as any).EventSource = vi.fn((url: string) => {
      callCount++;
      const instance = new MockEventSource(url);
      if (instance.openTimer) {
        clearTimeout(instance.openTimer);
        instance.openTimer = null;
      }
      mockEventSourceInstance = instance;
      return mockEventSourceInstance;
    });

    const onSessionInvalid = vi.fn();
    const onError = vi.fn();

    const { result } = renderHook(() =>
      useSSE({
        sessionId: 'sess_cap_test',
        maxReconnectAttempts: 2,
        fallbackPolling: false,
        onError,
        onSessionInvalid,
      })
    );

    expect(callCount).toBe(1);

    // First error -> should schedule reconnect
    act(() => {
      mockEventSourceInstance?.triggerError(new Error('Connection 1 failed'));
    });
    expect(result.current.isReconnecting).toBe(true);

    act(() => {
      vi.advanceTimersByTime(1600);
    });
    expect(callCount).toBe(2);

    // Second error -> reaches maxReconnectAttempts (2), should stop reconnecting
    act(() => {
      mockEventSourceInstance?.triggerError(new Error('Connection 2 failed'));
    });

    expect(result.current.isReconnecting).toBe(false);
    expect(onSessionInvalid).toHaveBeenCalledWith('sess_cap_test');

    // Advance time further -> no new EventSource connections created
    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(callCount).toBe(2);

    vi.useRealTimers();
  });

  it('falls back to polling /api/pipeline/status when SSE fails repeatedly', async () => {
    vi.useFakeTimers();
    let callCount = 0;
    (global as any).EventSource = vi.fn((url: string) => {
      callCount++;
      mockEventSourceInstance = new MockEventSource(url);
      return mockEventSourceInstance;
    });

    const onStateChange = vi.fn();
    const onSessionInvalid = vi.fn();

    // Mock fetch for fallback polling
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        session_id: 'sess_poll_test',
        status: PipelineState.COMPLETED,
        progress: { percent_complete: 100 },
      }),
    });
    (global as any).fetch = fetchMock;

    renderHook(() =>
      useSSE({
        sessionId: 'sess_poll_test',
        maxReconnectAttempts: 1,
        fallbackPolling: true,
        fallbackPollingIntervalMs: 2000,
        onStateChange,
        onSessionInvalid,
      })
    );

    expect(callCount).toBe(1);

    // Trigger error -> exceeds maxReconnectAttempts (1)
    act(() => {
      mockEventSourceInstance?.triggerError(new Error('SSE disconnected'));
    });

    // Advance to trigger fallback poll
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/pipeline/status?session_id=sess_poll_test')
    );
    expect(onStateChange).toHaveBeenCalledWith(
      expect.objectContaining({ status: PipelineState.COMPLETED })
    );

    vi.useRealTimers();
  });

  it('evicts stale session when fallback polling receives 404', async () => {
    vi.useFakeTimers();
    let callCount = 0;
    (global as any).EventSource = vi.fn((url: string) => {
      callCount++;
      mockEventSourceInstance = new MockEventSource(url);
      return mockEventSourceInstance;
    });

    const onSessionInvalid = vi.fn();

    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: 'Session not found' }),
    });
    (global as any).fetch = fetchMock;

    renderHook(() =>
      useSSE({
        sessionId: 'sess_stale_404',
        maxReconnectAttempts: 1,
        fallbackPolling: true,
        onSessionInvalid,
      })
    );

    // Trigger error -> starts fallback polling
    act(() => {
      mockEventSourceInstance?.triggerError(new Error('SSE connection refused'));
    });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/pipeline/status?session_id=sess_stale_404')
    );
    expect(onSessionInvalid).toHaveBeenCalledWith('sess_stale_404');

    vi.useRealTimers();
  });

  it('discards stale fallback polling updates when sessionId changes', async () => {
    vi.useFakeTimers();
    let callCount = 0;
    (global as any).EventSource = vi.fn((url: string) => {
      callCount++;
      mockEventSourceInstance = new MockEventSource(url);
      return mockEventSourceInstance;
    });

    const onStateChange = vi.fn();
    const fetchMock = vi.fn().mockImplementation(async () => {
      await new Promise((r) => setTimeout(r, 100));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          session_id: 'sess_old',
          status: PipelineState.COMPLETED,
          progress: { percent_complete: 100 },
        }),
      };
    });
    (global as any).fetch = fetchMock;

    const { rerender } = renderHook(
      ({ sid }) =>
        useSSE({
          sessionId: sid,
          maxReconnectAttempts: 1,
          fallbackPolling: true,
          onStateChange,
        }),
      { initialProps: { sid: 'sess_old' } }
    );

    // Error out sess_old to start fallback polling
    act(() => {
      mockEventSourceInstance?.triggerError(new Error('SSE failed'));
    });

    // Switch to sess_new before poll executes/resolves
    rerender({ sid: 'sess_new' });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    // onStateChange should not have been called with sess_old's data
    expect(onStateChange).not.toHaveBeenCalledWith(
      expect.objectContaining({ session_id: 'sess_old' })
    );

    vi.useRealTimers();
  });

  it('resets isConnected to false when enabled becomes false', async () => {
    vi.useFakeTimers();
    (global as any).EventSource = vi.fn((url: string) => {
      mockEventSourceInstance = new MockEventSource(url);
      return mockEventSourceInstance;
    });

    const { result, rerender } = renderHook(
      ({ isEnabled }) =>
        useSSE({
          sessionId: 'sess_toggle',
          enabled: isEnabled,
        }),
      { initialProps: { isEnabled: true } }
    );

    act(() => {
      vi.advanceTimersByTime(20);
    });

    expect(result.current.isConnected).toBe(true);

    // Disable hook (e.g. session cleared)
    rerender({ isEnabled: false });

    expect(result.current.isConnected).toBe(false);
    expect(result.current.isReconnecting).toBe(false);

    vi.useRealTimers();
  });

  it('evicts session when fallback polling receives status IDLE with session_id none', async () => {
    vi.useFakeTimers();
    (global as any).EventSource = vi.fn((url: string) => {
      mockEventSourceInstance = new MockEventSource(url);
      return mockEventSourceInstance;
    });

    const onSessionInvalid = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        session_id: 'none',
        status: PipelineState.IDLE,
      }),
    });
    (global as any).fetch = fetchMock;

    renderHook(() =>
      useSSE({
        sessionId: 'sess_unknown_poll',
        maxReconnectAttempts: 1,
        fallbackPolling: true,
        onSessionInvalid,
      })
    );

    // Trigger error -> starts fallback polling
    act(() => {
      mockEventSourceInstance?.triggerError(new Error('SSE closed'));
    });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(onSessionInvalid).toHaveBeenCalledWith('sess_unknown_poll');

    vi.useRealTimers();
  });

  it('evicts session when fallback polling receives status IDLE with session_id empty or missing', async () => {
    vi.useFakeTimers();
    (global as any).EventSource = vi.fn((url: string) => {
      mockEventSourceInstance = new MockEventSource(url);
      return mockEventSourceInstance;
    });

    const onSessionInvalid = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        session_id: '',
        status: PipelineState.IDLE,
      }),
    });
    (global as any).fetch = fetchMock;

    renderHook(() =>
      useSSE({
        sessionId: 'my_empty_session',
        maxReconnectAttempts: 1,
        fallbackPolling: true,
        onSessionInvalid,
      })
    );

    act(() => {
      mockEventSourceInstance?.triggerError(new Error('SSE closed'));
    });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(onSessionInvalid).toHaveBeenCalledWith('my_empty_session');

    vi.useRealTimers();
  });
});

