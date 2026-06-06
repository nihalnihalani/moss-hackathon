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
});
