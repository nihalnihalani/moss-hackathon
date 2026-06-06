import { useEffect, useRef } from 'react';

/**
 * Background atmosphere — three stacked layers of depth behind the whole app:
 *  1. an animated per-pixel canvas grain (low alpha, pixelated, screen-blended),
 *  2. a fine dot-grid (CSS),
 *  3. one cold radial spotlight top-center (CSS).
 *
 * The grain is the only animated part; it regenerates a small noise tile a few
 * times a second and lets the browser scale it up `pixelated` so it stays cheap.
 * Entirely decorative + `pointer-events:none`, and frozen under reduced-motion.
 */
export function Atmosphere(): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Small tile, upscaled by CSS (image-rendering: pixelated) for cheap grain.
    const TILE = 128;
    canvas.width = TILE;
    canvas.height = TILE;

    const reduce =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let raf = 0;
    let last = 0;

    const paint = (): void => {
      const image = ctx.createImageData(TILE, TILE);
      const data = image.data;
      for (let i = 0; i < data.length; i += 4) {
        const v = (Math.random() * 255) | 0;
        data[i] = v;
        data[i + 1] = v;
        data[i + 2] = v;
        data[i + 3] = 255;
      }
      ctx.putImageData(image, 0, 0);
    };

    paint();
    if (reduce) return;

    const loop = (now: number): void => {
      // ~12fps is plenty for film grain and keeps the main thread quiet.
      if (now - last > 80) {
        paint();
        last = now;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="atmosphere" aria-hidden="true">
      <canvas ref={canvasRef} className="atmosphere__grain" />
      <div className="atmosphere__grid" />
      <div className="atmosphere__spot" />
    </div>
  );
}
