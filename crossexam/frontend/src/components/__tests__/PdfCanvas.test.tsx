import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PdfCanvas } from '../PdfCanvas';
import { ANSWER_CITATION, DEMO_PAGE_WIDTH_PT, DEMO_PAGE_HEIGHT_PT } from '../../lib/mockData';
import { pdfBBoxToCanvasRect } from '../../lib/bbox';

const RENDER_SCALE = 1.25;

describe('PdfCanvas (mock mode)', () => {
  it('renders a placeholder page canvas with no PDF URL', () => {
    render(<PdfCanvas page={687} citation={null} renderScale={RENDER_SCALE} />);
    expect(screen.getByLabelText('Document page 687')).toBeInTheDocument();
  });

  it('does not draw a highlight before a citation lands', () => {
    render(<PdfCanvas page={687} citation={null} renderScale={RENDER_SCALE} />);
    expect(screen.queryByTestId('bbox-highlight')).toBeNull();
  });

  it('draws the bbox at the position lib/bbox.ts computes for the active page', () => {
    render(<PdfCanvas page={ANSWER_CITATION.bbox.page} citation={ANSWER_CITATION} renderScale={RENDER_SCALE} />);
    const box = screen.getByTestId('bbox-highlight');

    const expected = pdfBBoxToCanvasRect(ANSWER_CITATION.bbox, {
      pageWidthPt: DEMO_PAGE_WIDTH_PT,
      pageHeightPt: DEMO_PAGE_HEIGHT_PT,
      renderScale: RENDER_SCALE,
      canvasOffset: { x: 0, y: 0 },
      devicePixelRatio: window.devicePixelRatio || 1,
    });

    expect(box.style.left).toBe(`${expected.left}px`);
    expect(box.style.top).toBe(`${expected.top}px`);
    expect(box.style.width).toBe(`${expected.width}px`);
    expect(box.style.height).toBe(`${expected.height}px`);
  });

  it('hides the highlight when the shown page differs from the citation page', () => {
    render(<PdfCanvas page={1} citation={ANSWER_CITATION} renderScale={RENDER_SCALE} />);
    expect(screen.queryByTestId('bbox-highlight')).toBeNull();
  });

  it('renders one amber quad per Citation.quads[] entry at its computed coords (feat 2)', () => {
    const quads = ANSWER_CITATION.quads ?? [];
    expect(quads.length).toBeGreaterThan(1); // the mock answer wraps across lines

    render(<PdfCanvas page={ANSWER_CITATION.bbox.page} citation={ANSWER_CITATION} renderScale={RENDER_SCALE} />);

    const quadEls = screen.getAllByTestId('bbox-quad');
    expect(quadEls.length).toBe(quads.length);

    const geometry = {
      pageWidthPt: DEMO_PAGE_WIDTH_PT,
      pageHeightPt: DEMO_PAGE_HEIGHT_PT,
      renderScale: RENDER_SCALE,
      canvasOffset: { x: 0, y: 0 },
      devicePixelRatio: window.devicePixelRatio || 1,
    };

    quads.forEach((q, i) => {
      const expected = pdfBBoxToCanvasRect(q, geometry);
      const el = quadEls[i];
      expect(el).toBeDefined();
      expect(el?.style.left).toBe(`${expected.left}px`);
      expect(el?.style.top).toBe(`${expected.top}px`);
      expect(el?.style.width).toBe(`${expected.width}px`);
      expect(el?.style.height).toBe(`${expected.height}px`);
    });
  });

  it('falls back to a single union-bbox quad when quads are absent (feat 2)', () => {
    const noQuads = { ...ANSWER_CITATION, quads: undefined };
    render(<PdfCanvas page={noQuads.bbox.page} citation={noQuads} renderScale={RENDER_SCALE} />);
    expect(screen.getAllByTestId('bbox-quad').length).toBe(1);
  });
});
