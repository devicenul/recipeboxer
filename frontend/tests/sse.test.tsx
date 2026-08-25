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

  constructor(url: string) {
    this.url = url;
    setTimeout(() => {
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
});
