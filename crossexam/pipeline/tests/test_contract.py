"""Coordinate/chunk-schema contract test (BLOCKER 1 regression guard).

The pipeline emits bounding boxes, the backend consumes them, and the frontend
draws them. They MUST agree on one coordinate system. The canonical decision:

    bbox is PDF points (top-left origin), and every bbox dict carries
    page_width/page_height (also points):
        {page, x0, y0, x1, y1, page_width, page_height}

This test fails loudly if anyone reverts the pipeline to normalized [0, 1]
coordinates or drops the page-dimension fields, which would render the demo
citation box as a sub-pixel dot in the top-left corner.

It exercises the contract from both directions:

1. Backend -> pipeline: every record in the committed backend fixture
   ``backend/fixtures/sample_chunks.json`` must validate through the pipeline
   ``ParsedChunk`` model. (Before the fix the fixture's point coordinates blew
   past the pipeline's ``le=1.0`` bound and the page-dim keys tripped
   ``extra="forbid"`` -- this assertion failed.)

2. Pipeline -> backend: the pipeline's own fallback output must satisfy the
   backend chunk shape -- bbox carries all seven keys, is non-degenerate, and
   uses point-range coordinates (NOT normalized [0, 1]). A minimal copy of the
   backend ``BBox``/``Chunk`` models is used so this test has no dependency on
   the backend package being importable.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from crossexam_pipeline.fallback import DeterministicParser
from crossexam_pipeline.models import ParsedChunk

# backend/fixtures/sample_chunks.json relative to this test file.
_PIPELINE_ROOT = Path(__file__).resolve().parents[1]
_CROSSEXAM_ROOT = _PIPELINE_ROOT.parent
BACKEND_FIXTURE = _CROSSEXAM_ROOT / "backend" / "fixtures" / "sample_chunks.json"

# Expected key set for every bbox dict on the wire.
_BBOX_KEYS = {"page", "x0", "y0", "x1", "y1", "page_width", "page_height"}


# --- Minimal copy of the canonical backend Chunk shape (points) --------------
class _BackendBBox(BaseModel):
    """Mirror of crossexam_backend.models.BBox (PDF points, top-left origin)."""

    page: int = Field(ge=1)
    x0: float = Field(ge=0.0)
    y0: float = Field(ge=0.0)
    x1: float = Field(ge=0.0)
    y1: float = Field(ge=0.0)
    page_width: float = Field(default=612.0, gt=0.0)
    page_height: float = Field(default=792.0, gt=0.0)

    @model_validator(mode="after")
    def _ordering(self) -> _BackendBBox:
        if self.x1 < self.x0:
            raise ValueError("x1 must be >= x0")
        if self.y1 < self.y0:
            raise ValueError("y1 must be >= y0")
        return self


class _BackendChunk(BaseModel):
    """Mirror of crossexam_backend.models.Chunk."""

    id: str
    text: str
    page: int = Field(ge=1)
    bbox: _BackendBBox
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _page_matches(self) -> _BackendChunk:
        if self.bbox.page != self.page:
            raise ValueError("Chunk.page must match Chunk.bbox.page")
        return self


def test_backend_fixture_validates_through_pipeline_model() -> None:
    """Every committed backend chunk record must load as a pipeline ParsedChunk."""
    assert BACKEND_FIXTURE.exists(), BACKEND_FIXTURE
    records = json.loads(BACKEND_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(records, list) and records

    for rec in records:
        chunk = ParsedChunk.model_validate(rec)  # must not raise
        bbox = rec["bbox"]
        assert _BBOX_KEYS <= set(bbox.keys()), (
            f"bbox missing keys: {_BBOX_KEYS - set(bbox.keys())}"
        )
        # Coordinates are PDF points, not normalized [0, 1].
        assert max(bbox["x1"], bbox["y1"]) > 1.0, (
            f"bbox looks normalized, not points: {bbox}"
        )
        assert 0.0 <= chunk.bbox.x0 < chunk.bbox.x1 <= chunk.bbox.page_width
        assert 0.0 <= chunk.bbox.y0 < chunk.bbox.y1 <= chunk.bbox.page_height


def test_pipeline_output_satisfies_backend_chunk_shape() -> None:
    """Pipeline fallback output must validate as the backend Chunk shape in points."""
    chunks = DeterministicParser().parse()
    assert chunks, "expected at least one chunk"

    for c in chunks:
        rec = c.to_index_record()
        bbox = rec["bbox"]
        # All seven contract keys present.
        assert set(bbox.keys()) == _BBOX_KEYS, bbox
        # Non-degenerate.
        assert bbox["x1"] > bbox["x0"]
        assert bbox["y1"] > bbox["y0"]
        # Point range, not normalized [0, 1].
        assert bbox["x1"] > 1.0 and bbox["y1"] > 1.0, (
            f"coordinates look normalized, not points: {bbox}"
        )
        assert bbox["page_width"] > 1.0 and bbox["page_height"] > 1.0
        # The backend must be able to load it.
        backend_chunk = _BackendChunk.model_validate(rec)
        assert backend_chunk.bbox.x1 <= backend_chunk.bbox.page_width
        assert backend_chunk.bbox.y1 <= backend_chunk.bbox.page_height
