# CrossExam — Production Status

Snapshot of what's built, verified, and what remains. Honest by design — "production-ready as
possible" given that **no live sponsor API keys exist in this environment.**

> **End-to-end wiring audited & hardened.** An independent contract trace verified every hop
> (PDF → pipeline → fixture → backend retrieval → data-channel payload → frontend bbox overlay).
> All audit findings resolved: live-mode retrieval returns the correct page (p12 admission),
> Moss env var unified across backend+pipeline, backend Docker installs `[voice]`, lint/type are
> now **strict** CI gates. The demo fixture is **byte-identically regenerable** from the sample PDF.

## ✅ Done & verified (runs with mocks, no keys)
- **Backend** (LiveKit voice agent + Moss retrieval) — 31 tests green.
  - `on_user_turn_completed` (a LiveKit hook) injects Moss top-k as a `system` message — "no dead air".
  - Publishes structured citations over the LiveKit data channel in the exact shape the frontend parses.
  - `AgentSession` wired with guarded STT/LLM/TTS providers (clear error, never a silent no-op).
  - `MockIndex` fallback → app + tests run with zero API keys.
- **Pipeline** (Unsiloed parse → Moss index) — 17 tests green.
  - Deterministic network-free fallback; typer CLI (`parse`, `build-index`) with `--dry-run`.
- **Frontend** (React voice UI + PDF bbox snap) — 19 tests green, `tsc` clean, `vite build` clean, eslint clean.
  - Pure, unit-tested `lib/bbox.ts` (the demo-deciding transform); mock-mode runs the full 90s demo with no backend.
- **Contract** — bbox is **PDF points + page_width/page_height, top-left origin** end-to-end. A
  cross-subsystem test (`pipeline/tests/test_contract.py`) round-trips the backend fixture ↔ pipeline model.
- **Infra** — Dockerfiles (backend + frontend), docker-compose (boots in mock mode without `.env`),
  Makefile, GitHub Actions CI (green without keys), `.env.example`, runbook README.

**Total: 67 automated tests passing.**

## ✅ Resolved since the first cut (the completion round)
- Real **sample-deposition.pdf** generated (admission p12, contradiction p41) + a pdfplumber
  text-layer parser; the 419-chunk fixture is regenerated from it (`make fixture`).
- Moss + LiveKit provider surfaces **verified against docs and pinned**; recorded-response adapter
  tests lock the shape; Moss errors fail **loudly** when keys are present (no silent empty results).
- `crossexam-doctor` preflight reports READY/MISSING/MOCK without network calls.
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
| **Unsiloed REST surface** (`/v1/parse`, response keys) | `pipeline/.../unsiloed.py` | Validate against live Unsiloed docs (offline `--text-layer` path needs no key) |
| **Moss package name** inconsistent across npm/PyPI | install step | Adapter tries `inferedge_moss` then `moss`; confirm the working import |
| **Provider plugins** (Deepgram/OpenAI/Cartesia) + keys | `[voice]` extra + `.env` | `pip install '.[voice]'` (Docker does this) + set keys |

## How to run
```bash
cd crossexam
make setup     # install backend + pipeline + frontend deps
make test      # 67 tests, no keys needed
make dev       # backend worker + frontend dev (mock mode if no keys)
make index     # offline parse -> index dry-run
```
Demo walkthrough and architecture: see `README.md` and `../docs/`.
