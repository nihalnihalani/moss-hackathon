# CrossExam — Frontend

> Ask a 900-page document a question out loud and watch a glowing bounding box snap onto the exact cited line.

React + TypeScript + Vite. A LiveKit voice UI (audio orb, agent-state pill, streaming captions) on the left; a fixed-zoom PDF canvas on the right that snaps a glowing bbox onto the cited line. The **bbox snap is the hero moment**.

## Quick start (mock mode — no backend, no keys)

```bash
cd crossexam/frontend
npm install
npm run dev
```

Open the printed URL. The app lands straight on the document (no sign-in) rendering the **real sample deposition** from `public/sample-deposition.pdf`. Click **▶ Run demo** to play the scripted sequence:

`LISTENING → THINKING (page-jump “searching 912 pages”) → SPEAKING + SNAP (bbox on the warehouse-admission line, chip “found in 912 pages · 7ms”) → contradiction snap on the visitor-log line.`

With no backend running it lands in mock mode automatically. Even in mock mode the canvas renders the real PDF — only the agent state, captions, and citation timings are scripted.

## How live vs mock is decided

On startup `useBackendSession` (see `src/hooks/useBackendSession.ts`) runs this decision **once**:

1. `GET {VITE_API_URL}/config` (`VITE_API_URL` defaults to `http://localhost:8000`).
2. If the backend answers and `config.live === true`, it `POST`s `{VITE_API_URL}/token` to mint a LiveKit access token, then connects a **REAL** session — the backend agent drives agent-state, captions, and citations over the LiveKit data channel.
3. On **any** failure — backend unreachable, non-2xx, `live:false`, or no LiveKit URL — it **falls back to the scripted mock** so the offline demo still works.

The header badge surfaces the resolved mode: **LIVE · CONNECTED / CONNECTING…** vs **MOCK / OFFLINE** (hover for the fallback reason). Toggling **Force mock** skips the backend entirely and pins mock mode (also settable at build time via `VITE_MOCK_MODE=true`).

The typed backend client lives in `src/lib/api.ts` (`fetchConfig`, `requestToken`, `uploadDocument`, `checkHealth`) — all responses are runtime-narrowed and throw `ApiError` on failure; the base URL is `VITE_API_URL`.

## Uploading a document

The header has a **⬆ Upload PDF** control (click or drag-and-drop) that `POST`s the file to `{VITE_API_URL}/documents` (multipart), shows determinate progress, and on success renders the uploaded PDF on the canvas and updates the corpus page count from the ingest result (`pages`, `chunks_indexed`, `mode`). It works in both live and mock modes — in mock mode the backend's offline ingest still indexes it. Errors (non-PDF, network, non-2xx) are announced in an `aria-live` status line.

### The real PDF + bbox alignment

`VITE_PDF_URL` controls which document the canvas renders; it **defaults to `/sample-deposition.pdf`** (served from `public/`), so the real scanned transcript shows out of the box with no backend. If the file is missing or pdf.js fails to load its worker, `PdfCanvas` silently falls back to drawing a placeholder page, so the demo never hard-fails.

The canvas always shows the page a citation points to: `App` passes `page={targetPage}`, and both the mock script and the live data-channel handler set `targetPage = citation.bbox.page`. `PdfCanvas` navigates pdf.js to that page and only draws the overlay when `citation.bbox.page === page`, so the glowing box can never land on the wrong page.

For the snap to land on real text, the mock citations’ `page` + point bbox in `src/lib/mockData.ts` **must match the actual layout of `sample-deposition.pdf`** (and, ultimately, the pipeline-generated citation fixture). The “searched 912 pages” story is carried by `pagesSearched` / `DEMO_TOTAL_PAGES` and is intentionally decoupled from the (small) rendered page count of the sample PDF.

## Run against a live backend

Start the backend FastAPI service, then run the frontend. By default the frontend points at `http://localhost:8000` — no `.env` needed. To target a different host, copy `.env.example` to `.env` and set `VITE_API_URL`.

The backend must expose:

| Endpoint | Returns |
|---|---|
| `GET {VITE_API_URL}/config` | `{ livekit_url: string\|null, live: boolean }` |
| `POST {VITE_API_URL}/token` | body `{ room?, identity? }` → `{ token, url, room }` |
| `POST {VITE_API_URL}/documents` | multipart PDF → `{ document_id, pages, chunks_indexed, mode }` |
| `GET {VITE_API_URL}/healthz` | `200` when up |

| Var | Meaning |
|---|---|
| `VITE_API_URL` | Backend base URL. **Defaults to `http://localhost:8000`.** Drives `/config`, `/token`, `/documents` |
| `VITE_MOCK_MODE` | `true` to force mock and skip the backend entirely |
| `VITE_PDF_URL` | Initial PDF to render. **Defaults to `/sample-deposition.pdf`** (the real sample served from `public/`); uploads replace it. On fetch/worker failure the canvas falls back to a placeholder page |
| `VITE_LIVEKIT_URL` / `VITE_LIVEKIT_TOKEN` | _Optional/legacy._ Direct-connect overrides that bypass the backend token flow |

In live mode the hook connects to the LiveKit room and listens on the data channel. The backend should publish JSON frames shaped like:

```jsonc
{ "agentState": "speaking", "caption": "…", "question": "…",
  "citation": { "id": "…", "text": "…", "confidence": 0.94,
                "latencyMs": 7, "pagesSearched": 912,
                "bbox": { "page": 687, "x0": 84, "y0": 372, "x1": 540, "y1": 396 } } }
```

These shapes mirror `src/types.ts` (`Citation`, `BBox`, `AgentState`). LiveKit is imported lazily, so the app builds and runs with no keys.

## How the bbox math works (`src/lib/bbox.ts`)

This is the single most demo-critical file. A misaligned box loses the demo, so it is pure, exported, and unit-tested.

**Coordinate model.** Unsiloed/pdf.js emit a bbox in **PDF points** (72 pt = 1 inch) with a **top-left origin, y growing downward**. pdf.js renders the page at a chosen CSS scale `renderScale` (CSS px per point). On HiDPI we size the canvas **backing store** to `renderScale × devicePixelRatio` device pixels but lay it out via CSS at `renderScale` CSS px/pt.

The overlay div is positioned in **CSS pixels**, so the visible transform is:

```
cssX = pointX × renderScale + offsetX
cssY = pointY × renderScale + offsetY
width  = (x1 − x0) × renderScale
height = (y1 − y0) × renderScale
```

**`devicePixelRatio` does NOT scale the CSS-pixel overlay** — it only changes how many device pixels fill the backing store. (Use `toDevicePixels()` if you instead draw the highlight onto the canvas 2D context, whose space is the device-pixel backing store.)

Helpers: `normalizeBBox` (orders corners), `clampBBoxToPage` (validates finiteness + clamps to page bounds, throws if entirely off-page), `rectCenter` (for scroll-to).

**Pin the zoom for the demo.** The PDF renders at a fixed `renderScale` (default 1.25) and responsive reflow is disabled. Test at the **projector’s** DPI/resolution, not just the laptop.

## Scripts

| Script | Does |
|---|---|
| `npm run dev` | Vite dev server (mock mode by default) |
| `npm run build` | Type-check + production build |
| `npm run preview` | Preview the build |
| `npm test` | Vitest (bbox math + PdfCanvas render) |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |

## Layout

```
src/
  types.ts                 Citation, BBox, AgentState, PageRenderGeometry
  lib/api.ts               typed backend client (fetchConfig/requestToken/uploadDocument)
  lib/api.test.ts          config/token/upload happy + error paths
  lib/bbox.ts              THE transform (pure, tested)
  lib/bbox.test.ts         scale / offset / dpr / clamp / projector fixture
  lib/mockData.ts          scripted demo: warehouse answer + visitor-log contradiction
  hooks/useBackendSession.ts  startup live-vs-mock resolver (config -> token)
  hooks/useCrossExam.ts    LiveKit wiring + mock sequence driver
  hooks/__tests__/
    liveCitation.integration.test.tsx  live DataReceived → PdfCanvas snap
  __tests__/
    App.session.test.tsx   App enters live on config.live, falls back to mock
  components/
    VoiceOrb.tsx StatePill.tsx Captions.tsx
    DocumentUpload.tsx       PDF upload control (POST /documents, progress)
    DocumentUpload.test.tsx  (under __tests__/) upload success/error UI
    PdfCanvas.tsx           renders page (real PDF or placeholder) + snaps the bbox overlay
    LatencyChip.tsx PageJump.tsx
    __tests__/PdfCanvas.test.tsx __tests__/DocumentUpload.test.tsx
  App.tsx main.tsx styles.css
public/
  sample-deposition.pdf    the real sample rendered by default (VITE_PDF_URL)
```

The test suite covers both paths: `PdfCanvas.test.tsx` checks the mock-mode snap, and `liveCitation.integration.test.tsx` feeds a citation frame through the real LiveKit `DataReceived` handler in `useCrossExam` and asserts `PdfCanvas` draws the box at the exact rect `lib/bbox.ts` computes.
