"""Tests for :mod:`crossexam_backend.retrieval.multihop`.

These tests build a SELF-CONTAINED two-document chunk set inline so they do not
depend on the shipped fixture or any pipeline regeneration. Doc A is a witness
deposition with an alibi (stayed at the warehouse past midnight) and a later
recant (left before 8 p.m.); Doc B is an exhibit log corroborating the times.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crossexam_backend.models import BBox, Chunk
from crossexam_backend.retrieval.mock_index import MockIndex
from crossexam_backend.retrieval.multihop import (
    MultiHopRetriever,
    QueryDecomposer,
    contradiction_pair,
    detect_contradiction,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chunks.json"

# The CANONICAL demo contradiction question — the exact string the frontend mock
# uses (mockData.DEMO_CONTRADICTION_QUESTION) so mock == live. It does NOT echo
# "Harbor Street": robustness must come from the principled detector, not from
# the question literally repeating the location. A witness whose deposition alibi
# (page 12, the deposition document) collides with an independent exhibit (the
# downtown visitor log, a DIFFERENT document) placing him two miles away that
# same night.
DEMO_CONTRADICTION_Q = "Did the witness contradict himself about the night of the 14th?"
DEMO_PRIMARY_ID = "pdf-p12-l1"  # deposition alibi (clean presence claim)
DEMO_OTHER_ID = "exhibit-visitor-log-p2-l1"  # cross-doc separating exhibit
DEMO_FIELD_NOTES_DOC = "exhibit-field-notes"  # scanned exhibit — never the anchor

# Alternate phrasings of the same contradiction. None of these echoes "Harbor
# Street"; all must STILL anchor on the deposition (never the scanned
# field-notes), proving the selection is principled, not overfit to a string.
ALT_CONTRADICTION_QS = (
    "Did his account of the night of the 14th conflict with the other exhibits?",
    "Is there anything that conflicts with his warehouse alibi on the night of "
    "the 14th?",
)


def _chunk(
    cid: str,
    text: str,
    page: int,
    doc_id: str,
    *,
    y0: float = 100.0,
) -> Chunk:
    bbox = BBox(page=page, x0=72.0, y0=y0, x1=540.0, y1=y0 + 36.0)
    return Chunk(
        id=cid, text=text, page=page, bbox=bbox, confidence=0.95, documentId=doc_id
    )


def _two_doc_chunks() -> list[Chunk]:
    """A small, self-contained 2-document corpus with a built-in contradiction."""
    return [
        # --- Document A: the deposition (the contradiction lives here) -------
        _chunk(
            "depo-p12",
            "Q. Where were you on the night of the 14th? A. I was at the Harbor "
            "Street warehouse from approximately 9:00 p.m. until well past "
            "midnight, conducting the inventory count with Mr. Reyes. I remained "
            "there the entire evening of the fourteenth.",
            12,
            "deposition",
        ),
        _chunk(
            "depo-p41",
            "On further questioning the witness stated, contrary to his earlier "
            "testimony, that on the night of the 14th he had actually left the "
            "Harbor Street warehouse before 8:00 p.m. and departed for home, "
            "spending the rest of the evening away from the warehouse.",
            41,
            "deposition",
        ),
        _chunk(
            "depo-p3",
            "Q. Please state your full name for the record. A. Raymond Theodore "
            "Holloway.",
            3,
            "deposition",
        ),
        # --- Document B: a separate exhibit log (cross-document corroboration)
        _chunk(
            "exhibit-p1",
            "Exhibit 7, the keycard access log, records every entry to the Harbor "
            "Street warehouse on the night of the 14th between 6:00 p.m. and 6:00 "
            "a.m.",
            1,
            "exhibit",
        ),
        _chunk(
            "exhibit-p2",
            "The security guard Daniel Cole signed the warehouse visitor register "
            "for the evening shift.",
            2,
            "exhibit",
        ),
    ]


@pytest.fixture()
def index() -> MockIndex:
    """A MockIndex over the self-contained two-document corpus."""
    return MockIndex(_two_doc_chunks())


# --------------------------------------------------------------------------- #
# QueryDecomposer                                                             #
# --------------------------------------------------------------------------- #
def test_decomposer_splits_contradiction_question() -> None:
    """A contradiction question yields more than one sub-query."""
    dec = QueryDecomposer()
    subs = dec.decompose(
        "did the witness contradict himself about the night of the 14th?"
    )
    assert len(subs) > 1
    joined = " ".join(subs).lower()
    assert "14th" in joined or "night" in joined
    # The principled pair seeks the core claim and the evidence that
    # contradicts it (not a literal conjunction fragment). Updated from the old
    # "conflicting statement about ..." wording.
    assert any("contradict" in s.lower() for s in subs)
    # No contradiction-framing word leaks into the located-claim sub-query.
    assert "contradict" not in subs[0].lower()


def test_decomposer_flags_multihop() -> None:
    """The is_multihop heuristic recognises contradiction cues."""
    dec = QueryDecomposer()
    assert dec.is_multihop("did he contradict his earlier testimony?")
    assert not dec.is_multihop("what is your name?")


def test_decomposer_simple_question_single_hop() -> None:
    """A plain question decomposes to a single (cleaned) sub-query."""
    dec = QueryDecomposer()
    subs = dec.decompose("what is your full name?")
    assert subs == ["what is your full name?"]


def test_decomposer_llm_hook_used_when_present() -> None:
    """An injected LLM hook overrides the heuristic when it returns sub-queries."""
    dec = QueryDecomposer(llm_fn=lambda q: ["sub one", "sub two", "sub three"])
    assert dec.uses_llm
    assert dec.decompose("anything contradictory?") == ["sub one", "sub two", "sub three"]


def test_decomposer_llm_hook_failure_falls_back() -> None:
    """A raising LLM hook degrades to the heuristic instead of failing."""

    def boom(_q: str) -> list[str]:
        raise RuntimeError("llm down")

    dec = QueryDecomposer(llm_fn=boom)
    subs = dec.decompose("did the witness contradict himself about the warehouse?")
    assert len(subs) >= 1  # heuristic produced something


# --------------------------------------------------------------------------- #
# detect_contradiction                                                         #
# --------------------------------------------------------------------------- #
def test_detect_contradiction_on_opposing_pages() -> None:
    """The stayed-past-midnight vs left-before-8 pair is flagged."""
    chunks = _two_doc_chunks()
    from crossexam_backend.retrieval.mock_index import _chunk_to_citation

    cits = [
        _chunk_to_citation(chunks[0], 1.0),  # depo-p12: at warehouse past midnight
        _chunk_to_citation(chunks[1], 0.9),  # depo-p41: left warehouse, away/home
    ]
    contradiction, primary, cross_document = detect_contradiction(cits)
    assert contradiction is True
    assert primary == "depo-p12"
    # Both chunks are in the same deposition document -> not cross-document.
    assert cross_document is False


def test_no_contradiction_when_no_opposing_predicates() -> None:
    """Two unrelated citations are not a contradiction."""
    chunks = _two_doc_chunks()
    from crossexam_backend.retrieval.mock_index import _chunk_to_citation

    cits = [
        _chunk_to_citation(chunks[2], 1.0),  # name
        _chunk_to_citation(chunks[4], 0.9),  # guard register
    ]
    contradiction, primary, cross_document = detect_contradiction(cits)
    assert contradiction is False
    assert primary is None
    assert cross_document is False


# --------------------------------------------------------------------------- #
# Generalized cross-document conflict: OBLIGATION + NUMERIC TERM (contract vs   #
# email). SELF-CONTAINED inline fixtures — independent of the shipped fixture.  #
# --------------------------------------------------------------------------- #
# These mirror the spec's anchor/text exactly (§4.2 consent vs Acme-without-
# sign-off; Net-30 vs Net-60) so the detector is exercised on the principled
# cues, not on a regenerated fixture.
_CONTRACT_42 = (
    "Section 4.2 Subcontracting. Contractor shall not delegate, assign, or "
    "subcontract any of the Services, in whole or in part, to any third party "
    "without the prior written consent of Client. Any purported subcontracting "
    "in violation of this Section 4.2 shall constitute a material breach of "
    "this Agreement."
)
_EMAIL_ACME = (
    "Re: Section 4.2 / Acme work order -- Just to keep you in the loop, we "
    "already handed the integration work off to Acme Labs last week so we could "
    "hit the deadline. I know we never got the formal sign-off from your side, "
    "but it was faster to just get them started."
)
_CONTRACT_61 = (
    "Section 6.1 Payment. Client shall pay each undisputed invoice in full "
    "within thirty (30) days of receipt (Net-30). Payment terms may not be "
    "modified except by a written amendment signed by both parties."
)
_EMAIL_NET60 = (
    "Re: Invoice #2231 / payment -- Heads up on Invoice #2231, cash flow is "
    "tight this quarter, so we're going to pay this one on a Net-60 basis "
    "instead. We didn't sign anything to change the terms, but 60 days is just "
    "how it has to be for now."
)


def test_obligation_conflict_contract_clause_vs_email_admission() -> None:
    """§4.2 (shall-not subcontract without consent) vs the email Acme admission.

    Shared anchor (clause §4.2 / the subcontracting term / "Acme") + opposing
    obligation (prohibition vs. acting without the required sign-off). The
    governing CONTRACT clause must be the PRIMARY; the EMAIL is the cross-doc
    counter.
    """
    from crossexam_backend.retrieval.mock_index import _chunk_to_citation

    contract = _chunk("contract-42", _CONTRACT_42, 7, "contract-msa")
    email = _chunk("email-acme", _EMAIL_ACME, 1, "email-thread")
    cits = [
        _chunk_to_citation(contract, 0.95),
        _chunk_to_citation(email, 0.92),
    ]
    contradiction, primary, cross = detect_contradiction(cits)
    assert contradiction is True
    assert cross is True
    assert primary == "contract-42"  # the governing clause anchors


def test_numeric_term_conflict_net30_vs_net60() -> None:
    """Net-30 contract term vs the email's Net-60 admission for the same invoice.

    Shared anchor (the Net- payment term / Invoice #2231) + DIFFERENT numeric
    values (30 vs. 60). The CONTRACT clause (which also locks the term behind a
    written amendment) is the PRIMARY; the EMAIL is the cross-doc counter.
    """
    from crossexam_backend.retrieval.mock_index import _chunk_to_citation

    contract = _chunk("contract-61", _CONTRACT_61, 9, "contract-msa")
    email = _chunk("email-net60", _EMAIL_NET60, 1, "email-thread")
    cits = [
        _chunk_to_citation(contract, 0.95),
        _chunk_to_citation(email, 0.92),
    ]
    contradiction, primary, cross = detect_contradiction(cits)
    assert contradiction is True
    assert cross is True
    assert primary == "contract-61"  # the defining contract term anchors


def test_obligation_pair_builds_cross_document_frame() -> None:
    """build_frame carries contradiction + crossDocument for the §4.2 pair.

    Mirrors the live path: a contradiction MultiHopResult whose primary is the
    contract clause and whose counter is the email admission must produce a
    frame with ``contradiction: True``, ``crossDocument: True`` and
    ``primaryId`` = the contract clause.
    """
    from crossexam_backend.agent import build_frame
    from crossexam_backend.models import MultiHopResult
    from crossexam_backend.retrieval.mock_index import _chunk_to_citation

    contract = _chunk("contract-42", _CONTRACT_42, 7, "contract-msa")
    email = _chunk("email-acme", _EMAIL_ACME, 1, "email-thread")
    cits = [_chunk_to_citation(contract, 0.95), _chunk_to_citation(email, 0.92)]
    contradiction, primary, cross = detect_contradiction(cits)
    result = MultiHopResult(
        query="Does the email contradict the contract on subcontracting?",
        citations=cits,
        hops=[],
        contradiction=contradiction,
        cross_document=cross,
        primary_id=primary,
        latency_ms=0.0,
    )
    frame = build_frame(
        result,
        answer_text="The contract bars subcontracting without prior written consent.",
    )
    assert frame["contradiction"] is True
    assert frame["crossDocument"] is True
    assert frame["primaryId"] == "contract-42"
    by_id = {c["id"]: c for c in frame["citations"]}
    assert "contract-42" in by_id
    assert "email-acme" in by_id  # the counter survives
    assert by_id["contract-42"]["documentId"] != by_id["email-acme"]["documentId"]


def test_same_net_term_is_not_a_conflict() -> None:
    """Two passages naming the SAME Net- value agree (no numeric conflict)."""
    from crossexam_backend.retrieval.mock_index import _chunk_to_citation

    contract = _chunk("contract-61", _CONTRACT_61, 9, "contract-msa")
    email = _chunk(
        "email-net30",
        "Re: Invoice #2231 -- confirming we will pay Invoice #2231 on Net-30 as "
        "the agreement requires.",
        1,
        "email-thread",
    )
    cits = [_chunk_to_citation(contract, 0.95), _chunk_to_citation(email, 0.92)]
    contradiction, _primary, _cross = detect_contradiction(cits)
    assert contradiction is False


def test_obligation_routing_questions_are_multihop() -> None:
    """Obligation/term-conflict questions route through the multi-hop path."""
    dec = QueryDecomposer()
    assert dec.is_multihop("Does the email contradict the contract?")
    assert dec.is_multihop("Did the contractor breach the subcontracting clause?")
    assert dec.is_multihop("Is the payment term consistent across the file?")


# --------------------------------------------------------------------------- #
# MultiHopRetriever                                                            #
# --------------------------------------------------------------------------- #
async def test_multihop_returns_multiple_citations_and_flags_contradiction(
    index: MockIndex,
) -> None:
    """The contradiction question returns >1 citation across pages/docs."""
    retriever = MultiHopRetriever(index)
    result = await retriever.retrieve(
        "did the witness contradict himself about the night of the 14th at the "
        "warehouse?",
        top_k=6,
        per_hop_k=6,
    )
    assert len(result.citations) > 1
    pages = {c.chunk.page for c in result.citations}
    assert 12 in pages and 41 in pages
    assert result.contradiction is True
    assert result.primary_id is not None
    # Hops trail recorded (decomposed into >1 sub-query).
    assert len(result.hops) > 1
    assert all(h.subQuery for h in result.hops)


async def test_multihop_spans_multiple_documents(index: MockIndex) -> None:
    """Fused citations span more than one documentId."""
    retriever = MultiHopRetriever(index)
    result = await retriever.retrieve(
        "warehouse night of the 14th keycard access and the witness testimony",
        top_k=8,
        per_hop_k=8,
    )
    doc_ids = {c.documentId for c in result.citations}
    assert len(doc_ids) >= 2
    assert "deposition" in doc_ids
    assert "exhibit" in doc_ids


async def test_multihop_dedupes_citations(index: MockIndex) -> None:
    """A citation surfaced by multiple hops appears only once in the result."""
    retriever = MultiHopRetriever(index)
    result = await retriever.retrieve(
        "did the witness contradict himself about the warehouse on the 14th?",
        top_k=8,
        per_hop_k=8,
    )
    ids = [c.chunk.id for c in result.citations]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- #
# Structural detector / honesty on the REAL shipped fixture                    #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def real_index() -> MockIndex:
    """A MockIndex over the real shipped fixture (deposition + exhibits)."""
    return MockIndex.from_fixture(FIXTURE)


def test_separation_cue_beats_class_difference_cross_doc() -> None:
    """A separation-cue pair is preferred over a plain cross-doc class clash.

    Both an exhibit that says the subject was 'downtown' (class difference) and
    one that says he was 'two miles from the warehouse' (separation cue) clash
    with the deposition alibi. The detector must select the SEPARATION-cue pair
    and anchor on the presence-asserting deposition claim.
    """
    from crossexam_backend.retrieval.mock_index import _chunk_to_citation

    alibi = _chunk(
        "depo-p12",
        "I was at the Harbor Street warehouse from 9 p.m. past midnight on the "
        "night of the 14th.",
        12,
        "deposition",
    )
    downtown = _chunk(
        "field-notes",
        "On the night of the 14th: saw him downtown around 9:30 p.m.",
        1,
        "exhibit-notes",
    )
    separated = _chunk(
        "visitor-log",
        "He signed the downtown visitor log at 9:40 p.m. on the 14th -- two "
        "miles from the Harbor Street warehouse.",
        2,
        "exhibit-log",
    )
    cits = [
        _chunk_to_citation(alibi, 1.0),
        _chunk_to_citation(downtown, 0.98),  # higher score, but class-only clash
        _chunk_to_citation(separated, 0.80),  # lower score, separation cue
    ]
    contradiction, primary, cross = detect_contradiction(cits)
    assert contradiction is True
    assert cross is True
    assert primary == "depo-p12"  # presence-asserting claim is the anchor
    pair = contradiction_pair(cits)
    assert pair is not None
    assert pair[0] == "depo-p12"
    assert pair[1] == "visitor-log"  # separation-cue evidence wins the slot


async def test_demo_selects_cross_doc_pair_on_real_fixture(
    real_index: MockIndex,
) -> None:
    """The demo question resolves to the CROSS-DOC pair on the real fixture.

    Must select (pdf-p12-l1 [deposition], exhibit-visitor-log-p2-l1 [exhibit])
    — different documentIds — NOT the same-doc page-12/page-41 deposition pair.
    """
    retriever = MultiHopRetriever(real_index)
    result = await retriever.retrieve(DEMO_CONTRADICTION_Q, top_k=5, per_hop_k=5)

    assert result.contradiction is True
    assert result.cross_document is True
    assert result.primary_id == DEMO_PRIMARY_ID
    ids = {c.chunk.id for c in result.citations}
    # BOTH members of the cross-doc pair survive into the published citations.
    assert DEMO_PRIMARY_ID in ids
    assert DEMO_OTHER_ID in ids
    # The two sides live in DIFFERENT documents (cross-document conflict).
    by_id = {c.chunk.id: c for c in result.citations}
    assert by_id[DEMO_PRIMARY_ID].documentId != by_id[DEMO_OTHER_ID].documentId
    assert by_id[DEMO_PRIMARY_ID].documentId == "deposition-holloway"
    assert by_id[DEMO_OTHER_ID].documentId == "exhibit-visitor-log"
    # The scanned field-notes exhibit is NEVER the anchor — it can only ever be
    # the counter/evidence, so the page-jump never lands on a scanned source.
    assert by_id[DEMO_PRIMARY_ID].documentId != DEMO_FIELD_NOTES_DOC


async def test_canonical_question_build_frame_anchors_deposition(
    real_index: MockIndex,
) -> None:
    """The CANONICAL demo question (the exact string the frontend uses) anchors.

    Mirrors the live agent path: route the canonical contradiction question
    through the multi-hop retriever, then :func:`build_frame` with a real
    drafted answer. The published frame must carry ``contradiction: True``,
    ``crossDocument: True``, ``primaryId == 'pdf-p12-l1'`` (the deposition
    presence claim), and the counter citations must include
    ``exhibit-visitor-log-p2-l1`` from a DIFFERENT document.
    """
    from crossexam_backend.agent import build_frame

    retriever = MultiHopRetriever(real_index)
    result = await retriever.retrieve(DEMO_CONTRADICTION_Q, top_k=5, per_hop_k=5)
    # An answer that echoes ONLY the deposition side — the contradiction exempts
    # both sides from the answer-faithfulness gate, so the counter still
    # survives.
    frame = build_frame(
        result,
        answer_text="The witness testified he was at the Harbor Street warehouse.",
    )
    assert frame["contradiction"] is True
    assert frame["crossDocument"] is True
    assert frame["primaryId"] == DEMO_PRIMARY_ID
    by_id = {c["id"]: c for c in frame["citations"]}
    assert DEMO_PRIMARY_ID in by_id
    assert DEMO_OTHER_ID in by_id  # the counter survives into the frame
    assert by_id[DEMO_PRIMARY_ID]["documentId"] == "deposition-holloway"
    # Counter is a DIFFERENT document (cross-document conflict).
    assert by_id[DEMO_OTHER_ID]["documentId"] != by_id[DEMO_PRIMARY_ID]["documentId"]
    assert by_id[DEMO_PRIMARY_ID]["documentId"] != DEMO_FIELD_NOTES_DOC


@pytest.mark.parametrize("question", ALT_CONTRADICTION_QS)
async def test_alternate_phrasings_anchor_deposition_not_field_notes(
    real_index: MockIndex,
    question: str,
) -> None:
    """Alternate phrasings still anchor on the deposition, never the field-notes.

    None of these echoes "Harbor Street". The selection must be principled (the
    clean presence claim in the under-examination document wins the anchor), so
    the primary is always a deposition citation and NEVER the scanned,
    high-scoring field-notes exhibit. This is the anti-overfit guarantee.
    """
    retriever = MultiHopRetriever(real_index)
    result = await retriever.retrieve(question, top_k=5, per_hop_k=5)
    assert result.contradiction is True
    assert result.cross_document is True
    assert result.primary_id is not None
    by_id = {c.chunk.id: c for c in result.citations}
    primary = by_id[result.primary_id]
    # Anchor lives in the deposition (under examination), not a scanned exhibit.
    assert primary.documentId == "deposition-holloway"
    assert primary.documentId != DEMO_FIELD_NOTES_DOC
    assert primary.scanned is False
    # In fact the anchor is the same clean presence claim as the canonical
    # question, and the cross-doc counter exhibit is present — proving the
    # selection is robust to phrasing, not pinned to a string.
    assert result.primary_id == DEMO_PRIMARY_ID
    assert DEMO_OTHER_ID in by_id


async def test_published_hops_equal_decompose_output(
    real_index: MockIndex,
) -> None:
    """The hops the retriever publishes ARE what decompose() returned.

    Guards against hand-written hop fiction: the sub-query strings on the hop
    trail must equal the decomposer's actual output for the question.
    """
    decomposer = QueryDecomposer()
    expected = decomposer.decompose(DEMO_CONTRADICTION_Q)
    retriever = MultiHopRetriever(real_index, decomposer)
    result = await retriever.retrieve(DEMO_CONTRADICTION_Q, top_k=5, per_hop_k=5)
    assert [h.subQuery for h in result.hops] == expected


def test_decompose_emits_sensible_subqueries_not_conjunction_fragments() -> None:
    """decompose() yields real sub-queries, never a bare conjunction fragment."""
    dec = QueryDecomposer()
    # A conjunction question must NOT split into "did the supplier" + "the buyer".
    subs = dec.decompose(
        "Did the supplier and the buyer agree on the delivery terms?"
    )
    assert all(len(s.split()) >= 2 for s in subs)
    assert not any(s.strip().lower() == "did the supplier" for s in subs)
    # The canonical contradiction question yields the principled (claim,
    # contradicting) pair derived from subject+predicate, not a literal "and"
    # split.
    subs = dec.decompose(DEMO_CONTRADICTION_Q)
    assert len(subs) == 2
    # The core-claim sub-query carries the temporal anchor (the night under
    # examination), with the contradiction framing stripped out.
    assert "14th" in subs[0].lower()
    assert subs[1].lower().startswith("evidence that contradicts")
    # No contradiction-framing word leaks into the located-claim sub-query.
    assert "contradict" not in subs[0].lower()
