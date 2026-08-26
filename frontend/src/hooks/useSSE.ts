import { useEffect, useRef, useState, useCallback } from 'react';
import { PipelineStatus } from '../types/pipeline';
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
  onStateChange?: (status: PipelineStatus) => void;
  onProgress?: (status: PipelineStatus) => void;
  onHitlReady?: (data: { session_id: string; proposals_count: number }) => void;
  onRecipeExtracted?: (recipe: RecipeSchema) => void;
  onCompleted?: (status: PipelineStatus) => void;
  onError?: (err: any) => void;
}

export function useSSE({
  sessionId,
  enabled = true,
  onStateChange,
  onProgress,
  onHitlReady,
  onRecipeExtracted,
  onCompleted,
  onError,
}: UseSSEOptions) {
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [isReconnecting, setIsReconnecting] = useState<boolean>(false);
  const [reconnectCount, setReconnectCount] = useState<number>(0);
  const [lastEvent, setLastEvent] = useState<{ type: string; data: any } | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<any>(null);
  const backoffDelayRef = useRef<number>(1000);

  const callbacksRef = useRef({
    onStateChange,
    onProgress,
    onHitlReady,
    onRecipeExtracted,
    onCompleted,
    onError,
  });

  useEffect(() => {
    callbacksRef.current = {
      onStateChange,
      onProgress,
      onHitlReady,
      onRecipeExtracted,
      onCompleted,
      onError,
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
  }, []);

  const connect = useCallback(() => {
    if (!enabled || typeof window === 'undefined') return;

    cleanup();

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
        backoffDelayRef.current = 1000;
      };

      const handleEvent = (type: string, dataStr: string) => {
        try {
          const data = JSON.parse(dataStr);
          setLastEvent({ type, data });

          const callbacks = callbacksRef.current;
          switch (type) {
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

      es.addEventListener('state_change', (e: MessageEvent) => handleEvent('state_change', e.data));
      es.addEventListener('progress', (e: MessageEvent) => handleEvent('progress', e.data));
      es.addEventListener('hitl_ready', (e: MessageEvent) => handleEvent('hitl_ready', e.data));
      es.addEventListener('recipe_extracted', (e: MessageEvent) => handleEvent('recipe_extracted', e.data));
      es.addEventListener('completed', (e: MessageEvent) => handleEvent('completed', e.data));
      es.addEventListener('failed', (e: MessageEvent) => handleEvent('failed', e.data));
      es.addEventListener('cancelled', (e: MessageEvent) => handleEvent('cancelled', e.data));

      es.onerror = (err) => {
        setIsConnected(false);
        setIsReconnecting(true);
        callbacksRef.current.onError?.(err);

        // Schedule auto-reconnect with exponential backoff
        cleanup();
        const nextDelay = Math.min(backoffDelayRef.current * 1.5, 10000);
        backoffDelayRef.current = nextDelay;
        setReconnectCount((prev) => prev + 1);

        reconnectTimeoutRef.current = setTimeout(() => {
          connect();
        }, nextDelay);
      };
    } catch (e) {
      setIsConnected(false);
      setIsReconnecting(true);
      callbacksRef.current.onError?.(e);
    }
  }, [enabled, sessionId, cleanup]);

  useEffect(() => {
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
    reconnect: connect,
  };
}
