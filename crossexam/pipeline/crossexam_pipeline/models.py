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


def _bbox_to_dict(b: BBox) -> dict[str, Any]:
    """Serialize a :class:`BBox` to the canonical seven-key wire dict."""
    return {
        "page": b.page,
        "x0": b.x0,
        "y0": b.y0,
        "x1": b.x1,
        "y1": b.y1,
        "page_width": b.page_width,
        "page_height": b.page_height,
    }


# Default single-document identity used when a parser does not supply one. The
# demo's primary document is the Holloway deposition.
DEFAULT_DOCUMENT_ID = "deposition-holloway"
DEFAULT_DOCUMENT_TITLE = "Deposition of Raymond T. Holloway"


class ParsedChunk(BaseModel):
    """A single retrievable chunk of a parsed document.

    The first five fields (``id``, ``text``, ``page``, ``bbox``, ``confidence``)
    plus the depth-v2 fields (``documentId``, ``documentTitle``, ``scanned``,
    ``quads``) form the backend-compatible record. ``words`` and ``source`` are
    pipeline-side enrichment and are dropped by :meth:`to_index_record`.

    Depth-v2 additions (see ``docs/depth-v2-contract.md``):

    * ``document_id`` / ``document_title`` (feat 1, multi-document): which source
      document this chunk came from, and a human label for the doc switcher.
    * ``quads`` (feat 2): per-LINE bounding boxes that hug the actual glyphs of
      the chunk across line wraps. ``bbox`` remains the UNION rect for the
      page-jump and the label; the frontend renders ``quads``.
    * ``scanned`` (feat 3): the source page was a scan (OCR / vision path), so
      the frontend shows a "scanned source" badge and ``quads`` are region boxes
      rather than tight glyph lines.

    Attributes:
        id: Stable, deterministic identifier for the chunk.
        text: The chunk's plain text.
        page: 1-based page number the chunk is anchored to.
        bbox: Union bounding box covering the whole chunk on the page.
        confidence: Aggregate extraction confidence in ``[0, 1]``.
        document_id: Source document identifier (e.g. ``deposition-holloway``).
        document_title: Human-readable document label for the switcher.
        scanned: Whether the source page came from a scan / OCR path.
        quads: Per-line bounding boxes hugging the glyphs; union ~= ``bbox``.
        words: Optional word-level citations within the chunk.
        source: Optional provenance tag, e.g. ``"unsiloed"`` or ``"fallback"``.
    """

    # Accept (and ignore) unknown keys so a record serialized by a NEWER schema
    # still validates here, and so legacy records (without the depth-v2 fields)
    # still validate via the defaults below.
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    page: int = Field(..., ge=1)
    bbox: BBox
    confidence: float = Field(..., ge=0.0, le=1.0)
    document_id: str = Field(
        default=DEFAULT_DOCUMENT_ID,
        min_length=1,
        description="Source document id (feat 1).",
    )
    document_title: str | None = Field(
        default=DEFAULT_DOCUMENT_TITLE,
        description="Human label for the doc switcher (feat 1).",
    )
    scanned: bool = Field(
        default=False, description="Source page came from a scan / OCR path (feat 3)."
    )
    quads: list[BBox] = Field(
        default_factory=list,
        description="Per-line glyph boxes; their union ~= bbox (feat 2).",
    )
    words: list[WordCitation] = Field(default_factory=list)
    source: str | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _accept_wire_aliases(cls, data: Any) -> Any:  # noqa: ANN401 - validator sees arbitrary input
        """Accept the camelCase wire keys (``documentId``/``documentTitle``).

        ``to_index_record`` emits camelCase (the frontend contract), so a record
        round-tripped through the wire must also load back. Snake_case keys take
        precedence when both are present.
        """
        if isinstance(data, dict):
            mapped = {"documentId": "document_id", "documentTitle": "document_title"}
            for wire, snake in mapped.items():
                if wire in data and snake not in data:
                    data = {**data, snake: data[wire]}
        return data

    @model_validator(mode="after")
    def _bbox_page_matches(self) -> ParsedChunk:
        """The chunk's bbox and every quad must live on the chunk's own page."""
        if self.bbox.page != self.page:
            raise ValueError(
                f"bbox.page ({self.bbox.page}) must match chunk.page ({self.page})"
            )
        for q in self.quads:
            if q.page != self.page:
                raise ValueError(
                    f"quad.page ({q.page}) must match chunk.page ({self.page})"
                )
        return self

    def to_index_record(self) -> dict[str, Any]:
        """Project to the backend-compatible chunk record.

        Emits the original five backend keys plus the depth-v2 fields
        (``documentId``, ``documentTitle``, ``scanned``, and ``quads`` when
        present). Backend ``Chunk.model_validate`` ignores the extra keys, while
        the frontend reads them. ``words``/``source`` are pipeline-only and are
        dropped.

        Returns:
            A JSON-serializable dict. ``bbox`` (and each ``quads`` entry) is a
            plain dict with ``page, x0, y0, x1, y1, page_width, page_height``
            (all points).
        """
        record: dict[str, Any] = {
            "id": self.id,
            "text": self.text,
            "page": self.page,
            "bbox": _bbox_to_dict(self.bbox),
            "confidence": self.confidence,
            "documentId": self.document_id,
        }
        if self.document_title is not None:
            record["documentTitle"] = self.document_title
        if self.scanned:
            record["scanned"] = True
        if self.quads:
            record["quads"] = [_bbox_to_dict(q) for q in self.quads]
        return record


def chunks_to_index_records(chunks: list[ParsedChunk]) -> list[dict[str, Any]]:
    """Project a list of chunks to backend-compatible records.

    Args:
        chunks: Parsed chunks to project.

    Returns:
        A JSON-serializable list of backend-compatible chunk records.
    """
    return [c.to_index_record() for c in chunks]
