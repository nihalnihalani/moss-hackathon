/**
 * Shared motion helpers. Centralizes the prefers-reduced-motion check so any
 * JS-driven animation (count-ups, programmatic scrolling) can honor the user's
 * OS-level "reduce motion" preference.
 */

/** True when the user has asked the OS to minimize non-essential motion. */
export const prefersReducedMotion = (): boolean =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * ScrollBehavior to use for programmatic scrollIntoView: 'auto' (instant) when
 * reduced motion is preferred, otherwise 'smooth'.
 */
export const scrollBehavior = (): ScrollBehavior =>
  prefersReducedMotion() ? 'auto' : 'smooth';
