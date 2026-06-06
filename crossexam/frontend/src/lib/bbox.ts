/**
 * bbox.ts — THE demo-deciding transform.
 *
 * Maps a citation bounding box from PDF point-space onto the rendered PDF
 * canvas, in CSS pixels, so an absolutely-positioned overlay div lands exactly
 * on the cited line. A misaligned box loses the demo, so this module is pure,
 * fully exported, and unit-tested.
 *
 * Coordinate model
 * ----------------
 * - Unsiloed/pdf.js bbox is in PDF points with a TOP-LEFT origin and y growing
 *   downward (see types.ts BBox). 72 points = 1 inch.
 * - pdf.js renders a page at a chosen CSS scale `renderScale` (CSS px per point).
 *   On HiDPI we size the canvas BACKING STORE to `renderScale * devicePixelRatio`
 *   device pixels, but lay it out via CSS at `renderScale` CSS px per point. The
 *   overlay is positioned in CSS pixels, therefore the visible mapping is:
 *
 *       cssX = pointX * renderScale + offsetX
 *       cssY = pointY * renderScale + offsetY
 *
 *   devicePixelRatio does NOT scale the CSS-pixel overlay; it only affects the
 *   number of device pixels in the backing store. We still take it to validate
 *   it and to optionally express the rect in device pixels (see toDevicePixels).
 */

import type { BBox, CanvasRect, PageRenderGeometry } from '../types';

/** Thrown when geometry is degenerate (would produce NaN/Infinity rects). */
export class BBoxError extends Error {}

/** A geometry where every field is finite and strictly usable. */
function assertGeometry(g: PageRenderGeometry): void {
  const { pageWidthPt, pageHeightPt, renderScale, devicePixelRatio } = g;
  const finite = (n: number): boolean => Number.isFinite(n);
  if (!finite(pageWidthPt) || pageWidthPt <= 0) {
    throw new BBoxError(`pageWidthPt must be a positive finite number, got ${pageWidthPt}`);
  }
  if (!finite(pageHeightPt) || pageHeightPt <= 0) {
    throw new BBoxError(`pageHeightPt must be a positive finite number, got ${pageHeightPt}`);
  }
  if (!finite(renderScale) || renderScale <= 0) {
    throw new BBoxError(`renderScale must be a positive finite number, got ${renderScale}`);
  }
  if (!finite(devicePixelRatio) || devicePixelRatio <= 0) {
    throw new BBoxError(`devicePixelRatio must be a positive finite number, got ${devicePixelRatio}`);
  }
  if (!finite(g.canvasOffset.x) || !finite(g.canvasOffset.y)) {
    throw new BBoxError('canvasOffset must have finite x and y');
  }
}

/**
 * Normalize a box so x0<=x1 and y0<=y1, defending against sources that emit
 * corners in either order. Page is preserved.
 */
export function normalizeBBox(bbox: BBox): BBox {
  return {
    page: bbox.page,
    x0: Math.min(bbox.x0, bbox.x1),
    y0: Math.min(bbox.y0, bbox.y1),
    x1: Math.max(bbox.x0, bbox.x1),
    y1: Math.max(bbox.y0, bbox.y1),
  };
}

/**
 * Validate that a box is finite and lies (allowing a small epsilon) within the
 * page bounds. Returns the (page-clamped) box. Throws on non-finite input.
 *
 * `epsilon` (points) tolerates parser rounding at the page edge.
 */
export function clampBBoxToPage(
  bbox: BBox,
  pageWidthPt: number,
  pageHeightPt: number,
  epsilon = 1,
): BBox {
  const n = normalizeBBox(bbox);
  for (const [k, v] of Object.entries({ x0: n.x0, y0: n.y0, x1: n.x1, y1: n.y1 })) {
    if (!Number.isFinite(v)) {
      throw new BBoxError(`bbox.${k} is not finite: ${v}`);
    }
  }
  if (
    n.x1 < -epsilon ||
    n.y1 < -epsilon ||
    n.x0 > pageWidthPt + epsilon ||
    n.y0 > pageHeightPt + epsilon
  ) {
    throw new BBoxError(
      `bbox is entirely outside page bounds (${pageWidthPt}x${pageHeightPt}pt): ` +
        `[${n.x0},${n.y0},${n.x1},${n.y1}]`,
    );
  }
  const clampX = (x: number): number => Math.min(Math.max(x, 0), pageWidthPt);
  const clampY = (y: number): number => Math.min(Math.max(y, 0), pageHeightPt);
  return {
    page: n.page,
    x0: clampX(n.x0),
    y0: clampY(n.y0),
    x1: clampX(n.x1),
    y1: clampY(n.y1),
  };
}

/**
 * Map a PDF-point bbox to a CSS-pixel rectangle on the rendered canvas.
 *
 * This is THE transform. Output is in CSS pixels relative to the overlay
 * container, ready to drop into `style={{ left, top, width, height, position:'absolute' }}`.
 *
 * @param bbox     Citation box in PDF points (top-left origin, y down).
 * @param geometry How the page was rendered (scale, offset, dpr, intrinsic size).
 * @returns        Rect in CSS pixels: { left, top, width, height }.
 */
export function pdfBBoxToCanvasRect(bbox: BBox, geometry: PageRenderGeometry): CanvasRect {
  assertGeometry(geometry);
  const { renderScale, canvasOffset } = geometry;
  const box = clampBBoxToPage(bbox, geometry.pageWidthPt, geometry.pageHeightPt);

  const left = box.x0 * renderScale + canvasOffset.x;
  const top = box.y0 * renderScale + canvasOffset.y;
  const width = (box.x1 - box.x0) * renderScale;
  const height = (box.y1 - box.y0) * renderScale;

  return { left, top, width, height };
}

/**
 * Express a CSS-pixel rect in device (backing-store) pixels. Useful if you draw
 * the highlight directly onto the canvas 2D context instead of a DOM overlay,
 * since the context coordinate space is the device-pixel backing store.
 */
export function toDevicePixels(rect: CanvasRect, devicePixelRatio: number): CanvasRect {
  if (!Number.isFinite(devicePixelRatio) || devicePixelRatio <= 0) {
    throw new BBoxError(`devicePixelRatio must be positive finite, got ${devicePixelRatio}`);
  }
  return {
    left: rect.left * devicePixelRatio,
    top: rect.top * devicePixelRatio,
    width: rect.width * devicePixelRatio,
    height: rect.height * devicePixelRatio,
  };
}

/** Center point of a rect, in the rect's own coordinate space. Handy for scroll-to. */
export function rectCenter(rect: CanvasRect): { x: number; y: number } {
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}
