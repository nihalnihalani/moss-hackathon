import type { AgentState } from '../types';
import { useAudioLevel } from '../hooks/useAudioLevel';

export interface VoiceOrbProps {
  state: AgentState;
  /**
   * Drive the orb from REAL WebAudio (feat 4): mic level while listening, the
   * output (TTS) stream while speaking. When false (mock / no audio) the orb
   * falls back to its pure-CSS state animation. Default false.
   */
  audioReactive?: boolean;
  /** Optional output (TTS) MediaStream to meter while speaking. */
  outputStream?: MediaStream | null;
  /**
   * Explicit 0..1 level override (e.g. for tests/storybook). When provided, the
   * internal WebAudio meter is bypassed.
   */
  level?: number;
}

/**
 * Siri-style voice orb — pure CSS. Two blurred, saturated, rotating layers
 * (a conic gradient + a radial highlight) over a soft core. State is driven
 * entirely from CSS custom properties on `.orb--{state}` (animation duration,
 * blur, hue, scale), so there's no canvas/RAF cost in the idle path.
 *
 * Audio-reactive (feat 4): when `audioReactive`, the orb subscribes to a real
 * AnalyserNode RMS via useAudioLevel — mic input while listening, the TTS output
 * stream while speaking — and gently scales with it. The hook is fully guarded
 * (no crash if getUserMedia is denied) and honors prefers-reduced-motion (it
 * reports 0, so the orb stays static).
 *
 * Under prefers-reduced-motion the layers stop and collapse to a static,
 * state-colored dot (handled in styles.css) — feedback without movement.
 *
 * Purely decorative (aria-hidden): the StatePill is the single source of
 * spoken state for screen readers, so the orb doesn't double-announce.
 */
export function VoiceOrb({
  state,
  audioReactive = false,
  outputStream = null,
  level,
}: VoiceOrbProps): JSX.Element {
  const measured = useAudioLevel({
    state,
    enabled: audioReactive && typeof level !== 'number',
    outputStream,
  });
  const effectiveLevel = typeof level === 'number' ? level : measured;

  // Map amplitude to a gentle scale bump on top of the per-state base scale.
  const reactiveScale =
    effectiveLevel > 0
      ? ({ '--scale-audio': 1 + Math.min(Math.max(effectiveLevel, 0), 1) * 0.16 } as React.CSSProperties)
      : undefined;

  return (
    <div
      className={`orb orb--${state}${effectiveLevel > 0 ? ' orb--reactive' : ''}`}
      aria-hidden="true"
      data-testid="voice-orb"
      style={reactiveScale}
    >
      <span className="orb__layer orb__layer--conic" aria-hidden="true" />
      <span className="orb__layer orb__layer--radial" aria-hidden="true" />
      <span className="orb__core" aria-hidden="true" />
    </div>
  );
}
