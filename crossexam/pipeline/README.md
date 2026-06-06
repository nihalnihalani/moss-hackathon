# crossexam-pipeline

Offline document pipeline for **CrossExam**. It turns a source document (a PDF,
e.g. a long deposition) into Moss-indexable **chunks** that carry:

- the **page number** each chunk lives on,
- a **bounding box in PDF points** (top-left origin) so retrieval hits can be
  drawn on the page,
- **word-level citations** (each word has its own box), and
- a **confidence** score.

It then builds a Moss index from those chunks so the voice agent's retrieval
results can be highlighted on the document during cross-examination.

The pipeline runs **pre-demo / offline**. There are three parse paths:

1. **`--text-layer`** — parse a born-digital PDF's text layer locally with
   `pdfplumber` (no network, no Unsiloed key). This is what backs the demo:
   `make-sample-pdf` generates a real PDF whose key lines sit at known
   coordinates, and the text-layer parser reads them back into chunks whose
   boxes line up with the rendered glyphs.
2. **Unsiloed** (default, no flag) — async Parse/Extract for real **scans** /
   image-only PDFs. Requires `UNSILOED_API_KEY`.
3. **`--dry-run`** — a deterministic JSON fallback over the bundled synthetic
   `fixtures/sample_deposition.json` (legacy, fully reproducible, no I/O).

## Output format

All three parse paths produce the **same** on-disk shape that the CrossExam
backend mock index consumes — a JSON list of:

```json
{
  "id": "pdf-p12-l1",
  "text": "Q. Where were you on the night of the 14th? A. I was at the Harbor Street warehouse from approximately 9:00 p.m. until well past midnight, conducting the inventory count with Mr. Reyes.",
  "page": 12,
  "bbox": { "page": 12, "x0": 72.0, "y0": 123.94, "x1": 522.0, "y1": 159.94, "page_width": 612.0, "page_height": 792.0 },
  "confidence": 0.9445
}
```

`bbox` coordinates are in **PDF points with a top-left origin**, and every box
carries `page_width`/`page_height` (US Letter `612 x 792`) so the frontend can
map points to its render scale. This matches the canonical backend
`crossexam_backend.models.BBox` exactly. The pipeline's default `build-index`
output path is the backend's `crossexam/backend/fixtures/sample_chunks.json`, so
this pipeline regenerates that fixture directly — and it is now **PDF-backed**:
the fixture cites the same document the frontend renders.

### Coordinate conversion (bottom-left → top-left)

ReportLab (and PDF natively) draw with a **bottom-left** origin; the canonical
CrossExam bbox uses a **top-left** origin. The single conversion is
`y_top = page_height - y_bottom` (`crossexam_pipeline.pdf_parser.flip_y`,
unit-tested). pdfplumber already reports word `top`/`bottom` from the page top,
so the text parser uses those directly and the result coincides with the drawn
glyphs.

## Install

```bash
cd crossexam/pipeline
pip install -e ".[dev]"        # includes the PDF tooling used by the demo
# or, just the PDF tooling on top of the core deps:
pip install -e ".[pdf]"        # reportlab + pypdf + pdfplumber
```

Runtime deps: `pydantic`, `typer`, `httpx`. The optional **`pdf`** extra adds
`reportlab` (generate the sample PDF) and `pdfplumber`/`pypdf` (read a PDF text
layer). Dev: `pytest`, `ruff`, `mypy` plus the `pdf` extra. The core tests run on
**stdlib + pydantic** alone; the PDF tests skip cleanly when `reportlab`/
`pdfplumber` are absent.

## Usage

### 0. Generate the demo PDF (`make-sample-pdf`)

Generate the real, multi-page mock deposition PDF whose key lines sit at known
coordinates (requires the `pdf` extra):

```bash
python -m crossexam_pipeline.make_sample_pdf
# writes ../assets/sample-deposition.pdf and copies it to
# ../frontend/public/sample-deposition.pdf (served at VITE_PDF_URL).
```

The PDF is deterministic: page 12 carries the "warehouse on the night of the
14th" admission, page 41 carries the contradiction (the witness recants and
says he "left … before 8:00 p.m.").

### 1. Parse a PDF into chunk JSON

**Real PDF text layer — the demo path** (no network, no Unsiloed key):

```bash
crossexam-pipeline parse --input ../assets/sample-deposition.pdf --text-layer --out chunks.json
```

This reads each line's real per-word coordinates with `pdfplumber`, flips them
to top-left points, and emits chunks whose boxes line up with the rendered text.

Real Unsiloed parse for **scans / image-only PDFs** (requires `UNSILOED_API_KEY`):

```bash
crossexam-pipeline parse --input scan.pdf --out chunks.json
```

Legacy deterministic JSON fallback (no network, no keys, no PDF):

```bash
crossexam-pipeline parse --input deposition.pdf --out chunks.json --dry-run
```

> With `--dry-run` the `--input` PDF is not read; the bundled
> `fixtures/sample_deposition.json` is parsed instead. Override it with
> `--sample path/to/sample.json`.

If you run without `--text-layer`/`--dry-run` and no `UNSILOED_API_KEY` is set,
the CLI exits with a clear message.

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

### End-to-end (offline, PDF-backed demo prep)

```bash
# 1. Generate the real demo PDF (assets/ + frontend/public/).
python -m crossexam_pipeline.make_sample_pdf
# 2. Parse its text layer into chunks (no network, no keys).
crossexam-pipeline parse --input ../assets/sample-deposition.pdf --text-layer --out chunks.json
# 3. Regenerate the backend mock fixture from those chunks.
crossexam-pipeline build-index --input chunks.json --index-name crossexam-demo --dry-run
```

The result: the backend mock index answers the demo questions against the SAME
document the frontend renders, with citation boxes that land on the rendered
glyphs.

## How the parsers work

`PdfTextParser` (the demo path) opens a born-digital PDF with `pdfplumber`,
clusters words into physical lines, merges wrapped lines back into paragraphs
(so each demo sentence stays one chunk), and emits chunks with top-left point
boxes + per-word citations. It is **pure**: re-parsing the same PDF yields
byte-identical output.

`DeterministicParser` (legacy `--dry-run`) reads `fixtures/sample_deposition.json`
(synthetic deposition pages, warehouse admission on page 147 and its
contradiction on page 488) and lays each line out on a synthetic point grid.
Also pure and idempotent.

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
