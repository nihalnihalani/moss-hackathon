import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { BBox, Citation, PageRenderGeometry } from '../types';
import { pdfBBoxToCanvasRect } from '../lib/bbox';
import { scrollBehavior } from '../lib/motion';
import { DEMO_PAGE_HEIGHT_PT, DEMO_PAGE_WIDTH_PT } from '../lib/mockData';
import { ZoomIn, ZoomOut, Maximize, ScanLine } from 'lucide-react';

export interface PdfCanvasProps {
  /** Page currently shown (1-based). */
  page: number;
  /** The primary citation to highlight + focus, or null for no overlay. */
  citation: Citation | null;
  /**
   * All citations to render on the current page (multi-hop). When omitted, only
   * `citation` is rendered. Each citation whose `bbox.page === page` is drawn;
   * the one matching `citation.id` is the focused/labelled primary.
   */
  citations?: Citation[];
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
  /**
   * The active citation was surfaced UNPROMPTED. Adds an ambient treatment to the
   * box (distinct ring class + an "● SURFACED AUTOMATICALLY" tag on the label).
   */
  proactive?: boolean;
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
 * Renders a single PDF page (real or placeholder) at a FIXED zoom and overlays
 * glowing amber bounding boxes positioned by lib/bbox.ts. The snap+glow is the
 * hero moment; the primary box is a focusable, keyboard-navigable target (Enter
 * scrolls it into view, Esc clears).
 *
 * Feat 2 — quad highlights: when a citation carries `quads[]`, EACH quad is drawn
 * as its own amber rect (so the highlight hugs real text across line wraps); the
 * snap/glow + p.N·% label sit on the union `bbox`. Falls back to `bbox` when no
 * quads are present.
 */
export function PdfCanvas({
  page,
  citation,
  citations,
  pdfUrl,
  renderScale = DEFAULT_SCALE,
  onClearCitation,
  refocusSignal,
  proactive = false,
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

  const geometry: PageRenderGeometry = useMemo(
    () => ({
      pageWidthPt: pageSizePt.w,
      pageHeightPt: pageSizePt.h,
      renderScale: scale,
      canvasOffset: { x: 0, y: 0 }, // canvas is the overlay's positioned ancestor
      devicePixelRatio: dpr,
    }),
    [pageSizePt.w, pageSizePt.h, scale, dpr],
  );

  // Every citation visible on the current page (multi-hop). The primary is the
  // one matching `citation`; others render as dim secondary boxes.
  const pageCitations = useMemo(() => {
    const list = citations && citations.length > 0 ? citations : citation ? [citation] : [];
    return list.filter((c) => c.bbox.page === page);
  }, [citations, citation, page]);

  // Compute the primary overlay rect (drives focus, label, scroll-into-view).
  const overlayRect = useMemo(() => {
    if (!citation || citation.bbox.page !== page) return null;
    try {
      return pdfBBoxToCanvasRect(citation.bbox, geometry);
    } catch {
      return null;
    }
  }, [citation, page, geometry]);

  // Per-quad rects for the primary citation (feat 2). Falls back to the union
  // bbox when no quads are present, so a quad-less citation still highlights.
  const primaryQuadRects = useMemo(() => {
    if (!citation || citation.bbox.page !== page) return [];
    const quads: BBox[] =
      citation.quads && citation.quads.length > 0 ? citation.quads : [citation.bbox];
    const rects = [];
    for (const q of quads) {
      if (q.page !== page) continue;
      try {
        rects.push(pdfBBoxToCanvasRect(q, geometry));
      } catch {
        /* skip degenerate quad */
      }
    }
    return rects;
  }, [citation, page, geometry]);

  // Flip the label ABOVE the box when the box sits near the page foot — otherwise
  // the default bottom:-30px label renders off the page and gets clipped.
  const labelAbove = useMemo(() => {
    if (!overlayRect) return false;
    const pageHeightCss = pageSizePt.h * scale;
    const LABEL_GUTTER = 34; // label height (~24px) + offset, in CSS px
    return overlayRect.top + overlayRect.height + LABEL_GUTTER > pageHeightCss;
  }, [overlayRect, pageSizePt.h, scale]);

  // Re-center the box when it snaps in (and on an explicit refocus request).
  useEffect(() => {
    if (overlayRect && boxRef.current) {
      boxRef.current.scrollIntoView?.({ block: 'center', behavior: scrollBehavior() });
    }
  }, [overlayRect, citation?.id]);

  // A transcript chip click bumps refocusSignal: re-center AND move focus there.
  useEffect(() => {
    if (refocusSignal && boxRef.current) {
      boxRef.current.scrollIntoView?.({ block: 'center', behavior: scrollBehavior() });
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
        boxRef.current?.scrollIntoView({ block: 'center', behavior: scrollBehavior() });
      }
    },
    [onClearCitation],
  );

  const primaryScanned = citation?.scanned === true && citation.bbox.page === page;

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

        {/* Scanned-source badge near the page corner (feat 3). */}
        {primaryScanned ? (
          <span className="scanned-badge scanned-badge--page" data-testid="scanned-badge-page">
            <ScanLine size={11} aria-hidden="true" /> Scanned source
          </span>
        ) : null}

        {/* Secondary citation boxes from other hops on this page (dim). */}
        {pageCitations
          .filter((c) => c.id !== citation?.id)
          .map((c) => {
            const rect = (() => {
              try {
                return pdfBBoxToCanvasRect(c.bbox, geometry);
              } catch {
                return null;
              }
            })();
            if (!rect) return null;
            return (
              <div
                key={`secondary-${c.id}`}
                className="bbox-highlight bbox-highlight--secondary"
                data-testid="bbox-secondary"
                aria-hidden="true"
                style={{
                  left: `${rect.left}px`,
                  top: `${rect.top}px`,
                  width: `${rect.width}px`,
                  height: `${rect.height}px`,
                }}
              />
            );
          })}

        {overlayRect ? (
          <div
            key={citation?.id}
            ref={boxRef}
            className={`bbox-highlight${proactive ? ' bbox-highlight--ambient' : ''}`}
            data-testid="bbox-highlight"
            data-proactive={proactive ? 'true' : undefined}
            style={{
              left: `${overlayRect.left}px`,
              top: `${overlayRect.top}px`,
              width: `${overlayRect.width}px`,
              height: `${overlayRect.height}px`,
            }}
            tabIndex={0}
            role="button"
            aria-label={`${proactive ? 'Surfaced automatically. ' : ''}${
              citation?.scanned ? 'Scanned source. ' : ''
            }Cited text: ${citation?.text ?? ''}`}
            onKeyDown={onBoxKeyDown}
          >
            <span className="bbox-highlight__tick" aria-hidden="true" />
            {proactive ? (
              <span
                className={`bbox-highlight__ambient${labelAbove ? ' bbox-highlight__ambient--above' : ''}`}
                data-testid="bbox-ambient-tag"
              >
                <span className="bbox-highlight__ambient-dot" aria-hidden="true" />
                Surfaced automatically
              </span>
            ) : null}
            <span className={`bbox-highlight__label${labelAbove ? ' bbox-highlight__label--above' : ''}`}>
              p.{citation?.bbox.page} · {Math.round((citation?.confidence ?? 0) * 100)}%
            </span>
          </div>
        ) : null}

        {/* Per-quad highlights for the primary citation (feat 2): one amber rect
            per line so the highlight hugs the real text across wraps. Rendered
            ABOVE the union box (which carries the glow/label) but non-interactive. */}
        {primaryQuadRects.map((rect, i) => (
          <div
            key={`quad-${citation?.id}-${i}`}
            className="bbox-quad"
            data-testid="bbox-quad"
            aria-hidden="true"
            style={{
              left: `${rect.left}px`,
              top: `${rect.top}px`,
              width: `${rect.width}px`,
              height: `${rect.height}px`,
            }}
          />
        ))}
      </div>
    </div>
  );
}
