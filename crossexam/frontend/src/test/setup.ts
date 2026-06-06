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
      createRadialGradient: () => ({ addColorStop: noop }),
      createLinearGradient: () => ({ addColorStop: noop }),
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

if (!window.requestAnimationFrame) {
  window.requestAnimationFrame = (cb: FrameRequestCallback): number =>
    setTimeout(() => cb(performance.now()), 0) as unknown as number;
  window.cancelAnimationFrame = (id: number): void => clearTimeout(id);
}
