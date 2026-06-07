/**
 * App.session.test.tsx — verifies the live-vs-mock startup decision.
 *
 * The api client is mocked so we control /config and /token; livekit-client is
 * mocked with a fake Room so the LIVE branch connects without a network. We then
 * assert the mode badge reflects LIVE when config.live === true, and falls back
 * to MOCK when /config rejects.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { App } from '../App';
import { ApiError } from '../lib/api';
import { ToastProvider } from '../components/ToastContext';

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    fetchConfig: vi.fn(),
    requestToken: vi.fn(),
    uploadDocument: vi.fn(),
  };
});

// Fake LiveKit Room so the hook's live branch resolves connected.
const RoomEvent = { DataReceived: 'dataReceived' } as const;
class FakeRoom {
  connect = vi.fn(async (): Promise<void> => undefined);
  disconnect = vi.fn(async (): Promise<void> => undefined);
  on(): this {
    return this;
  }
  off(): this {
    return this;
  }
}
vi.mock('livekit-client', () => ({ Room: FakeRoom, RoomEvent }));

// PdfCanvas pulls in pdfjs lazily; the canvas stub in setup.ts covers rendering.
import * as api from '../lib/api';
const fetchConfig = vi.mocked(api.fetchConfig);
const requestToken = vi.mocked(api.requestToken);

function renderApp(): void {
  render(
    <StrictMode>
      <ToastProvider>
        <App />
      </ToastProvider>
    </StrictMode>,
  );
}

describe('App startup session resolution', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('enters LIVE mode when /config returns live:true', async () => {
    fetchConfig.mockResolvedValue({ livekitUrl: 'wss://x.livekit.cloud', live: true });
    requestToken.mockResolvedValue({
      token: 'tok',
      url: 'wss://x.livekit.cloud',
      room: 'room-1',
    });

    renderApp();

    await waitFor(() =>
      expect(screen.getByTestId('mode-badge').textContent).toMatch(/LIVE/),
    );
    expect(fetchConfig).toHaveBeenCalledTimes(1);
    expect(requestToken).toHaveBeenCalledTimes(1);
    expect(requestToken).toHaveBeenCalledWith(
      { room: expect.stringMatching(/^crossexam-[a-zA-Z0-9_-]+$/) },
    );
  });

  it('falls back to MOCK when /config fails (backend unreachable)', async () => {
    fetchConfig.mockRejectedValue(new ApiError('unreachable'));

    renderApp();

    await waitFor(() =>
      expect(screen.getByTestId('mode-badge').textContent).toBe('MOCK / OFFLINE'),
    );
    expect(requestToken).not.toHaveBeenCalled();
  });

  it('falls back to MOCK when /config reports live:false', async () => {
    fetchConfig.mockResolvedValue({ livekitUrl: null, live: false });

    renderApp();

    await waitFor(() =>
      expect(screen.getByTestId('mode-badge').textContent).toBe('MOCK / OFFLINE'),
    );
    expect(requestToken).not.toHaveBeenCalled();
  });
});
