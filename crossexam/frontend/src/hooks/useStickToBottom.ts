/**
 * useStickToBottom — keep a scroll container pinned to its newest content, but
 * yield control the moment the user scrolls up to read back.
 *
 * Behaviour (matches the v3 spec, Quick Win #3):
 *  - While the user is at (or within `threshold` px of) the bottom, the hook
 *    auto-scrolls to the bottom whenever `dep` changes (new caption arrives).
 *  - If the user scrolls up past the threshold, auto-scroll STOPS and `atBottom`
 *    flips false so the caller can show a "Jump to latest" affordance.
 *  - `scrollToBottom()` re-pins and resumes sticking.
 *  - prefers-reduced-motion => instant jumps (no smooth scroll).
 *
 * The threshold is a band (never exact equality) so sub-pixel rounding and
 * fractional device-pixel ratios don't cause flicker. Default 32px per spec.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { prefersReducedMotion } from '../lib/motion';

export interface StickToBottom<T extends HTMLElement> {
  /** Attach to the scrollable container. */
  ref: React.RefObject<T>;
  /** True when the viewport is within `threshold` px of the bottom. */
  atBottom: boolean;
  /** Programmatically scroll to the bottom and resume sticking. */
  scrollToBottom: () => void;
}

export interface UseStickToBottomOptions {
  /**
   * A value that changes when new content is appended (e.g. the caption text or
   * a content length). When it changes AND we're stuck, we scroll to bottom.
   */
  dep: unknown;
  /** Distance from the bottom (px) still considered "at bottom". Default 32. */
  threshold?: number;
}

/** Compute whether a scroll container is within `threshold` px of its bottom. */
export function isNearBottom(el: HTMLElement, threshold: number): boolean {
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
  return distance <= threshold;
}

export function useStickToBottom<T extends HTMLElement>({
  dep,
  threshold = 32,
}: UseStickToBottomOptions): StickToBottom<T> {
  const ref = useRef<T>(null);
  const [atBottom, setAtBottom] = useState(true);
  // Source of truth for "should we auto-stick" — kept in a ref so the dep-effect
  // reads the latest value without re-subscribing.
  const stuckRef = useRef(true);

  const scrollToBottom = useCallback((): void => {
    const el = ref.current;
    if (!el) return;
    pinToBottom(el);
    stuckRef.current = true;
    setAtBottom(true);
  }, []);

  // Track the user's scroll position; flip stickiness when they leave/return.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = (): void => {
      const near = isNearBottom(el, threshold);
      stuckRef.current = near;
      setAtBottom((prev) => (prev === near ? prev : near));
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    // Initialise from current geometry.
    onScroll();
    return () => el.removeEventListener('scroll', onScroll);
  }, [threshold]);

  // New content arrived: stick to bottom only if the user hasn't scrolled away.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (stuckRef.current) {
      pinToBottom(el);
      // Geometry may not have grown yet in a layout sense; assert atBottom.
      setAtBottom(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dep]);

  return { ref, atBottom, scrollToBottom };
}

/**
 * Scroll an element to its bottom, honoring reduced-motion. Feature-detects
 * `scrollTo` (jsdom omits it) and falls back to assigning `scrollTop`.
 */
function pinToBottom(el: HTMLElement): void {
  const top = el.scrollHeight;
  if (typeof el.scrollTo === 'function') {
    el.scrollTo({ top, behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
  } else {
    el.scrollTop = top;
  }
}
