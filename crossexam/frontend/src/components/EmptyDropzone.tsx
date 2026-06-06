/**
 * EmptyDropzone — the first-run document canvas (Quick Win #2).
 *
 * When no document is loaded, the centre stage IS a frosted-glass drop target:
 * drag a Deposition, Contract, or Brief onto it (or click "browse files") to
 * ingest. Drop is routed through the SAME ingest path as the command-bar
 * uploader (POST /documents in live, the offline ingest in mock), and the file
 * is rendered locally so the canvas reflects it immediately.
 *
 * States (frosted idle / accept / reject / busy) are driven by react-dropzone's
 * isDragActive/isDragAccept/isDragReject and surfaced via a `data-drag` attr so
 * the styling lives in CSS. Keyboard-openable (noKeyboard:false), labelled, and
 * success/failure is announced through an aria-live region.
 */

import { useCallback, useState } from 'react';
import { useDropzone, type FileRejection } from 'react-dropzone';
import { UploadCloud, FileText } from 'lucide-react';
import { ApiError, uploadDocument, type UploadResponse } from '../lib/api';
import { useToast } from './ToastContext';

export interface EmptyDropzoneProps {
  /** Called once the backend has ingested the dropped file (same contract as DocumentUpload). */
  onUploaded: (result: UploadResponse, file: File) => void;
  /** Disable interaction (e.g. while a session is still resolving). */
  disabled?: boolean;
}

/**
 * Accepted document types: PDF only. The backend /documents endpoint rejects
 * anything else with a 415, so the dropzone must not advertise docx/txt.
 * Single file only.
 */
const ACCEPT = {
  'application/pdf': ['.pdf'],
} as const;

export function EmptyDropzone({ onUploaded, disabled = false }: EmptyDropzoneProps): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const toast = useToast();

  const ingest = useCallback(
    (file: File): void => {
      setBusy(true);
      setStatus(`Ingesting ${file.name}…`);
      void uploadDocument(file)
        .then((result) => {
          setBusy(false);
          setStatus(`Loaded ${file.name}: ${result.pages} pages in evidence.`);
          toast.success(
            `Indexed ${file.name}: ${result.pages} pages, ${result.chunksIndexed} chunks (${result.mode}).`,
          );
          onUploaded(result, file);
        })
        .catch((err: unknown) => {
          setBusy(false);
          const msg =
            err instanceof ApiError
              ? `Could not ingest ${file.name}: ${err.message}`
              : `Could not ingest ${file.name}: ${err instanceof Error ? err.message : String(err)}`;
          setStatus(msg);
          toast.error(msg);
        });
    },
    [onUploaded, toast],
  );

  const onDrop = useCallback(
    (accepted: File[], rejections: FileRejection[]): void => {
      if (disabled || busy) return;
      const file = accepted[0];
      if (file) {
        ingest(file);
        return;
      }
      const reason = rejections[0]?.errors[0]?.message;
      const msg = reason
        ? `That file can't be used: ${reason}`
        : 'That file type is not supported. Use a PDF.';
      setStatus(msg);
      toast.error(msg);
    },
    [disabled, busy, ingest, toast],
  );

  const { getRootProps, getInputProps, isDragActive, isDragAccept, isDragReject, open } =
    useDropzone({
      onDrop,
      accept: ACCEPT,
      multiple: false,
      noKeyboard: false,
      disabled: disabled || busy,
    });

  const dragState = isDragReject
    ? 'reject'
    : isDragAccept
      ? 'accept'
      : isDragActive
        ? 'active'
        : 'idle';

  return (
    <div className="empty-dropzone" data-testid="empty-dropzone">
      <div
        {...getRootProps({
          className: 'empty-dropzone__panel',
          'aria-label':
            'Drag and drop a Deposition, Contract, or Brief, or activate to browse files',
          'data-drag': dragState,
          'data-busy': busy ? 'true' : undefined,
        })}
      >
        <input {...getInputProps()} data-testid="empty-dropzone-input" />

        <span className="empty-dropzone__glyph" aria-hidden="true">
          {busy ? <FileText size={34} /> : <UploadCloud size={34} />}
        </span>

        <p className="empty-dropzone__headline">
          {busy ? 'Submitting into evidence…' : 'Drag & drop a Deposition, Contract, or Brief to begin'}
        </p>
        <p className="empty-dropzone__hint">PDF · single file</p>

        <button
          type="button"
          className="btn btn--primary empty-dropzone__browse"
          onClick={(e) => {
            e.stopPropagation();
            open();
          }}
          disabled={disabled || busy}
        >
          Browse files
        </button>
      </div>

      {/* Announce ingest success/failure to assistive tech. */}
      <p className="visually-hidden" role="status" aria-live="polite" data-testid="empty-dropzone-status">
        {status}
      </p>
    </div>
  );
}
