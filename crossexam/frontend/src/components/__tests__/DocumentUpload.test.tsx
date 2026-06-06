/**
 * DocumentUpload.test.tsx — UI states for the PDF upload control.
 *
 * uploadDocument is mocked so we can drive success/error without a backend.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { DocumentUpload } from '../DocumentUpload';
import { ApiError } from '../../lib/api';

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api');
  return { ...actual, uploadDocument: vi.fn() };
});

import * as api from '../../lib/api';
const uploadDocument = vi.mocked(api.uploadDocument);

const pdf = new File([new Uint8Array([1])], 'dep.pdf', { type: 'application/pdf' });

function selectFile(file: File): void {
  const input = document.querySelector<HTMLInputElement>('.doc-upload__input');
  expect(input).not.toBeNull();
  if (input) fireEvent.change(input, { target: { files: [file] } });
}

describe('DocumentUpload', () => {
  beforeEach(() => vi.clearAllMocks());

  it('reports the ingest result on success', async () => {
    uploadDocument.mockResolvedValue({
      documentId: 'doc-1',
      pages: 42,
      chunksIndexed: 117,
      mode: 'live',
    });
    const onUploaded = vi.fn();
    render(<DocumentUpload onUploaded={onUploaded} />);

    selectFile(pdf);

    await waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('status').textContent).toMatch(/42 pages/);
  });

  it('shows an error message when the upload fails', async () => {
    uploadDocument.mockRejectedValue(new ApiError('boom', 500));
    render(<DocumentUpload onUploaded={vi.fn()} />);

    selectFile(pdf);

    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Upload failed/));
  });

  it('rejects a non-PDF file before calling the API', () => {
    render(<DocumentUpload onUploaded={vi.fn()} />);
    selectFile(new File(['x'], 'notes.txt', { type: 'text/plain' }));
    expect(uploadDocument).not.toHaveBeenCalled();
    expect(screen.getByRole('status').textContent).toMatch(/choose a PDF/i);
  });
});
