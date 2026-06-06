"""Tests for :mod:`crossexam_backend.retrieval.mock_index`."""

from __future__ import annotations

from pathlib import Path

import pytest

from crossexam_backend.retrieval.mock_index import MockIndex

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chunks.json"


@pytest.fixture()
def index() -> MockIndex:
    """A MockIndex loaded from the shipped fixture."""
    return MockIndex.from_fixture(FIXTURE)


async def test_warehouse_query_returns_relevant_chunk(index: MockIndex) -> None:
    """The warehouse-on-the-14th query surfaces the alibi testimony first."""
    result = await index.query("where were you the night of the 14th warehouse", top_k=5)
    assert result.citations, "expected at least one citation"
    top_text = result.citations[0].chunk.text.lower()
    assert "warehouse" in top_text
    assert "14th" in top_text or "fourteenth" in top_text


async def test_bbox_and_page_passthrough(index: MockIndex) -> None:
    """Each citation carries the original page number and bbox."""
    result = await index.query("warehouse keys access night", top_k=3)
    cit = result.citations[0]
    assert cit.chunk.page == cit.chunk.bbox.page
    assert cit.chunk.bbox.x1 >= cit.chunk.bbox.x0
    assert cit.chunk.bbox.y1 >= cit.chunk.bbox.y0
    # normalized form is available for the frontend
    assert 0.0 <= cit.chunk.bbox.normalized["x0"] <= 1.0


async def test_latency_is_measured_and_submillisecond(index: MockIndex) -> None:
    """Latency is populated and realistically tiny for an in-memory index."""
    result = await index.query("indemnification", top_k=2)
    assert result.latency_ms > 0.0
    assert result.latency_ms < 50.0


async def test_top_k_is_respected(index: MockIndex) -> None:
    """No more than ``top_k`` citations are returned."""
    result = await index.query("warehouse night witness testimony", top_k=2)
    assert len(result.citations) <= 2


async def test_ranking_is_deterministic(index: MockIndex) -> None:
    """Repeated identical queries yield identical ordering."""
    q = "warehouse night of the 14th inventory count"
    first = await index.query(q, top_k=5)
    second = await index.query(q, top_k=5)
    assert [c.chunk.id for c in first.citations] == [
        c.chunk.id for c in second.citations
    ]


async def test_contradiction_present_across_pages(index: MockIndex) -> None:
    """The retrieval surfaces both the alibi (p12) and the recant (p41)."""
    result = await index.query(
        "did the witness leave the warehouse on the night of the 14th", top_k=8
    )
    pages = {c.chunk.page for c in result.citations}
    # Page 12: stayed past midnight. Page 41: left before 8pm -> contradiction.
    assert 12 in pages
    assert 41 in pages


async def test_scores_sorted_descending(index: MockIndex) -> None:
    """Citations come back sorted best-first."""
    result = await index.query("warehouse security guard keys", top_k=5)
    scores = [c.score for c in result.citations]
    assert scores == sorted(scores, reverse=True)


async def test_alpha_extremes_change_results(index: MockIndex) -> None:
    """Pure-keyword vs pure-semantic weighting both return results."""
    kw = await index.query("termination notice", top_k=3, alpha=0.0)
    sem = await index.query("termination notice", top_k=3, alpha=1.0)
    assert kw.citations
    assert sem.citations


def test_from_fixture_missing_file() -> None:
    """A missing fixture raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        MockIndex.from_fixture(Path("/nonexistent/does-not-exist.json"))
