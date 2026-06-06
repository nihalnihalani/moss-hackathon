import { useEffect, useRef, useState } from 'react';

export interface LatencyChipProps {
  /** Total corpus pages searched. */
  pages: number;
  /** Retrieval latency in milliseconds. */
  latencyMs: number;
  /** Show the chip only after a result has landed. */
  visible: boolean;
}

const prefersReducedMotion = (): boolean =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Ease toward the target so the numbers feel like they *resolve*, not tick. */
function useCountUp(target: number, run: boolean): number {
  const [value, setValue] = useState(run ? 0 : target);
  const raf = useRef<number>(0);

  useEffect(() => {
    cancelAnimationFrame(raf.current);
    if (!run) {
      setValue(target);
      return;
    }
    if (prefersReducedMotion()) {
      setValue(target);
      return;
    }
    const DURATION = 520;
    const start = performance.now();
    const tick = (now: number): void => {
      const t = Math.min(1, (now - start) / DURATION);
      // easeOutCubic
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(target * eased));
      if (t < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [target, run]);

  return value;
}

/** The Moss-as-hero chip: count-up to "found in 912 pages · 7ms". */
export function LatencyChip({ pages, latencyMs, visible }: LatencyChipProps): JSX.Element | null {
  const shownPages = useCountUp(pages, visible);
  const shownMs = useCountUp(latencyMs, visible);

  if (!visible) return null;
  return (
    <div className="glass-pill latency-chip" role="status" aria-live="polite">
      <span className="latency-chip__dot" aria-hidden="true" />
      <span className="latency-chip__nums">
        found in {shownPages.toLocaleString()} pages&nbsp;·&nbsp;<strong>{shownMs}ms</strong>
      </span>
    </div>
  );
}
