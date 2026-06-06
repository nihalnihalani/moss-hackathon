# CrossExam — Frontend

> Ask a 900-page document a question out loud and watch a glowing bounding box snap onto the exact cited line.

React + TypeScript + Vite. A LiveKit voice UI (audio orb, agent-state pill, streaming captions) on the left; a fixed-zoom PDF canvas on the right that snaps a glowing bbox onto the cited line. The **bbox snap is the hero moment**.

## Quick start (mock mode — no backend, no keys)

```bash
cd crossexam/frontend
npm install
npm run dev
```

Open the printed URL. The app lands straight on the document (no sign-in). Click **▶ Run demo** to play the scripted sequence:

`LISTENING → THINKING (page-jump “searching 912 pages”) → SPEAKING + SNAP (bbox on p.687, chip “found in 912 pages · 7ms”) → contradiction snap on p.203.`

Mock mode is automatic whenever `VITE_LIVEKIT_URL` / `VITE_LIVEKIT_TOKEN` are unset, or when **Force mock** is toggled.

## Run against a live backend

Copy `.env.example` to `.env` and fill in:

| Var | Meaning |
|---|---|
| `VITE_LIVEKIT_URL` | LiveKit server WS URL, e.g. `wss://you.livekit.cloud` |
| `VITE_LIVEKIT_TOKEN` | Short-lived room access token from your token endpoint |
| `VITE_MOCK_MODE` | `true` to force mock regardless of the above |
| `VITE_PDF_URL` | Optional URL of the real scanned PDF to render instead of the placeholder |

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
  lib/bbox.ts              THE transform (pure, tested)
  lib/bbox.test.ts         scale / offset / dpr / clamp / projector fixture
  lib/mockData.ts          scripted demo: p.687 answer + p.203 contradiction
  hooks/useCrossExam.ts    LiveKit wiring + mock sequence driver
  components/
    VoiceOrb.tsx StatePill.tsx Captions.tsx
    PdfCanvas.tsx           renders page + snaps the bbox overlay
    LatencyChip.tsx PageJump.tsx
    __tests__/PdfCanvas.test.tsx
  App.tsx main.tsx styles.css
```
