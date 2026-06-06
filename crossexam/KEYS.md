# CrossExam — Keys & Accounts Runbook

CrossExam runs **fully offline in mock mode with zero secrets**. This document is
the precise list of keys/accounts you need to flip it to **fully real**, grouped
by capability, with what each unlocks, where to put it, and the one command to
verify everything: **`make verify-live`**.

## TL;DR

```bash
cd crossexam
cp .env.example .env                 # .env is gitignored; never commit it
pip install -e 'backend[api,voice]'  # web (api) + voice plugins in one shot
#  ...edit .env: set USE_MOCKS=false and fill the keys below...
make verify-live                     # doctor table + /healthz + /config probe
```

`make verify-live` prints a READY / MISSING / MOCK table (no network calls) and
then best-effort curls the running API. Fill in whatever shows **MISSING**.

## Where keys go

All keys live in **`crossexam/.env`** (copied from `.env.example`). `.env` and
`.env.*` are **gitignored** — only `.env.example` (no secrets) is committed.
Frontend `VITE_*` values are **baked in at build time** (`npm run build` /
`docker compose build`), not read at runtime.

## The package install (do this first)

```bash
pip install -e 'backend[api,voice]'
```

- `[api]`  → `fastapi`, `uvicorn`, `python-multipart`, **`livekit-api`** (token
  minting), `pdfplumber` (PDF upload parsing). Required to run `crossexam-api`.
- `[voice]` → `livekit-plugins-{deepgram,openai,cartesia,silero}`. Required for a
  live STT→LLM→TTS voice session.

The Docker backend image installs `[api,voice]` by default, so one image runs
both the worker and the API service.

---

## Capability 1 — Retrieval (Moss)  ·  REQUIRED for "real"

| Key | What it unlocks |
|---|---|
| `MOSS_PROJECT_ID` | Moss project to query/upsert against |
| `MOSS_PROJECT_KEY` | Single key used by BOTH backend (query) and pipeline (upsert) |
| `MOSS_INDEX_NAME` | Index name (has a default: `crossexam-documents`) |
| `USE_MOCKS=false` | **Flips off the mock fallback** so the real path is used |

- **Account:** a Moss / InferEdge project (sub-10ms in-process semantic retrieval).
- **With keys + `USE_MOCKS=false`:** `POST /documents` upserts parsed chunks into
  Moss and the agent retrieves real citations from it.
- **Without keys (or `USE_MOCKS` unset/true):** an in-process `MockIndex` loads
  `backend/fixtures/sample_chunks.json`; uploads append to that fixture. Fully
  functional demo, but not your real corpus.

> Important: setting the Moss keys alone auto-resolves to live, but set
> `USE_MOCKS=false` explicitly to make the real path unambiguous and to make
> `make verify-live` report MISSING (not MOCK) if a credential is absent.

---

## Capability 2 — Voice transport + tokens (LiveKit Cloud)  ·  REQUIRED for live voice

| Key | What it unlocks |
|---|---|
| `LIVEKIT_URL` | LiveKit server / Cloud ws URL (e.g. `wss://<proj>.livekit.cloud`) |
| `LIVEKIT_API_KEY` | API key — needed to mint join tokens and connect the worker |
| `LIVEKIT_API_SECRET` | API secret — signs the access tokens |
| `LIVEKIT_DEFAULT_ROOM` | Room `/token` joins by default (default: `crossexam`) |
| `TOKEN_TTL_SECONDS` | Minted token lifetime (default 3600) |

- **Account:** [LiveKit Cloud](https://livekit.io) (free tier works) or a self-hosted server.
- **With keys (+ `livekit-api` from `[api]`):** `POST /token` mints real join
  tokens; the browser connects to the room; the worker speaks in it.
- **Without keys:** `POST /token` returns a clear **503**; the frontend stays in
  mock UI mode. The worker prints guidance and runs the mock retrieval loop.

---

## Capability 3 — Voice providers (STT / LLM / TTS)  ·  REQUIRED for live voice quality

| Key | Leg | Default provider | What it unlocks |
|---|---|---|---|
| `DEEPGRAM_API_KEY` | STT | `deepgram` | Speech-to-text on the inbound audio |
| `OPENAI_API_KEY` | LLM | `openai` | The grounded reasoning over Moss citations |
| `CARTESIA_API_KEY` | TTS | `cartesia` | Text-to-speech for the spoken answer |

- Providers are selected by `STT_PROVIDER` / `LLM_PROVIDER` / `TTS_PROVIDER`
  (defaults: deepgram / openai / cartesia). Each leg needs its matching key
  **and** its `[voice]` plugin installed.
- **With all three keys:** a real `AgentSession(stt, llm, tts)` runs end-to-end.
- **Missing a key/plugin in live mode:** the worker fails **loudly** for that leg
  (never a silent no-op); `make verify-live` shows it MISSING.
- **Mock/offline mode:** the voice pipeline never starts, so these are not needed.

(VAD uses `livekit-plugins-silero`, no key — it downloads weights lazily.)

---

## Capability 4 — Scanned-PDF parsing (Unsiloed)  ·  OPTIONAL

| Key | What it unlocks |
|---|---|
| `UNSILOED_API_KEY` | Unsiloed Parse/Extract for **scanned / image-only PDFs** |

- **Only needed** when running the offline pipeline `parse` against the real
  Unsiloed API for PDFs **without a text layer** (scanned documents).
- **Not needed** for: `parse --dry-run`, text-layer PDFs, the test suite, or the
  HTTP `POST /documents` upload path (that uses `pdfplumber`'s text-layer parse).

---

## Mock vs Real — at a glance

| Capability | No keys (default) | With keys + `USE_MOCKS=false` |
|---|---|---|
| Retrieval | `MockIndex` over bundled fixture | Real Moss query/upsert |
| `POST /documents` | Appends to mock fixture | Upserts to Moss |
| `POST /token` | 503 (LiveKit not configured) | Real LiveKit join token |
| Voice session | Not started (mock UI) | Real STT→LLM→TTS in a LiveKit room |
| Frontend | Mock UI | Live UI (reads `/config` → `live: true`) |

## Verify

```bash
make verify-live   # doctor READY/MISSING/MOCK table + curls /healthz & /config
```

- Every row **READY** (plus Moss/LiveKit/providers, with `USE_MOCKS=false`) and
  `/healthz` reporting `"mode":"live"` ⇒ you are fully real.
- Any **MISSING** row tells you exactly which key/dep/account to add.
- Start the API first (`make api`, `make dev-live`, or `docker compose up`) so the
  endpoint probe can reach it.
