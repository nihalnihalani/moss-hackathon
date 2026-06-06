/**
 * App.reset.test.tsx — Reset must restore the empty state (audit finding M2).
 *
 * Once a citation is surfaced (here via the mock "Run demo" walkthrough), the
 * stage flips from the EmptyDropzone to the rendered canvas (docLoaded === true).
 * Clicking Reset must clear that flag and bring the EmptyDropzone back, rather
 * than leaving a citation-less PDF on screen.
 *
 * /config is rejected so the app resolves to MOCK mode (Run demo enabled).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, act, waitFor, cleanup } from '@testing-library/react';
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

import * as api from '../lib/api';
const fetchConfig = vi.mocked(api.fetchConfig);

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('App reset restores the empty state (M2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Unreachable backend => MOCK / OFFLINE mode, Run demo enabled.
    fetchConfig.mockRejectedValue(new ApiError('unreachable'));
  });

  it('after a citation makes docLoaded true, Reset brings back the EmptyDropzone', async () => {
    render(
      <ToastProvider>
        <App />
      </ToastProvider>,
    );

    // Wait for the session to resolve to mock (badge confirms it).
    await waitFor(() =>
      expect(screen.getByTestId('mode-badge').textContent).toBe('MOCK / OFFLINE'),
    );

    // First-run: the empty dropzone owns the stage.
    expect(screen.getByTestId('empty-dropzone')).toBeInTheDocument();

    // Run the scripted demo and advance to the first snap; a citation surfaces,
    // flipping the stage to the rendered canvas (docLoaded -> true).
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole('button', { name: /run demo/i }));
    act(() => {
      vi.advanceTimersByTime(2700);
    });
    vi.useRealTimers();

    expect(screen.queryByTestId('empty-dropzone')).not.toBeInTheDocument();

    // Reset must restore the empty state.
    fireEvent.click(screen.getByRole('button', { name: /reset session/i }));

    expect(screen.getByTestId('empty-dropzone')).toBeInTheDocument();
  });
});
