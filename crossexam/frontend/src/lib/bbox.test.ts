import { describe, it, expect } from 'vitest';
import {
  pdfBBoxToCanvasRect,
  clampBBoxToPage,
  normalizeBBox,
  toDevicePixels,
  rectCenter,
  BBoxError,
} from './bbox';
import type { BBox, PageRenderGeometry } from '../types';

// US Letter page in PDF points.
const LETTER: Pick<PageRenderGeometry, 'pageWidthPt' | 'pageHeightPt'> = {
  pageWidthPt: 612,
  pageHeightPt: 792,
};

function geom(over: Partial<PageRenderGeometry> = {}): PageRenderGeometry {
  return {
    ...LETTER,
    renderScale: 1,
    canvasOffset: { x: 0, y: 0 },
    devicePixelRatio: 1,
    ...over,
  };
}

const box: BBox = { page: 687, x0: 72, y0: 144, x1: 540, y1: 162 };

describe('pdfBBoxToCanvasRect', () => {
  it('is identity at scale 1, zero offset, dpr 1', () => {
    const r = pdfBBoxToCanvasRect(box, geom());
    expect(r).toEqual({ left: 72, top: 144, width: 468, height: 18 });
  });

  it('applies render scale to position and size', () => {
    const r = pdfBBoxToCanvasRect(box, geom({ renderScale: 2 }));
    expect(r).toEqual({ left: 144, top: 288, width: 936, height: 36 });
  });

  it('applies a fractional render scale', () => {
    const r = pdfBBoxToCanvasRect(box, geom({ renderScale: 1.5 }));
    expect(r).toEqual({ left: 108, top: 216, width: 702, height: 27 });
  });

  it('adds the canvas offset (page letterboxed inside the container)', () => {
    const r = pdfBBoxToCanvasRect(box, geom({ canvasOffset: { x: 40, y: 100 } }));
    expect(r).toEqual({ left: 112, top: 244, width: 468, height: 18 });
  });

  it('composes scale and offset (scale applies to point, offset added after)', () => {
    const r = pdfBBoxToCanvasRect(box, geom({ renderScale: 2, canvasOffset: { x: 40, y: 100 } }));
    // left = 72*2 + 40 = 184 ; top = 144*2 + 100 = 388
    expect(r).toEqual({ left: 184, top: 388, width: 936, height: 36 });
  });

  it('does NOT scale the CSS-pixel overlay by devicePixelRatio', () => {
    // The overlay is laid out in CSS px; dpr only affects the backing store.
    const base = pdfBBoxToCanvasRect(box, geom({ renderScale: 1.5, devicePixelRatio: 1 }));
    const hidpi = pdfBBoxToCanvasRect(box, geom({ renderScale: 1.5, devicePixelRatio: 2 }));
    expect(hidpi).toEqual(base);
  });

  it('known projector fixture: p.687 line @ 1.75x on a 2x display', () => {
    // Render scale 1.75 CSS-px/pt, page centered with a 24px gutter, Retina projector dpr 2.
    const r = pdfBBoxToCanvasRect(
      { page: 687, x0: 90, y0: 360, x1: 522, y1: 378 },
      geom({ renderScale: 1.75, canvasOffset: { x: 24, y: 12 }, devicePixelRatio: 2 }),
    );
    expect(r.left).toBeCloseTo(90 * 1.75 + 24, 6); // 181.5
    expect(r.top).toBeCloseTo(360 * 1.75 + 12, 6); // 642
    expect(r.width).toBeCloseTo((522 - 90) * 1.75, 6); // 756
    expect(r.height).toBeCloseTo((378 - 360) * 1.75, 6); // 31.5
    expect(r).toEqual({ left: 181.5, top: 642, width: 756, height: 31.5 });
  });

  it('throws on non-positive render scale', () => {
    expect(() => pdfBBoxToCanvasRect(box, geom({ renderScale: 0 }))).toThrow(BBoxError);
  });

  it('throws on non-finite geometry', () => {
    expect(() => pdfBBoxToCanvasRect(box, geom({ pageWidthPt: NaN }))).toThrow(BBoxError);
  });
});

describe('normalizeBBox', () => {
  it('orders corners regardless of input order', () => {
    const n = normalizeBBox({ page: 1, x0: 540, y0: 162, x1: 72, y1: 144 });
    expect(n).toEqual({ page: 1, x0: 72, y0: 144, x1: 540, y1: 162 });
  });
});

describe('clampBBoxToPage', () => {
  it('clamps a box that overhangs the page edge', () => {
    const c = clampBBoxToPage({ page: 1, x0: -5, y0: 780, x1: 620, y1: 800 }, 612, 792);
    expect(c).toEqual({ page: 1, x0: 0, y0: 780, x1: 612, y1: 792 });
  });

  it('throws when the box is entirely off-page', () => {
    expect(() => clampBBoxToPage({ page: 1, x0: 700, y0: 10, x1: 800, y1: 20 }, 612, 792)).toThrow(
      BBoxError,
    );
  });

  it('throws on non-finite coordinates', () => {
    expect(() => clampBBoxToPage({ page: 1, x0: 0, y0: 0, x1: Infinity, y1: 10 }, 612, 792)).toThrow(
      BBoxError,
    );
  });
});

describe('toDevicePixels', () => {
  it('scales a CSS rect into the backing store space', () => {
    const css = { left: 10, top: 20, width: 100, height: 50 };
    expect(toDevicePixels(css, 2)).toEqual({ left: 20, top: 40, width: 200, height: 100 });
  });
});

describe('rectCenter', () => {
  it('returns the geometric center', () => {
    expect(rectCenter({ left: 100, top: 200, width: 40, height: 20 })).toEqual({ x: 120, y: 210 });
  });
});
