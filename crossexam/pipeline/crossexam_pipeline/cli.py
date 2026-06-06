"""Typer CLI for the CrossExam offline document pipeline.

Commands:

* ``parse``       -- parse a document into chunk JSON (Unsiloed, or
                     deterministic fallback with ``--dry-run``).
* ``build-index`` -- load chunk JSON and build a Moss index, or write the
                     backend-compatible mock fixture with ``--dry-run``.

The pipeline is offline-first: with ``--dry-run`` (or absent credentials) no
network calls are made, so the demo and CI are fully reproducible.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import typer

from crossexam_pipeline.build_index import build_index as _build_index
from crossexam_pipeline.fallback import DEFAULT_SAMPLE_PATH, DeterministicParser
from crossexam_pipeline.models import (
    DEFAULT_DOCUMENT_ID,
    DEFAULT_DOCUMENT_TITLE,
    ParsedChunk,
    chunks_to_index_records,
)
from crossexam_pipeline.pdf_parser import PdfTextParser
from crossexam_pipeline.unsiloed import (
    MissingCredentialsError,
    ScannedFallbackParser,
    UnsiloedParser,
)

logger = logging.getLogger("crossexam_pipeline")

app = typer.Typer(
    help="CrossExam offline document pipeline (Unsiloed parse -> Moss index).",
    no_args_is_help=True,
    add_completion=False,
)


def _configure_logging(verbose: bool) -> None:
    """Configure root logging for the CLI.

    Args:
        verbose: If ``True``, emit DEBUG-level logs; otherwise INFO.
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _write_chunk_records(chunks: list[ParsedChunk], out: Path) -> None:
    """Serialize backend-compatible chunk records to ``out`` (idempotent)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    records = chunks_to_index_records(sorted(chunks, key=lambda c: (c.page, c.id)))
    out.write_text(
        json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _load_chunks_json(path: Path) -> list[ParsedChunk]:
    """Load chunk records from JSON into :class:`ParsedChunk` objects.

    Args:
        path: Path to a JSON list of chunk records.

    Returns:
        Parsed chunks.

    Raises:
        typer.Exit: If the file is missing or malformed.
    """
    if not path.exists():
        typer.secho(f"Input chunks file not found: {path}", fg="red", err=True)
        raise typer.Exit(code=2)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [ParsedChunk.model_validate(rec) for rec in data]
    except Exception as exc:  # noqa: BLE001 - surface any load error to the user
        typer.secho(f"Failed to load chunks from {path}: {exc}", fg="red", err=True)
        raise typer.Exit(code=2) from exc


@app.command()
def parse(
    input: Path | None = typer.Option(  # noqa: A002 - matches required CLI flag name
        None,
        "--input",
        "-i",
        help="Source document to parse (PDF, required for Unsiloed). Ignored "
        "for --dry-run, which uses the bundled --sample instead.",
    ),
    out: Path = typer.Option(
        ...,
        "--out",
        "-o",
        help="Where to write the chunk JSON.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Use the deterministic, network-free JSON fallback parser.",
    ),
    text_layer: bool = typer.Option(
        False,
        "--text-layer",
        help="Parse a born-digital PDF's text layer locally (pdfplumber, no "
        "network, no Unsiloed key). Reads real per-word coordinates from "
        "--input and emits canonical top-left point boxes.",
    ),
    scanned: bool = typer.Option(
        False,
        "--scanned",
        help="Treat the source as a scanned / image-only document (feat 3). "
        "Uses the Unsiloed VISION/OCR path when UNSILOED_API_KEY is set; "
        "otherwise (or with --dry-run) uses the deterministic offline scanned "
        "fallback that emits region-box quads and marks chunks scanned=true.",
    ),
    document_id: str = typer.Option(
        DEFAULT_DOCUMENT_ID,
        "--document-id",
        help="Source document id stamped on every chunk (feat 1).",
    ),
    document_title: str = typer.Option(
        DEFAULT_DOCUMENT_TITLE,
        "--document-title",
        help="Human-readable document label for the doc switcher (feat 1).",
    ),
    sample: Path = typer.Option(
        DEFAULT_SAMPLE_PATH,
        "--sample",
        help="Sample JSON used by the fallback parser (--dry-run only).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    """Parse a document into backend-compatible chunk JSON.

    Parse paths:

    * ``--dry-run``    -- deterministic JSON fallback (bundled sample, no I/O).
    * ``--scanned``    -- scanned / image-only path (feat 3): Unsiloed VISION/OCR
      when a key is set, else the deterministic offline scanned fallback
      (region-box quads, ``scanned=true``).
    * ``--text-layer`` -- read a born-digital PDF's text layer locally
      (``pdfplumber``); no network and no Unsiloed key required.
    * default          -- Unsiloed Parse/Extract (requires ``UNSILOED_API_KEY``)
      for real scans / image-only PDFs.
    """
    _configure_logging(verbose)

    if dry_run:
        if scanned:
            logger.info("Parsing with deterministic scanned fallback (feat 3).")
            chunks = ScannedFallbackParser(
                document_id=document_id, document_title=document_title
            ).parse()
        else:
            logger.info("Parsing with deterministic fallback (sample=%s).", sample)
            chunks = DeterministicParser(sample_path=sample).parse()
    elif scanned:
        # Scanned/vision path: real Unsiloed VISION when configured, else the
        # deterministic offline scanned fallback so the demo still works.
        try:
            parser = UnsiloedParser.from_env(
                document_id=document_id, document_title=document_title
            )
        except MissingCredentialsError:
            logger.info(
                "No Unsiloed key; using the deterministic offline scanned "
                "fallback (region-box quads, scanned=true)."
            )
            chunks = ScannedFallbackParser(
                document_id=document_id, document_title=document_title
            ).parse()
        else:
            if input is None:
                typer.secho(
                    "--input is required with --scanned when UNSILOED_API_KEY is "
                    "set (path to the scanned PDF to OCR via the vision path).",
                    fg="red",
                    err=True,
                )
                raise typer.Exit(code=2)
            logger.info("Parsing %s with Unsiloed VISION (scanned).", input)
            chunks = asyncio.run(parser.parse_scanned(input))
    elif text_layer:
        if input is None:
            typer.secho(
                "--input is required with --text-layer (path to the PDF whose "
                "text layer to parse).",
                fg="red",
                err=True,
            )
            raise typer.Exit(code=2)
        logger.info("Parsing %s text layer locally (pdfplumber).", input)
        chunks = PdfTextParser(
            input, document_id=document_id, document_title=document_title
        ).parse()
    else:
        if input is None:
            typer.secho(
                "--input is required without --dry-run (path to the source "
                "document to parse with Unsiloed).",
                fg="red",
                err=True,
            )
            raise typer.Exit(code=2)
        try:
            parser = UnsiloedParser.from_env(
                document_id=document_id, document_title=document_title
            )
        except MissingCredentialsError as exc:
            typer.secho(str(exc), fg="yellow", err=True)
            raise typer.Exit(code=3) from exc
        logger.info("Parsing %s with Unsiloed.", input)
        chunks = asyncio.run(parser.parse(input))

    _write_chunk_records(chunks, out)
    typer.secho(
        f"Parsed {len(chunks)} chunk(s) -> {out}",
        fg="green",
    )


@app.command("build-index")
def build_index_cmd(
    input: Path = typer.Option(  # noqa: A002 - matches required CLI flag name
        ...,
        "--input",
        "-i",
        help="Chunk JSON produced by `parse`.",
    ),
    index_name: str = typer.Option(
        ...,
        "--index-name",
        "-n",
        help="Moss index name to create/refresh.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        "-o",
        help="Override JSON output path for disk/dry-run modes "
        "(defaults to the backend mock fixture).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Skip Moss; write backend-compatible chunks JSON to disk.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    """Build/refresh a Moss index from chunk JSON, or write the mock fixture.

    With ``--dry-run`` (or when Moss credentials are absent) the chunks are
    written as a backend-compatible JSON list that the mock index can load.
    """
    _configure_logging(verbose)
    chunks = _load_chunks_json(input)
    summary = _build_index(
        chunks,
        index_name=index_name,
        out_path=out,
        dry_run=dry_run,
    )
    typer.secho(
        f"build-index [{summary['mode']}]: {summary['chunk_count']} chunk(s) "
        f"-> {summary.get('path') or summary.get('index')}",
        fg="green",
    )


@app.command("sample-fixture")
def sample_fixture_cmd(
    out: Path | None = typer.Option(
        None,
        "--out",
        "-o",
        help="Fixture path to write (defaults to the backend mock fixture).",
    ),
    include_scanned: bool = typer.Option(
        True,
        "--include-scanned/--no-scanned",
        help="Append a scanned (OCR) document so a scanned=true source exists.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    """Regenerate the multi-document demo fixture (feats 1-3).

    Generates both demo PDFs, parses each into chunks carrying their own
    ``documentId``/``documentTitle`` and per-line ``quads``, optionally appends a
    scanned document (region-box quads, ``scanned=true``), and writes the
    backend-compatible fixture. The deposition keeps the ``pdf-...`` chunk ids so
    existing demo ranking and eval gold ids still resolve.
    """
    _configure_logging(verbose)
    from crossexam_pipeline import make_sample_pdf as msp
    from crossexam_pipeline.build_index import DEFAULT_CHUNKS_OUT

    chunks = _build_sample_fixture_chunks(include_scanned=include_scanned)
    target = out or DEFAULT_CHUNKS_OUT
    _write_chunk_records(chunks, target)
    docs = sorted({c.document_id for c in chunks})
    typer.secho(
        f"sample-fixture: wrote {len(chunks)} chunk(s) across {len(docs)} "
        f"document(s) {docs} -> {target}",
        fg="green",
    )
    _ = msp  # keep import referenced for side-effect-free linting


def _build_sample_fixture_chunks(include_scanned: bool = True) -> list[ParsedChunk]:
    """Generate both demo PDFs and parse them into multi-document chunks.

    Returns:
        Deposition chunks (ids ``pdf-...``, ``deposition-holloway``) +
        visitor-log chunks (``exhibit-visitor-log``) + optional scanned chunks
        (``scanned=true``).
    """
    import tempfile

    from crossexam_pipeline import make_sample_pdf as msp
    from crossexam_pipeline.unsiloed import ScannedFallbackParser

    chunks: list[ParsedChunk] = []
    with tempfile.TemporaryDirectory() as tmp:
        dep_pdf = Path(tmp) / "deposition.pdf"
        log_pdf = Path(tmp) / "visitor-log.pdf"
        contract_pdf = Path(tmp) / "contract-msa.pdf"
        email_pdf = Path(tmp) / "email-thread.pdf"
        msp.make_pdf(dep_pdf)
        msp.make_visitor_log_pdf(log_pdf)
        msp.make_contract_pdf(contract_pdf)
        msp.make_email_thread_pdf(email_pdf)
        # Deposition keeps the historical "pdf" id prefix so pdf-p12-l1 etc.
        # survive for the existing demo ranking + eval gold ids.
        chunks += PdfTextParser(
            dep_pdf,
            id_prefix="pdf",
            document_id=msp.DEPOSITION_DOC_ID,
            document_title=msp.DEPOSITION_DOC_TITLE,
        ).parse()
        chunks += PdfTextParser(
            log_pdf,
            id_prefix="exhibit-visitor-log",
            document_id=msp.VISITOR_LOG_DOC_ID,
            document_title=msp.VISITOR_LOG_DOC_TITLE,
        ).parse()
        # Contract-vs-email demo (feat: multi-doc cross-exam). Chunk-id prefixes
        # match the documentId so cross-exam pairs land on contract-msa-pNN-lN /
        # email-thread-pNN-lN, which the backend + frontend target.
        chunks += PdfTextParser(
            contract_pdf,
            id_prefix="contract-msa",
            document_id=msp.CONTRACT_DOC_ID,
            document_title=msp.CONTRACT_DOC_TITLE,
        ).parse()
        chunks += PdfTextParser(
            email_pdf,
            id_prefix="email-thread",
            document_id=msp.EMAIL_DOC_ID,
            document_title=msp.EMAIL_DOC_TITLE,
        ).parse()
    if include_scanned:
        chunks += ScannedFallbackParser().parse()
    return chunks


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
