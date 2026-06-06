/**
 * liveCitation.integration.test.tsx — end-to-end-ish coverage of the LIVE path.
 *
 * Asserts that a citation arriving on the LiveKit data channel flows through the
 * SAME code path the app uses in production (useCrossExam's RoomEvent.DataReceived
 * handler) and ends up drawn by PdfCanvas at exactly the rect lib/bbox.ts computes.
 *
 * We mock `livekit-client` so the hook's lazy `import('livekit-client')` resolves
 * to a fake Room that captures the real `onData` callback the hook registers. The
 * test then encodes a citation frame (JSON -> Uint8Array, identical to a backend
 * frame) and feeds it through that captured callback — no stubbing of the hook's
 * own logic. A small <Harness/> wires the hook output into <PdfCanvas/> exactly
 * like App.tsx does (page = targetPage, citation = activeCitation).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';
import { PdfCanvas } from '../../components/PdfCanvas';
import { Captions } from '../../components/Captions';
import { LiveLatencyBadge } from '../../components/LiveLatencyBadge';
import { useCrossExam } from '../useCrossExam';
import { pdfBBoxToCanvasRect } from '../../lib/bbox';
import { DEMO_PAGE_HEIGHT_PT, DEMO_PAGE_WIDTH_PT } from '../../lib/mockData';
import type { Citation } from '../../types';

const RENDER_SCALE = 1.25;

/** RoomEvent enum surface the hook touches. */
const RoomEvent = { DataReceived: 'dataReceived' } as const;

/** Captures the data handler the hook registers so the test can emit frames. */
let capturedOnData: ((payload: Uint8Array) => void) | undefined;

class FakeRoom {
  private handlers = new Map<string, (payload: Uint8Array) => void>();
  connect = vi.fn(async (): Promise<void> => undefined);
  disconnect = vi.fn(async (): Promise<void> => undefined);
  on(event: string, cb: (payload: Uint8Array) => void): this {
    this.handlers.set(event, cb);
    if (event === RoomEvent.DataReceived) capturedOnData = cb;
    return this;
  }
  off(event: string): this {
    this.handlers.delete(event);
    return this;
  }
}

vi.mock('livekit-client', () => ({
  Room: FakeRoom,
  RoomEvent,
}));

/** Mirrors App.tsx's wiring of the hook into the canvas. */
function Harness(): JSX.Element {
  const cx = useCrossExam({
    livekitUrl: 'wss://test.livekit.cloud',
    livekitToken: 'test-token',
  });
  return (
    <div>
      <span data-testid="mode">{cx.isMock ? 'mock' : 'live'}</span>
      <span data-testid="connected">{String(cx.isConnected)}</span>
      <span data-testid="agent-state">{cx.agentState}</span>
      <span data-testid="proactive">{String(cx.proactive)}</span>
      <span data-testid="silence">{cx.silenceReason ?? 'none'}</span>
      <LiveLatencyBadge latencyMs={cx.lastLatencyMs} />
      <Captions
        text={cx.caption}
        question={cx.question}
        citation={cx.activeCitation}
        proactive={cx.proactive}
        silenceReason={cx.silenceReason}
      />
      <PdfCanvas
        page={cx.targetPage}
        citation={cx.activeCitation}
        renderScale={RENDER_SCALE}
        proactive={cx.proactive}
      />
    </div>
  );
}

/** A live citation as the backend would publish it (note snake_case page dims). */
const LIVE_CITATION: Citation = {
  id: 'cite-live-42',
  text: 'A: Yes, I drove the forklift in bay three that evening.',
  confidence: 0.91,
  latencyMs: 8,
  pagesSearched: 912,
  bbox: {
    page: 42,
    x0: 96,
    y0: 410,
    x1: 516,
    y1: 432,
    page_width: DEMO_PAGE_WIDTH_PT,
    page_height: DEMO_PAGE_HEIGHT_PT,
  },
};

function encodeFrame(obj: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(obj));
}

describe('live citation path (LiveKit DataReceived -> PdfCanvas)', () => {
  beforeEach(() => {
    capturedOnData = undefined;
  });

  it('runs in live mode (not mock) when URL + token are present', async () => {
    render(<Harness />);
    expect(screen.getByTestId('mode').textContent).toBe('live');
    await waitFor(() => expect(screen.getByTestId('connected').textContent).toBe('true'));
  });

  it('draws the box where lib/bbox.ts computes when a live citation frame arrives', async () => {
    render(<Harness />);

    // Hook has connected and registered its real DataReceived handler.
    await waitFor(() => expect(capturedOnData).toBeDefined());

    // No highlight before any citation lands.
    expect(screen.queryByTestId('bbox-highlight')).toBeNull();

    // Feed a citation frame through the SAME callback the hook uses in prod.
    act(() => {
      capturedOnData?.(encodeFrame({ citation: LIVE_CITATION }));
    });

    // The hook navigated to the citation's page and set it speaking.
    await waitFor(() => expect(screen.getByTestId('agent-state').textContent).toBe('speaking'));

    const box = await screen.findByTestId('bbox-highlight');

    const expected = pdfBBoxToCanvasRect(LIVE_CITATION.bbox, {
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
    expect(box).toHaveAttribute('aria-label', `Cited text: ${LIVE_CITATION.text}`);
  });

  it('parses proactive + latencyMs + faithfulness from a live frame', async () => {
    render(<Harness />);
    await waitFor(() => expect(capturedOnData).toBeDefined());

    const proactiveCite: Citation = {
      ...LIVE_CITATION,
      id: 'cite-live-proactive',
      faithfulness: { supported: true, score: 0.97, method: 'nli' },
    };

    act(() => {
      capturedOnData?.(
        encodeFrame({
          citation: proactiveCite,
          proactive: true,
          latencyMs: 11,
          caption: 'Flagging this unprompted.',
        }),
      );
    });

    // Ambient flag flows through to state + UI.
    await waitFor(() => expect(screen.getByTestId('proactive').textContent).toBe('true'));
    expect(screen.getByTestId('ambient-tag')).toBeInTheDocument();
    expect(screen.getByTestId('bbox-ambient-tag')).toBeInTheDocument();

    // Persistent latency badge shows the frame's latencyMs.
    expect(screen.getByTestId('live-latency-value').textContent).toBe('11ms');

    // Grounded-confidence indicator renders the faithfulness score.
    expect(screen.getByTestId('grounded-indicator').textContent).toContain('grounded 0.97');
  });

  it('renders the honest not-found empty state on a not_found_in_document frame', async () => {
    render(<Harness />);
    await waitFor(() => expect(capturedOnData).toBeDefined());

    // First land a normal citation so we can prove the box gets CLEARED.
    act(() => {
      capturedOnData?.(encodeFrame({ citation: LIVE_CITATION, latencyMs: 7 }));
    });
    await screen.findByTestId('bbox-highlight');

    // Now a null citation with the silence reason.
    act(() => {
      capturedOnData?.(encodeFrame({ citation: null, reason: 'not_found_in_document', latencyMs: 5 }));
    });

    await waitFor(() =>
      expect(screen.getByTestId('silence').textContent).toBe('not_found_in_document'),
    );
    // Box cleared, honest empty state shown, latency persisted.
    expect(screen.queryByTestId('bbox-highlight')).toBeNull();
    expect(screen.getByTestId('not-found-state')).toBeInTheDocument();
    expect(screen.getByTestId('live-latency-value').textContent).toBe('5ms');
  });

  it('clears a prior silence state when a new citation snaps in', async () => {
    render(<Harness />);
    await waitFor(() => expect(capturedOnData).toBeDefined());

    act(() => {
      capturedOnData?.(encodeFrame({ citation: null, reason: 'not_found_in_document' }));
    });
    await waitFor(() =>
      expect(screen.getByTestId('silence').textContent).toBe('not_found_in_document'),
    );

    act(() => {
      capturedOnData?.(encodeFrame({ citation: LIVE_CITATION }));
    });
    await waitFor(() => expect(screen.getByTestId('silence').textContent).toBe('none'));
    expect(screen.queryByTestId('not-found-state')).toBeNull();
    expect(screen.getByTestId('bbox-highlight')).toBeInTheDocument();
  });

  it('ignores malformed frames without throwing or drawing a box', async () => {
    render(<Harness />);
    await waitFor(() => expect(capturedOnData).toBeDefined());

    act(() => {
      capturedOnData?.(new TextEncoder().encode('not json {{{'));
      capturedOnData?.(encodeFrame({ citation: { id: 'bad' } })); // fails isCitation guard
    });

    expect(screen.queryByTestId('bbox-highlight')).toBeNull();
  });
});
