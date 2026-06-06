import { useEffect, useRef, useState } from 'react';

export interface PageJumpProps {
  /** Whether the agent is actively searching (drives the spinning page counter). */
  active: boolean;
  /** The page we are jumping to. */
  targetPage: number;
  /** Total corpus size, the denominator in "p.687 / 912". */
  totalPages: number;
}

/**
 * The "searching 912 pages" beat: while active, rapidly flips a page counter
 * toward the target, conveying that real work is happening across the corpus.
 */
export function PageJump({ active, targetPage, totalPages }: PageJumpProps): JSX.Element | null {
  const [display, setDisplay] = useState(1);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    cancelAnimationFrame(rafRef.current);
    if (!active) {
      setDisplay(targetPage);
      return;
    }
    let current = 1;
    let last = performance.now();
    const tick = (now: number): void => {
      if (now - last > 28) {
        // Accelerate toward the target so it feels like a scan, then settle.
        const step = Math.max(1, Math.floor((targetPage - current) / 6));
        current = Math.min(targetPage, current + step);
        setDisplay(current);
        last = now;
      }
      if (current < targetPage) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [active, targetPage]);

  if (!active) return null;
  return (
    <div className="page-jump" role="status" aria-live="off" aria-label="Searching pages">
      <span className="page-jump__label">searching</span>
      <span className="page-jump__counter">
        p.{display.toLocaleString()} <span className="page-jump__total">/ {totalPages.toLocaleString()}</span>
      </span>
    </div>
  );
}
