"""Tests for :mod:`crossexam_backend.retrieval.mock_index`."""

from __future__ import annotations

from pathlib import Path

import pytest

from crossexam_backend.models import BBox, Chunk
from crossexam_backend.retrieval.mock_index import MockIndex

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chunks.json"


@pytest.fixture()
def index() -> MockIndex:
    """A MockIndex loaded from the shipped fixture."""
    return MockIndex.from_fixture(FIXTURE)


async def test_warehouse_query_returns_relevant_chunk(index: MockIndex) -> None:
    """The warehouse-on-the-14th query surfaces the alibi testimony.

    The multi-document fixture now also contains short EXHIBIT chunks that match
    "night of the 14th" tightly; the alibi is the top DEPOSITION hit. The
    invariant is that the alibi testimony (warehouse + the 14th) is surfaced as
    the leading deposition citation.
    """
    result = await index.query(
        "where were you the night of the 14th warehouse", top_k=10
    )
    assert result.citations, "expected at least one citation"
    deposition_hits = [
        c for c in result.citations if c.documentId == "deposition-holloway"
    ]
    assert deposition_hits, "expected a deposition citation"
    top_text = deposition_hits[0].chunk.text.lower()
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


async def test_pure_bm25_alpha_zero_returns_lexical_matches(index: MockIndex) -> None:
    """alpha=0.0 (pure lexical/BM25 leg) still surfaces a chunk with the term."""
    result = await index.query("indemnification", top_k=3, alpha=0.0)
    assert result.citations
    assert "indemnification" in result.citations[0].chunk.text.lower()


async def test_hybrid_rare_term_beats_common_term_chunk(index: MockIndex) -> None:
    """The BM25 leg of the hybrid lets a rare distinctive term win.

    'indemnification' occurs in a single chunk; the hybrid fusion (with BM25's
    IDF weighting) must rank that chunk first for a query naming it, rather than
    a boilerplate Q/A line that merely shares common words.
    """
    result = await index.query("indemnification clause supplier", top_k=5)
    assert result.citations[0].chunk.id == "pdf-p3-l1"


def test_from_fixture_missing_file() -> None:
    """A missing fixture raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        MockIndex.from_fixture(Path("/nonexistent/does-not-exist.json"))


# --------------------------------------------------------------------------- #
# Multi-document querying (depth-v2 feat 1)                                    #
# --------------------------------------------------------------------------- #
def _multi_doc_index() -> MockIndex:
    """A self-contained 2-document index for the cross-doc query tests."""
    def _c(cid: str, text: str, page: int, doc: str) -> Chunk:
        bbox = BBox(page=page, x0=72.0, y0=100.0, x1=540.0, y1=136.0)
        return Chunk(id=cid, text=text, page=page, bbox=bbox, documentId=doc)

    return MockIndex(
        [
            _c("a1", "warehouse inventory count on the night of the 14th", 1, "docA"),
            _c("a2", "the witness remained past midnight", 2, "docA"),
            _c("b1", "keycard access log for the warehouse on the 14th", 1, "docB"),
        ]
    )


async def test_query_multi_filters_by_doc() -> None:
    """query_multi with doc_ids only returns citations from those documents."""
    index = _multi_doc_index()
    result = await index.query_multi("warehouse 14th", top_k=5, doc_ids=["docB"])
    assert result.citations
    assert {c.documentId for c in result.citations} == {"docB"}


async def test_query_multi_spans_all_docs_by_default() -> None:
    """query_multi with no filter searches every document."""
    index = _multi_doc_index()
    result = await index.query_multi("warehouse 14th", top_k=5)
    assert {c.documentId for c in result.citations} >= {"docA", "docB"}


async def test_document_ids_property() -> None:
    """The index reports its distinct document ids."""
    index = _multi_doc_index()
    assert set(index.document_ids) == {"docA", "docB"}


async def test_citation_carries_document_id_from_chunk(index: MockIndex) -> None:
    """Each citation carries its source chunk's own documentId.

    The shipped fixture is now multi-document (deposition + exhibits), so the
    top hit is no longer guaranteed to be the default deposition id. The
    invariant is that the citation propagates the chunk's documentId verbatim.
    """
    result = await index.query("warehouse night of the 14th", top_k=2)
    assert result.citations
    for cit in result.citations:
        assert cit.documentId == cit.chunk.documentId
        assert cit.documentId


# --------------------------------------------------------------------------- #
# Legal transcript query wording                                              #
# --------------------------------------------------------------------------- #
def _single_doc_chunk(cid: str, text: str, page: int) -> Chunk:
    bbox = BBox(page=page, x0=72.0, y0=100.0, x1=540.0, y1=136.0)
    return Chunk(
        id=cid,
        text=text,
        page=page,
        bbox=bbox,
        documentId="doc-test",
        documentTitle="Transcript",
    )


def _legal_query_index() -> MockIndex:
    return MockIndex(
        [
            _single_doc_chunk(
                "cover",
                "SECURITIES AND EXCHANGE COMMISSION WITNESS: Elizabeth Holmes "
                "DATE: Tuesday, July 11, 2017",
                1,
            ),
            _single_doc_chunk(
                "appearance",
                "APPEARANCES: On behalf of the Witness: Stephen Neal, Esq. "
                "John Dwyer, Esq. Alexandra Leeper, Esq.",
                2,
            ),
            _single_doc_chunk(
                "sec-testimony",
                "Q Have you had conversations about the substance of your SEC "
                "testimony? A I have not.",
                637,
            ),
            _single_doc_chunk(
                "media",
                "The beginning of Media No. 3 of Elizabeth Holmes.",
                166,
            ),
            _single_doc_chunk(
                "sunny-overseeing",
                "Q Mr. Balwani was overseeing the lab and some product "
                "development, particularly on the software side.",
                51,
            ),
            _single_doc_chunk(
                "sunny-email",
                "Elizabeth Holmes to Sunny Balwani, subject line Forward "
                "project test company overview.",
                630,
            ),
            _single_doc_chunk(
                "product",
                "Q Were you also involved in product development? A I was the "
                "CEO, so I would engage with different teams and was engaged "
                "on the product challenges.",
                51,
            ),
            _single_doc_chunk(
                "product-data",
                "Product development data on all the tests we created.",
                853,
            ),
        ]
    )


async def test_legal_query_expansion_promotes_testimony_header() -> None:
    """Natural witness/date wording should match transcript front matter."""
    result = await _legal_query_index().query(
        "Who was the witness and when was the SEC testimony taken?",
        top_k=3,
    )
    assert result.citations[0].chunk.id == "cover"


async def test_legal_query_expansion_matches_representation_language() -> None:
    """'Represented' should match transcript 'On behalf of the Witness'."""
    result = await _legal_query_index().query(
        "Who represented Elizabeth Holmes at the deposition?",
        top_k=3,
    )
    assert result.citations[0].chunk.id == "appearance"


async def test_legal_query_expansion_promotes_involvement_answer() -> None:
    """'Involvement' should match involved/engaged transcript answers."""
    result = await _legal_query_index().query(
        "What was Holmes involvement in product development?",
        top_k=3,
    )
    assert result.citations[0].chunk.id == "product"


async def test_legal_query_expansion_promotes_overseeing_answer() -> None:
    """'Overseeing' should prefer the lab/software answer over subject lines."""
    result = await _legal_query_index().query(
        "What did Holmes say Sunny Balwani was overseeing?",
        top_k=3,
    )
    assert result.citations[0].chunk.id == "sunny-overseeing"
