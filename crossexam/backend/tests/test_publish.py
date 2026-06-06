"""Tests for the frontend citation bridge (``build_citation_payload``).

These assert that the published payload matches the EXACT shape that
``frontend/src/hooks/useCrossExam.ts`` (``isCitation``) parses off the LiveKit
data channel. They must run WITHOUT ``livekit-agents`` installed, so they only
touch the pure helper and the mock index.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crossexam_backend.agent import build_citation_payload
from crossexam_backend.models import (
    BBox,
    Chunk,
    Citation,
    RetrievalResult,
)
from crossexam_backend.retrieval.mock_index import MockIndex

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chunks.json"


@pytest.fixture()
def index() -> MockIndex:
    """A MockIndex loaded from the shipped fixture."""
    return MockIndex.from_fixture(FIXTURE)


async def test_payload_shape_matches_frontend_contract(index: MockIndex) -> None:
    """The payload exactly matches what ``useCrossExam.ts`` ``isCitation`` parses."""
    result = await index.query(
        "where were you the night of the 14th warehouse", top_k=5
    )
    payload = build_citation_payload(result)
    assert payload is not None

    # Top level: a single "citation" object (the frame may also carry
    # agentState/caption, but the citation key is what matters here).
    assert set(payload.keys()) == {"citation"}
    citation = payload["citation"]

    # Frontend isCitation: id (str), text (str), confidence (number), bbox.
    assert isinstance(citation["id"], str)
    assert isinstance(citation["text"], str)
    assert isinstance(citation["confidence"], float)
    # Task contract additionally requires a relevance "score".
    assert isinstance(citation["score"], float)

    bbox = citation["bbox"]
    # bbox keys incl. page_width/page_height per the wire shape (types.ts BBox).
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


async def test_payload_values_come_from_top_citation(index: MockIndex) -> None:
    """The payload is built from the top (best) citation's chunk and score."""
    result = await index.query(
        "where were you the night of the 14th warehouse", top_k=5
    )
    payload = build_citation_payload(result)
    assert payload is not None

    top = result.citations[0]
    citation = payload["citation"]
    assert citation["id"] == top.chunk.id
    assert citation["text"] == top.chunk.text
    assert citation["confidence"] == top.chunk.confidence
    assert citation["score"] == top.score
    assert citation["bbox"]["page"] == top.chunk.bbox.page
    assert citation["bbox"]["x0"] == top.chunk.bbox.x0
    assert citation["bbox"]["y1"] == top.chunk.bbox.y1
    assert citation["bbox"]["page_width"] == top.chunk.bbox.page_width
    assert citation["bbox"]["page_height"] == top.chunk.bbox.page_height


def test_payload_is_json_serializable() -> None:
    """The payload round-trips through JSON unchanged (it is the wire frame)."""
    result = RetrievalResult(
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
                ),
                score=0.91,
            )
        ],
        latency_ms=7.0,
    )
    payload = build_citation_payload(result)
    assert payload is not None
    decoded = json.loads(json.dumps(payload))
    assert decoded == payload
    assert decoded["citation"]["bbox"]["page"] == 12
    assert decoded["citation"]["score"] == pytest.approx(0.91)


def test_payload_none_when_no_citations() -> None:
    """An empty result yields no payload (nothing to highlight)."""
    result = RetrievalResult(query="q", citations=[], latency_ms=1.0)
    assert build_citation_payload(result) is None
