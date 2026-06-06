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
  /**
   * The agent just acted UNPROMPTED. Adds a subtle ambient pulse + an "UNPROMPTED"
   * marker so it's unmistakable the agent surfaced something without being asked.
   */
  proactive?: boolean;
}

/**
 * Mono HUD state readout with a ping-dot (an expanding ring behind a solid dot).
 * Hue tracks the agent state; announced politely for screen readers.
 *
 * When `proactive` is set the pill gains an ambient glow + an UNPROMPTED tag,
 * signalling the agent acted on its own.
 */
export function StatePill({ state, proactive = false }: StatePillProps): JSX.Element {
  return (
    <div
      className={`state-pill state-pill--${state}${proactive ? ' state-pill--ambient' : ''}`}
      role="status"
      aria-live="polite"
      aria-label={`Agent state: ${LABELS[state]}${proactive ? ', acting unprompted' : ''}`}
      data-proactive={proactive ? 'true' : undefined}
    >
      <span className="state-pill__dotwrap" aria-hidden="true">
        <span className="state-pill__ping" />
        <span className="state-pill__dot" />
      </span>
      <span className="state-pill__label">{LABELS[state]}</span>
      {proactive ? <span className="state-pill__ambient">unprompted</span> : null}
    </div>
  );
}
