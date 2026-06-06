/**
 * useAudioLevel — drive the VoiceOrb from REAL WebAudio amplitude (feat 4).
 *
 * Returns a smoothed 0..1 RMS level sampled from an AnalyserNode:
 *  - In `listening`, the source is the microphone (getUserMedia).
 *  - In `speaking`, the source is the supplied output stream (TTS) when present.
 *  - Otherwise the hook is dormant and reports 0, so the orb falls back to its
 *    pure-CSS state animation (mock / no-audio).
 *
 * Fully guarded: if `getUserMedia` is denied/unavailable, WebAudio is missing, or
 * the user prefers reduced motion, the hook never throws and reports 0 (static
 * orb). All resources are torn down on deactivate/unmount.
 */

import { useEffect, useRef, useState } from 'react';
import type { AgentState } from '../types';
import { prefersReducedMotion } from '../lib/motion';

export interface UseAudioLevelOptions {
  /** Current agent state — decides whether we listen to mic vs. an output stream. */
  state: AgentState;
  /** Master switch — pass false in mock mode to keep the orb on CSS animation. */
  enabled: boolean;
  /**
   * Optional output (TTS) MediaStream to meter while `speaking`. When absent,
   * the speaking orb falls back to its CSS animation.
   */
  outputStream?: MediaStream | null;
}

/** WebAudio constructor surface, including the webkit-prefixed fallback. */
type AudioCtxCtor = typeof AudioContext;

function getAudioContextCtor(): AudioCtxCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    AudioContext?: AudioCtxCtor;
    webkitAudioContext?: AudioCtxCtor;
  };
  return w.AudioContext ?? w.webkitAudioContext ?? null;
}

/**
 * @returns level — a smoothed amplitude in [0,1]; 0 when dormant or unavailable.
 */
export function useAudioLevel({ state, enabled, outputStream }: UseAudioLevelOptions): number {
  const [level, setLevel] = useState(0);
  const rafRef = useRef<number>(0);
  const smoothedRef = useRef(0);

  useEffect(() => {
    // Decide the source: mic while listening, output stream while speaking.
    const wantMic = state === 'listening';
    const wantOutput = state === 'speaking' && !!outputStream;
    const active = enabled && !prefersReducedMotion() && (wantMic || wantOutput);

    if (!active) {
      setLevel(0);
      smoothedRef.current = 0;
      return;
    }

    const Ctor = getAudioContextCtor();
    if (!Ctor) {
      setLevel(0);
      return;
    }

    let disposed = false;
    let ctx: AudioContext | null = null;
    let analyser: AnalyserNode | null = null;
    let micStream: MediaStream | null = null;
    let source: MediaStreamAudioSourceNode | null = null;

    const start = (stream: MediaStream): void => {
      if (disposed) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      try {
        ctx = new Ctor();
        analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.8;
        source = ctx.createMediaStreamSource(stream);
        source.connect(analyser);

        const buffer = new Uint8Array(analyser.frequencyBinCount);
        const tick = (): void => {
          if (disposed || !analyser) return;
          analyser.getByteTimeDomainData(buffer);
          // RMS around the 128 midpoint, normalized to ~[0,1].
          let sum = 0;
          for (let i = 0; i < buffer.length; i++) {
            const v = ((buffer[i] ?? 128) - 128) / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / buffer.length);
          const norm = Math.min(1, rms * 2.4); // gentle gain so speech reads clearly
          // Exponential smoothing for a calm, breathing response.
          smoothedRef.current = smoothedRef.current * 0.75 + norm * 0.25;
          setLevel(smoothedRef.current);
          rafRef.current = requestAnimationFrame(tick);
        };
        rafRef.current = requestAnimationFrame(tick);
      } catch {
        /* WebAudio wiring failed — stay silent (orb falls back to CSS). */
        setLevel(0);
      }
    };

    if (wantOutput && outputStream) {
      start(outputStream);
    } else if (
      wantMic &&
      typeof navigator !== 'undefined' &&
      navigator.mediaDevices?.getUserMedia
    ) {
      navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then((stream) => {
          micStream = stream;
          start(stream);
        })
        .catch(() => {
          // Permission denied / no device — silent fallback.
          if (!disposed) setLevel(0);
        });
    } else {
      setLevel(0);
    }

    return () => {
      disposed = true;
      cancelAnimationFrame(rafRef.current);
      try {
        source?.disconnect();
        analyser?.disconnect();
      } catch {
        /* ignore */
      }
      // Only stop tracks we own (the mic); never stop a caller-owned outputStream.
      micStream?.getTracks().forEach((t) => t.stop());
      void ctx?.close().catch(() => undefined);
      smoothedRef.current = 0;
    };
  }, [state, enabled, outputStream]);

  return level;
}
