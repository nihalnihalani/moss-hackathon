/**
 * EmptyDropzone.test.tsx — the first-run drop target (Quick Win #2).
 *
 * uploadDocument is mocked so a dropped PDF routes through the ingest path
 * without a backend. We assert the empty state renders, exposes a labelled
 * keyboard-openable target + browse button, and that dropping a PDF triggers
 * ingest and reports the result back to the parent.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { EmptyDropzone } from '../EmptyDropzone';
import { ToastProvider } from '../ToastContext';

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, uploadDocument: vi.fn() };
});

import * as api from '../../lib/api';
const uploadDocument = vi.mocked(api.uploadDocument);

const pdf = new File([new Uint8Array([1, 2, 3])], 'brief.pdf', { type: 'application/pdf' });

function renderDropzone(onUploaded = vi.fn()): { onUploaded: ReturnType<typeof vi.fn> } {
  render(
    <ToastProvider>
      <EmptyDropzone onUploaded={onUploaded} />
    </ToastProvider>,
  );
  return { onUploaded };
}

describe('EmptyDropzone', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders the frosted empty state with copy + a browse button', () => {
    renderDropzone();
    expect(screen.getByTestId('empty-dropzone')).toBeInTheDocument();
    expect(screen.getByText(/Drag & drop a Deposition, Contract, or Brief to begin/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /browse files/i })).toBeInTheDocument();
    // The drop panel is labelled and keyboard-openable (react-dropzone role/aria).
    const panel = screen.getByLabelText(/Drag and drop a Deposition, Contract, or Brief/i);
    expect(panel).toHaveAttribute('data-drag', 'idle');
  });

  it('advertises PDF only (backend rejects other types with 415)', () => {
    renderDropzone();
    // The hint copy must say PDF only — no docx/txt.
    const hint = screen.getByText(/single file/i);
    expect(hint.textContent).toMatch(/PDF/);
    expect(hint.textContent).not.toMatch(/DOCX/i);
    expect(hint.textContent).not.toMatch(/TXT/i);
    // react-dropzone reflects the accept map onto the hidden input's `accept`
    // attribute: PDF mime + .pdf extension only, nothing else.
    const input = screen.getByTestId('empty-dropzone-input');
    const accept = input.getAttribute('accept') ?? '';
    expect(accept).toContain('application/pdf');
    expect(accept).toContain('.pdf');
    expect(accept).not.toMatch(/wordprocessingml|\.docx|text\/plain|\.txt/i);
  });

  it('accepts a dropped PDF and routes it through the ingest path', async () => {
    uploadDocument.mockResolvedValue({
      documentId: 'doc-brief',
      pages: 12,
      chunksIndexed: 40,
      mode: 'mock',
    });
    const { onUploaded } = renderDropzone();

    const input = screen.getByTestId('empty-dropzone-input');
    // react-dropzone reads File objects off the input's files on change.
    Object.defineProperty(input, 'files', { value: [pdf], configurable: true });
    fireEvent.change(input);

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(1));
    expect(uploadDocument.mock.calls[0]?.[0]).toBe(pdf);
    await waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(1));
    expect(onUploaded.mock.calls[0]?.[0]).toMatchObject({ documentId: 'doc-brief', pages: 12 });
  });
});
