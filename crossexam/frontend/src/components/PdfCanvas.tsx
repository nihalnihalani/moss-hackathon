import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Citation, PageRenderGeometry } from '../types';
import { pdfBBoxToCanvasRect } from '../lib/bbox';
import { DEMO_PAGE_HEIGHT_PT, DEMO_PAGE_WIDTH_PT } from '../lib/mockData';
import { ZoomIn, ZoomOut, Maximize } from 'lucide-react';

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
  /** Clear the active citation (Esc on a focused box). */
  onClearCitation?: () => void;
  /** Bump to re-center + focus the active box (e.g. a transcript chip click). */
  refocusSignal?: number;
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
const MIN_SCALE = 0.5;
const MAX_SCALE = 3.0;
const SCALE_STEP = 0.25;

/**
 * Renders a single PDF page (real or placeholder) at a FIXED zoom and overlays a
 * glowing amber bounding box positioned by lib/bbox.ts. The snap+glow is the hero
 * moment; the box is a focusable, keyboard-navigable target (Enter scrolls it into
 * view, Esc clears).
 */
export function PdfCanvas({
  page,
  citation,
  pdfUrl,
  renderScale = DEFAULT_SCALE,
  onClearCitation,
  refocusSignal,
}: PdfCanvasProps): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = useState(renderScale);
  const [rendering, setRendering] = useState(false);
  const [pageSizePt, setPageSizePt] = useState<{ w: number; h: number }>({
    w: DEMO_PAGE_WIDTH_PT,
    h: DEMO_PAGE_HEIGHT_PT,
  });

  const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1;

  const handleZoomIn = () => setScale((s) => Math.min(MAX_SCALE, s + SCALE_STEP));
  const handleZoomOut = () => setScale((s) => Math.max(MIN_SCALE, s - SCALE_STEP));
  const handleZoomReset = () => setScale(renderScale);

  // Render the page bitmap (placeholder unless a real PDF URL is supplied).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let cancelled = false;

    const drawPlaceholder = (wPt: number, hPt: number): void => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      const cssW = wPt * scale;
      const cssH = hPt * scale;
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // Paper.
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, cssW, cssH);
      // Header band.
      ctx.fillStyle = '#f4f4f5';
      ctx.fillRect(0, 0, cssW, 46 * scale);
      ctx.fillStyle = '#71717a';
      ctx.font = `600 ${11 * scale}px ui-sans-serif, system-ui, sans-serif`;
      ctx.fillText(`DEPOSITION TRANSCRIPT — PAGE ${page}`, 16 * scale, 28 * scale);
      // Faux text lines.
      ctx.fillStyle = '#e4e4e7';
      const lineH = 20 * scale;
      for (let y = 70 * scale; y < cssH - lineH; y += lineH) {
        const w = (0.55 + Math.random() * 0.4) * (cssW - 32 * scale);
        ctx.fillRect(16 * scale, y, w, 8 * scale);
      }
      setPageSizePt({ w: wPt, h: hPt });
    };

    if (!pdfUrl) {
      drawPlaceholder(DEMO_PAGE_WIDTH_PT, DEMO_PAGE_HEIGHT_PT);
      return;
    }

    setRendering(true);
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
        const viewport = pdfPage.getViewport({ scale: scale * dpr });
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${baseViewport.width * scale}px`;
        canvas.style.height = `${baseViewport.height * scale}px`;
        await pdfPage.render({ canvasContext: ctx, viewport }).promise;
        if (cancelled) return;
        setPageSizePt({ w: baseViewport.width, h: baseViewport.height });
      } catch {
        // Any failure (no worker, bad URL) falls back to the placeholder.
        drawPlaceholder(DEMO_PAGE_WIDTH_PT, DEMO_PAGE_HEIGHT_PT);
      } finally {
        if (!cancelled) setRendering(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [pdfUrl, page, scale, dpr]);

  // Compute the overlay rect from the citation using the pinned transform.
  const overlayRect = useMemo(() => {
    if (!citation || citation.bbox.page !== page) return null;
    const geometry: PageRenderGeometry = {
      pageWidthPt: pageSizePt.w,
      pageHeightPt: pageSizePt.h,
      renderScale: scale,
      canvasOffset: { x: 0, y: 0 }, // canvas is the overlay's positioned ancestor
      devicePixelRatio: dpr,
    };
    try {
      return pdfBBoxToCanvasRect(citation.bbox, geometry);
    } catch {
      return null;
    }
  }, [citation, page, pageSizePt.w, pageSizePt.h, scale, dpr]);

  // Re-center the box when it snaps in (and on an explicit refocus request).
  useEffect(() => {
    if (overlayRect && boxRef.current) {
      boxRef.current.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    }
  }, [overlayRect, citation?.id]);

  // A transcript chip click bumps refocusSignal: re-center AND move focus there.
  useEffect(() => {
    if (refocusSignal && boxRef.current) {
      boxRef.current.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
      boxRef.current.focus();
    }
  }, [refocusSignal]);

  const onBoxKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>): void => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClearCitation?.();
      } else if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        boxRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }
    },
    [onClearCitation],
  );

  return (
    <div className="pdf-canvas" ref={containerRef}>
      <div className="pdf-controls">
        <button
          className="pdf-controls__btn"
          onClick={handleZoomOut}
          aria-label="Zoom out"
          disabled={scale <= MIN_SCALE}
        >
          <ZoomOut size={16} />
        </button>
        <span className="pdf-controls__zoom" aria-hidden="true">
          {Math.round(scale * 100)}%
        </span>
        <button className="pdf-controls__btn" onClick={handleZoomReset} aria-label="Reset zoom">
          <Maximize size={16} />
        </button>
        <button
          className="pdf-controls__btn"
          onClick={handleZoomIn}
          aria-label="Zoom in"
          disabled={scale >= MAX_SCALE}
        >
          <ZoomIn size={16} />
        </button>
      </div>

      <div className="pdf-canvas__page">
        <canvas ref={canvasRef} className="pdf-canvas__bitmap" aria-label={`Document page ${page}`} />
        {rendering ? <div className="pdf-skeleton" aria-hidden="true" /> : null}
        {overlayRect ? (
          <div
            key={citation?.id}
            ref={boxRef}
            className="bbox-highlight"
            data-testid="bbox-highlight"
            style={{
              left: `${overlayRect.left}px`,
              top: `${overlayRect.top}px`,
              width: `${overlayRect.width}px`,
              height: `${overlayRect.height}px`,
            }}
            tabIndex={0}
            role="button"
            aria-label={`Cited text: ${citation?.text ?? ''}`}
            onKeyDown={onBoxKeyDown}
          >
            <span className="bbox-highlight__tick" aria-hidden="true" />
            <span className="bbox-highlight__label">
              p.{citation?.bbox.page} · {Math.round((citation?.confidence ?? 0) * 100)}%
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
