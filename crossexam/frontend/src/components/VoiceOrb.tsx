import type { AgentState } from '../types';

export interface VoiceOrbProps {
  state: AgentState;
  /** 0..1 amplitude to drive a live scale; when present, nudges the orb size. */
  level?: number;
}

/**
 * Siri-style voice orb — pure CSS. Two blurred, saturated, rotating layers
 * (a conic gradient + a radial highlight) over a soft core. State is driven
 * entirely from CSS custom properties on `.orb--{state}` (animation duration,
 * blur, hue, scale), so there's no canvas/RAF cost. Audio-reactive `level`
 * gently scales the orb when a real RMS is supplied (live mode).
 *
 * Under prefers-reduced-motion the layers stop and collapse to a static,
 * state-colored dot (handled in styles.css) — feedback without movement.
 *
 * Purely decorative (aria-hidden): the StatePill is the single source of
 * spoken state for screen readers, so the orb doesn't double-announce.
 */
export function VoiceOrb({ state, level }: VoiceOrbProps): JSX.Element {
  const reactiveScale =
    typeof level === 'number' ? { '--scale': 1 + Math.min(Math.max(level, 0), 1) * 0.12 } : undefined;

  return (
    <div
      className={`orb orb--${state}`}
      aria-hidden="true"
      style={reactiveScale as React.CSSProperties | undefined}
    >
      <span className="orb__layer orb__layer--conic" aria-hidden="true" />
      <span className="orb__layer orb__layer--radial" aria-hidden="true" />
      <span className="orb__core" aria-hidden="true" />
    </div>
  );
}
