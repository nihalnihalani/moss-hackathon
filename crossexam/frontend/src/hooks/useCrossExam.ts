/**
 * useCrossExam — the single source of truth for the depth-v2 demo UI.
 *
 * Two modes:
 *  - MOCK (default, no backend): drives the scripted listening → thinking →
 *    speaking → SNAP sequence with timers, including the cross-document
 *    contradiction, a memory recall, and a meeting-mode proactive beat.
 *  - LIVE: connects to a LiveKit room, mirrors the agent's state, and parses the
 *    depth-v2 Frame arriving on the data channel EXACTLY per the shared contract.
 *
 * LiveKit is imported lazily inside the live branch so the app builds and runs
 * with no keys and no network.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  AgentState,
  Citation,
  HopTrace,
  MemoryRef,
  SilenceReason,
  Speaker,
} from '../types';
import {
  ANSWER_CITATION,
  ANSWER_TRANSCRIPT,
  CONTRADICTION_CITATION,
  CONTRADICTION_HOPS,
  CONTRADICTION_TRANSCRIPT,
  DEMO_CONTRADICTION_QUESTION,
  DEMO_QUESTION,
  DEMO_TOTAL_PAGES,
  MEMORY_RECALL,
  MEMORY_TRANSCRIPT,
  NOT_FOUND_CLAIM,
  NOT_FOUND_TRANSCRIPT,
  PROACTIVE_CITATION,
  PROACTIVE_CLAIM,
  PROACTIVE_TRANSCRIPT,
  SPEAKER_COUNSEL,
  SPEAKER_WITNESS,
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
  /** The currently highlighted (primary) citation, or null before the first snap. */
  activeCitation: Citation | null;
  /** All citations surfaced this turn (across docs/pages for multi-hop). */
  citations: Citation[];
  /** Which citation to page-jump to / highlight first this turn. */
  primaryId: string | null;
  /** The surfaced citations conflict (cross-page/cross-doc). */
  contradiction: boolean;
  /** The agentic decomposition trail for "how I found this". */
  hops: HopTrace[];
  /** Memory recalls referenced this turn (render recall chips, no re-snap). */
  memory: MemoryRef[];
  /** Who triggered this frame (meeting mode), or null. */
  speaker: Speaker | null;
  /** Target page the canvas should be showing (drives the page-jump). */
  targetPage: number;
  /** Total corpus size for the latency/“searched N pages” chip. */
  totalPages: number;
  /**
   * Whether the active citation was surfaced UNPROMPTED (ambient co-pilot). Drives
   * the "● SURFACED AUTOMATICALLY" tag + orb/pill pulse. Cleared on reset/new ask.
   */
  proactive: boolean;
  /**
   * The LAST retrieval latency observed (ms), persisted across snaps. Drives the
   * always-on live latency badge — distinct from the per-snap LatencyChip.
   * Undefined until the first retrieval lands.
   */
  lastLatencyMs: number | undefined;
  /**
   * Set when the agent declined to surface a box (honest silence). Drives the
   * "No grounded source — staying silent" empty state. Cleared on any new snap.
   */
  silenceReason: SilenceReason | null;
  /**
   * The agent's output (TTS) audio as a MediaStream, when a live room has
   * subscribed to a remote audio track. Feeds the audio-reactive orb so it
   * breathes to the agent's voice while `speaking`. Null in mock / before any
   * remote audio arrives.
   */
  outputStream: MediaStream | null;
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
  primaryId: string | null;
  contradiction: boolean;
  hops: HopTrace[];
  memory: MemoryRef[];
  speaker: Speaker | null;
  targetPage: number;
  /** Did the active citation arrive unprompted? */
  proactive: boolean;
  /** Last retrieval latency in ms (persists across snaps). */
  lastLatencyMs: number | undefined;
  /** Honest-silence reason, or null. */
  silenceReason: SilenceReason | null;
}

/** The scripted depth-v2 beats, compressed for a live demo cadence. */
const MOCK_SCRIPT: ScriptStep[] = [
  {
    at: 0,
    apply: (s) => {
      s.agentState = 'listening';
      s.question = DEMO_QUESTION;
      s.caption = '';
      s.proactive = false;
      s.silenceReason = null;
      s.contradiction = false;
      s.hops = [];
      s.memory = [];
      s.speaker = SPEAKER_COUNSEL;
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
      // THE SNAP — the warehouse admission in the deposition.
      s.agentState = 'speaking';
      s.activeCitation = ANSWER_CITATION;
      s.citations = [ANSWER_CITATION];
      s.primaryId = ANSWER_CITATION.id;
      s.caption = ANSWER_TRANSCRIPT;
      s.proactive = false;
      s.lastLatencyMs = ANSWER_CITATION.latencyMs;
    },
  },
  {
    at: 6200,
    apply: (s) => {
      s.agentState = 'thinking';
      // The CANONICAL contradiction follow-up: the exact string the live backend
      // routes multi-hop and resolves to the pdf-p12-l1 + visitor-log cross-doc
      // pair (mock == live). The first ask stays single-hop above.
      s.question = DEMO_CONTRADICTION_QUESTION;
      s.caption = ANSWER_TRANSCRIPT;
      // Jump into the SEPARATE exhibit document.
      s.targetPage = CONTRADICTION_CITATION.bbox.page;
    },
  },
  {
    at: 7600,
    apply: (s) => {
      // The cross-document contradiction climax: a second box in the exhibit,
      // the contradiction banner + the hops trail.
      s.agentState = 'speaking';
      s.activeCitation = CONTRADICTION_CITATION;
      s.citations = [ANSWER_CITATION, CONTRADICTION_CITATION];
      s.primaryId = CONTRADICTION_CITATION.id;
      s.caption = CONTRADICTION_TRANSCRIPT;
      s.contradiction = true;
      s.hops = CONTRADICTION_HOPS;
      s.proactive = false;
      s.lastLatencyMs = CONTRADICTION_CITATION.latencyMs;
    },
  },
  // ---- MEMORY-RECALL BEAT: the agent references a prior citation via a recall
  // chip ("as we saw on page 12") instead of re-snapping the box.
  {
    at: 11800,
    apply: (s) => {
      s.agentState = 'thinking';
      s.question = 'So does the warehouse alibi still hold?';
      s.caption = '';
      s.speaker = SPEAKER_COUNSEL;
    },
  },
  {
    at: 13000,
    apply: (s) => {
      s.agentState = 'speaking';
      s.caption = MEMORY_TRANSCRIPT;
      s.memory = [MEMORY_RECALL];
      // No re-snap: keep the contradiction box on screen.
      s.contradiction = true;
    },
  },
  // ---- MEETING-MODE PROACTIVE BEAT: a speaker utters a claim aloud; the
  // co-pilot surfaces the contradicting exhibit UNPROMPTED, tagged with the speaker.
  {
    at: 17200,
    apply: (s) => {
      s.agentState = 'listening';
      s.question = PROACTIVE_CLAIM;
      s.caption = '';
      s.activeCitation = null;
      s.proactive = false;
      s.silenceReason = null;
      s.memory = [];
      s.speaker = SPEAKER_WITNESS;
    },
  },
  {
    at: 18400,
    apply: (s) => {
      // No question was asked — the agent surfaces this on its own, tagged with
      // the witness who triggered it (meeting mode).
      s.agentState = 'speaking';
      s.activeCitation = PROACTIVE_CITATION;
      s.citations = [ANSWER_CITATION, CONTRADICTION_CITATION, PROACTIVE_CITATION];
      s.primaryId = PROACTIVE_CITATION.id;
      s.caption = PROACTIVE_TRANSCRIPT;
      s.targetPage = PROACTIVE_CITATION.bbox.page;
      s.proactive = true;
      s.speaker = SPEAKER_WITNESS;
      s.lastLatencyMs = PROACTIVE_CITATION.latencyMs;
    },
  },
  // ---- HONEST-SILENCE BEAT: a claim with no grounded source. No box; stay quiet.
  {
    at: 22800,
    apply: (s) => {
      s.agentState = 'thinking';
      s.question = NOT_FOUND_CLAIM;
      s.caption = '';
      s.proactive = false;
      s.speaker = SPEAKER_COUNSEL;
    },
  },
  {
    at: 24100,
    apply: (s) => {
      s.agentState = 'idle';
      s.activeCitation = null;
      s.silenceReason = 'not_found_in_document';
      s.caption = NOT_FOUND_TRANSCRIPT;
      s.proactive = false;
    },
  },
  {
    at: 28300,
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
  primaryId: null,
  contradiction: false,
  hops: [],
  memory: [],
  speaker: null,
  targetPage: 1,
  proactive: false,
  lastLatencyMs: undefined,
  silenceReason: null,
};

export function useCrossExam(config: CrossExamConfig): CrossExamState {
  const isMock = useMemo(
    () => config.forceMock === true || !config.livekitUrl || !config.livekitToken,
    [config.forceMock, config.livekitUrl, config.livekitToken],
  );

  const [snap, setSnap] = useState<MutableSnapshot>(() => ({ ...IDLE_SNAPSHOT }));
  const [isConnected, setIsConnected] = useState(false);
  // The agent's remote (TTS) audio track, surfaced as a MediaStream for the orb.
  const [outputStream, setOutputStream] = useState<MediaStream | null>(null);
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
          const next: MutableSnapshot = {
            ...prev,
            citations: [...prev.citations],
            hops: [...prev.hops],
            memory: [...prev.memory],
          };
          step.apply(next);
          return next;
        });
      }, step.at);
      timers.current.push(t);
    }
  }, [clearTimers]);

  // LIVE mode: connect to LiveKit lazily and mirror agent state + frame data.
  useEffect(() => {
    if (isMock) {
      setIsConnected(false);
      setOutputStream(null);
      return;
    }
    let disposed = false;
    let cleanup: (() => void) | undefined;

    void (async () => {
      try {
        const { Room, RoomEvent, Track } = await import('livekit-client');
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

        // Surface the agent's remote audio (TTS) track to the orb. A remote
        // MICROPHONE/audio track maps to a one-track MediaStream for metering.
        const onTrackSubscribed = (track: { kind: string; mediaStreamTrack?: MediaStreamTrack }): void => {
          if (disposed) return;
          if (track.kind === Track.Kind.Audio && track.mediaStreamTrack) {
            setOutputStream(new MediaStream([track.mediaStreamTrack]));
          }
        };
        const onTrackUnsubscribed = (track: { kind: string }): void => {
          if (track.kind === Track.Kind.Audio) setOutputStream(null);
        };
        room.on(RoomEvent.TrackSubscribed, onTrackSubscribed);
        room.on(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed);

        cleanup = () => {
          room.off(RoomEvent.DataReceived, onData);
          room.off(RoomEvent.TrackSubscribed, onTrackSubscribed);
          room.off(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed);
          setOutputStream(null);
          void room.disconnect();
        };
      } catch {
        // Fall back gracefully: stay in a connected=false state, UI still renders.
        if (!disposed) {
          setIsConnected(false);
          setOutputStream(null);
        }
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
    primaryId: snap.primaryId,
    contradiction: snap.contradiction,
    hops: snap.hops,
    memory: snap.memory,
    speaker: snap.speaker,
    targetPage: snap.targetPage,
    totalPages: DEMO_TOTAL_PAGES,
    proactive: snap.proactive,
    lastLatencyMs: snap.lastLatencyMs,
    silenceReason: snap.silenceReason,
    outputStream,
    runDemo,
    reset,
  };
}

/**
 * Narrow an unknown LiveKit data payload into a state update. Defensive by design.
 *
 * Parses the shared depth-v2 wire frame EXACTLY (see /docs/depth-v2-contract.md):
 *   { citations: Citation[], primaryId?, contradiction?, hops?, memory?,
 *     speaker?, proactive?, latencyMs?, reason?, agentState?, caption? }
 *
 * Branches:
 *   1. Non-empty `citations[]` => snap the primary (primaryId, else first),
 *      capture contradiction/hops/memory/speaker/proactive/latencyMs, clear
 *      silence, and merge into the running session citation list.
 *   2. Empty `citations[]` + `reason: "not_found_in_document"` => honest silence:
 *      clear the box, set the silence reason (still capture latencyMs).
 *   3. Neither => only the scalar fields (agentState/caption/speaker/latency) apply.
 */
function handleLivePayload(
  parsed: unknown,
  setSnap: React.Dispatch<React.SetStateAction<MutableSnapshot>>,
): void {
  if (typeof parsed !== 'object' || parsed === null) return;
  const msg = parsed as Record<string, unknown>;

  // Frame-level retrieval latency (persists across snaps via lastLatencyMs).
  const latencyMs = typeof msg.latencyMs === 'number' ? msg.latencyMs : undefined;
  const proactive = msg.proactive === true;
  const contradiction = msg.contradiction === true;
  const speaker = isSpeaker(msg.speaker) ? msg.speaker : null;
  const hops = parseHops(msg.hops);
  const memory = parseMemory(msg.memory);

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
  if (speaker) {
    setSnap((prev) => ({ ...prev, speaker }));
  }

  // The citations array — the heart of the depth-v2 frame.
  const citations = Array.isArray(msg.citations)
    ? (msg.citations as unknown[]).filter(isCitation)
    : [];

  if (citations.length > 0) {
    const primaryIdRaw = typeof msg.primaryId === 'string' ? msg.primaryId : undefined;
    const primary = citations.find((c) => c.id === primaryIdRaw) ?? citations[0];
    if (!primary) return;

    setSnap((prev) => {
      // Merge this turn's citations into the running session list (de-duped by id).
      const merged = [...prev.citations];
      for (const c of citations) {
        const idx = merged.findIndex((m) => m.id === c.id);
        if (idx === -1) merged.push(c);
        else merged[idx] = c;
      }
      return {
        ...prev,
        activeCitation: primary,
        agentState: 'speaking',
        targetPage: primary.bbox.page,
        primaryId: primary.id,
        contradiction,
        hops: hops ?? prev.hops,
        memory: memory ?? [],
        speaker: speaker ?? prev.speaker,
        proactive,
        silenceReason: null,
        lastLatencyMs: latencyMs ?? primary.latencyMs ?? prev.lastLatencyMs,
        citations: merged,
      };
    });
    return;
  }

  // A memory-only turn: recalls referenced without re-snapping a box.
  if (memory && memory.length > 0) {
    setSnap((prev) => ({
      ...prev,
      memory,
      contradiction: contradiction || prev.contradiction,
      hops: hops ?? prev.hops,
      proactive: false,
      silenceReason: null,
      lastLatencyMs: latencyMs ?? prev.lastLatencyMs,
    }));
    return;
  }

  // Honest-silence: empty citations with a not_found reason. The agent stayed
  // quiet rather than show a wrong box — clear any prior box, flag silence.
  if (msg.reason === 'not_found_in_document') {
    setSnap((prev) => ({
      ...prev,
      activeCitation: null,
      primaryId: null,
      contradiction: false,
      hops: [],
      memory: [],
      proactive: false,
      silenceReason: 'not_found_in_document',
      lastLatencyMs: latencyMs ?? prev.lastLatencyMs,
    }));
    return;
  }

  // A frame may carry latency without citations (e.g. a retrieval heartbeat).
  if (latencyMs !== undefined) {
    setSnap((prev) => ({ ...prev, lastLatencyMs: latencyMs }));
  }
}

function isBBoxLike(b: unknown): boolean {
  if (typeof b !== 'object' || b === null) return false;
  const bb = b as Record<string, unknown>;
  return (
    typeof bb.page === 'number' &&
    typeof bb.x0 === 'number' &&
    typeof bb.y0 === 'number' &&
    typeof bb.x1 === 'number' &&
    typeof bb.y1 === 'number'
  );
}

function isCitation(value: unknown): value is Citation {
  if (typeof value !== 'object' || value === null) return false;
  const c = value as Record<string, unknown>;
  return (
    typeof c.id === 'string' &&
    typeof c.text === 'string' &&
    typeof c.confidence === 'number' &&
    typeof c.documentId === 'string' &&
    isBBoxLike(c.bbox)
  );
}

function isSpeaker(value: unknown): value is Speaker {
  if (typeof value !== 'object' || value === null) return false;
  const s = value as Record<string, unknown>;
  return typeof s.id === 'string' && typeof s.label === 'string';
}

function parseHops(value: unknown): HopTrace[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const hops: HopTrace[] = [];
  for (const h of value) {
    if (typeof h !== 'object' || h === null) continue;
    const hh = h as Record<string, unknown>;
    if (typeof hh.subQuery !== 'string') continue;
    const ids = Array.isArray(hh.citationIds)
      ? hh.citationIds.filter((x): x is string => typeof x === 'string')
      : [];
    hops.push({ subQuery: hh.subQuery, citationIds: ids });
  }
  return hops;
}

function parseMemory(value: unknown): MemoryRef[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const refs: MemoryRef[] = [];
  for (const m of value) {
    if (typeof m !== 'object' || m === null) continue;
    const mm = m as Record<string, unknown>;
    if (
      mm.kind === 'recall' &&
      typeof mm.citationId === 'string' &&
      typeof mm.documentId === 'string' &&
      typeof mm.page === 'number' &&
      typeof mm.note === 'string'
    ) {
      refs.push({
        kind: 'recall',
        citationId: mm.citationId,
        documentId: mm.documentId,
        page: mm.page,
        note: mm.note,
      });
    }
  }
  return refs;
}
