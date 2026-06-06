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
from crossexam_pipeline.models import ParsedChunk, chunks_to_index_records
from crossexam_pipeline.unsiloed import MissingCredentialsError, UnsiloedParser

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
    input: Path = typer.Option(  # noqa: A002 - matches required CLI flag name
        ...,
        "--input",
        "-i",
        help="Source document to parse (PDF for Unsiloed; ignored sample is "
        "used for --dry-run).",
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
        help="Use the deterministic, network-free fallback parser.",
    ),
    sample: Path = typer.Option(
        DEFAULT_SAMPLE_PATH,
        "--sample",
        help="Sample JSON used by the fallback parser (--dry-run only).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    """Parse a document into backend-compatible chunk JSON.

    With ``--dry-run``, the bundled sample is parsed deterministically (no
    network). Without it, Unsiloed is used and ``UNSILOED_API_KEY`` must be set.
    """
    _configure_logging(verbose)

    if dry_run:
        logger.info("Parsing with deterministic fallback (sample=%s).", sample)
        chunks = DeterministicParser(sample_path=sample).parse()
    else:
        try:
            parser = UnsiloedParser.from_env()
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


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
