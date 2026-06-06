import type { AgentState } from '../types';

/** Forensic HUD wording — the demo flips LISTENING / ANALYZING / CITING. */
const LABELS: Record<AgentState, string> = {
  idle: 'STANDBY',
  listening: 'LISTENING',
  thinking: 'ANALYZING',
  speaking: 'CITING',
};

export interface StatePillProps {
  state: AgentState;
}

/**
 * Mono HUD state readout with a ping-dot (an expanding ring behind a solid dot).
 * Hue tracks the agent state; announced politely for screen readers.
 */
export function StatePill({ state }: StatePillProps): JSX.Element {
  return (
    <div
      className={`state-pill state-pill--${state}`}
      role="status"
      aria-live="polite"
      aria-label={`Agent state: ${LABELS[state]}`}
    >
      <span className="state-pill__dotwrap" aria-hidden="true">
        <span className="state-pill__ping" />
        <span className="state-pill__dot" />
      </span>
      <span className="state-pill__label">{LABELS[state]}</span>
    </div>
  );
}
