import { prefersReducedMotion } from '../lib/motion';

export interface LiveLatencyBadgeProps {
  /**
   * The LAST retrieval latency in ms, persisted across snaps. Undefined until the
   * first retrieval lands (badge shows a dormant "retrieval —" placeholder).
   */
  latencyMs: number | undefined;
}

/**
 * The persistent, always-on retrieval-latency readout — the proof-on-screen that
 * the agent is grounding fast. Distinct from the per-snap LatencyChip ("found in
 * 912 pages · 7ms"): this one lives in the command bar, persists, and shows the
 * LAST retrieval ms live. Mono tabular-nums with an amber pulse dot.
 *
 * Under prefers-reduced-motion the dot holds steady (no pulse) — the number is
 * shown directly with no animation, per the spec.
 */
export function LiveLatencyBadge({ latencyMs }: LiveLatencyBadgeProps): JSX.Element {
  const reduced = prefersReducedMotion();
  const hasValue = typeof latencyMs === 'number';

  return (
    <div
      className={`live-latency${hasValue ? ' live-latency--active' : ''}${reduced ? ' live-latency--reduced' : ''}`}
      role="status"
      aria-live="polite"
      aria-label={
        hasValue ? `Last retrieval ${latencyMs}ms` : 'Awaiting first retrieval'
      }
      data-testid="live-latency"
    >
      <span className="live-latency__dot" aria-hidden="true" />
      <span className="live-latency__label">retrieval</span>
      <span className="live-latency__value" data-testid="live-latency-value">
        {hasValue ? `${latencyMs}ms` : '—'}
      </span>
    </div>
  );
}
