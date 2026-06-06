/**
 * useStickToBottom.test.tsx — the transcript stick-to-bottom behaviour (Quick Win #3).
 *
 * jsdom doesn't lay out / scroll, so we drive the scroll geometry directly:
 * scrollHeight/clientHeight are defined per element, scrollTop is writable, and
 * scrollTo is captured. We then assert that NEW content pins to the bottom only
 * when the user is at the bottom, and that the atBottom flag flips on scroll.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useState } from 'react';
import { render, screen, act, fireEvent } from '@testing-library/react';
import { useStickToBottom, isNearBottom } from '../useStickToBottom';

/** Install a controllable scroll geometry on an element. */
function setGeometry(el: HTMLElement, scrollHeight: number, clientHeight: number, scrollTop = 0): void {
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true });
  Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true });
  let _top = scrollTop;
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => _top,
    set: (v: number) => {
      _top = v;
    },
  });
}

/** Test harness exposing the hook + a button to append "content". */
function Harness(): JSX.Element {
  const [count, setCount] = useState(0);
  const { ref, atBottom, scrollToBottom } = useStickToBottom<HTMLDivElement>({ dep: count });
  return (
    <div>
      <div ref={ref} data-testid="scroller" />
      <span data-testid="at-bottom">{atBottom ? 'yes' : 'no'}</span>
      <button data-testid="append" onClick={() => setCount((c) => c + 1)}>
        append
      </button>
      <button data-testid="jump" onClick={scrollToBottom}>
        jump
      </button>
    </div>
  );
}

describe('isNearBottom', () => {
  it('treats a distance within the threshold as at-bottom', () => {
    const el = document.createElement('div');
    setGeometry(el, 1000, 400, 580); // distance = 1000 - 580 - 400 = 20
    expect(isNearBottom(el, 32)).toBe(true);
  });
  it('treats a larger distance as not at-bottom', () => {
    const el = document.createElement('div');
    setGeometry(el, 1000, 400, 400); // distance = 200
    expect(isNearBottom(el, 32)).toBe(false);
  });
});

describe('useStickToBottom', () => {
  let scrollToCalls: number;

  beforeEach(() => {
    scrollToCalls = 0;
  });

  function instrument(el: HTMLElement): void {
    Object.defineProperty(el, 'scrollTo', {
      configurable: true,
      value: (opts: ScrollToOptions) => {
        scrollToCalls += 1;
        if (typeof opts?.top === 'number') (el as HTMLElement).scrollTop = opts.top;
      },
    });
  }

  it('auto-scrolls to the bottom when new content arrives and the user is at the bottom', () => {
    render(<Harness />);
    const scroller = screen.getByTestId('scroller');
    // At bottom: distance 0.
    setGeometry(scroller, 1000, 400, 600);
    instrument(scroller);

    expect(screen.getByTestId('at-bottom').textContent).toBe('yes');

    act(() => {
      screen.getByTestId('append').click();
    });

    expect(scrollToCalls).toBeGreaterThan(0);
    expect(screen.getByTestId('at-bottom').textContent).toBe('yes');
  });

  it('does NOT auto-scroll when the user has scrolled up', () => {
    render(<Harness />);
    const scroller = screen.getByTestId('scroller');
    setGeometry(scroller, 1000, 400, 600);
    instrument(scroller);

    // User scrolls up: distance now 300 (> threshold) -> stickiness off.
    act(() => {
      (scroller as HTMLElement).scrollTop = 300;
      fireEvent.scroll(scroller);
    });
    expect(screen.getByTestId('at-bottom').textContent).toBe('no');

    const before = scrollToCalls;
    act(() => {
      screen.getByTestId('append').click();
    });
    // No new scroll-to-bottom while scrolled up.
    expect(scrollToCalls).toBe(before);
    expect(screen.getByTestId('at-bottom').textContent).toBe('no');
  });

  it('scrollToBottom re-pins and resumes sticking', () => {
    render(<Harness />);
    const scroller = screen.getByTestId('scroller');
    setGeometry(scroller, 1000, 400, 600);
    instrument(scroller);

    act(() => {
      (scroller as HTMLElement).scrollTop = 200;
      fireEvent.scroll(scroller);
    });
    expect(screen.getByTestId('at-bottom').textContent).toBe('no');

    act(() => {
      screen.getByTestId('jump').click();
    });
    expect(scrollToCalls).toBeGreaterThan(0);
    expect(screen.getByTestId('at-bottom').textContent).toBe('yes');

    // Now new content sticks again.
    const before = scrollToCalls;
    act(() => {
      screen.getByTestId('append').click();
    });
    expect(scrollToCalls).toBeGreaterThan(before);
  });
});
