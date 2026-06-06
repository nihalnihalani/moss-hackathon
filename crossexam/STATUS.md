# CrossExam — Production Status

Snapshot of what's built, verified, and what remains. Honest by design — "production-ready as
possible" given that **no live sponsor API keys exist in this environment.**

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

## ⚠️ Requires live keys / docs to finalize (defensively coded, documented)
These are written against the documented/assumed API surface and fail *safely* (clear errors or
empty results) until verified on-site — they are NOT silent landmines, but must be checked at the event:

| Item | File | Action before demo |
|---|---|---|
| **Moss SDK surface** (constructor, `query` kwargs, response shape) is best-effort | `backend/.../retrieval/moss_client.py` | Pin to the real Moss SDK; add one integration test with a recorded client |
| **Unsiloed REST surface** (`/v1/parse`, field names, response keys) is best-effort | `pipeline/.../unsiloed.py` | Validate against live Unsiloed docs |
| **Moss package name** is inconsistent across npm/PyPI | install step | Resolve exact import in the first 30 min |
| **Provider plugins** (Deepgram/OpenAI/Cartesia) not installed here | `backend/pyproject.toml [voice]` | `pip install '.[voice]'` + set keys |
| **Real sample PDF** for the live (non-dry-run) pipeline path | `pipeline/` | Drop in the actual deposition PDF |
| CI lint/type gates are advisory (`continue-on-error`) due to tool version drift | `.github/workflows/ci.yml` | Pin ruff/mypy versions for strict gating |

## How to run
```bash
cd crossexam
make setup     # install backend + pipeline + frontend deps
make test      # 67 tests, no keys needed
make dev       # backend worker + frontend dev (mock mode if no keys)
make index     # offline parse -> index dry-run
```
Demo walkthrough and architecture: see `README.md` and `../docs/`.
