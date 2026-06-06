/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend FastAPI base URL. Defaults to http://localhost:8000. */
  readonly VITE_API_URL?: string;
  readonly VITE_LIVEKIT_URL?: string;
  readonly VITE_LIVEKIT_TOKEN?: string;
  readonly VITE_MOCK_MODE?: string;
  readonly VITE_PDF_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
