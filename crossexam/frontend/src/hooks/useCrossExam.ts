/**
 * useCrossExam — the single source of truth for the demo UI.
 *
 * Two modes:
 *  - MOCK (default, no backend): drives the scripted listening → thinking →
 *    speaking → SNAP sequence with timers, including the contradiction follow-up.
 *  - LIVE: connects to a LiveKit room, mirrors the agent's state, and parses
 *    citation payloads arriving on the data channel.
 *
 * LiveKit is imported lazily inside the live branch so the app builds and runs
 * with no keys and no network.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AgentState, Citation } from '../types';
import {
  ANSWER_CITATION,
  ANSWER_TRANSCRIPT,
  CONTRADICTION_CITATION,
  CONTRADICTION_TRANSCRIPT,
  DEMO_QUESTION,
  DEMO_TOTAL_PAGES,
} from '../lib/mockData';

export interface CrossExamConfig {
  /** LiveKit ws URL; absent => mock mode. */
  livekitUrl?: string | undefined;
  /** LiveKit access token; absent => mock mode. */
  livekitToken?: string | undefined;
  /** Force mock regardless of URL/token. */
  forceMock?: boolean;
}

export interface CrossExamState {
  /** True if running entirely on mock data (no LiveKit). */
  isMock: boolean;
  /** True once a live LiveKit room is connected. */
  isConnected: boolean;
  agentState: AgentState;
  /** Streaming caption text currently being "spoken". */
  caption: string;
  /** The user's last question (for display). */
  question: string;
  /** The currently highlighted citation, or null before the first snap. */
  activeCitation: Citation | null;
  /** All citations surfaced so far this session (answer, then contradiction). */
  citations: Citation[];
  /** Target page the canvas should be showing (drives the page-jump). */
  targetPage: number;
  /** Total corpus size for the latency/“searched N pages” chip. */
  totalPages: number;
  /** Kick off the scripted demo (mock) or send a prompt (live, best-effort). */
  runDemo: () => void;
  /** Reset back to the idle/establishing-the-haystack state. */
  reset: () => void;
}

interface ScriptStep {
  at: number; // ms from runDemo()
  apply: (s: MutableSnapshot) => void;
}

interface MutableSnapshot {
  agentState: AgentState;
  caption: string;
  question: string;
  activeCitation: Citation | null;
  citations: Citation[];
  targetPage: number;
}

/** The scripted 90s beats, compressed for a live demo cadence. */
const MOCK_SCRIPT: ScriptStep[] = [
  {
    at: 0,
    apply: (s) => {
      s.agentState = 'listening';
      s.question = DEMO_QUESTION;
      s.caption = '';
    },
  },
  {
    at: 1200,
    apply: (s) => {
      s.agentState = 'thinking';
      s.targetPage = ANSWER_CITATION.bbox.page; // triggers the page-jump animation
    },
  },
  {
    at: 2600,
    apply: (s) => {
      // THE SNAP.
      s.agentState = 'speaking';
      s.activeCitation = ANSWER_CITATION;
      s.citations = [ANSWER_CITATION];
      s.caption = ANSWER_TRANSCRIPT;
    },
  },
  {
    at: 6200,
    apply: (s) => {
      s.agentState = 'thinking';
      s.caption = ANSWER_TRANSCRIPT;
      s.targetPage = CONTRADICTION_CITATION.bbox.page;
    },
  },
  {
    at: 7600,
    apply: (s) => {
      // The contradiction climax.
      s.agentState = 'speaking';
      s.activeCitation = CONTRADICTION_CITATION;
      s.citations = [ANSWER_CITATION, CONTRADICTION_CITATION];
      s.caption = CONTRADICTION_TRANSCRIPT;
    },
  },
  {
    at: 11500,
    apply: (s) => {
      s.agentState = 'idle';
    },
  },
];

const IDLE_SNAPSHOT: MutableSnapshot = {
  agentState: 'idle',
  caption: '',
  question: '',
  activeCitation: null,
  citations: [],
  targetPage: 1,
};

export function useCrossExam(config: CrossExamConfig): CrossExamState {
  const isMock = useMemo(
    () => config.forceMock === true || !config.livekitUrl || !config.livekitToken,
    [config.forceMock, config.livekitUrl, config.livekitToken],
  );

  const [snap, setSnap] = useState<MutableSnapshot>(() => ({ ...IDLE_SNAPSHOT }));
  const [isConnected, setIsConnected] = useState(false);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const clearTimers = useCallback(() => {
    timers.current.forEach((t) => clearTimeout(t));
    timers.current = [];
  }, []);

  const reset = useCallback(() => {
    clearTimers();
    setSnap({ ...IDLE_SNAPSHOT });
  }, [clearTimers]);

  const runMockDemo = useCallback(() => {
    clearTimers();
    setSnap({ ...IDLE_SNAPSHOT });
    for (const step of MOCK_SCRIPT) {
      const t = setTimeout(() => {
        setSnap((prev) => {
          const next: MutableSnapshot = { ...prev, citations: [...prev.citations] };
          step.apply(next);
          return next;
        });
      }, step.at);
      timers.current.push(t);
    }
  }, [clearTimers]);

  // LIVE mode: connect to LiveKit lazily and mirror agent state + citation data.
  useEffect(() => {
    if (isMock) {
      setIsConnected(false);
      return;
    }
    let disposed = false;
    let cleanup: (() => void) | undefined;

    void (async () => {
      try {
        const { Room, RoomEvent } = await import('livekit-client');
        const room = new Room();
        await room.connect(config.livekitUrl as string, config.livekitToken as string);
        if (disposed) {
          await room.disconnect();
          return;
        }
        setIsConnected(true);

        const onData = (payload: Uint8Array): void => {
          try {
            const parsed: unknown = JSON.parse(new TextDecoder().decode(payload));
            handleLivePayload(parsed, setSnap);
          } catch {
            /* ignore malformed frames */
          }
        };
        room.on(RoomEvent.DataReceived, onData);

        cleanup = () => {
          room.off(RoomEvent.DataReceived, onData);
          void room.disconnect();
        };
      } catch {
        // Fall back gracefully: stay in a connected=false state, UI still renders.
        if (!disposed) setIsConnected(false);
      }
    })();

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [isMock, config.livekitUrl, config.livekitToken]);

  useEffect(() => () => clearTimers(), [clearTimers]);

  const runDemo = useCallback(() => {
    if (isMock) runMockDemo();
    // In live mode the backend drives state via the data channel; runDemo is a no-op
    // trigger point you can wire to a "send prompt" RPC if desired.
  }, [isMock, runMockDemo]);

  return {
    isMock,
    isConnected,
    agentState: snap.agentState,
    caption: snap.caption,
    question: snap.question,
    activeCitation: snap.activeCitation,
    citations: snap.citations,
    targetPage: snap.targetPage,
    totalPages: DEMO_TOTAL_PAGES,
    runDemo,
    reset,
  };
}

/** Narrow an unknown LiveKit data payload into a state update. Defensive by design. */
function handleLivePayload(
  parsed: unknown,
  setSnap: React.Dispatch<React.SetStateAction<MutableSnapshot>>,
): void {
  if (typeof parsed !== 'object' || parsed === null) return;
  const msg = parsed as Record<string, unknown>;

  if (typeof msg.agentState === 'string') {
    const state = msg.agentState as AgentState;
    setSnap((prev) => ({ ...prev, agentState: state }));
  }
  if (typeof msg.caption === 'string') {
    const caption = msg.caption;
    setSnap((prev) => ({ ...prev, caption }));
  }
  if (typeof msg.question === 'string') {
    const question = msg.question;
    setSnap((prev) => ({ ...prev, question }));
  }
  if (isCitation(msg.citation)) {
    const citation = msg.citation;
    setSnap((prev) => ({
      ...prev,
      activeCitation: citation,
      agentState: 'speaking',
      targetPage: citation.bbox.page,
      citations: prev.citations.some((c) => c.id === citation.id)
        ? prev.citations
        : [...prev.citations, citation],
    }));
  }
}

function isCitation(value: unknown): value is Citation {
  if (typeof value !== 'object' || value === null) return false;
  const c = value as Record<string, unknown>;
  const b = c.bbox as Record<string, unknown> | undefined;
  return (
    typeof c.id === 'string' &&
    typeof c.text === 'string' &&
    typeof c.confidence === 'number' &&
    typeof b === 'object' &&
    b !== null &&
    typeof b.page === 'number' &&
    typeof b.x0 === 'number' &&
    typeof b.y0 === 'number' &&
    typeof b.x1 === 'number' &&
    typeof b.y1 === 'number'
  );
}
