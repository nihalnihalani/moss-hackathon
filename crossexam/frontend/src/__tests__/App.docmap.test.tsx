/**
 * App.docmap.test.tsx — verifies the documentId -> PDF url resolution for LIVE /
 * uploaded docs (audit MAJOR #4).
 *
 * The api client is mocked: /config returns live:true, /token connects, and
 * /documents returns a chosen documentId for the uploaded file. livekit-client is
 * mocked with a FakeRoom that captures the hook's real DataReceived handler so we
 * can feed live citation frames exactly like the backend would.
 *
 * Asserts:
 *  - a live citation whose documentId matches an UPLOADED doc draws its box
 *    (resolves to the uploaded object URL, not a bundled fixture).
 *  - a live citation whose documentId is UNKNOWN (no bundled url, not uploaded)
 *    shows the "source PDF unavailable" state instead of drawing boxes on the
 *    wrong document.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, act, fireEvent, cleanup } from '@testing-library/react';
import { App } from '../App';
import { ToastProvider } from '../components/ToastContext';
import type { Citation } from '../types';

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    fetchConfig: vi.fn(),
    requestToken: vi.fn(),
    uploadDocument: vi.fn(),
  };
});

const RoomEvent = {
  DataReceived: 'dataReceived',
  TrackSubscribed: 'trackSubscribed',
  TrackUnsubscribed: 'trackUnsubscribed',
} as const;
const Track = { Kind: { Audio: 'audio', Video: 'video', Unknown: 'unknown' } } as const;

let capturedOnData: ((payload: Uint8Array) => void) | undefined;

class FakeRoom {
  localParticipant = {
    setMicrophoneEnabled: vi.fn(async (): Promise<void> => undefined),
    publishData: vi.fn(async (): Promise<void> => undefined),
  };
  connect = vi.fn(async (): Promise<void> => undefined);
  disconnect = vi.fn(async (): Promise<void> => undefined);
  on(event: string, cb: (payload: Uint8Array) => void): this {
    if (event === RoomEvent.DataReceived) capturedOnData = cb;
    return this;
  }
  off(): this {
    return this;
  }
}
vi.mock('livekit-client', () => ({ Room: FakeRoom, RoomEvent, Track }));

import * as api from '../lib/api';
const fetchConfig = vi.mocked(api.fetchConfig);
const requestToken = vi.mocked(api.requestToken);
const uploadDocument = vi.mocked(api.uploadDocument);

const UPLOADED_ID = 'uploaded-doc-xyz';

function citationFor(documentId: string): Citation {
  return {
    id: `cite-${documentId}`,
    text: 'A grounded passage from the uploaded document.',
    confidence: 0.9,
    latencyMs: 8,
    pagesSearched: 100,
    documentId,
    bbox: { page: 1, x0: 72, y0: 100, x1: 500, y1: 120, page_width: 612, page_height: 792 },
  };
}

function encodeFrame(obj: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(obj));
}

describe('App documentId -> PDF url resolution (live/uploaded docs)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedOnData = undefined;
    // jsdom has no object-URL impl; provide one so uploads can mint/revoke urls.
    URL.createObjectURL = vi.fn(() => `blob:mock/${Math.random().toString(36).slice(2)}`);
    URL.revokeObjectURL = vi.fn();
    fetchConfig.mockResolvedValue({ livekitUrl: 'wss://x.livekit.cloud', live: true });
    requestToken.mockResolvedValue({ token: 'tok', url: 'wss://x.livekit.cloud', room: 'r' });
    uploadDocument.mockResolvedValue({
      documentId: UPLOADED_ID,
      pages: 5,
      chunksIndexed: 20,
      mode: 'live',
    });
  });

  afterEach(() => {
    cleanup();
  });

  it('maps an uploaded doc id to its object URL and draws the citation box', async () => {
    render(<ToastProvider><App /></ToastProvider>);
    await waitFor(() => expect(screen.getByTestId('mode-badge').textContent).toMatch(/LIVE/));
    await waitFor(() => expect(capturedOnData).toBeDefined());

    // Upload a PDF — the mocked /documents returns UPLOADED_ID.
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([new Uint8Array([1, 2, 3])], 'evidence.pdf', { type: 'application/pdf' });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
    await waitFor(() => expect(uploadDocument).toHaveBeenCalled());

    // A live citation referencing the uploaded doc resolves to its url -> box drawn.
    act(() => {
      capturedOnData?.(encodeFrame({ citations: [citationFor(UPLOADED_ID)] }));
    });

    await screen.findByTestId('bbox-highlight');
    expect(screen.queryByTestId('source-unavailable')).toBeNull();
  });

  it('shows the source-unavailable state for an unknown documentId', async () => {
    render(<ToastProvider><App /></ToastProvider>);
    await waitFor(() => expect(screen.getByTestId('mode-badge').textContent).toMatch(/LIVE/));
    await waitFor(() => expect(capturedOnData).toBeDefined());

    act(() => {
      capturedOnData?.(encodeFrame({ citations: [citationFor('totally-unknown-doc')] }));
    });

    await screen.findByTestId('source-unavailable');
    expect(screen.queryByTestId('bbox-highlight')).toBeNull();
  });
});
