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
    """The admission question ranks the page-12 chunk first WITHIN the deposition.

    The shipped fixture now also contains independent EXHIBIT documents (field
    notes / visitor log) that legitimately match "night of the 14th"; the old
    assertion that page 12 was the single global #1 hit encoded the obsolete
    single-document world. The canonical demo invariant is that, within the
    deposition itself, page 12 (the alibi) is the top hit the agent cites.
    """
    result = await index.query(question, top_k=10)
    assert result.citations, "expected at least one citation"
    deposition_hits = [
        c for c in result.citations if c.documentId == "deposition-holloway"
    ]
    assert deposition_hits, "expected at least one deposition citation"
    top = deposition_hits[0].chunk
    assert top.id == ADMISSION_CHUNK_ID, (
        f"expected {ADMISSION_CHUNK_ID} first in deposition, got {top.id} "
        f"(page {top.page})"
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
    """A recant question ranks a page-41 chunk first WITHIN the deposition.

    As above, the multi-document fixture now surfaces matching exhibit chunks;
    the invariant the demo relies on is that the recant page (41) is the top
    deposition hit for a recant/"when did he leave" question.
    """
    result = await index.query(question, top_k=10)
    assert result.citations, "expected at least one citation"
    deposition_hits = [
        c for c in result.citations if c.documentId == "deposition-holloway"
    ]
    assert deposition_hits, "expected at least one deposition citation"
    top = deposition_hits[0].chunk
    assert top.page == RECANT_PAGE, (
        f"expected a page-41 deposition chunk first, got {top.id} "
        f"(page {top.page})"
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
    # Scope to the deposition: independent exhibit chunks now also match the
    # query (multi-document fixture), but within the deposition the admission
    # (page 12) must still outrank the recant (page 41) and be its #1 hit.
    deposition = [
        c.chunk.id for c in result.citations if c.documentId == "deposition-holloway"
    ]
    ranks = {cid: i for i, cid in enumerate(deposition)}
    assert ADMISSION_CHUNK_ID in ranks
    assert ranks[ADMISSION_CHUNK_ID] == 0
    if RECANT_CHUNK_ID in ranks:
        assert ranks[ADMISSION_CHUNK_ID] < ranks[RECANT_CHUNK_ID]
