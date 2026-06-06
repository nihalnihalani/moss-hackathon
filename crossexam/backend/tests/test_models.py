"""Tests for :mod:`crossexam_backend.models`."""

from __future__ import annotations

import pytest

from crossexam_backend.models import BBox, Chunk, Citation, RetrievalResult


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
