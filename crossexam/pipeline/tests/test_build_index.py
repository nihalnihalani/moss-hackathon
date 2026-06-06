"""Tests for build_index dry-run / disk mode producing backend-compatible JSON."""

from __future__ import annotations

import json
from pathlib import Path

from crossexam_pipeline.build_index import build_index, write_chunks_json
from crossexam_pipeline.fallback import DeterministicParser
from crossexam_pipeline.models import ParsedChunk


def test_dry_run_writes_backend_compatible_chunks(tmp_path: Path) -> None:
    chunks = DeterministicParser().parse()
    out = tmp_path / "sample_chunks.json"

    summary = build_index(chunks, index_name="crossexam-demo", out_path=out, dry_run=True)

    assert summary["mode"] == "dry-run"
    assert summary["chunk_count"] == len(chunks)
    assert out.exists()

    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data

    for rec in data:
        # Exact backend mock-index shape.
        assert set(rec.keys()) == {"id", "text", "page", "bbox", "confidence"}
        assert set(rec["bbox"].keys()) == {"page", "x0", "y0", "x1", "y1"}
        assert isinstance(rec["page"], int)
        assert isinstance(rec["confidence"], float)
        # Each record must re-validate as a ParsedChunk (backend can load it).
        ParsedChunk.model_validate(rec)


def test_disk_mode_when_no_moss_credentials(tmp_path: Path, monkeypatch) -> None:
    for var in ("MOSS_PROJECT_ID", "MOSS_API_KEY", "MOSS_INDEX_NAME"):
        monkeypatch.delenv(var, raising=False)
    chunks = DeterministicParser().parse()
    out = tmp_path / "chunks.json"

    summary = build_index(chunks, index_name="x", out_path=out, dry_run=False)

    assert summary["mode"] == "disk"
    assert out.exists()


def test_write_is_idempotent(tmp_path: Path) -> None:
    chunks = DeterministicParser().parse()
    out = tmp_path / "chunks.json"
    write_chunks_json(chunks, out)
    first = out.read_text(encoding="utf-8")
    # Re-run with re-ordered input -> identical bytes (sorted output).
    write_chunks_json(list(reversed(chunks)), out)
    second = out.read_text(encoding="utf-8")
    assert first == second


def test_output_round_trips_to_parsed_chunks(tmp_path: Path) -> None:
    chunks = DeterministicParser().parse()
    out = tmp_path / "chunks.json"
    write_chunks_json(chunks, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    loaded = [ParsedChunk.model_validate(rec) for rec in data]
    assert len(loaded) == len(chunks)
