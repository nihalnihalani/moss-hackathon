"""Tests for build_index dry-run / disk mode producing backend-compatible JSON."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crossexam_pipeline.build_index import (
    _index_payload,
    build_index,
    write_chunks_json,
)
from crossexam_pipeline.fallback import DeterministicParser
from crossexam_pipeline.models import BBox, ParsedChunk


def test_dry_run_writes_backend_compatible_chunks(tmp_path: Path) -> None:
    """Dry-run writes the exact backend mock-index JSON shape."""
    chunks = DeterministicParser().parse()
    out = tmp_path / "sample_chunks.json"

    summary = build_index(chunks, index_name="crossexam-demo", out_path=out, dry_run=True)

    assert summary["mode"] == "dry-run"
    assert summary["chunk_count"] == len(chunks)
    assert out.exists()

    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data

    for rec in data:
        # Original backend keys plus depth-v2 documentId (always emitted) and
        # the optional documentTitle/scanned/quads. The backend Chunk model
        # ignores the extras.
        assert {"id", "text", "page", "bbox", "confidence", "documentId"} <= set(rec)
        assert set(rec.keys()) <= {
            "id", "text", "page", "bbox", "confidence",
            "documentId", "documentTitle", "scanned", "quads",
        }
        assert set(rec["bbox"].keys()) == {
            "page", "x0", "y0", "x1", "y1", "page_width", "page_height"
        }
        assert isinstance(rec["page"], int)
        assert isinstance(rec["confidence"], float)
        # Each record must re-validate as a ParsedChunk (backend can load it).
        ParsedChunk.model_validate(rec)


def test_disk_mode_when_no_moss_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without Moss credentials, build_index falls back to disk mode."""
    for var in ("MOSS_PROJECT_ID", "MOSS_API_KEY", "MOSS_INDEX_NAME"):
        monkeypatch.delenv(var, raising=False)
    chunks = DeterministicParser().parse()
    out = tmp_path / "chunks.json"

    summary = build_index(chunks, index_name="x", out_path=out, dry_run=False)

    assert summary["mode"] == "disk"
    assert out.exists()


def test_write_is_idempotent(tmp_path: Path) -> None:
    """Writing the same chunks in any order yields identical bytes."""
    chunks = DeterministicParser().parse()
    out = tmp_path / "chunks.json"
    write_chunks_json(chunks, out)
    first = out.read_text(encoding="utf-8")
    # Re-run with re-ordered input -> identical bytes (sorted output).
    write_chunks_json(list(reversed(chunks)), out)
    second = out.read_text(encoding="utf-8")
    assert first == second


def test_index_payload_persists_depth_v2_metadata() -> None:
    """The Moss upsert payload round-trips documentId/scanned/quads/title.

    moss_client._to_citation reads these back out of metadata, and the frontend
    REQUIRES documentId to be a string. Without persisting them here, every live
    citation would carry a null documentId and be rejected by the frontend.
    """
    bbox = BBox(page=2, x0=1, y0=2, x1=3, y1=4, page_width=612, page_height=792)
    chunk = ParsedChunk(
        id="c1",
        text="hello world",
        page=2,
        bbox=bbox,
        confidence=0.9,
        document_id="doc-xyz",
        document_title="My Doc",
        scanned=True,
        quads=[bbox],
    )

    payload = _index_payload([chunk], "idx")

    assert payload["index"] == "idx"
    doc = payload["documents"][0]
    # documentId is surfaced both top-level and in metadata (read from either).
    assert doc["documentId"] == "doc-xyz"
    meta = doc["metadata"]
    assert meta["documentId"] == "doc-xyz"
    assert meta["documentTitle"] == "My Doc"
    assert meta["scanned"] is True
    assert len(meta["quads"]) == 1
    assert set(meta["quads"][0].keys()) == {
        "page", "x0", "y0", "x1", "y1", "page_width", "page_height"
    }


def test_index_payload_omits_optional_fields_when_absent() -> None:
    """documentTitle/quads are omitted (single-doc back-compat) when unset."""
    bbox = BBox(page=1, x0=0, y0=0, x1=1, y1=1, page_width=612, page_height=792)
    chunk = ParsedChunk(
        id="c2",
        text="plain chunk",
        page=1,
        bbox=bbox,
        confidence=1.0,
        document_id="doc-1",
        document_title=None,
        scanned=False,
        quads=[],
    )

    meta = _index_payload([chunk], "idx")["documents"][0]["metadata"]

    assert meta["documentId"] == "doc-1"
    assert meta["scanned"] is False
    assert "documentTitle" not in meta
    assert "quads" not in meta


def test_output_round_trips_to_parsed_chunks(tmp_path: Path) -> None:
    """Written JSON re-validates back into ParsedChunk records."""
    chunks = DeterministicParser().parse()
    out = tmp_path / "chunks.json"
    write_chunks_json(chunks, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    loaded = [ParsedChunk.model_validate(rec) for rec in data]
    assert len(loaded) == len(chunks)
