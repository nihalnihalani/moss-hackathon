export interface LatencyChipProps {
  /** Total corpus pages searched. */
  pages: number;
  /** Retrieval latency in milliseconds. */
  latencyMs: number;
  /** Show the chip only after a result has landed. */
  visible: boolean;
}

/** The Moss-as-hero chip: "found in 912 pages · 7ms". */
export function LatencyChip({ pages, latencyMs, visible }: LatencyChipProps): JSX.Element | null {
  if (!visible) return null;
  return (
    <div className="latency-chip" role="status" aria-live="polite">
      <span className="latency-chip__spark" aria-hidden="true" />
      found in {pages.toLocaleString()} pages&nbsp;·&nbsp;
      <strong>{latencyMs}ms</strong>
    </div>
  );
}
