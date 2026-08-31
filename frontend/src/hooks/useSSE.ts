import { useEffect, useRef, useState, useCallback } from 'react';
import { PipelineStatus, PipelineState } from '../types/pipeline';
import { RecipeSchema } from '../types/recipe';

export interface SSEEventMap {
  state_change?: PipelineStatus;
  progress?: PipelineStatus;
  hitl_ready?: { session_id: string; proposals_count: number };
  recipe_extracted?: RecipeSchema;
  completed?: PipelineStatus;
  failed?: PipelineStatus;
  cancelled?: PipelineStatus;
}

export interface UseSSEOptions {
  sessionId?: string | null;
  enabled?: boolean;
  maxReconnectAttempts?: number;
  fallbackPolling?: boolean;
  fallbackPollingIntervalMs?: number;
  onStateChange?: (status: PipelineStatus) => void;
  onProgress?: (status: PipelineStatus) => void;
  onHitlReady?: (data: { session_id: string; proposals_count: number }) => void;
  onRecipeExtracted?: (recipe: RecipeSchema) => void;
  onCompleted?: (status: PipelineStatus) => void;
  onError?: (err: any) => void;
  onSessionInvalid?: (sessionId: string) => void;
}

export function useSSE({
  sessionId,
  enabled = true,
  maxReconnectAttempts = 3,
  fallbackPolling = true,
  fallbackPollingIntervalMs = 3000,
  onStateChange,
  onProgress,
  onHitlReady,
  onRecipeExtracted,
  onCompleted,
  onError,
  onSessionInvalid,
}: UseSSEOptions) {
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [isReconnecting, setIsReconnecting] = useState<boolean>(false);
  const [reconnectCount, setReconnectCount] = useState<number>(0);
  const [lastEvent, setLastEvent] = useState<{ type: string; data: any } | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<any>(null);
  const pollingTimeoutRef = useRef<any>(null);
  const backoffDelayRef = useRef<number>(1000);
  const consecutiveErrorsRef = useRef<number>(0);

  const currentSidRef = useRef<string | null | undefined>(sessionId);

  const callbacksRef = useRef({
    onStateChange,
    onProgress,
    onHitlReady,
    onRecipeExtracted,
    onCompleted,
    onError,
    onSessionInvalid,
  });

  useEffect(() => {
    currentSidRef.current = sessionId;
    callbacksRef.current = {
      onStateChange,
      onProgress,
      onHitlReady,
      onRecipeExtracted,
      onCompleted,
      onError,
      onSessionInvalid,
    };
  });

  const cleanup = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
    if (pollingTimeoutRef.current) {
      clearTimeout(pollingTimeoutRef.current);
      pollingTimeoutRef.current = null;
    }
  }, []);

  const startFallbackPolling = useCallback((sid: string) => {
    if (!fallbackPolling || !enabled || typeof window === 'undefined') return;

    if (pollingTimeoutRef.current) {
      clearTimeout(pollingTimeoutRef.current);
    }

    const poll = async () => {
      if (currentSidRef.current !== sid) return;
      try {
        const response = await fetch(`/api/pipeline/status?session_id=${encodeURIComponent(sid)}`);
        if (currentSidRef.current !== sid) return;
        if (response.status === 404) {
          // Session does not exist or was evicted
          callbacksRef.current.onSessionInvalid?.(sid);
          return;
        }
        if (!response.ok) {
          pollingTimeoutRef.current = setTimeout(poll, fallbackPollingIntervalMs);
          return;
        }
        const data: PipelineStatus = await response.json();
        if (currentSidRef.current !== sid) return;

        // Evict if backend returned IDLE for unknown session ('none')
        if (
          data.status === PipelineState.IDLE &&
          (data.session_id === 'none' || !data.session_id)
        ) {
          callbacksRef.current.onSessionInvalid?.(sid);
          return;
        }

        callbacksRef.current.onStateChange?.(data);
        callbacksRef.current.onProgress?.(data);

        if (
          data.status === PipelineState.COMPLETED ||
          data.status === ('COMPLETED' as any)
        ) {
          callbacksRef.current.onCompleted?.(data);
          return;
        }

        if (
          data.status === PipelineState.FAILED ||
          data.status === PipelineState.CANCELLED ||
          data.status === ('FAILED' as any) ||
          data.status === ('CANCELLED' as any)
        ) {
          return;
        }

        pollingTimeoutRef.current = setTimeout(poll, fallbackPollingIntervalMs);
      } catch {
        if (currentSidRef.current !== sid) return;
        pollingTimeoutRef.current = setTimeout(poll, fallbackPollingIntervalMs);
      }
    };

    pollingTimeoutRef.current = setTimeout(poll, 500);
  }, [fallbackPolling, enabled, fallbackPollingIntervalMs]);

  const connect = useCallback(() => {
    if (!enabled || typeof window === 'undefined') {
      setIsConnected(false);
      setIsReconnecting(false);
      cleanup();
      return;
    }

    cleanup();
    setIsConnected(false);

    const url = sessionId
      ? `/api/events?session_id=${encodeURIComponent(sessionId)}`
      : '/api/events';

    try {
      const es = new EventSource(url);
      eventSourceRef.current = es;

      es.onopen = () => {
        setIsConnected(true);
        setIsReconnecting(false);
        setReconnectCount(0);
        consecutiveErrorsRef.current = 0;
        backoffDelayRef.current = 1000;
        if (pollingTimeoutRef.current) {
          clearTimeout(pollingTimeoutRef.current);
          pollingTimeoutRef.current = null;
        }
      };

      const handleEvent = (type: string, dataStr: string) => {
        try {
          const data = JSON.parse(dataStr);
          setLastEvent({ type, data });

          const callbacks = callbacksRef.current;
          switch (type) {
            case 'status':
            case 'state_change':
              callbacks.onStateChange?.(data);
              break;
            case 'progress':
              callbacks.onProgress?.(data);
              break;
            case 'hitl_ready':
              callbacks.onHitlReady?.(data);
              break;
            case 'recipe_extracted':
              callbacks.onRecipeExtracted?.(data);
              break;
            case 'completed':
              callbacks.onCompleted?.(data);
              break;
            case 'failed':
              callbacks.onStateChange?.(data);
              break;
            case 'cancelled':
              callbacks.onStateChange?.(data);
              break;
          }
        } catch {
          // Non-JSON ping or custom data
        }
      };

      es.addEventListener('status', (e: MessageEvent) => handleEvent('status', e.data));
      es.addEventListener('state_change', (e: MessageEvent) => handleEvent('state_change', e.data));
      es.addEventListener('progress', (e: MessageEvent) => handleEvent('progress', e.data));
      es.addEventListener('hitl_ready', (e: MessageEvent) => handleEvent('hitl_ready', e.data));
      es.addEventListener('recipe_extracted', (e: MessageEvent) => handleEvent('recipe_extracted', e.data));
      es.addEventListener('completed', (e: MessageEvent) => handleEvent('completed', e.data));
      es.addEventListener('failed', (e: MessageEvent) => handleEvent('failed', e.data));
      es.addEventListener('cancelled', (e: MessageEvent) => handleEvent('cancelled', e.data));
      es.onmessage = (e: MessageEvent) => handleEvent('status', e.data);

      es.onerror = (err) => {
        setIsConnected(false);
        callbacksRef.current.onError?.(err);

        cleanup();

        const newErrors = consecutiveErrorsRef.current + 1;
        consecutiveErrorsRef.current = newErrors;
        setReconnectCount(newErrors);

        if (newErrors < maxReconnectAttempts) {
          setIsReconnecting(true);
          const nextDelay = Math.min(backoffDelayRef.current * 1.5, 10000);
          backoffDelayRef.current = nextDelay;
          reconnectTimeoutRef.current = setTimeout(() => {
            connect();
          }, nextDelay);
        } else {
          // Exceeded maximum reconnect attempts: avoid infinite reconnect freeze
          setIsReconnecting(false);
          if (sessionId) {
            if (fallbackPolling) {
              startFallbackPolling(sessionId);
            } else {
              callbacksRef.current.onSessionInvalid?.(sessionId);
            }
          }
        }
      };
    } catch (e) {
      setIsConnected(false);
      setIsReconnecting(false);
      callbacksRef.current.onError?.(e);
      if (sessionId) {
        if (fallbackPolling) {
          startFallbackPolling(sessionId);
        } else {
          callbacksRef.current.onSessionInvalid?.(sessionId);
        }
      }
    }
  }, [enabled, sessionId, maxReconnectAttempts, fallbackPolling, startFallbackPolling, cleanup]);

  useEffect(() => {
    consecutiveErrorsRef.current = 0;
    connect();
    return () => {
      cleanup();
    };
  }, [connect, cleanup]);

  return {
    isConnected,
    isReconnecting,
    reconnectCount,
    lastEvent,
    reconnect: () => {
      consecutiveErrorsRef.current = 0;
      connect();
    },
  };
}
