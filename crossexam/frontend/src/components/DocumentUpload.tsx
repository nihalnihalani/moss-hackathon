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

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ApiError, uploadDocument, type UploadResponse } from '../lib/api';
import { useToast } from './ToastContext';
import { Upload, Check, FilePlus2 } from 'lucide-react';

export interface DocumentUploadProps {
  /** Called once the backend has ingested the PDF. */
  onUploaded: (result: UploadResponse, file: File) => void;
  /** Disable the control (e.g. while a session is still resolving). */
  disabled?: boolean;
  /**
   * 'default' is the command-bar "Submit document into evidence" control.
   * 'compact' is the floating "+ New document" affordance shown over the canvas
   * once a document is loaded (Quick Win #2) — same picker, smaller chrome.
   */
  variant?: 'default' | 'compact';
}

export function DocumentUpload({ onUploaded, disabled, variant = 'default' }: DocumentUploadProps): JSX.Element {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<number | undefined>(undefined);
  const [dragOver, setDragOver] = useState(false);
  const [done, setDone] = useState(false);
  const toast = useToast();

  // Briefly show the "in evidence" success state, then return to the prompt.
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(false), 2600);
    return () => clearTimeout(t);
  }, [done]);

  const startUpload = useCallback(
    (file: File): void => {
      if (file.type && file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
        toast.error('Please choose a PDF file.');
        return;
      }
      setUploading(true);
      setProgress(0);

      void uploadDocument(file, (frac) => setProgress(frac))
        .then((result) => {
          setUploading(false);
          setProgress(undefined);
          setDone(true);
          toast.success(
            `Indexed ${file.name}: ${result.pages} pages, ${result.chunksIndexed} chunks (${result.mode}).`,
          );
          onUploaded(result, file);
        })
        .catch((err: unknown) => {
          setUploading(false);
          setProgress(undefined);
          toast.error(
            err instanceof ApiError
              ? `Upload failed: ${err.message}`
              : `Upload failed: ${err instanceof Error ? err.message : String(err)}`,
          );
        });
    },
    [onUploaded, toast],
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

  const pct = progress === undefined ? undefined : Math.round(progress * 100);
  const compact = variant === 'compact';

  return (
    <div className={`doc-upload${compact ? ' doc-upload--compact' : ''}`}>
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
        className={`doc-upload__zone${dragOver ? ' doc-upload__zone--over' : ''}${done ? ' doc-upload__zone--done' : ''}`}
        onClick={openPicker}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        disabled={disabled || uploading}
        aria-label={
          compact
            ? 'Add a new PDF document. Click to browse or drop a file here.'
            : 'Submit a PDF document into evidence. Click to browse or drop a file here.'
        }
        aria-busy={uploading}
      >
        {uploading ? (
          pct === undefined ? (
            'Submitting…'
          ) : (
            `Submitting ${pct}%`
          )
        ) : done ? (
          <>
            <Check size={14} /> In evidence
          </>
        ) : compact ? (
          <>
            <FilePlus2 size={14} /> New document
          </>
        ) : (
          <>
            <Upload size={14} /> Submit document into evidence
          </>
        )}
      </button>

      {uploading ? (
        <div className="animated-progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Upload progress">
          <div className="animated-progress__fill" style={{ width: `${pct ?? 0}%` }} />
        </div>
      ) : null}
    </div>
  );
}
