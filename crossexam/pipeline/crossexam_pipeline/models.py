"""Pydantic data models for the CrossExam pipeline.

These shapes mirror what the CrossExam backend mock index consumes. The
canonical on-disk chunk record (one element of the JSON list) is::

    {"id": str, "text": str, "page": int,
     "bbox": {"page": int, "x0": float, "y0": float, "x1": float, "y1": float,
              "page_width": float, "page_height": float},
     "confidence": float}

All bbox coordinates are in PDF points (top-left origin), matching the backend
:class:`crossexam_backend.models.BBox`. ``page_width``/``page_height`` carry the
page size (also in points) so the frontend can map points to its render scale.

``ParsedChunk`` carries extra metadata (word-level citations, source) that the
backend ignores; :meth:`ParsedChunk.to_index_record` projects a chunk down to
the exact backend-compatible shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BBox(BaseModel):
    """An axis-aligned bounding box on a single page.

    Coordinates are in PDF points (origin top-left, y growing downward) where
    ``(x0, y0)`` is the top-left corner and ``(x1, y1)`` is the bottom-right.
    This matches the canonical backend :class:`crossexam_backend.models.BBox`.
    ``page_width``/``page_height`` carry the page size (also in points) so the
    frontend can map points onto its render scale at any resolution.

    Attributes:
        page: 1-based page number the box lives on.
        x0: Left edge (points, inclusive).
        y0: Top edge (points, inclusive).
        x1: Right edge (points).
        y1: Bottom edge (points).
        page_width: Page width in points (default US Letter 612.0).
        page_height: Page height in points (default US Letter 792.0).
    """

    # Allow (and ignore) any extra keys so the canonical backend bbox shape
    # validates here unchanged.
    model_config = ConfigDict(extra="ignore")

    page: int = Field(..., ge=1, description="1-based page number.")
    x0: float = Field(..., ge=0.0, description="Left edge (points).")
    y0: float = Field(..., ge=0.0, description="Top edge (points).")
    x1: float = Field(..., ge=0.0, description="Right edge (points).")
    y1: float = Field(..., ge=0.0, description="Bottom edge (points).")
    page_width: float = Field(default=612.0, gt=0.0, description="Page width (points).")
    page_height: float = Field(default=792.0, gt=0.0, description="Page height (points).")

    @model_validator(mode="after")
    def _check_ordering(self) -> BBox:
        """Ensure the box is non-degenerate (x0 <= x1 and y0 <= y1)."""
        if self.x1 < self.x0:
            raise ValueError(f"bbox x1 ({self.x1}) must be >= x0 ({self.x0})")
        if self.y1 < self.y0:
            raise ValueError(f"bbox y1 ({self.y1}) must be >= y0 ({self.y0})")
        return self


class WordCitation(BaseModel):
    """A single word with its own bounding box on the page.

    Word-level citations let the agent point at the *exact* phrase it is
    quoting (e.g. highlight "warehouse on the night of the 14th") rather than
    the whole chunk.

    Attributes:
        text: The word's text (no surrounding whitespace).
        bbox: Bounding box of the word.
        confidence: Per-word OCR/extraction confidence in ``[0, 1]``.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1)
    bbox: BBox
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ParsedChunk(BaseModel):
    """A single retrievable chunk of a parsed document.

    The first five fields (``id``, ``text``, ``page``, ``bbox``, ``confidence``)
    are exactly the backend-compatible record. ``words`` and ``source`` are
    pipeline-side enrichment and are dropped by :meth:`to_index_record`.

    Attributes:
        id: Stable, deterministic identifier for the chunk.
        text: The chunk's plain text.
        page: 1-based page number the chunk is anchored to.
        bbox: Bounding box covering the chunk on the page.
        confidence: Aggregate extraction confidence in ``[0, 1]``.
        words: Optional word-level citations within the chunk.
        source: Optional provenance tag, e.g. ``"unsiloed"`` or ``"fallback"``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    page: int = Field(..., ge=1)
    bbox: BBox
    confidence: float = Field(..., ge=0.0, le=1.0)
    words: list[WordCitation] = Field(default_factory=list)
    source: str | None = Field(default=None)

    @model_validator(mode="after")
    def _bbox_page_matches(self) -> ParsedChunk:
        """The chunk's bbox must live on the chunk's own page."""
        if self.bbox.page != self.page:
            raise ValueError(
                f"bbox.page ({self.bbox.page}) must match chunk.page ({self.page})"
            )
        return self

    def to_index_record(self) -> dict[str, Any]:
        """Project to the exact backend-compatible chunk record.

        Returns:
            A dict with keys ``id``, ``text``, ``page``, ``bbox``, ``confidence``
            and nothing else. ``bbox`` is itself a plain dict with
            ``page, x0, y0, x1, y1, page_width, page_height`` (all points).
        """
        return {
            "id": self.id,
            "text": self.text,
            "page": self.page,
            "bbox": {
                "page": self.bbox.page,
                "x0": self.bbox.x0,
                "y0": self.bbox.y0,
                "x1": self.bbox.x1,
                "y1": self.bbox.y1,
                "page_width": self.bbox.page_width,
                "page_height": self.bbox.page_height,
            },
            "confidence": self.confidence,
        }


def chunks_to_index_records(chunks: list[ParsedChunk]) -> list[dict[str, Any]]:
    """Project a list of chunks to backend-compatible records.

    Args:
        chunks: Parsed chunks to project.

    Returns:
        A JSON-serializable list of backend-compatible chunk records.
    """
    return [c.to_index_record() for c in chunks]
