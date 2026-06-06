/**
 * api.test.ts — unit coverage for the typed backend client.
 *
 * fetch is mocked per-test (global.fetch); uploadDocument is exercised against a
 * fake XMLHttpRequest. Covers happy + error paths for config/token/upload and
 * the malformed-payload guards that keep bad data out of app state.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  ApiError,
  apiBaseUrl,
  checkHealth,
  fetchConfig,
  requestToken,
  uploadDocument,
  DEFAULT_API_URL,
} from './api';

function jsonResponse(body: unknown, init?: { status?: number; ok?: boolean }): Response {
  const status = init?.status ?? 200;
  return {
    ok: init?.ok ?? (status >= 200 && status < 300),
    status,
    url: 'http://localhost:8000/x',
    json: async () => body,
  } as unknown as Response;
}

describe('apiBaseUrl', () => {
  it('defaults to localhost:8000 and trims trailing slashes', () => {
    // import.meta.env.VITE_API_URL is unset under vitest -> default.
    expect(apiBaseUrl()).toBe(DEFAULT_API_URL);
  });
});

describe('fetchConfig', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns a normalized config on success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ livekit_url: 'wss://x.livekit.cloud', live: true })),
    );
    const cfg = await fetchConfig();
    expect(cfg).toEqual({ livekitUrl: 'wss://x.livekit.cloud', live: true });
  });

  it('maps a null livekit_url to null', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ livekit_url: null, live: false })));
    const cfg = await fetchConfig();
    expect(cfg).toEqual({ livekitUrl: null, live: false });
  });

  it('throws ApiError on non-2xx', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({}, { status: 500 })));
    await expect(fetchConfig()).rejects.toBeInstanceOf(ApiError);
  });

  it('throws ApiError on a malformed payload (missing live)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ livekit_url: 'x' })));
    await expect(fetchConfig()).rejects.toBeInstanceOf(ApiError);
  });

  it('throws ApiError on a network failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('ECONNREFUSED');
      }),
    );
    await expect(fetchConfig()).rejects.toBeInstanceOf(ApiError);
  });
});

describe('checkHealth', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns true on 2xx', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({}, { status: 200 })));
    expect(await checkHealth()).toBe(true);
  });

  it('returns false on non-2xx and never throws on network error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({}, { status: 503 })));
    expect(await checkHealth()).toBe(false);

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new Error('down');
      }),
    );
    expect(await checkHealth()).toBe(false);
  });
});

describe('requestToken', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns a token on success and POSTs the body', async () => {
    const fetchMock = vi.fn(
      async (_input: string, _init?: RequestInit): Promise<Response> =>
        jsonResponse({ token: 'tok', url: 'wss://x', room: 'r1' }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const out = await requestToken({ room: 'r1', identity: 'me' });
    expect(out).toEqual({ token: 'tok', url: 'wss://x', room: 'r1' });

    const call = fetchMock.mock.calls[0];
    expect(call?.[0]).toBe('http://localhost:8000/token');
    const init = call?.[1];
    expect(init?.method).toBe('POST');
    expect(JSON.parse(init?.body as string)).toEqual({ room: 'r1', identity: 'me' });
  });

  it('accepts a livekit_url-only payload (legacy backend shape)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ token: 'tok', livekit_url: 'wss://legacy', room: 'r1' })),
    );
    const out = await requestToken();
    expect(out).toEqual({ token: 'tok', url: 'wss://legacy', room: 'r1' });
  });

  it('prefers url over livekit_url when both are present', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({ token: 'tok', url: 'wss://new', livekit_url: 'wss://legacy', room: 'r1' }),
      ),
    );
    const out = await requestToken();
    expect(out).toEqual({ token: 'tok', url: 'wss://new', room: 'r1' });
  });

  it('throws ApiError on non-2xx', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({}, { status: 401 })));
    await expect(requestToken()).rejects.toBeInstanceOf(ApiError);
  });

  it('throws ApiError on a malformed payload (no url or livekit_url)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ token: 'tok', room: 'r1' })));
    await expect(requestToken()).rejects.toBeInstanceOf(ApiError);
  });
});

// --- uploadDocument: fake XMLHttpRequest -----------------------------------

interface FakeUpload {
  onprogress: ((e: ProgressEvent) => void) | null;
}
class FakeXHR {
  static instances: FakeXHR[] = [];
  upload: FakeUpload = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  status = 200;
  response: unknown = null;
  responseType = '';
  method = '';
  url = '';
  sent: unknown = null;
  constructor() {
    FakeXHR.instances.push(this);
  }
  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
  }
  send(body: unknown): void {
    this.sent = body;
  }
  abort(): void {
    this.onabort?.();
  }
}

describe('uploadDocument', () => {
  beforeEach(() => {
    FakeXHR.instances = [];
    vi.stubGlobal('XMLHttpRequest', FakeXHR as unknown as typeof XMLHttpRequest);
  });
  afterEach(() => vi.restoreAllMocks());

  const file = new File([new Uint8Array([1, 2, 3])], 'dep.pdf', { type: 'application/pdf' });

  it('resolves with a normalized response and reports progress', async () => {
    const progress: Array<number | undefined> = [];
    const promise = uploadDocument(file, (f) => progress.push(f));

    const xhr = FakeXHR.instances[0];
    expect(xhr).toBeDefined();
    expect(xhr?.url).toBe('http://localhost:8000/documents');

    // Emit a determinate progress event.
    xhr?.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 } as ProgressEvent);
    // Emit a successful load.
    if (xhr) {
      xhr.status = 201;
      xhr.response = {
        document_id: 'doc-1',
        pages: 42,
        chunks_indexed: 117,
        mode: 'live',
      };
      xhr.onload?.();
    }

    await expect(promise).resolves.toEqual({
      documentId: 'doc-1',
      pages: 42,
      chunksIndexed: 117,
      mode: 'live',
    });
    expect(progress).toContain(0.5);
  });

  it('rejects with ApiError on a non-2xx status', async () => {
    const promise = uploadDocument(file);
    const xhr = FakeXHR.instances[0];
    if (xhr) {
      xhr.status = 500;
      xhr.onload?.();
    }
    await expect(promise).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError on a malformed payload', async () => {
    const promise = uploadDocument(file);
    const xhr = FakeXHR.instances[0];
    if (xhr) {
      xhr.status = 200;
      xhr.response = { document_id: 'doc-1' };
      xhr.onload?.();
    }
    await expect(promise).rejects.toBeInstanceOf(ApiError);
  });

  it('rejects with ApiError on a network error', async () => {
    const promise = uploadDocument(file);
    FakeXHR.instances[0]?.onerror?.();
    await expect(promise).rejects.toBeInstanceOf(ApiError);
  });
});
