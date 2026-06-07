"""Pydantic data models shared across the CrossExam backend.

These models describe document chunks and the retrieval results that are
streamed to the frontend so it can draw citation boxes. Every retrieval result
carries a page number, a bounding box and a confidence score.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

# Default single-document id used for back-compat. A single-doc demo carries one
# id like this on every chunk/citation (see the contract invariants).
DEFAULT_DOCUMENT_ID = "deposition-holloway"


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
        documentId: Which document this chunk belongs to (depth-v2 multi-doc).
            Defaults to a single id so single-doc fixtures stay valid.
        documentTitle: Optional human label for the document switcher.
        quads: Optional per-line bounding boxes hugging the glyphs (all on
            ``bbox.page``); carried through onto the citation for rendering.
        quadTexts: Optional per-line text snippets parallel to ``quads``. Used
            to focus a broad retrieved chunk down to the exact evidence lines
            before publishing highlight geometry.
        scanned: Whether the source page was a scan (OCR).
    """

    id: str
    text: str
    page: int = Field(ge=1)
    bbox: BBox
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    documentId: str = Field(default=DEFAULT_DOCUMENT_ID)  # noqa: N815 - wire contract field name
    documentTitle: str | None = None  # noqa: N815 - wire contract field name
    quads: list[BBox] | None = None
    quadTexts: list[str] | None = None  # noqa: N815 - wire contract field name
    scanned: bool = False

    @model_validator(mode="after")
    def _page_matches_bbox(self) -> Chunk:
        if self.bbox.page != self.page:
            raise ValueError("Chunk.page must match Chunk.bbox.page")
        if self.quads is not None:
            for quad in self.quads:
                if quad.page != self.bbox.page:
                    raise ValueError("Chunk.quads must be on the same page as bbox")
        if (
            self.quads is not None
            and self.quadTexts is not None
            and len(self.quadTexts) != len(self.quads)
        ):
            raise ValueError("Chunk.quadTexts must be parallel to Chunk.quads")
        return self


class Citation(BaseModel):
    """A single retrieved chunk together with its relevance score.

    Carries the depth-v2 multi-document / quad-highlight / scanned-source fields
    from the shared contract. The geometry stays in :attr:`chunk` (the union
    ``bbox`` is used for page-jump + label); :attr:`quads`, when present, are the
    per-line boxes the frontend renders.

    Attributes:
        chunk: The retrieved :class:`Chunk`.
        score: Relevance score in ``[0, 1]`` (higher is more relevant).
        quads: Optional per-line bounding boxes hugging the actual glyphs. All
            must lie on the same page as ``chunk.bbox`` (the union rect).
        documentId: Which document this citation came from. Defaults to a single
            id (:data:`DEFAULT_DOCUMENT_ID`) so single-doc callers stay valid.
        documentTitle: Optional human label for the document switcher.
        scanned: Whether the source page was a scan (OCR) — drives the "scanned
            source" badge in the UI.
    """

    chunk: Chunk
    score: float = Field(ge=0.0, le=1.0)
    quads: list[BBox] | None = None
    documentId: str = Field(default=DEFAULT_DOCUMENT_ID)  # noqa: N815 - wire contract field name
    documentTitle: str | None = None  # noqa: N815 - wire contract field name
    scanned: bool = False

    @property
    def confidence(self) -> float:
        """Ingest-time confidence of the underlying chunk (contract field)."""
        return self.chunk.confidence

    @model_validator(mode="after")
    def _quads_on_bbox_page(self) -> Citation:
        """Every quad must sit on the same page as the union ``bbox``."""
        if self.quads is not None:
            page = self.chunk.bbox.page
            for quad in self.quads:
                if quad.page != page:
                    raise ValueError(
                        "Citation.quads must be on the same page as chunk.bbox"
                    )
        return self


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

    @property
    def top_text(self) -> str:
        """Text of the best (first) citation's chunk, or ``""`` when empty.

        A convenience for the faithfulness gate, which verifies the agent's
        answer against the chunk it is about to highlight (the top citation).
        """
        if not self.citations:
            return ""
        return self.citations[0].chunk.text

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


class MemoryRef(BaseModel):
    """A recall of a citation already surfaced earlier this session (feat 5).

    Instead of re-snapping a box the agent already showed, the backend emits a
    :class:`MemoryRef` so the agent can say "as we saw on page N".

    Attributes:
        kind: Always ``"recall"`` (a literal discriminator on the wire frame).
        citationId: The id of the chunk being recalled.
        documentId: Which document the recalled citation lives in.
        page: 1-based page the recalled citation is on.
        note: Human phrasing for the recall, e.g. ``"as we saw on page 12"``.
    """

    kind: Literal["recall"] = "recall"
    citationId: str  # noqa: N815 - wire contract field name
    documentId: str = Field(default=DEFAULT_DOCUMENT_ID)  # noqa: N815 - wire contract field name
    page: int = Field(ge=1)
    note: str


class HopTrace(BaseModel):
    """One step of the agentic query-decomposition trail (feat 1).

    Attributes:
        subQuery: The decomposed sub-question that was retrieved.
        citationIds: Ids of the citations this sub-query surfaced.
    """

    subQuery: str  # noqa: N815 - wire contract field name
    citationIds: list[str] = Field(default_factory=list)  # noqa: N815 - wire contract field name


class Speaker(BaseModel):
    """Who triggered a turn in meeting mode (feat 4).

    Attributes:
        id: Stable speaker id, e.g. ``"spk_1"``.
        label: Human label, e.g. ``"Counsel"``.
    """

    id: str
    label: str


class MultiHopResult(BaseModel):
    """The result of a multi-hop retrieval over one (possibly complex) question.

    Composes the single-hop :class:`RetrievalResult` shape: it carries a fused,
    de-duplicated list of citations spanning sub-queries / documents / pages,
    the decomposition trail, and a cross-document contradiction flag.

    Attributes:
        query: The original (complex) user-turn text.
        citations: Fused, de-duplicated citations (best first), 0..N across docs.
        hops: The decomposition trail — one :class:`HopTrace` per sub-query.
        contradiction: Whether two high-confidence citations assert mutually
            exclusive facts (cross-page / cross-doc).
        cross_document: Whether the conflicting pair spans two different
            ``documentId``s (an alibi vs. an independent exhibit) rather than an
            in-document inconsistency. Only meaningful when ``contradiction``.
        primary_id: The citation id to page-jump to first (best hit), or ``None``
            when there are no citations.
        latency_ms: Wall-clock retrieval latency in milliseconds.
    """

    query: str
    citations: list[Citation] = Field(default_factory=list)
    hops: list[HopTrace] = Field(default_factory=list)
    contradiction: bool = False
    cross_document: bool = False
    primary_id: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)

    def to_retrieval_result(self) -> RetrievalResult:
        """Project down to a single-hop :class:`RetrievalResult` (back-compat)."""
        return RetrievalResult(
            query=self.query,
            citations=list(self.citations),
            latency_ms=self.latency_ms,
        )
