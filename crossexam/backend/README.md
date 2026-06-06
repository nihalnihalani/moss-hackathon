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
| `crossexam_backend/server.py` | LiveKit worker entrypoint with prewarm |

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
