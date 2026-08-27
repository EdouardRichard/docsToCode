import { useState, useEffect, useRef, useCallback } from 'react';
import type { SSEEvent } from '../types';

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const RECONNECT_DELAY_MS = 3000;

interface UseSSEResult {
  events: SSEEvent[];
  connected: boolean;
  lastEvent: SSEEvent | null;
}

export function useSSE(topics: string[]): UseSSEResult {
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<SSEEvent | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const topicsRef = useRef(topics);
  topicsRef.current = topics;

  const connect = useCallback(() => {
    // Abort any existing connection
    if (abortRef.current) {
      abortRef.current.abort();
    }

    const controller = new AbortController();
    abortRef.current = controller;

    const params = new URLSearchParams();
    for (const topic of topicsRef.current) {
      params.append('topics', topic);
    }

    const url = `${BASE_URL}/api/events?${params.toString()}`;

    fetch(url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`SSE connection failed: ${response.status}`);
        }
        if (!response.body) {
          throw new Error('SSE response has no body');
        }

        setConnected(true);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        function processChunk(): void {
          reader.read().then(({ done, value }) => {
            if (done) {
              setConnected(false);
              scheduleReconnect();
              return;
            }

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            // Keep the last potentially incomplete line in the buffer
            buffer = lines.pop() || '';

            let currentEvent = '';
            let currentData = '';

            for (const line of lines) {
              if (line.startsWith('event:')) {
                currentEvent = line.slice(6).trim();
              } else if (line.startsWith('data:')) {
                currentData = line.slice(5).trim();
              } else if (line === '' && currentData) {
                // Empty line signals end of event
                try {
                  const parsed: SSEEvent = {
                    event: currentEvent as SSEEvent['event'],
                    data: JSON.parse(currentData),
                  };
                  setEvents((prev) => [...prev, parsed]);
                  setLastEvent(parsed);
                } catch {
                  // Skip malformed events
                }
                currentEvent = '';
                currentData = '';
              }
            }

            if (!controller.signal.aborted) {
              processChunk();
            }
          }).catch((err) => {
            if (err.name !== 'AbortError') {
              setConnected(false);
              scheduleReconnect();
            }
          });
        }

        processChunk();
      })
      .catch((err) => {
        if (err.name !== 'AbortError') {
          setConnected(false);
          scheduleReconnect();
        }
      });
  }, []);

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
    }
    reconnectTimerRef.current = setTimeout(() => {
      connect();
    }, RECONNECT_DELAY_MS);
  }, [connect]);

  useEffect(() => {
    connect();

    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
    };
  }, [connect]);

  return { events, connected, lastEvent };
}
