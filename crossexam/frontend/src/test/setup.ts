import '@testing-library/jest-dom/vitest';

// jsdom's canvas 2D context throws "Not implemented". Always override with a
// minimal stub so components that draw a placeholder page (PdfCanvas, VoiceOrb)
// render without throwing.
{
  const noop = (): void => undefined;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (HTMLCanvasElement.prototype as any).getContext = function getContext(): unknown {
    return {
      canvas: this,
      setTransform: noop,
      scale: noop,
      clearRect: noop,
      fillRect: noop,
      fillText: noop,
      beginPath: noop,
      arc: noop,
      fill: noop,
      createImageData: (w: number, h: number) => ({
        data: new Uint8ClampedArray(w * h * 4),
        width: w,
        height: h,
      }),
      putImageData: noop,
      createRadialGradient: () => ({ addColorStop: noop }),
      createLinearGradient: () => ({ addColorStop: noop }),
      createConicGradient: () => ({ addColorStop: noop }),
      set fillStyle(_v: unknown) {},
      get fillStyle() {
        return '#000';
      },
      set font(_v: unknown) {},
      get font() {
        return '';
      },
    };
  };
}

if (!('devicePixelRatio' in window)) {
  Object.defineProperty(window, 'devicePixelRatio', { value: 1, configurable: true });
}

// jsdom doesn't implement scrollIntoView; the PdfCanvas re-centers the box with it.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = (): void => undefined;
}

// jsdom doesn't implement Element.scrollTo; the stick-to-bottom transcript uses it.
if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = (): void => undefined;
}

// matchMedia is consulted by motion-aware components (LatencyChip, Atmosphere).
if (typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}

if (!window.requestAnimationFrame) {
  window.requestAnimationFrame = (cb: FrameRequestCallback): number =>
    setTimeout(() => cb(performance.now()), 0) as unknown as number;
  window.cancelAnimationFrame = (id: number): void => clearTimeout(id);
}
