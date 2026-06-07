# CrossExam Backend

CrossExam is a **voice agent that interrogates large documents**. It runs
LiveKit Agents' real-time voice loop (STT → LLM → TTS) and, on every user turn,
queries **Moss** (sub-10ms in-process semantic retrieval) and injects the top-k
passages as a `role="system"` message into the turn context — so the LLM always
has grounding context before it answers ("no dead air").

Each retrieval result carries a **page number + bounding box + confidence**, so
the frontend can draw the citation box on the source document.

## Architecture

```
voice (LiveKit STT) ──▶ on_user_turn_completed()  ◀── LiveKit Agents hook
                              │
                              ▼
                     RetrievalIndex.query()         ── Moss or MockIndex
                              │  (page + bbox + score)
                              ▼
            inject role="system" grounding message into turn_ctx
                              │
                              ▼
                          LLM ──▶ TTS  (+ citations forwarded to frontend)
```

> **Note:** `on_user_turn_completed()` is a **LiveKit Agents** lifecycle hook
> (invoked after the user's turn is transcribed, before the LLM responds). It is
> *not* a Moss feature — it is simply the right place to enrich the turn context
> with retrieved passages.

### Modules

| File | Responsibility |
|------|----------------|
| `crossexam_backend/config.py` | `pydantic-settings` config; auto-enables mocks when Moss keys are absent |
| `crossexam_backend/models.py` | `BBox`, `Chunk`, `Citation`, `RetrievalResult` |
| `crossexam_backend/retrieval/base.py` | `RetrievalIndex` ABC (`async query(text, top_k, alpha)`) |
| `crossexam_backend/retrieval/moss_client.py` | `MossIndex` — wraps the real Moss client, measures latency |
| `crossexam_backend/retrieval/mock_index.py` | `MockIndex` — deterministic in-memory hybrid (semantic + keyword) over a JSON fixture |
| `crossexam_backend/retrieval/factory.py` | `get_index(settings)` — picks Moss or Mock |
| `crossexam_backend/agent.py` | `CrossExamAgent` with `on_user_turn_completed()`; guarded LiveKit imports |
| `crossexam_backend/server.py` | LiveKit worker entrypoint with prewarm; builds STT/LLM/TTS providers |
| `crossexam_backend/doctor.py` | Preflight/doctor — prints a READY/MISSING/MOCK status table (no network) |
| `crossexam_backend/api.py` | FastAPI HTTP service — LiveKit token minting + document upload/index |
| `crossexam_backend/ingest.py` | PDF text-layer parse to backend chunk records (prefers the pipeline parser, falls back to `pdfplumber`) |

## HTTP API service (`crossexam-api`)

The LiveKit *worker* speaks audio inside a room, but the browser still needs a
way to **get a join token** and to **upload a document** to index. The FastAPI
service in `crossexam_backend/api.py` provides exactly that. It imports and tests
without live keys or optional deps (the LiveKit token lib import is guarded; the
ingest path uses the offline fixture + `MockIndex` when Moss is absent).

### Endpoints

| Method & path | Body | Returns | Needs |
|---------------|------|---------|-------|
| `GET /healthz` | – | `{status, mode: "live"\|"mock", livekit_configured, moss_configured}` | nothing |
| `GET /config` | – | `{livekit_url, live}` — public connection info, **never secrets** | nothing |
| `POST /token` | `{room?, identity?}` | `{token, room, identity, livekit_url}` | `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` **and** the `livekit-api` package, else **503** |
| `POST /documents` | multipart `file` (PDF) | `{document_id, pages, chunks_indexed, mode}` | `pdfplumber` to parse; Moss creds (+ `crossexam-pipeline`) to upsert to Moss, else writes the fixture and reloads `MockIndex` offline |

`/documents` validates the content type and the `%PDF-` header, enforces the
`MAX_UPLOAD_MB` size limit, parses the text layer to chunks, and either upserts
to Moss (live) or appends to the fixture and hot-swaps the in-memory `MockIndex`
(offline) so the uploaded document is immediately queryable. Errors are explicit:
`415` non-PDF, `400` empty, `413` too large, `422` no extractable text, `503`
when a required live dependency is missing.

### Which parts need which keys

- `GET /healthz`, `GET /config` — **no keys**; always available (report mode).
- `POST /token` — needs all three LiveKit env vars **and** `livekit-api`
  installed; otherwise returns **503** with a clear message.
- `POST /documents` — needs `pdfplumber` (in the `[api]` extra) to parse. With
  Moss credentials set **and** `crossexam-pipeline` installed it upserts to Moss
  (`mode: "moss"`); otherwise it indexes into the offline `MockIndex`
  (`mode: "mock"`) — no keys required.

### Running the API

```bash
pip install -e '.[api]'      # fastapi, uvicorn, python-multipart, livekit-api, pdfplumber
crossexam-api                # or: python -m crossexam_backend.api
```

It binds `API_HOST:API_PORT` (default `0.0.0.0:8000`) and enables CORS for
`CORS_ORIGINS` (default `http://localhost:5173`, the Vite dev origin).

## Verified API surfaces (pinned adapters)

These were researched and pinned so on-site bring-up is a config swap, not a
rewrite. Each real-SDK call is isolated behind a small swappable method.

### Moss Python client — `retrieval/moss_client.py`

Sources: [PyPI `inferedge-moss`](https://pypi.org/project/inferedge-moss/),
[Moss SDK docs](https://moss-docs-seven.vercel.app/),
[GitHub `usemoss/moss`](https://github.com/usemoss/moss),
[moss.dev](https://www.moss.dev/).

**Install (live path):** the real retrieval path is the in-process Moss SDK,
**not a REST endpoint** — install it with `pip install '.[moss]'` (resolves the
`inferedge-moss` distribution). The mock/test path needs nothing.

**Package-name inconsistency (documented):** the PyPI *distribution* is
`inferedge-moss` (`pip install '.[moss]'`), but the *import package* is reported
as `inferedge_moss` by the docs site while the GitHub README shows
`from moss import MossClient` (the npm sibling is `@inferedge/moss`). Because
upstream is genuinely inconsistent, the adapter tries both import names
(`inferedge_moss`, then `moss`) in `_load_moss_module()`.

**Verified surface (high confidence — consistent across PyPI + docs + GitHub):**

```python
from inferedge_moss import MossClient, QueryOptions   # or: from moss import ...
client = MossClient("project_id", "project_key")      # POSITIONAL args
await client.load_index("index-name")
results = await client.query(
    "index-name", "query text",
    QueryOptions(top_k=3, alpha=0.6, filter=...),  # alpha default 0.8
)
for doc in results.docs:               # ranked list
    doc.id, doc.text, doc.score, doc.metadata  # per-doc fields
results.time_taken_ms                  # server-measured latency
```

**Verified metadata contract:** Moss document metadata values are **all
strings**. The pipeline writes `documentId`, `documentTitle` (optional),
`scanned` (`"true"`/`"false"`), `page` (e.g. `"3"`), `confidence` (e.g.
`"0.97"`), and the geometry (`bbox`, `words`, optional `quads`) as
**JSON-encoded strings**. `_to_citation` is **dual-tolerant**: it parses both the
real string contract and the older nested-dict form (DI/test fakes), JSON-decodes
string geometry with a safe fallback so a malformed value never crashes the turn,
and coerces page/confidence/scanned tolerantly. Locked by
`tests/test_moss_adapter.py`.

**Verified filter grammar:** single field is
`{"field": "documentId", "condition": {"$eq": id}}`; compound is
`{"$and": [{"$or": [<per-id $eq clauses>]}]}`. The kwarg is `filter=` (singular).
`query_multi` pushes this server-side when `QueryOptions` accepts `filter`, and
otherwise falls back to over-fetch + post-filter.

**Strict vs lenient mode:** `MossIndex` defaults to **strict** when Moss
credentials are present — a query failure raises `MossQueryError` so a broken
integration is *visible*, not silently empty. Pass `strict=False` for the
graceful empty-result behavior. A structurally-broken client (no `query`)
always raises `MossClientUnavailableError`.

### LiveKit Agents provider plugins — `server.py`

Source: [`livekit/agents`](https://github.com/livekit/agents) (via context7).
Confirmed import paths and constructors (provider-key path, not LiveKit
Inference):

```python
from livekit.plugins import deepgram, openai, cartesia, silero
stt = deepgram.STT(model="nova-3")        # livekit-plugins-deepgram
llm = openai.LLM(model="gpt-4.1-mini")    # livekit-plugins-openai
tts = cartesia.TTS(model="sonic-3", voice="...")  # livekit-plugins-cartesia
vad = silero.VAD.load()                   # livekit-plugins-silero
session = AgentSession(stt=stt, llm=llm, tts=tts, vad=vad)
```

The agent's `on_user_turn_completed(turn_ctx, new_message)` hook and
`turn_ctx.add_message(role=, content=)` match LiveKit Agents `0.12.x`
(the pinned `livekit-agents>=0.12,<1.0` range). LiveKit `1.0+` switched to
`AgentServer` + `@server.rtc_session()`; if you upgrade, update `server.py`'s
worker bootstrap accordingly.

## Preflight / doctor

Before a live session, run the doctor to see what is wired vs missing — it makes
**no network calls**:

```bash
python -m crossexam_backend.doctor      # or: crossexam-doctor
```

It prints a table with `READY` / `MISSING` / `MOCK` for retrieval, the LiveKit
runtime, each STT/LLM/TTS provider (key + plugin import) and Silero VAD, and
exits non-zero if anything required is `MISSING`.

## Voice pipeline plugins (`[voice]` extra)

The STT/LLM/TTS/VAD plugins are optional so the mock/test path installs without
them. Install for a live session with:

```bash
pip install 'crossexam-backend[voice]'
```

This pulls `livekit-plugins-deepgram`, `livekit-plugins-openai`,
`livekit-plugins-cartesia` and `livekit-plugins-silero`. Each provider also
needs its key (`DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, `CARTESIA_API_KEY`); a
missing key or plugin raises a clear `ProviderConfigError` instead of a silent
no-op session.

## How the mocks work

There are **no real API keys** required to develop or test. The factory selects
`MossIndex` only when `USE_MOCKS` is false **and** both Moss credentials are set;
otherwise it falls back to `MockIndex`, which loads `fixtures/sample_chunks.json`
into memory and answers queries with a deterministic TF-IDF cosine ("semantic")
blended with token-overlap ("keyword") via `alpha`. Latency is measured and is
realistically sub-millisecond.

Likewise, `crossexam_backend.agent` guards its LiveKit imports: if
`livekit-agents` is not installed, the agent subclasses a thin shim base and a
`ShimChatContext` provides the `add_message(role=, content=)` surface, so unit
tests run with only `pydantic` + stdlib.

## Environment variables

| Var | Default | Notes |
|-----|---------|-------|
| `MOSS_PROJECT_ID` | – | Required for the real Moss client |
| `MOSS_PROJECT_KEY` | – | Required for the real Moss client |
| `MOSS_INDEX_NAME` | `crossexam-documents` | Moss index to query |
| `MOSS_MODEL_ID` | `moss-minilm` | Embedding model (optional); backend (query) and pipeline (build) must agree |
| `MOSS_AUTO_REFRESH` | `false` | Re-pull the index on an interval so live-upserted docs become queryable without a manual reload |
| `MOSS_REFRESH_INTERVAL_S` | `600` | Refresh cadence when `MOSS_AUTO_REFRESH=true` (30–86400) |
| `MOSS_JOB_TIMEOUT_S` | `120` | Timeout for the async create-index / add-docs job poll loop |
| `MOSS_JOB_POLL_INTERVAL_S` | `1` | Poll interval for that job loop |
| `LIVEKIT_URL` | – | e.g. `wss://your-project.livekit.cloud` |
| `LIVEKIT_API_KEY` | – | |
| `LIVEKIT_API_SECRET` | – | |
| `STT_PROVIDER` | `deepgram` | STT plugin to construct |
| `LLM_PROVIDER` | `openai` | LLM plugin to construct |
| `TTS_PROVIDER` | `cartesia` | TTS plugin to construct |
| `DEEPGRAM_API_KEY` | – | Required when `STT_PROVIDER=deepgram` |
| `OPENAI_API_KEY` | – | Required when `LLM_PROVIDER=openai` |
| `CARTESIA_API_KEY` | – | Required when `TTS_PROVIDER=cartesia` |
| `TOP_K` | `5` | Citations per turn |
| `ALPHA` | `0.8` | Hybrid weight (1.0 = pure semantic, 0.0 = pure keyword) |
| `USE_MOCKS` | auto | Auto-`true` when Moss keys are missing; set explicitly to override |
| `MOCK_FIXTURE_PATH` | `fixtures/sample_chunks.json` | Mock corpus (also the offline `/documents` write target) |
| `LOG_LEVEL` | `INFO` | |
| `API_HOST` | `0.0.0.0` | Bind host for `crossexam-api` |
| `API_PORT` | `8000` | Bind port for `crossexam-api` |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed frontend origins |
| `LIVEKIT_DEFAULT_ROOM` | `crossexam` | Room used by `/token` when the body omits `room` |
| `TOKEN_TTL_SECONDS` | `3600` | TTL for minted LiveKit tokens (applied via `with_ttl` on the real SDK) |
| `MAX_UPLOAD_MB` | `25` | Max accepted `/documents` upload size |

Copy these into a `.env` file in this directory; `pydantic-settings` loads it
automatically.

## Running

Install (dev):

```bash
pip install -e '.[dev]'
```

Run the worker:

```bash
python -m crossexam_backend.server dev
```

- With `livekit-agents` installed **and** LiveKit credentials set, this starts a
  real-time worker (prewarms the index, then serves voice sessions).
- Without `livekit-agents`, it prints a helpful message and exits cleanly after
  confirming the retrieval index is ready — useful for local dev / CI.

## Tests

```bash
python -m pytest -q
```

All pure logic (config resolution, models, mock ranking incl. the
"warehouse on the night of the 14th" query + the cross-page contradiction, and
the `on_user_turn_completed` system-message injection) is covered and passes
without any external services or API keys.
