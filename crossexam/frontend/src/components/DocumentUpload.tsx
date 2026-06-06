/**
 * DocumentUpload — accessible PDF upload control.
 *
 * POSTs a PDF to {VITE_API_URL}/documents, shows determinate progress, and on
 * success reports the ingest result (document_id, pages, chunks_indexed, mode)
 * back to the parent so the canvas/corpus info can update. Works in both live
 * and mock modes — in mock mode the backend's offline ingest still indexes it.
 *
 * Keyboard- and screen-reader-friendly: the dropzone is a real <button>, the
 * file input is labelled, and status changes are announced via aria-live.
 */

import { useCallback, useId, useRef, useState } from 'react';
import { ApiError, uploadDocument, type UploadResponse } from '../lib/api';

export interface DocumentUploadProps {
  /** Called once the backend has ingested the PDF. */
  onUploaded: (result: UploadResponse, file: File) => void;
  /** Disable the control (e.g. while a session is still resolving). */
  disabled?: boolean;
}

type Phase = 'idle' | 'uploading' | 'success' | 'error';

export function DocumentUpload({ onUploaded, disabled }: DocumentUploadProps): JSX.Element {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [progress, setProgress] = useState<number | undefined>(undefined);
  const [message, setMessage] = useState<string>('');
  const [dragOver, setDragOver] = useState(false);

  const startUpload = useCallback(
    (file: File): void => {
      if (file.type && file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
        setPhase('error');
        setMessage('Please choose a PDF file.');
        return;
      }
      setPhase('uploading');
      setProgress(0);
      setMessage(`Uploading ${file.name}…`);

      void uploadDocument(file, (frac) => setProgress(frac))
        .then((result) => {
          setPhase('success');
          setProgress(1);
          setMessage(
            `Indexed ${file.name}: ${result.pages} pages, ${result.chunksIndexed} chunks (${result.mode}).`,
          );
          onUploaded(result, file);
        })
        .catch((err: unknown) => {
          setPhase('error');
          setProgress(undefined);
          setMessage(
            err instanceof ApiError
              ? `Upload failed: ${err.message}`
              : `Upload failed: ${err instanceof Error ? err.message : String(err)}`,
          );
        });
    },
    [onUploaded],
  );

  const onFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>): void => {
      const file = e.target.files?.[0];
      if (file) startUpload(file);
      // Reset so re-selecting the same file fires change again.
      e.target.value = '';
    },
    [startUpload],
  );

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLButtonElement>): void => {
      e.preventDefault();
      setDragOver(false);
      if (disabled) return;
      const file = e.dataTransfer.files?.[0];
      if (file) startUpload(file);
    },
    [disabled, startUpload],
  );

  const openPicker = useCallback((): void => {
    if (!disabled) inputRef.current?.click();
  }, [disabled]);

  const uploading = phase === 'uploading';
  const pct = progress === undefined ? undefined : Math.round(progress * 100);

  return (
    <div className="doc-upload">
      <input
        ref={inputRef}
        id={inputId}
        className="doc-upload__input"
        type="file"
        accept="application/pdf,.pdf"
        onChange={onFileChange}
        disabled={disabled || uploading}
        aria-hidden="true"
        tabIndex={-1}
      />
      <button
        type="button"
        className={`doc-upload__zone${dragOver ? ' doc-upload__zone--over' : ''}`}
        onClick={openPicker}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        disabled={disabled || uploading}
        aria-label="Upload a PDF document. Click to browse or drop a file here."
        aria-busy={uploading}
      >
        {uploading
          ? pct === undefined
            ? 'Uploading…'
            : `Uploading ${pct}%`
          : '⬆ Upload PDF'}
      </button>

      {uploading ? (
        <progress
          className="doc-upload__progress"
          max={100}
          value={pct ?? undefined}
          aria-label="Upload progress"
        />
      ) : null}

      <p
        className={`doc-upload__status doc-upload__status--${phase}`}
        role="status"
        aria-live="polite"
      >
        {message}
      </p>
    </div>
  );
}
