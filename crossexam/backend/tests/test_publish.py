"""Tests for the frontend citation bridge (``build_frame``).

These assert that the published payload matches the EXACT depth-v2 wire frame the
frontend parses off the LiveKit data channel::

    { citations: Citation[], primaryId?, contradiction?, hops?, memory?,
      speaker?, proactive?, latencyMs?, reason?, ... }

where each Citation carries ``quads``/``documentId``/``scanned``/``faithfulness``.
A single-answer turn is ``citations:[c]`` with ``primaryId=c.id``; not-found is
``citations:[]`` + ``reason``. They run WITHOUT ``livekit-agents`` installed,
touching only the pure helper and the mock index.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crossexam_backend.agent import REASON_NOT_FOUND, build_frame
from crossexam_backend.models import (
    BBox,
    Chunk,
    Citation,
    HopTrace,
    MemoryRef,
    MultiHopResult,
    RetrievalResult,
    Speaker,
)
from crossexam_backend.retrieval.mock_index import MockIndex

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chunks.json"


def _supported_result() -> RetrievalResult:
    """A result whose answer is trivially supported by the chunk (same text)."""
    return RetrievalResult(
        query="q",
        citations=[
            Citation(
                chunk=Chunk(
                    id="dep-0001",
                    text="At the Harbor Street warehouse past midnight.",
                    page=12,
                    bbox=BBox(
                        page=12,
                        x0=72.0,
                        y0=120.0,
                        x1=540.0,
                        y1=168.0,
                        page_width=612.0,
                        page_height=792.0,
                    ),
                    confidence=0.98,
                    documentId="deposition-holloway",
                ),
                score=0.91,
                documentId="deposition-holloway",
            )
        ],
        latency_ms=7.0,
    )


@pytest.fixture()
def index() -> MockIndex:
    """A MockIndex loaded from the shipped fixture."""
    return MockIndex.from_fixture(FIXTURE)


async def test_frame_shape_matches_frontend_contract(index: MockIndex) -> None:
    """The supported frame matches the wire contract (citations[] + faithfulness)."""
    result = await index.query(
        "where were you the night of the 14th warehouse", top_k=5
    )
    # answer_text=None -> each chunk's own text is used (vacuously supported),
    # so at least one citation is always produced for a non-empty result here.
    frame = build_frame(result)

    # Top level: citations[] + primaryId + latencyMs (no reason when supported).
    assert isinstance(frame["citations"], list)
    assert frame["citations"]
    assert isinstance(frame["latencyMs"], float)
    assert "reason" not in frame
    assert frame["primaryId"] == frame["citations"][0]["id"]

    citation = frame["citations"][0]
    # Frontend isCitation: id (str), text (str), confidence (number), bbox.
    assert isinstance(citation["id"], str)
    assert isinstance(citation["text"], str)
    assert isinstance(citation["confidence"], float)
    assert isinstance(citation["score"], float)
    # depth-v2: documentId always present on a citation.
    assert isinstance(citation["documentId"], str)

    # faithfulness {supported, score, method}.
    fth = citation["faithfulness"]
    assert isinstance(fth["supported"], bool)
    assert isinstance(fth["score"], float)
    assert isinstance(fth["method"], str)

    bbox = citation["bbox"]
    assert set(bbox.keys()) == {
        "page",
        "x0",
        "y0",
        "x1",
        "y1",
        "page_width",
        "page_height",
    }
    assert isinstance(bbox["page"], int)
    for key in ("x0", "y0", "x1", "y1", "page_width", "page_height"):
        assert isinstance(bbox[key], float)


async def test_frame_values_come_from_top_citation(index: MockIndex) -> None:
    """The primary citation is built from the top (best) citation's chunk/score."""
    result = await index.query(
        "where were you the night of the 14th warehouse", top_k=5
    )
    frame = build_frame(result)

    top = result.citations[0]
    citation = frame["citations"][0]
    assert citation["id"] == top.chunk.id
    assert citation["text"] == top.chunk.text
    assert citation["confidence"] == top.chunk.confidence
    assert citation["score"] == top.score
    assert citation["documentId"] == top.documentId
    assert citation["bbox"]["page"] == top.chunk.bbox.page
    assert citation["bbox"]["x0"] == top.chunk.bbox.x0
    assert citation["bbox"]["y1"] == top.chunk.bbox.y1
    assert citation["bbox"]["page_width"] == top.chunk.bbox.page_width
    assert citation["bbox"]["page_height"] == top.chunk.bbox.page_height


def test_frame_is_json_serializable() -> None:
    """The frame round-trips through JSON unchanged (it is the wire frame)."""
    frame = build_frame(_supported_result())
    decoded = json.loads(json.dumps(frame))
    assert decoded == frame
    assert decoded["citations"][0]["bbox"]["page"] == 12
    assert decoded["citations"][0]["score"] == pytest.approx(0.91)
    assert decoded["primaryId"] == "dep-0001"
    assert decoded["latencyMs"] == pytest.approx(7.0)


def test_single_answer_is_citations_list_with_primary_id() -> None:
    """A single-answer turn is citations:[c] with primaryId=c.id (back-compat)."""
    frame = build_frame(_supported_result())
    assert len(frame["citations"]) == 1
    assert frame["primaryId"] == frame["citations"][0]["id"]


def test_frame_empty_when_no_citations() -> None:
    """An empty result yields citations:[] + reason (nothing to consider)."""
    result = RetrievalResult(query="q", citations=[], latency_ms=1.0)
    frame = build_frame(result)
    assert frame["citations"] == []
    assert frame["reason"] == REASON_NOT_FOUND
    assert frame["latencyMs"] == pytest.approx(1.0)
    assert "primaryId" not in frame


def test_supported_frame_emits_caption_and_speaking_state() -> None:
    """A supported frame carries a one-line caption + agentState='speaking'."""
    frame = build_frame(_supported_result())
    assert frame["agentState"] == "speaking"
    # Caption mentions the source doc and page of the top citation.
    assert frame["caption"] == "Found in deposition-holloway p.12"
    # caption/agentState are JSON-safe wire hints.
    json.loads(json.dumps(frame))


def test_empty_frame_caption_and_idle_state() -> None:
    """Honest silence emits a 'not found' caption + agentState='idle'."""
    result = RetrievalResult(query="q", citations=[], latency_ms=1.0)
    frame = build_frame(result)
    assert frame["agentState"] == "idle"
    assert frame["caption"] == "Not found in the document"


def test_supported_answer_attaches_faithfulness() -> None:
    """A supported answer yields a citation carrying a supported faithfulness."""
    frame = build_frame(
        _supported_result(),
        answer_text="The warehouse on Harbor Street, past midnight.",
    )
    assert frame["citations"]
    fth = frame["citations"][0]["faithfulness"]
    assert fth["supported"] is True
    assert fth["score"] >= 0.5
    assert "reason" not in frame


def test_unsupported_answer_returns_not_found() -> None:
    """An unsupported answer publishes citations:[] + reason, not a wrong box."""
    frame = build_frame(
        _supported_result(),
        answer_text="The quarterly revenue forecast exceeded analyst expectations.",
    )
    assert frame["citations"] == []
    assert frame["reason"] == REASON_NOT_FOUND
    assert frame["latencyMs"] == pytest.approx(7.0)
    assert "primaryId" not in frame


def test_proactive_flag_set_when_requested() -> None:
    """A proactive supported frame carries ``proactive: true``."""
    frame = build_frame(
        _supported_result(),
        answer_text="At the Harbor Street warehouse past midnight.",
        proactive=True,
    )
    assert frame["citations"]
    assert frame["proactive"] is True


def test_speaker_passes_through() -> None:
    """A speaker is threaded onto the frame (meeting mode, feat 4)."""
    frame = build_frame(
        _supported_result(), speaker=Speaker(id="spk_1", label="Counsel")
    )
    assert frame["speaker"] == {"id": "spk_1", "label": "Counsel"}


def test_memory_refs_emitted_as_memory_recalls() -> None:
    """Recalls are emitted as memory[] (feat 5)."""
    ref = MemoryRef(
        citationId="dep-0001",
        documentId="deposition-holloway",
        page=12,
        note="as we saw on page 12",
    )
    frame = build_frame(_supported_result(), memory_refs=[ref])
    assert frame["memory"] == [
        {
            "kind": "recall",
            "citationId": "dep-0001",
            "documentId": "deposition-holloway",
            "page": 12,
            "note": "as we saw on page 12",
        }
    ]


def _two_doc_contradiction_result() -> MultiHopResult:
    """A multi-hop result with two opposing citations across docs/pages."""
    stayed = Citation(
        chunk=Chunk(
            id="depo-p12",
            text="I remained at the Harbor Street warehouse past midnight.",
            page=12,
            bbox=BBox(page=12, x0=72.0, y0=100.0, x1=540.0, y1=136.0),
            documentId="deposition",
        ),
        score=1.0,
        documentId="deposition",
    )
    left = Citation(
        chunk=Chunk(
            id="depo-p41",
            text="I had left the Harbor Street warehouse before 8 p.m.",
            page=41,
            bbox=BBox(page=41, x0=72.0, y0=100.0, x1=540.0, y1=136.0),
            documentId="deposition",
        ),
        score=0.9,
        documentId="deposition",
    )
    return MultiHopResult(
        query="did the witness contradict himself about the warehouse?",
        citations=[stayed, left],
        hops=[
            HopTrace(subQuery="where warehouse 14th", citationIds=["depo-p12"]),
            HopTrace(
                subQuery="conflicting statement warehouse", citationIds=["depo-p41"]
            ),
        ],
        contradiction=True,
        primary_id="depo-p12",
        latency_ms=5.0,
    )


def test_contradiction_frame_carries_hops_and_flag() -> None:
    """A contradiction multi-hop result publishes >1 citation + hops + flag."""
    frame = build_frame(_two_doc_contradiction_result())
    assert len(frame["citations"]) > 1
    assert frame["contradiction"] is True
    assert frame["primaryId"] == "depo-p12"
    assert len(frame["hops"]) == 2
    assert all(h["subQuery"] for h in frame["hops"])
    # hops carry the citation ids surfaced per sub-query.
    assert frame["hops"][0]["citationIds"] == ["depo-p12"]


def test_contradiction_frame_caption_mentions_conflict() -> None:
    """A contradiction frame's caption summarizes the cross-source conflict."""
    frame = build_frame(_two_doc_contradiction_result())
    assert frame["agentState"] == "speaking"
    assert "Conflict found across" in frame["caption"]


def test_contradiction_pair_survives_a_real_answer_text() -> None:
    """A real answer_text must NOT drop the contradiction pair (feat 3 fix).

    Contradiction citations are evidence of CONFLICT, not support for one
    answer; the answer-faithfulness gate must not silently drop the side the
    answer doesn't echo. An answer that agrees only with the alibi (page 12)
    must still leave BOTH conflicting citations and the contradiction flag on
    the frame.
    """
    result = _two_doc_contradiction_result()
    # The drafted answer agrees with the page-12 alibi only (it says nothing
    # about leaving before 8 p.m.); the page-41 side shares little content.
    frame = build_frame(
        result,
        answer_text=(
            "The witness testified he remained at the Harbor Street warehouse "
            "past midnight."
        ),
    )
    surfaced = {c["id"] for c in frame["citations"]}
    assert "depo-p12" in surfaced
    assert "depo-p41" in surfaced  # NOT dropped by the answer gate
    assert frame["contradiction"] is True
    assert frame["primaryId"] == "depo-p12"


def test_cross_document_contradiction_marks_cross_document_flag() -> None:
    """A cross-document contradiction frame exposes crossDocument:true."""
    result = _two_doc_contradiction_result().model_copy(
        update={"cross_document": True}
    )
    frame = build_frame(result, answer_text="warehouse past midnight")
    assert frame["contradiction"] is True
    assert frame["crossDocument"] is True


def _contract_email_contradiction_result() -> MultiHopResult:
    """A §4.2 contract clause vs the email Acme admission (cross-document)."""
    clause = Citation(
        chunk=Chunk(
            id="contract-msa-p7-l1",
            text=(
                "Section 4.2 Subcontracting. Contractor shall not delegate, "
                "assign, or subcontract any of the Services, in whole or in "
                "part, to any third party without the prior written consent of "
                "Client."
            ),
            page=7,
            bbox=BBox(page=7, x0=72.0, y0=100.0, x1=540.0, y1=136.0),
            documentId="contract-msa",
        ),
        score=0.95,
        documentId="contract-msa",
    )
    email = Citation(
        chunk=Chunk(
            id="email-thread-p1-l2",
            text=(
                "From: Dana Vance Subject: Re: Section 4.2 / Acme work order -- "
                "we already handed the integration work off to Acme Labs last "
                "week, and never got the formal sign-off from your side."
            ),
            page=1,
            bbox=BBox(page=1, x0=72.0, y0=100.0, x1=540.0, y1=136.0),
            documentId="email-thread",
        ),
        score=0.92,
        documentId="email-thread",
    )
    return MultiHopResult(
        query="Does the email breach §4.2 by subcontracting to Acme?",
        citations=[clause, email],
        hops=[],
        contradiction=True,
        cross_document=True,
        primary_id="contract-msa-p7-l1",
        latency_ms=5.0,
    )


def test_contradiction_frame_emits_clause_anchor() -> None:
    """A §4.2 contradiction frame emits anchor='§4.2 Subcontracting' (FIX 1).

    The frontend "CONFLICT — Anchor: §4.2 Subcontracting" banner reads
    ``frame.anchor``; it must now be emitted in LIVE mode (was mock-only), paired
    from the clause number and its heading label.
    """
    frame = build_frame(
        _contract_email_contradiction_result(),
        answer_text="The contract bars subcontracting without prior written consent.",
    )
    assert frame["contradiction"] is True
    assert frame["crossDocument"] is True
    assert frame["anchor"] == "§4.2 Subcontracting"
    # The anchor is JSON-serialisable on the wire frame.
    assert json.loads(json.dumps(frame))["anchor"] == "§4.2 Subcontracting"


def test_non_contradiction_frame_omits_anchor() -> None:
    """A plain (non-contradiction) result carries no ``anchor`` field."""
    frame = build_frame(_supported_result())
    assert "anchor" not in frame
    # A multi-hop result without the contradiction flag also omits it.
    no_conflict = _two_doc_contradiction_result().model_copy(
        update={"contradiction": False}
    )
    assert "anchor" not in build_frame(no_conflict)
