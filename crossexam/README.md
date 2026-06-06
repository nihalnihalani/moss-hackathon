# CrossExam

CrossExam is a real-time voice agent that **cross-examines large documents**. You talk to it;
it answers out loud and **snaps a highlight box onto the exact cited line** of the PDF. It uses
Moss for sub-10ms in-process semantic retrieval, an offline Unsiloed pipeline to turn PDFs into
citation-grade chunks (page + bounding box + confidence), and LiveKit for the live voice loop.
Everything runs end-to-end in **mock mode with no API keys**, so you can demo it offline.

## Architecture

```
                 OFFLINE (pipeline)                         ONLINE (live demo)
   ┌──────────────────────────────────────┐     ┌────────────────────────────────────────┐
   │  PDF ──► Unsiloed Parse/Extract       │     │   Browser (frontend, React+Vite)         │
   │        (or deterministic --dry-run)   │     │   • LiveKit voice UI                      │
   │            │                          │     │   • pdf.js viewer + bbox highlight        │
   │            ▼                          │     │            ▲  audio          ▲ citations  │
   │   ParsedChunk JSON                    │     └────────────┼───────────────┼─────────────┘
   │   (page, bbox, words, confidence)     │            LiveKit room          │
   │            │ build-index              │                  │               │
   │            ▼                          │     ┌────────────┴───────────────┴─────────────┐
   │   Moss index  ◄────── chunks ────────►│     │   Backend worker (LiveKit agent)          │
   │   (or backend mock fixture JSON)      │────►│   STT ─► LLM grounded on Moss retrieval ─► │
   └──────────────────────────────────────┘     │   TTS ; returns answer + cited chunk bbox  │
                                                 └────────────────────────────────────────────┘
```

Subsystems (each has its own README):

| Path           | What                                   | Stack                          |
| -------------- | -------------------------------------- | ------------------------------ |
| `backend/`     | LiveKit voice-agent worker + retrieval | Python, `crossexam_backend`    |
| `pipeline/`    | Offline PDF → indexable chunks         | Python, `crossexam_pipeline`   |
| `frontend/`    | Voice UI + PDF highlight viewer        | React + TypeScript + Vite      |

## Quickstart

Requires Python 3.10+ and Node.js 20+. From this directory (`crossexam/`):

```bash
make setup     # install backend + pipeline deps (editable) and `npm ci` the frontend
make test      # run all suites: pytest (backend + pipeline) + vitest (frontend)
make dev       # run the backend worker AND the frontend dev server together
make dev-live  # run the HTTP API + worker + frontend together (token + upload + voice)
```

Then open http://localhost:5173 . `make dev` / `make dev-live` run in mock mode unless you
provide keys.

The browser talks to a small **HTTP API service** (`crossexam-api`, FastAPI on
`http://localhost:8000`): it mints LiveKit join tokens (`POST /token`), ingests uploaded
PDFs (`POST /documents`), and reports readiness (`GET /healthz`, `GET /config`). The frontend
is **live-by-default**: it reads `VITE_API_URL` (default `http://localhost:8000`), fetches
`/config`, and connects live as soon as keys are present — falling back to the mock UI
otherwise. Run just the API with `make api`.

To go fully real, see **[`KEYS.md`](./KEYS.md)** for the exact key list and run
`make verify-live` — it prints the doctor READY/MISSING/MOCK table and probes `/healthz`
and `/config`.

> Tip: use an isolated virtualenv for the Python packages:
> `python3 -m venv .venv && source .venv/bin/activate` before `make setup`.

### All make targets

```
make help        # list targets
make setup       # install everything (setup-backend / -pipeline / -frontend)
make dev         # backend worker + frontend dev server
make test        # pytest x2 + vitest
make lint        # ruff (python, advisory) + eslint (frontend)
make fmt         # ruff format + prettier
make typecheck   # mypy (python, advisory) + tsc --noEmit
make api         # run the FastAPI HTTP service (token + upload + config) locally
make dev-live    # HTTP API + voice worker + frontend dev server together
make verify-live # doctor READY/MISSING/MOCK table + curl /healthz & /config
make build       # docker build backend + frontend images
make index       # offline pipeline dry-run: parse → build-index (no keys)
make clean       # remove caches / build artifacts
```

Lint/typecheck for the Python packages are **advisory**: the source targets specific
ruff/mypy/livekit-agents versions, and newer releases flag version-drift style/stub noise on
otherwise-unchanged code. They report findings but never block. `pytest`, `tsc`, `eslint`,
`vitest`, and `vite build` are the hard correctness gates.

## Mock mode (no keys needed)

CrossExam is built to demo with **zero secrets**:

- **Backend** auto-detects missing Moss credentials and falls back to an in-process mock index
  loaded from `backend/fixtures/sample_chunks.json` (see `crossexam_backend/config.py`). If
  `livekit-agents` or LiveKit creds are absent it prints guidance instead of crashing.
- **Pipeline** `parse --dry-run` uses a deterministic, network-free parser over a bundled sample;
  `build-index --dry-run` writes backend-compatible chunk JSON instead of calling Moss.
- **Frontend** defaults to a mock UI when `VITE_LIVEKIT_URL` / `VITE_LIVEKIT_TOKEN` are unset
  (or `VITE_MOCK_MODE=true`).

Copy `.env.example` → `.env` only when you want to wire up the real services. `.env` is gitignored.

## Run the 90-second demo

```bash
make setup           # 1. install (once)
make index           # 2. build the (mock) index from the bundled sample deposition
make dev             # 3. start backend worker + frontend
# open http://localhost:5173
```

Then, in the browser: start the session and ask the agent a question about the document
(e.g. *"Where was the witness on the night of the 14th?"*). It answers by voice and the PDF
pane snaps a highlight onto the cited line. All of this runs offline in mock mode.

Containerized alternative:

```bash
docker compose up --build      # backend worker + frontend on http://localhost:5173
```

`docker-compose.yml` treats `.env` as optional, so the stack still comes up in mock mode with
no secrets present.

## Environment variables

Full documentation lives in [`.env.example`](./.env.example). Summary:

| Variable                                            | Used by            | Required?                          |
| --------------------------------------------------- | ------------------ | ---------------------------------- |
| `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY`               | backend, pipeline  | Real Moss only (else mock)         |
| `MOSS_INDEX_NAME`                                   | backend, pipeline  | Optional (defaults set)            |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | backend         | Live voice only                    |
| `UNSILOED_API_KEY`                                  | pipeline           | Real `parse` only (not `--dry-run`)|
| `MINIMAX_API_KEY` / `AWS_*`                         | backend            | Optional LLM/TTS provider          |
| `TOP_K`, `ALPHA`, `LOG_LEVEL`, `USE_MOCKS`          | backend            | Optional tuning (`USE_MOCKS=false` => real) |
| `API_HOST`, `API_PORT`, `CORS_ORIGINS`              | backend (HTTP API) | Optional (local-dev defaults)      |
| `TOKEN_TTL_SECONDS`, `MAX_UPLOAD_MB`, `LIVEKIT_DEFAULT_ROOM` | backend (HTTP API) | Optional (defaults set)    |
| `VITE_API_URL`                                      | frontend (build)   | Optional (default `http://localhost:8000`) |
| `VITE_LIVEKIT_URL`, `VITE_LIVEKIT_TOKEN`            | frontend (build)   | Live voice only                    |
| `VITE_PDF_URL`                                      | frontend (build)   | Optional (PDF to render)           |
| `VITE_MOCK_MODE`                                    | frontend (build)   | Optional (force mock)              |

`VITE_*` values are baked in at build time (`npm run build` / `docker compose build`), not at runtime.

## CI

`.github/workflows/ci.yml` runs three jobs on every push and pull request — backend, pipeline,
frontend — entirely in mock mode (no secrets). pytest / eslint / tsc / vitest / vite build are
blocking; ruff and mypy are advisory (see note above).

## Docs

Additional design notes and demo material live in [`../docs/`](../docs/).
