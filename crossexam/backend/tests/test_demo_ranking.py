"""Regression tests for the canonical CrossExam demo ranking.

These guard the LIVE-mode wiring: the agent publishes ``citations[0]``, so the
top hit returned by :class:`MockIndex` must match the page the agent talks
about. Before the retrieval-ranking fix the admission question surfaced the
page-41 recant first while the agent cited page 12 -- a visible break in the
citation box. The recant question must symmetrically surface page 41.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crossexam_backend.retrieval.mock_index import MockIndex

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chunks.json"

# The admission lives on page 12 (chunk pdf-p12-l1); the recant on page 41.
ADMISSION_CHUNK_ID = "pdf-p12-l1"
ADMISSION_PAGE = 12
RECANT_CHUNK_ID = "pdf-p41-l1"
RECANT_PAGE = 41


@pytest.fixture()
def index() -> MockIndex:
    """A MockIndex loaded from the real shipped fixture (419 chunks)."""
    return MockIndex.from_fixture(FIXTURE)


# Several natural phrasings of the canonical demo admission question. Each must
# rank the page-12 admission first.
@pytest.mark.parametrize(
    "question",
    [
        "Did the witness admit they were at the warehouse on the night of the 14th?",
        "where were you on the night of the 14th",
        "were you at the warehouse on the night of the 14th",
    ],
)
async def test_admission_question_ranks_page_12_first(
    index: MockIndex, question: str
) -> None:
    """The admission question returns the page-12 chunk as the #1 hit."""
    result = await index.query(question, top_k=5)
    assert result.citations, "expected at least one citation"
    top = result.citations[0].chunk
    assert top.id == ADMISSION_CHUNK_ID, (
        f"expected {ADMISSION_CHUNK_ID} first, got {top.id} (page {top.page})"
    )
    assert top.page == ADMISSION_PAGE


@pytest.mark.parametrize(
    "question",
    [
        "Did the witness contradict himself or change his testimony "
        "about the night of the 14th?",
        "when did he say he left the warehouse",
    ],
)
async def test_recant_question_ranks_page_41_first(
    index: MockIndex, question: str
) -> None:
    """A contradiction/recant question returns a page-41 chunk as the #1 hit."""
    result = await index.query(question, top_k=5)
    assert result.citations, "expected at least one citation"
    top = result.citations[0].chunk
    assert top.page == RECANT_PAGE, (
        f"expected a page-41 chunk first, got {top.id} (page {top.page})"
    )


async def test_admission_outranks_recant_chunk_directly(index: MockIndex) -> None:
    """For the admission question, the page-12 chunk beats the page-41 recant.

    A chunk that contains both salient query terms (``warehouse`` AND the
    tightly-clustered ``night of the 14th`` context) outranks one where those
    terms are scattered, which is precisely what distinguishes the admission
    from the recant.
    """
    result = await index.query(
        "Did the witness admit they were at the warehouse "
        "on the night of the 14th?",
        top_k=10,
    )
    ranks = {c.chunk.id: i for i, c in enumerate(result.citations)}
    assert ADMISSION_CHUNK_ID in ranks
    assert ranks[ADMISSION_CHUNK_ID] == 0
    if RECANT_CHUNK_ID in ranks:
        assert ranks[ADMISSION_CHUNK_ID] < ranks[RECANT_CHUNK_ID]
