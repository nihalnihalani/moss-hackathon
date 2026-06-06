# crossexam-pipeline

Offline document pipeline for **CrossExam**. It turns a source document (a PDF,
e.g. a 912-page deposition) into Moss-indexable **chunks** that carry:

- the **page number** each chunk lives on,
- a normalized **bounding box** so retrieval hits can be drawn on the page,
- **word-level citations** (each word has its own box), and
- a **confidence** score.

It then builds a Moss index from those chunks so the voice agent's retrieval
results can be highlighted on the document during cross-examination.

The pipeline runs **pre-demo / offline**: Unsiloed parsing is asynchronous and
cannot complete during the live demo, so a deterministic, network-free
**fallback** produces the same artifact for the demo and the test-suite.

## Output format

Both the fallback and the real Unsiloed path produce the **same** on-disk shape
that the CrossExam backend mock index consumes — a JSON list of:

```json
{
  "id": "…",
  "text": "I was at the warehouse on the night of the 14th, working the late shift.",
  "page": 147,
  "bbox": { "page": 147, "x0": 0.08, "y0": 0.1, "x1": 0.6, "y1": 0.145 },
  "confidence": 0.93
}
```

`bbox` coordinates are **normalized to `[0, 1]`** (top-left origin) so the
frontend can draw the box at any render resolution. The pipeline's default
`build-index` output path is the backend's
`crossexam/backend/fixtures/sample_chunks.json`, so this pipeline can regenerate
that fixture directly.

## Install

```bash
cd crossexam/pipeline
pip install -e ".[dev]"
```

Runtime deps: `pydantic`, `typer`, `httpx`. Dev: `pytest`, `ruff`, `mypy`.
The tests run on **stdlib + pydantic** alone (the `httpx`/Moss paths are imported
lazily and are not exercised offline).

## Usage

### 1. Parse a PDF into chunk JSON

Real Unsiloed parse (requires `UNSILOED_API_KEY`):

```bash
crossexam-pipeline parse --input deposition.pdf --out chunks.json
```

Offline deterministic fallback (no network, no keys) — use this for the demo:

```bash
crossexam-pipeline parse --input deposition.pdf --out chunks.json --dry-run
```

> With `--dry-run` the `--input` PDF is not read; the bundled
> `fixtures/sample_deposition.json` is parsed instead. Override it with
> `--sample path/to/sample.json`.

If you run without `--dry-run` and no `UNSILOED_API_KEY` is set, the CLI exits
with a clear message telling you to re-run with `--dry-run`.

### 2. Build the index

Upsert to Moss (requires all three Moss env vars):

```bash
crossexam-pipeline build-index --input chunks.json --index-name crossexam-demo
```

Offline — write the backend-compatible mock fixture instead:

```bash
crossexam-pipeline build-index --input chunks.json --index-name crossexam-demo --dry-run
# writes ../backend/fixtures/sample_chunks.json by default (override with --out)
```

When Moss credentials are incomplete, `build-index` automatically falls back to
the disk path and logs that Moss was skipped.

### End-to-end (offline demo prep)

```bash
crossexam-pipeline parse --input deposition.pdf --out chunks.json --dry-run
crossexam-pipeline build-index --input chunks.json --index-name crossexam-demo --dry-run
```

## How the fallback works

`DeterministicParser` reads `fixtures/sample_deposition.json` (the relevant
pages of a synthetic 912-page deposition, including the "warehouse on the night
of the 14th" admission on page 147 and its contradiction on page 488). Each
non-empty line becomes one chunk laid out on a synthetic page grid with
plausible normalized boxes, per-word citation boxes, and a hash-derived
confidence. It is **pure**: identical input always yields byte-identical output,
keeping the generated fixture idempotent and the tests reproducible.

## Environment variables

| Variable            | Purpose                                              |
| ------------------- | ---------------------------------------------------- |
| `UNSILOED_API_KEY`  | Enables the real Unsiloed Parse/Extract path.        |
| `UNSILOED_BASE_URL` | Optional Unsiloed API base URL override.             |
| `MOSS_PROJECT_ID`   | Moss project id (required to upsert to Moss).        |
| `MOSS_API_KEY`      | Moss API key (required to upsert to Moss).           |
| `MOSS_INDEX_NAME`   | Moss index name (required to upsert to Moss).        |
| `MOSS_BASE_URL`     | Optional Moss API base URL override.                 |

No secrets are stored in code; all credentials come from the environment.

## Tests

```bash
cd crossexam/pipeline
python3 -m pytest -q
```
