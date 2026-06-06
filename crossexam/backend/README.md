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

## Verified API surfaces (pinned adapters)

These were researched and pinned so on-site bring-up is a config swap, not a
rewrite. Each real-SDK call is isolated behind a small swappable method.

### Moss Python client — `retrieval/moss_client.py`

Sources: [PyPI `inferedge-moss`](https://pypi.org/project/inferedge-moss/),
[Moss SDK docs](https://moss-docs-seven.vercel.app/),
[GitHub `usemoss/moss`](https://github.com/usemoss/moss),
[moss.dev](https://www.moss.dev/).

**Package-name inconsistency (documented):** the PyPI *distribution* is
`inferedge-moss` (`pip install inferedge-moss`), but the *import package* is
reported as `inferedge_moss` by the docs site while the GitHub README shows
`from moss import MossClient` / `pip install moss` (the npm sibling is
`@inferedge/moss`). Because upstream is genuinely inconsistent, the adapter tries
both import names (`inferedge_moss`, then `moss`) in `_load_moss_module()`.

**Verified surface (high confidence — consistent across PyPI + docs + GitHub):**

```python
from inferedge_moss import MossClient, QueryOptions   # or: from moss import ...
client = MossClient("project_id", "project_key")      # POSITIONAL args
await client.load_index("index-name")
results = await client.query(
    "index-name", "query text",
    QueryOptions(top_k=3, alpha=0.6),  # alpha default 0.8; 0.0=keyword 1.0=semantic
)
for doc in results.docs:               # ranked list
    doc.id, doc.text, doc.score        # per-doc fields
results.time_taken_ms                  # server-measured latency
```

**Could NOT verify (kept defensive, locked by `tests/test_moss_adapter.py`):**
no public source documents bbox/page/page_width/page_height fields on a Moss
document — `DocumentInfo` is `id` + `text` + optional `metadata` dict. For PDF
citations the adapter therefore reads bbox/page from `doc.metadata` (or top-level
attributes if a future version adds them). The exact key names are an assumption
and are locked by a recorded-response test so a real swap is a fixture update.

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
| `MOCK_FIXTURE_PATH` | `fixtures/sample_chunks.json` | Mock corpus |
| `LOG_LEVEL` | `INFO` | |

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
