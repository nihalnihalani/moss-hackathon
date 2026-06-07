/**
 * api.ts — typed client for the CrossExam backend (FastAPI).
 *
 * The backend exposes:
 *   GET  {VITE_API_URL}/config    -> { livekit_url: string|null, live: boolean }
 *   POST {VITE_API_URL}/token     -> { token, room, url | livekit_url }
 *   POST {VITE_API_URL}/documents -> { document_id, pages, chunks_indexed, mode, pdf_url? }
 *   GET  {VITE_API_URL}/documents/{id}/pdf -> persisted uploaded PDF bytes
 *   GET  {VITE_API_URL}/healthz   -> 200 when up
 *
 * Every call is defensively typed: responses are narrowed at runtime so a
 * malformed/partial payload throws an ApiError instead of poisoning state.
 * No `any` in the public surface. The base URL comes from VITE_API_URL and
 * defaults to http://localhost:8000.
 */

/** Default backend base URL when VITE_API_URL is unset. */
export const DEFAULT_API_URL = 'http://localhost:8000';

/** Resolve the configured backend base URL (trailing slash trimmed). */
export function apiBaseUrl(): string {
  const env = import.meta.env as { VITE_API_URL?: string };
  const raw = env.VITE_API_URL?.trim() || DEFAULT_API_URL;
  return raw.replace(/\/+$/, '');
}

/** Raised for any non-2xx response or malformed body. */
export class ApiError extends Error {
  readonly status: number | undefined;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Shape of GET /config. `livekitUrl` is null when the backend has no LiveKit configured. */
export interface BackendConfig {
  livekitUrl: string | null;
  live: boolean;
}

/** Shape of POST /token. */
export interface TokenResponse {
  token: string;
  url: string;
  room: string;
}

/** Body for POST /token. Both fields optional — backend defaults if absent. */
export interface TokenRequest {
  room?: string;
  identity?: string;
}

/** Shape of POST /documents. */
export interface UploadResponse {
  documentId: string;
  pages: number;
  chunksIndexed: number;
  mode: string;
  pdfUrl?: string;
}

/** Progress callback for uploadDocument (0..1, or undefined if indeterminate). */
export type UploadProgress = (fraction: number | undefined) => void;

/** Resolve the API URL serving a persisted uploaded PDF. */
export function documentPdfUrl(documentId: string): string {
  return `${apiBaseUrl()}/documents/${encodeURIComponent(documentId)}/pdf`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

async function parseJson(res: Response): Promise<unknown> {
  try {
    return (await res.json()) as unknown;
  } catch {
    throw new ApiError(`Invalid JSON in response from ${res.url}`, res.status);
  }
}

/**
 * GET /config — discover whether the backend wants a LIVE session.
 * Throws ApiError on network failure, non-2xx, or a malformed body.
 */
export async function fetchConfig(signal?: AbortSignal): Promise<BackendConfig> {
  const url = `${apiBaseUrl()}/config`;
  let res: Response;
  try {
    res = await fetch(url, { method: 'GET', signal });
  } catch (err) {
    throw new ApiError(`Network error reaching ${url}: ${errMessage(err)}`);
  }
  if (!res.ok) throw new ApiError(`GET /config failed (${res.status})`, res.status);

  const body = await parseJson(res);
  if (!isRecord(body) || typeof body.live !== 'boolean') {
    throw new ApiError('Malformed /config payload: expected { live: boolean }', res.status);
  }
  const livekitUrl =
    typeof body.livekit_url === 'string' ? body.livekit_url : null;
  return { livekitUrl, live: body.live };
}

/** GET /healthz — true when the backend answers 2xx, false otherwise (never throws). */
export async function checkHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const res = await fetch(`${apiBaseUrl()}/healthz`, { method: 'GET', signal });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * POST /token — request a LiveKit access token for a room.
 * Throws ApiError on network failure, non-2xx, or a malformed body.
 */
export async function requestToken(
  body: TokenRequest = {},
  signal?: AbortSignal,
): Promise<TokenResponse> {
  const url = `${apiBaseUrl()}/token`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    throw new ApiError(`Network error reaching ${url}: ${errMessage(err)}`);
  }
  if (!res.ok) throw new ApiError(`POST /token failed (${res.status})`, res.status);

  const data = await parseJson(res);
  // The backend is migrating from `livekit_url` to `url`; accept EITHER so live
  // mode engages regardless of which shape the deployed backend sends. Prefer
  // `url`, fall back to `livekit_url`.
  const wsUrl =
    isRecord(data) && typeof data.url === 'string'
      ? data.url
      : isRecord(data) && typeof data.livekit_url === 'string'
        ? data.livekit_url
        : undefined;
  if (
    !isRecord(data) ||
    typeof data.token !== 'string' ||
    wsUrl === undefined ||
    typeof data.room !== 'string'
  ) {
    throw new ApiError(
      'Malformed /token payload: expected { token, room, url|livekit_url }',
      res.status,
    );
  }
  return { token: data.token, url: wsUrl, room: data.room };
}

/**
 * POST /documents — upload a PDF for ingest/indexing.
 *
 * Uses XMLHttpRequest (not fetch) so we can report deterministic upload
 * progress. Throws ApiError on network failure, non-2xx, or a malformed body.
 */
export function uploadDocument(
  file: File,
  onProgress?: UploadProgress,
  signal?: AbortSignal,
): Promise<UploadResponse> {
  const url = `${apiBaseUrl()}/documents`;
  return new Promise<UploadResponse>((resolve, reject) => {
    const form = new FormData();
    form.append('file', file, file.name);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.responseType = 'json';

    if (onProgress && xhr.upload) {
      xhr.upload.onprogress = (e: ProgressEvent): void => {
        onProgress(e.lengthComputable ? e.loaded / e.total : undefined);
      };
    }

    xhr.onload = (): void => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new ApiError(`POST /documents failed (${xhr.status})`, xhr.status));
        return;
      }
      const data: unknown = xhr.response;
      if (
        !isRecord(data) ||
        typeof data.document_id !== 'string' ||
        typeof data.pages !== 'number' ||
        typeof data.chunks_indexed !== 'number' ||
        typeof data.mode !== 'string'
      ) {
        reject(
          new ApiError(
            'Malformed /documents payload: expected { document_id, pages, chunks_indexed, mode }',
            xhr.status,
          ),
        );
        return;
      }
      resolve({
        documentId: data.document_id,
        pages: data.pages,
        chunksIndexed: data.chunks_indexed,
        mode: data.mode,
        pdfUrl: typeof data.pdf_url === 'string' ? data.pdf_url : undefined,
      });
    };

    xhr.onerror = (): void => reject(new ApiError(`Network error reaching ${url}`));
    xhr.onabort = (): void => reject(new ApiError('Upload aborted'));

    if (signal) {
      if (signal.aborted) {
        xhr.abort();
      } else {
        signal.addEventListener('abort', () => xhr.abort(), { once: true });
      }
    }

    xhr.send(form);
  });
}

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
