import type { AgentState } from '../types';

const LABELS: Record<AgentState, string> = {
  idle: 'READY',
  listening: 'LISTENING',
  thinking: 'THINKING',
  speaking: 'SPEAKING',
};

export interface StatePillProps {
  state: AgentState;
}

/** The agent-state badge that flips LISTENING / THINKING / SPEAKING during the demo. */
export function StatePill({ state }: StatePillProps): JSX.Element {
  return (
    <div
      className={`state-pill state-pill--${state}`}
      role="status"
      aria-live="polite"
      aria-label={`Agent state: ${LABELS[state]}`}
    >
      <span className="state-pill__dot" aria-hidden="true" />
      <span className="state-pill__label">{LABELS[state]}</span>
    </div>
  );
}
