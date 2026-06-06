"""Tests for :mod:`crossexam_backend.models`."""

from __future__ import annotations

import pytest

from crossexam_backend.models import (
    DEFAULT_DOCUMENT_ID,
    BBox,
    Chunk,
    Citation,
    HopTrace,
    MemoryRef,
    MultiHopResult,
    RetrievalResult,
    Speaker,
)


def _make_chunk(cid: str = "c1", page: int = 12) -> Chunk:
    bbox = BBox(page=page, x0=72.0, y0=120.0, x1=540.0, y1=168.0)
    return Chunk(id=cid, text="hello warehouse", page=page, bbox=bbox, confidence=0.9)


def test_bbox_normalized() -> None:
    """Normalized form divides by page dimensions."""
    bbox = BBox(page=1, x0=153.0, y0=198.0, x1=306.0, y1=396.0)
    norm = bbox.normalized
    assert norm["x0"] == pytest.approx(153.0 / 612.0)
    assert norm["y1"] == pytest.approx(396.0 / 792.0)


def test_bbox_width_height() -> None:
    """Width and height are computed from the corners."""
    bbox = BBox(page=1, x0=10.0, y0=20.0, x1=110.0, y1=70.0)
    assert bbox.width == pytest.approx(100.0)
    assert bbox.height == pytest.approx(50.0)


def test_bbox_rejects_inverted_corners() -> None:
    """x1 < x0 (or y1 < y0) is rejected."""
    with pytest.raises(ValueError):
        BBox(page=1, x0=100.0, y0=0.0, x1=10.0, y1=50.0)


def test_chunk_page_must_match_bbox_page() -> None:
    """A chunk's page must agree with its bbox's page."""
    bbox = BBox(page=5, x0=0.0, y0=0.0, x1=10.0, y1=10.0)
    with pytest.raises(ValueError):
        Chunk(id="x", text="t", page=6, bbox=bbox)


def test_citation_score_bounds() -> None:
    """Citation scores must be within [0, 1]."""
    chunk = _make_chunk()
    with pytest.raises(ValueError):
        Citation(chunk=chunk, score=1.5)


def test_retrieval_result_to_system_prompt_includes_pages() -> None:
    """The rendered system prompt surfaces page numbers and text."""
    chunk = _make_chunk(page=41)
    result = RetrievalResult(
        query="where on the 14th?",
        citations=[Citation(chunk=chunk, score=0.83)],
        latency_ms=0.4,
    )
    prompt = result.to_system_prompt()
    assert "page 41" in prompt
    assert "warehouse" in prompt
    assert "0.83" in prompt


def test_retrieval_result_empty_prompt() -> None:
    """With no citations a graceful fallback prompt is produced."""
    result = RetrievalResult(query="q", citations=[], latency_ms=0.1)
    prompt = result.to_system_prompt()
    assert "No supporting passages" in prompt


# --------------------------------------------------------------------------- #
# Depth-v2 contract fields                                                     #
# --------------------------------------------------------------------------- #
def test_citation_defaults_document_id_for_backcompat() -> None:
    """A citation built the old way still gets the default documentId."""
    cit = Citation(chunk=_make_chunk(), score=0.5)
    assert cit.documentId == DEFAULT_DOCUMENT_ID
    assert cit.quads is None
    assert cit.scanned is False
    # confidence proxies the chunk's ingest confidence.
    assert cit.confidence == pytest.approx(0.9)


def test_citation_with_quads_document_and_scanned() -> None:
    """A citation validates with quads, documentId, documentTitle and scanned."""
    quad = BBox(page=12, x0=72.0, y0=120.0, x1=300.0, y1=132.0)
    cit = Citation(
        chunk=_make_chunk(page=12),
        score=0.8,
        quads=[quad],
        documentId="exhibit-7",
        documentTitle="Exhibit 7",
        scanned=True,
    )
    assert cit.quads == [quad]
    assert cit.documentId == "exhibit-7"
    assert cit.documentTitle == "Exhibit 7"
    assert cit.scanned is True


def test_citation_quads_must_match_bbox_page() -> None:
    """A quad on a different page than the union bbox is rejected."""
    bad_quad = BBox(page=13, x0=72.0, y0=120.0, x1=300.0, y1=132.0)
    with pytest.raises(ValueError):
        Citation(chunk=_make_chunk(page=12), score=0.8, quads=[bad_quad])


def test_chunk_carries_document_fields() -> None:
    """Chunk validates with documentId/quads/scanned."""
    quad = BBox(page=12, x0=72.0, y0=120.0, x1=300.0, y1=132.0)
    chunk = Chunk(
        id="c1",
        text="t",
        page=12,
        bbox=BBox(page=12, x0=72.0, y0=120.0, x1=540.0, y1=168.0),
        documentId="exhibit-7",
        documentTitle="Exhibit 7",
        quads=[quad],
        scanned=True,
    )
    assert chunk.documentId == "exhibit-7"
    assert chunk.scanned is True


def test_memory_ref_model() -> None:
    """MemoryRef validates with the recall discriminator and note."""
    ref = MemoryRef(citationId="depo-p12", page=12, note="as we saw on page 12")
    assert ref.kind == "recall"
    assert ref.documentId == DEFAULT_DOCUMENT_ID


def test_hop_trace_and_speaker_models() -> None:
    """HopTrace and Speaker validate per the contract."""
    hop = HopTrace(subQuery="where on the 14th", citationIds=["depo-p12"])
    assert hop.citationIds == ["depo-p12"]
    spk = Speaker(id="spk_1", label="Counsel")
    assert spk.label == "Counsel"


def test_multihop_result_composes_and_projects() -> None:
    """MultiHopResult carries hops/contradiction and projects to RetrievalResult."""
    cit = Citation(chunk=_make_chunk(page=12), score=0.9, documentId="deposition")
    mh = MultiHopResult(
        query="contradiction?",
        citations=[cit],
        hops=[HopTrace(subQuery="where", citationIds=["c1"])],
        contradiction=True,
        primary_id="c1",
        latency_ms=2.5,
    )
    projected = mh.to_retrieval_result()
    assert isinstance(projected, RetrievalResult)
    assert projected.citations == [cit]
    assert projected.latency_ms == pytest.approx(2.5)
