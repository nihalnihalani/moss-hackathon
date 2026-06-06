"""Tests for the pydantic models and their backend projection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crossexam_pipeline.models import BBox, ParsedChunk, WordCitation, chunks_to_index_records


def _bbox(page: int = 1) -> BBox:
    # PDF points (top-left origin), US Letter page.
    return BBox(page=page, x0=72.0, y0=120.0, x1=540.0, y1=168.0)


def test_bbox_rejects_negative_coordinates() -> None:
    """BBox rejects negative coordinates."""
    with pytest.raises(ValidationError):
        BBox(page=1, x0=-1.0, y0=0.0, x1=540.0, y1=168.0)


def test_bbox_rejects_inverted_coordinates() -> None:
    """BBox rejects boxes where x1 < x0."""
    with pytest.raises(ValidationError):
        BBox(page=1, x0=540.0, y0=120.0, x1=72.0, y1=168.0)


def test_chunk_bbox_page_must_match() -> None:
    """A chunk's page must match its bbox page."""
    with pytest.raises(ValidationError):
        ParsedChunk(
            id="c1",
            text="hello",
            page=2,
            bbox=_bbox(page=1),
            confidence=0.9,
        )


def test_to_index_record_has_exact_backend_shape() -> None:
    """to_index_record emits exactly the backend's key set."""
    chunk = ParsedChunk(
        id="c1",
        text="warehouse on the night of the 14th",
        page=147,
        bbox=BBox(page=147, x0=72.0, y0=120.0, x1=540.0, y1=168.0),
        confidence=0.93,
        words=[WordCitation(text="warehouse", bbox=_bbox(147), confidence=0.95)],
        source="fallback",
    )
    rec = chunk.to_index_record()
    # The five original backend keys plus depth-v2 documentId (always emitted).
    # documentTitle is present here because the chunk carries the default title.
    assert {"id", "text", "page", "bbox", "confidence", "documentId"} <= set(rec.keys())
    # Backend Chunk.model_validate ignores the extra keys; only pipeline-only
    # enrichment (words/source) is dropped.
    assert set(rec.keys()) <= {
        "id", "text", "page", "bbox", "confidence",
        "documentId", "documentTitle", "scanned", "quads",
    }
    assert set(rec["bbox"].keys()) == {
        "page", "x0", "y0", "x1", "y1", "page_width", "page_height"
    }
    # Coordinates are PDF points, not normalized [0, 1].
    assert rec["bbox"]["x1"] > 1.0
    assert rec["bbox"]["page_width"] == 612.0
    assert rec["bbox"]["page_height"] == 792.0
    assert rec["page"] == 147
    assert isinstance(rec["page"], int)
    assert isinstance(rec["confidence"], float)
    assert "words" not in rec
    assert "source" not in rec


def test_chunks_to_index_records_roundtrips_through_validation() -> None:
    """Projected index records re-validate as ParsedChunk."""
    chunk = ParsedChunk(
        id="c1", text="t", page=3, bbox=_bbox(3), confidence=0.8
    )
    records = chunks_to_index_records([chunk])
    # The projected record must itself re-validate as a ParsedChunk.
    revalidated = ParsedChunk.model_validate(records[0])
    assert revalidated.id == "c1"
    assert revalidated.page == 3
