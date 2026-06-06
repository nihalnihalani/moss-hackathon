"""Tests for :mod:`crossexam_backend.agent`.

These run without ``livekit-agents`` installed, exercising the shim path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crossexam_backend.agent import REASON_NOT_FOUND, CrossExamAgent, ShimChatContext
from crossexam_backend.models import BBox, Chunk
from crossexam_backend.models import Speaker as SpeakerModel
from crossexam_backend.retrieval.mock_index import MockIndex

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chunks.json"


@pytest.fixture()
def agent() -> CrossExamAgent:
    """A CrossExamAgent wired to the mock index."""
    index = MockIndex.from_fixture(FIXTURE)
    return CrossExamAgent(index, top_k=3, alpha=0.8)


def _two_doc_index() -> MockIndex:
    """A small two-document corpus with a built-in cross-page contradiction."""

    def chunk(cid: str, text: str, page: int, doc: str) -> Chunk:
        bbox = BBox(page=page, x0=72.0, y0=100.0, x1=540.0, y1=136.0)
        return Chunk(id=cid, text=text, page=page, bbox=bbox, documentId=doc)

    return MockIndex(
        [
            chunk(
                "depo-p12",
                "Q. Where were you on the night of the 14th? A. I remained at the "
                "Harbor Street warehouse from 9 p.m. well past midnight conducting "
                "the inventory count.",
                12,
                "deposition",
            ),
            chunk(
                "depo-p41",
                "Contrary to his earlier testimony, on the night of the 14th the "
                "witness had actually left the Harbor Street warehouse before 8 "
                "p.m. and departed for home.",
                41,
                "deposition",
            ),
            chunk(
                "exhibit-p1",
                "Exhibit 7, the keycard access log, records entries to the Harbor "
                "Street warehouse on the night of the 14th.",
                1,
                "exhibit",
            ),
        ]
    )


class FakeChatMessage:
    """Minimal stand-in for a LiveKit ChatMessage exposing ``text_content``."""

    def __init__(self, text: str) -> None:
        """Store ``text`` as ``text_content``."""
        self.text_content = text


async def test_on_user_turn_injects_system_message(agent: CrossExamAgent) -> None:
    """The hook adds exactly one role=system message grounded in citations."""
    turn_ctx = ShimChatContext()
    await agent.on_user_turn_completed(
        turn_ctx, FakeChatMessage("where were you on the night of the 14th?")
    )
    system_msgs = [m for m in turn_ctx.messages if m["role"] == "system"]
    assert len(system_msgs) == 1
    content = system_msgs[0]["content"]
    assert "page" in content.lower()
    assert "warehouse" in content.lower()


async def test_latest_citations_exposed_for_frontend(agent: CrossExamAgent) -> None:
    """After a turn the agent exposes citations with page + bbox for the UI."""
    turn_ctx = ShimChatContext()
    await agent.on_user_turn_completed(
        turn_ctx, "warehouse keys access on the night of the 14th"
    )
    cits = agent.latest_citations
    assert cits
    assert agent.latest_result is not None
    assert agent.latest_result.query
    first = cits[0]
    assert first.chunk.bbox.page == first.chunk.page
    assert 0.0 <= first.score <= 1.0


async def test_plain_string_message_supported(agent: CrossExamAgent) -> None:
    """The hook handles a plain string ``new_message``."""
    turn_ctx = ShimChatContext()
    await agent.on_user_turn_completed(turn_ctx, "indemnification clause")
    assert any(m["role"] == "system" for m in turn_ctx.messages)


async def test_empty_message_skips_injection(agent: CrossExamAgent) -> None:
    """An empty/whitespace turn injects nothing and does not crash."""
    turn_ctx = ShimChatContext()
    await agent.on_user_turn_completed(turn_ctx, "   ")
    assert turn_ctx.messages == []
    assert agent.latest_citations == []


async def test_retrieve_caches_result(agent: CrossExamAgent) -> None:
    """retrieve() returns and caches the latest result."""
    result = await agent.retrieve("termination notice thirty days")
    assert result is agent.latest_result
    assert result.query == "termination notice thirty days"


class _RecordingRoom:
    """Captures frames published to the data channel for assertions."""

    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []
        self.local_participant = self

    async def publish_data(self, data: bytes) -> None:
        import json

        self.frames.append(json.loads(data.decode("utf-8")))


async def test_speculative_prefetch_then_retrieve_hits_cache(
    agent: CrossExamAgent,
) -> None:
    """A prefetched partial is consumed by retrieve() for the final transcript."""
    await agent.prefetch_partial("where were you on the night of the")
    result = await agent.retrieve("where were you on the night of the 14th?")
    assert result is agent.latest_result
    assert result.citations  # the cached speculative result carried citations


async def test_speculative_disabled_via_flag() -> None:
    """With speculative disabled, prefetch is a no-op and retrieve still works."""
    index = MockIndex.from_fixture(FIXTURE)
    a = CrossExamAgent(index, top_k=3, speculative_enabled=False)
    await a.prefetch_partial("where were you")  # no-op, no crash
    result = await a.retrieve("where were you on the night of the 14th warehouse")
    assert result.citations


async def test_proactive_publishes_unprompted_on_claim(
    agent: CrossExamAgent,
) -> None:
    """A spoken claim the document supports is published with proactive=true."""
    room = _RecordingRoom()
    agent.room = room
    # A declarative assertion (claim) that echoes the warehouse passage.
    await agent.on_user_turn_completed(
        ShimChatContext(),
        "The warehouse keys were accessed on the night of the 14th.",
    )
    assert room.frames, "expected a proactive citation frame to be published"
    frame = room.frames[-1]
    assert frame.get("proactive") is True
    assert frame["citations"]
    assert frame.get("primaryId") == frame["citations"][0]["id"]
    assert "latencyMs" in frame


async def test_question_does_not_trigger_proactive(agent: CrossExamAgent) -> None:
    """A question publishes a normal (non-proactive) citation frame."""
    room = _RecordingRoom()
    agent.room = room
    await agent.on_user_turn_completed(
        ShimChatContext(), "where were you on the night of the 14th?"
    )
    assert room.frames
    frame = room.frames[-1]
    assert frame.get("proactive") is None
    assert isinstance(frame["citations"], list)


async def test_contradiction_question_publishes_multi_citation_frame() -> None:
    """A contradiction question publishes >1 citation + contradiction + hops."""
    agent = CrossExamAgent(_two_doc_index(), top_k=6)
    room = _RecordingRoom()
    agent.room = room
    await agent.on_user_turn_completed(
        ShimChatContext(),
        "did the witness contradict himself about the warehouse on the night of "
        "the 14th?",
    )
    assert room.frames
    frame = room.frames[-1]
    assert len(frame["citations"]) > 1
    pages = {c["bbox"]["page"] for c in frame["citations"]}
    assert 12 in pages and 41 in pages
    assert frame.get("contradiction") is True
    assert frame.get("hops")
    assert len(frame["hops"]) > 1
    assert frame.get("primaryId") is not None


async def test_repeat_citation_recalled_as_memory_not_resnapped() -> None:
    """A citation seen twice in a session publishes as memory[] recall, not a box."""
    agent = CrossExamAgent(_two_doc_index(), top_k=3, proactive_enabled=False)
    room = _RecordingRoom()
    agent.room = room
    q = "where were you on the night of the 14th warehouse inventory count"

    await agent.on_user_turn_completed(ShimChatContext(), q)
    first = room.frames[-1]
    assert first["citations"], "first turn surfaces a fresh citation"
    first_id = first["citations"][0]["id"]
    assert "memory" not in first

    # Same question again -> the same citation is now a recall, not re-snapped.
    await agent.on_user_turn_completed(ShimChatContext(), q)
    second = room.frames[-1]
    surfaced_ids = {c["id"] for c in second["citations"]}
    assert first_id not in surfaced_ids
    recalled_ids = {r["citationId"] for r in second.get("memory", [])}
    assert first_id in recalled_ids


async def test_reasked_contradiction_keeps_primary_anchor_box() -> None:
    """A re-asked contradiction never loses its PRIMARY anchor box to dedupe."""
    agent = CrossExamAgent(_two_doc_index(), top_k=6, proactive_enabled=False)
    room = _RecordingRoom()
    agent.room = room
    q = (
        "did the witness contradict himself about the warehouse on the night of "
        "the 14th?"
    )

    await agent.on_user_turn_completed(ShimChatContext(), q)
    first = room.frames[-1]
    assert first.get("contradiction") is True
    primary = first["primaryId"]

    # Re-ask: the primary contradiction anchor must STILL be a fresh box (not
    # collapsed into a memory[] recall), so the conflict keeps its highlight.
    await agent.on_user_turn_completed(ShimChatContext(), q)
    second = room.frames[-1]
    surfaced_ids = {c["id"] for c in second["citations"]}
    assert primary in surfaced_ids
    recalled_ids = {r["citationId"] for r in second.get("memory", [])}
    assert primary not in recalled_ids


async def test_speaker_passes_through_on_turn() -> None:
    """A speaker passed to the hook is threaded onto the published frame."""
    agent = CrossExamAgent(_two_doc_index(), top_k=3, proactive_enabled=False)
    room = _RecordingRoom()
    agent.room = room
    await agent.on_user_turn_completed(
        ShimChatContext(),
        "where were you on the night of the 14th warehouse",
        speaker=SpeakerModel(id="spk_2", label="Witness"),
    )
    assert room.frames
    assert room.frames[-1]["speaker"] == {"id": "spk_2", "label": "Witness"}


async def test_not_found_publishes_empty_citations_with_reason(
    agent: CrossExamAgent,
) -> None:
    """An answer unsupported by the chunk publishes citations:[] + reason."""
    room = _RecordingRoom()
    agent.room = room
    # publish_frame directly with an answer that shares no content with any chunk.
    result = await agent.retrieve("warehouse keys access on the night of the 14th")
    published = await agent.publish_frame(
        result,
        answer_text="Quarterly revenue forecasts exceeded analyst expectations.",
    )
    assert published is True
    frame = room.frames[-1]
    assert frame["citations"] == []
    assert frame["reason"] == REASON_NOT_FOUND
