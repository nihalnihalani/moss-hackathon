/**
 * useBackendSession — resolves, on startup, whether to run a REAL backend-driven
 * LiveKit session or fall back to the scripted mock.
 *
 * Decision flow (runs once on mount unless forceMock):
 *   1. GET {VITE_API_URL}/config.
 *   2. If the backend is reachable AND config.live === true:
 *        - POST /token to mint a LiveKit access token.
 *        - resolve the room URL (token.url, falling back to config.livekitUrl).
 *        - => status 'live' with { url, token }.
 *   3. On ANY failure (network, non-2xx, live:false, missing url) => status 'mock'.
 *
 * `forceMock` short-circuits straight to mock without touching the network, so
 * the offline demo stays instant and deterministic.
 *
 * The resolved { url, token } are fed verbatim into useCrossExam's existing LIVE
 * branch — the backend agent then drives state + citations over the data channel.
 */

import { useEffect, useRef, useState } from 'react';
import { fetchConfig, requestToken } from '../lib/api';

export type SessionStatus = 'resolving' | 'live' | 'mock';

export interface BackendSession {
  /** Where we are in the live-vs-mock decision. */
  status: SessionStatus;
  /** LiveKit ws URL, set only when status === 'live'. */
  livekitUrl: string | undefined;
  /** LiveKit access token, set only when status === 'live'. */
  livekitToken: string | undefined;
  /** Human-readable reason we fell back to mock (for the UI tooltip), if any. */
  reason: string | undefined;
}

export interface UseBackendSessionOptions {
  /** Skip the backend entirely and resolve straight to mock. */
  forceMock?: boolean;
}

function newSessionRoom(): string {
  const randomId =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2);
  return `crossexam-${randomId.replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 12)}`;
}

export function useBackendSession(options: UseBackendSessionOptions = {}): BackendSession {
  const { forceMock } = options;
  const [session, setSession] = useState<BackendSession>({
    status: forceMock ? 'mock' : 'resolving',
    livekitUrl: undefined,
    livekitToken: undefined,
    reason: forceMock ? 'Forced mock mode' : undefined,
  });

  // Re-resolve whenever forceMock flips.
  const forceRef = useRef(forceMock);
  forceRef.current = forceMock;

  useEffect(() => {
    if (forceMock) {
      setSession({
        status: 'mock',
        livekitUrl: undefined,
        livekitToken: undefined,
        reason: 'Forced mock mode',
      });
      return;
    }

    let cancelled = false;
    const controller = new AbortController();
    setSession((prev) => ({ ...prev, status: 'resolving', reason: undefined }));

    void (async () => {
      try {
        const config = await fetchConfig(controller.signal);
        if (cancelled) return;
        if (!config.live) {
          setSession({
            status: 'mock',
            livekitUrl: undefined,
            livekitToken: undefined,
            reason: 'Backend reports live:false',
          });
          return;
        }
        const token = await requestToken({ room: newSessionRoom() }, controller.signal);
        if (cancelled) return;
        const url = token.url || config.livekitUrl || undefined;
        if (!url) {
          setSession({
            status: 'mock',
            livekitUrl: undefined,
            livekitToken: undefined,
            reason: 'Backend live but no LiveKit URL provided',
          });
          return;
        }
        setSession({
          status: 'live',
          livekitUrl: url,
          livekitToken: token.token,
          reason: undefined,
        });
      } catch (err) {
        if (cancelled) return;
        // A simple fetch failure means the backend is unreachable — this is the
        // EXPECTED offline/no-backend path (mock demo). Tag it as "offline:" so
        // the UI treats it silently (the MOCK/OFFLINE badge already says it all).
        setSession({
          status: 'mock',
          livekitUrl: undefined,
          livekitToken: undefined,
          reason: `offline: ${err instanceof Error ? err.message : String(err)}`,
        });
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [forceMock]);

  return session;
}
