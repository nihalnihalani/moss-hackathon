"""Pydantic data models shared across the CrossExam backend.

These models describe document chunks and the retrieval results that are
streamed to the frontend so it can draw citation boxes. Every retrieval result
carries a page number, a bounding box and a confidence score.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator


class BBox(BaseModel):
    """An axis-aligned bounding box on a single document page.

    Coordinates are in PDF point space (origin top-left) where ``(x0, y0)`` is
    the top-left corner and ``(x1, y1)`` is the bottom-right corner.

    Attributes:
        page: 1-based page number the box lives on.
        x0: Left edge.
        y0: Top edge.
        x1: Right edge.
        y1: Bottom edge.
        page_width: Width of the page in points, used to normalize coordinates.
        page_height: Height of the page in points, used to normalize.
    """

    page: int = Field(ge=1)
    x0: float = Field(ge=0.0)
    y0: float = Field(ge=0.0)
    x1: float = Field(ge=0.0)
    y1: float = Field(ge=0.0)
    page_width: float = Field(default=612.0, gt=0.0)
    page_height: float = Field(default=792.0, gt=0.0)

    @field_validator("x1")
    @classmethod
    def _x1_after_x0(cls, v: float, info: object) -> float:
        x0 = getattr(info, "data", {}).get("x0", 0.0)
        if v < x0:
            raise ValueError("x1 must be >= x0")
        return v

    @field_validator("y1")
    @classmethod
    def _y1_after_y0(cls, v: float, info: object) -> float:
        y0 = getattr(info, "data", {}).get("y0", 0.0)
        if v < y0:
            raise ValueError("y1 must be >= y0")
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def normalized(self) -> dict[str, float]:
        """Return the box normalized to ``[0, 1]`` relative to the page size.

        Useful for the frontend, which renders the page at an arbitrary scale.
        """
        return {
            "x0": self.x0 / self.page_width,
            "y0": self.y0 / self.page_height,
            "x1": self.x1 / self.page_width,
            "y1": self.y1 / self.page_height,
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def width(self) -> float:
        """Width of the box in points."""
        return self.x1 - self.x0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def height(self) -> float:
        """Height of the box in points."""
        return self.y1 - self.y0


class Chunk(BaseModel):
    """A retrievable span of document text with its physical location.

    Attributes:
        id: Stable unique identifier for the chunk.
        text: The chunk's text content.
        page: 1-based page number (mirrors ``bbox.page`` for convenience).
        bbox: Bounding box locating the chunk on the page.
        confidence: Ingest-time OCR/parse confidence in ``[0, 1]``.
    """

    id: str
    text: str
    page: int = Field(ge=1)
    bbox: BBox
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _page_matches_bbox(self) -> Chunk:
        if self.bbox.page != self.page:
            raise ValueError("Chunk.page must match Chunk.bbox.page")
        return self


class Citation(BaseModel):
    """A single retrieved chunk together with its relevance score.

    Attributes:
        chunk: The retrieved :class:`Chunk`.
        score: Relevance score in ``[0, 1]`` (higher is more relevant).
    """

    chunk: Chunk
    score: float = Field(ge=0.0, le=1.0)


class RetrievalResult(BaseModel):
    """The full result of a retrieval query for one user turn.

    Attributes:
        query: The user-turn text that was queried.
        citations: Ranked list of citations (best first).
        latency_ms: Wall-clock retrieval latency in milliseconds.
    """

    query: str
    citations: list[Citation] = Field(default_factory=list)
    latency_ms: float = Field(default=0.0, ge=0.0)

    def to_system_prompt(self) -> str:
        """Render the citations as a ``role="system"`` grounding message.

        The format intentionally surfaces page numbers and the verbatim text so
        the LLM can ground its answer and quote precisely.
        """
        if not self.citations:
            return (
                "No supporting passages were found in the document for the "
                "current question. Answer carefully and say so if unsure."
            )
        lines = [
            "Relevant passages retrieved from the document "
            "(use these to ground your answer; cite the page number):",
        ]
        for i, cit in enumerate(self.citations, start=1):
            lines.append(
                f"[{i}] (page {cit.chunk.page}, relevance {cit.score:.2f}) "
                f"{cit.chunk.text.strip()}"
            )
        return "\n".join(lines)
