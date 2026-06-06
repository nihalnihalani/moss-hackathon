import { useEffect, useMemo, useRef, useState } from 'react';
import type { Citation, PageRenderGeometry } from '../types';
import { pdfBBoxToCanvasRect } from '../lib/bbox';
import { DEMO_PAGE_HEIGHT_PT, DEMO_PAGE_WIDTH_PT } from '../lib/mockData';

export interface PdfCanvasProps {
  /** Page currently shown (1-based). */
  page: number;
  /** The citation to highlight, or null for no overlay. */
  citation: Citation | null;
  /**
   * Optional real PDF URL. When absent (mock mode) a placeholder page is drawn
   * so the hero bbox snap shows with no backend.
   */
  pdfUrl?: string | undefined;
  /** Fixed render scale (CSS px per PDF point). Demo runs at a pinned zoom. */
  renderScale?: number;
}

/** Minimal pdf.js typings we rely on (kept local to avoid a hard import dependency). */
interface PdfViewport {
  width: number;
  height: number;
}
interface PdfPage {
  getViewport(opts: { scale: number }): PdfViewport;
  render(opts: {
    canvasContext: CanvasRenderingContext2D;
    viewport: PdfViewport;
  }): { promise: Promise<void> };
}
interface PdfDocument {
  numPages: number;
  getPage(n: number): Promise<PdfPage>;
}

const DEFAULT_SCALE = 1.25;

/**
 * Renders a single PDF page (real or placeholder) at a FIXED zoom and overlays a
 * glowing bounding box positioned by lib/bbox.ts. The snap+glow is the hero moment.
 */
export function PdfCanvas({
  page,
  citation,
  pdfUrl,
  renderScale = DEFAULT_SCALE,
}: PdfCanvasProps): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [pageSizePt, setPageSizePt] = useState<{ w: number; h: number }>({
    w: DEMO_PAGE_WIDTH_PT,
    h: DEMO_PAGE_HEIGHT_PT,
  });

  const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1;

  // Render the page bitmap (placeholder unless a real PDF URL is supplied).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let cancelled = false;

    const drawPlaceholder = (wPt: number, hPt: number): void => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      const cssW = wPt * renderScale;
      const cssH = hPt * renderScale;
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // Paper.
      ctx.fillStyle = '#f7f5ef';
      ctx.fillRect(0, 0, cssW, cssH);
      // Header band.
      ctx.fillStyle = '#e7e2d6';
      ctx.fillRect(0, 0, cssW, 46 * renderScale);
      ctx.fillStyle = '#6b6354';
      ctx.font = `${11 * renderScale}px ui-monospace, monospace`;
      ctx.fillText(`DEPOSITION TRANSCRIPT — PAGE ${page}`, 16 * renderScale, 28 * renderScale);
      // Faux text lines.
      ctx.fillStyle = '#cfc9ba';
      const lineH = 20 * renderScale;
      for (let y = 70 * renderScale; y < cssH - lineH; y += lineH) {
        const w = (0.55 + Math.random() * 0.4) * (cssW - 32 * renderScale);
        ctx.fillRect(16 * renderScale, y, w, 8 * renderScale);
      }
      setPageSizePt({ w: wPt, h: hPt });
    };

    if (!pdfUrl) {
      drawPlaceholder(DEMO_PAGE_WIDTH_PT, DEMO_PAGE_HEIGHT_PT);
      return;
    }

    void (async () => {
      try {
        const pdfjs = await import('pdfjs-dist');
        // Worker: use the bundled module worker.
        const workerUrl = new URL(
          'pdfjs-dist/build/pdf.worker.min.mjs',
          import.meta.url,
        ).toString();
        (pdfjs as unknown as { GlobalWorkerOptions: { workerSrc: string } }).GlobalWorkerOptions.workerSrc =
          workerUrl;

        const doc = (await (
          pdfjs as unknown as { getDocument(src: string): { promise: Promise<PdfDocument> } }
        )
          .getDocument(pdfUrl)
          .promise) as PdfDocument;
        if (cancelled) return;
        const clampedPage = Math.min(Math.max(1, page), doc.numPages);
        const pdfPage = await doc.getPage(clampedPage);
        if (cancelled) return;

        const baseViewport = pdfPage.getViewport({ scale: 1 });
        const viewport = pdfPage.getViewport({ scale: renderScale * dpr });
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${baseViewport.width * renderScale}px`;
        canvas.style.height = `${baseViewport.height * renderScale}px`;
        await pdfPage.render({ canvasContext: ctx, viewport }).promise;
        if (cancelled) return;
        setPageSizePt({ w: baseViewport.width, h: baseViewport.height });
      } catch {
        // Any failure (no worker, bad URL) falls back to the placeholder.
        drawPlaceholder(DEMO_PAGE_WIDTH_PT, DEMO_PAGE_HEIGHT_PT);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [pdfUrl, page, renderScale, dpr]);

  // Compute the overlay rect from the citation using the pinned transform.
  const overlayRect = useMemo(() => {
    if (!citation || citation.bbox.page !== page) return null;
    const geometry: PageRenderGeometry = {
      pageWidthPt: pageSizePt.w,
      pageHeightPt: pageSizePt.h,
      renderScale,
      canvasOffset: { x: 0, y: 0 }, // canvas is the overlay's positioned ancestor
      devicePixelRatio: dpr,
    };
    try {
      return pdfBBoxToCanvasRect(citation.bbox, geometry);
    } catch {
      return null;
    }
  }, [citation, page, pageSizePt.w, pageSizePt.h, renderScale, dpr]);

  return (
    <div className="pdf-canvas" ref={containerRef}>
      <div className="pdf-canvas__page">
        <canvas ref={canvasRef} className="pdf-canvas__bitmap" aria-label={`Document page ${page}`} />
        {overlayRect ? (
          <div
            key={citation?.id}
            className="bbox-highlight"
            data-testid="bbox-highlight"
            style={{
              left: `${overlayRect.left}px`,
              top: `${overlayRect.top}px`,
              width: `${overlayRect.width}px`,
              height: `${overlayRect.height}px`,
            }}
            role="mark"
            aria-label={`Cited text: ${citation?.text ?? ''}`}
          >
            <span className="bbox-highlight__tag">
              p.{citation?.bbox.page} · {Math.round((citation?.confidence ?? 0) * 100)}%
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
