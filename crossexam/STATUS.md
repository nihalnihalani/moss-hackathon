# CrossExam — Production Status

CrossExam is a **real-time voice co-pilot (ambient, grounded citations)** — it listens and snaps a
verifiable source line onto the exact line of a document in <10ms, not a notetaker.

Snapshot of what's built, verified, and what remains. Honest by design — "production-ready as
possible" given that **no live sponsor API keys exist in this environment.**

> **End-to-end wiring audited & hardened.** An independent contract trace verified every hop
> (PDF → pipeline → fixture → backend retrieval → data-channel payload → frontend bbox overlay).
> All audit findings resolved: live-mode retrieval returns the correct page (p12 admission),
> Moss env var unified across backend+pipeline, backend Docker installs `[voice]`, lint/type are
> now **strict** CI gates. The demo fixture is **byte-identically regenerable** from the sample PDF.

## ✅ Done & verified (runs with mocks, no keys)
- **Backend** (LiveKit voice agent + Moss retrieval) — 277 tests green.
  - `on_user_turn_completed` (a LiveKit hook) injects Moss top-k as a `system` message — "no dead air".
  - Publishes structured citations over the LiveKit data channel in the exact shape the frontend parses.
  - `AgentSession` wired with guarded STT/LLM/TTS providers (clear error, never a silent no-op).
  - `MockIndex` fallback → app + tests run with zero API keys.
- **Pipeline** (Unsiloed parse → Moss index) — 99 tests green.
  - Deterministic network-free fallback; typer CLI (`parse`, `build-index`) with `--dry-run`.
- **Frontend** (React voice UI + PDF bbox snap) — 100 tests green, `tsc` clean, `vite build` clean, eslint clean.
  - Pure, unit-tested `lib/bbox.ts` (the demo-deciding transform); mock-mode runs the full 90s demo with no backend.
  - **Live-by-default**: reads `VITE_API_URL` (default `http://localhost:8000`), fetches `/config`,
    connects live when keys are present, and falls back to mock UI otherwise. Includes a PDF upload widget.
- **HTTP API** (`crossexam-api`, FastAPI) — the browser-facing service the worker can't provide.
  - `GET /healthz` + `GET /config` (readiness + public connection info, no secrets),
    `POST /token` (real LiveKit join token; 503 if unconfigured), `POST /documents`
    (PDF → parse → index; Moss upsert when keyed, else appends to the mock fixture).
  - Ships in the `[api]` extra; the backend Docker image now installs `[api,voice]` so one
    image runs both the worker and the API. Compose adds an `api` service on `:8000`.
- **Contract** — bbox is **PDF points + page_width/page_height, top-left origin** end-to-end. A
  cross-subsystem test (`pipeline/tests/test_contract.py`) round-trips the backend fixture ↔ pipeline model.
- **Infra** — Dockerfiles (backend + frontend), docker-compose (boots in mock mode without `.env`),
  Makefile, GitHub Actions CI (green without keys), `.env.example`, runbook README.

**Total: 476 automated tests passing.**

## ✅ Resolved since the first cut (the completion round)
- Real **sample-deposition.pdf** generated (admission p12, contradiction p41) + a pdfplumber
  text-layer parser; the 419-chunk fixture is regenerated from it (`make fixture`).
- Moss + LiveKit provider surfaces **verified against docs and pinned**; LiveKit/OpenAI now
  prefers the documented Responses API path, recorded-response adapter tests lock the shape,
  and Moss errors fail **loudly** when keys are present (no silent empty results).
- `crossexam-doctor` preflight reports READY/MISSING/MOCK without network calls — now ALSO
  covers HTTP API readiness (fastapi/pdfplumber importable) and the `/token` minting leg
  (livekit-api import + LiveKit creds). `make verify-live` runs it and probes `/healthz` + `/config`.
- Frontend renders the real PDF (`VITE_PDF_URL`); a live-citation integration test covers the
  data-channel path; `mockData` coords match the fixture exactly.
- **Live-mode ranking fixed** (admission → page 12) with a regression test.
- Moss env var unified (`MOSS_PROJECT_KEY` for backend *and* pipeline); backend Docker installs
  `.[voice]`; ruff + mypy are **strict** CI gates (pinned ruff 0.15.0 / mypy 1.19.1).

## ⚠️ Still requires live keys / on-site verification (defensively coded, fails loudly)
The external API *shapes* are verified against docs but cannot be exercised without real keys here:

| Item | File | Action at the event |
|---|---|---|
| **Moss SDK call** (constructor/`query`/result shape; bbox-in-metadata assumption) | `backend/.../retrieval/moss_client.py` | Run `crossexam-doctor`; swap creds; the recorded test documents the expected shape |
| **Unsiloed REST surface** (`POST /parse`, `GET /parse/{job_id}`, response keys) | `pipeline/.../unsiloed.py` | Validate against live Unsiloed with a real key (offline `--text-layer` path needs no key) |
| **Moss package name** inconsistent across npm/PyPI | install step | Adapter tries `inferedge_moss` then `moss`; confirm the working import |
| **Provider plugins** (Deepgram/OpenAI/Cartesia) + keys | `[voice]` extra + `.env` | `pip install '.[voice]'` (Docker does this) + set keys |

## How to run
```bash
cd crossexam
make setup     # install backend + pipeline + frontend deps
make test      # 67 tests, no keys needed
make dev       # backend worker + frontend dev (mock mode if no keys)
make dev-live  # HTTP API + worker + frontend (token + upload + voice)
make verify-live  # doctor READY/MISSING/MOCK + curl /healthz & /config
make index     # offline parse -> index dry-run
```
To go fully real: fill `crossexam/.env` per **`KEYS.md`** (Moss + LiveKit + voice providers,
`USE_MOCKS=false`), `pip install '.[api,voice]'`, then `make verify-live`.

Demo walkthrough and architecture: see `README.md` and `../docs/`.
