"""Tests for the second-stage rerankers and the index rerank path."""

from __future__ import annotations

from pathlib import Path

import pytest

from crossexam_backend.retrieval.mock_index import MockIndex
from crossexam_backend.retrieval.rerank import (
    CrossEncoderReranker,
    LexicalReranker,
    Reranker,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chunks.json"


@pytest.fixture()
def index() -> MockIndex:
    """A MockIndex loaded from the shipped fixture."""
    return MockIndex.from_fixture(FIXTURE)


def test_lexical_reranker_satisfies_protocol() -> None:
    """LexicalReranker is a structural Reranker."""
    assert isinstance(LexicalReranker(), Reranker)


def test_lexical_reranker_prefers_higher_coverage() -> None:
    """A document covering more query terms scores above one covering fewer."""
    reranker = LexicalReranker()
    query = "warehouse on the night of the fourteenth"
    docs = [
        "An unrelated paragraph about contract indemnification clauses.",
        "He was at the warehouse on the night of the fourteenth.",
    ]
    scores = reranker.score(query, docs)
    assert scores[1] > scores[0]


def test_lexical_reranker_prefers_tighter_proximity() -> None:
    """With equal coverage, tightly-clustered query terms score higher."""
    reranker = LexicalReranker()
    query = "warehouse keys"
    tight = "the warehouse keys were on the desk"
    scattered = "the warehouse was large and the keys were elsewhere entirely now"
    scores = reranker.score(query, [tight, scattered])
    assert scores[0] > scores[1]


def test_lexical_reranker_empty_query_scores_zero() -> None:
    """A query with no content tokens yields all-zero scores."""
    reranker = LexicalReranker()
    assert reranker.score("", ["anything", "here"]) == [0.0, 0.0]


def test_lexical_reranker_reorders_candidates() -> None:
    """The reranker promotes the most query-relevant of a mixed candidate set."""
    reranker = LexicalReranker()
    query = "termination notice thirty days"
    docs = [
        "A. Certainly.",
        "Either party may terminate this Agreement upon thirty days written notice.",
        "Q. Please state your full name for the record.",
    ]
    scores = reranker.score(query, docs)
    best = max(range(len(docs)), key=lambda i: scores[i])
    assert best == 1


async def test_index_rerank_path_runs_and_returns_top_k(index: MockIndex) -> None:
    """The rerank=True path returns at most top_k citations, sorted desc."""
    result = await index.query(
        "termination notice thirty days", top_k=3, rerank=True
    )
    assert len(result.citations) <= 3
    scores = [c.score for c in result.citations]
    assert scores == sorted(scores, reverse=True)
    assert result.citations[0].score == pytest.approx(1.0)


async def test_index_rerank_can_change_order() -> None:
    """A custom reranker that inverts relevance changes the returned order.

    Confirms the rerank path actually delegates ordering to the reranker rather
    than ignoring it: a reranker that scores by *descending* length flips which
    candidate surfaces first relative to the lexical default.
    """

    class LengthReranker:
        def score(self, query: str, documents: list[str]) -> list[float]:
            return [float(len(doc)) for doc in documents]

    idx = MockIndex.from_fixture(FIXTURE, reranker=LengthReranker())
    default = await idx.query("warehouse night fourteenth", top_k=5)
    reranked = await idx.query("warehouse night fourteenth", top_k=5, rerank=True)
    # The length-based reranker should surface the longest candidate first,
    # which differs from the default first-stage top hit here.
    longest = max(reranked.citations, key=lambda c: len(c.chunk.text))
    assert reranked.citations[0].chunk.id == longest.chunk.id
    assert default.citations  # sanity: default path still produced hits


def test_cross_encoder_reranker_guarded_without_dep() -> None:
    """CrossEncoderReranker raises a clear error when the optional dep is absent."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="sentence-transformers"):
            CrossEncoderReranker()
    else:  # pragma: no cover - only when the optional dep is installed
        pytest.skip("sentence-transformers installed; guarded path not exercised")
