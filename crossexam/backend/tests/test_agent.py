"""Tests for :mod:`crossexam_backend.agent`.

These run without ``livekit-agents`` installed, exercising the shim path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crossexam_backend.agent import CrossExamAgent, ShimChatContext
from crossexam_backend.retrieval.mock_index import MockIndex

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sample_chunks.json"


@pytest.fixture()
def agent() -> CrossExamAgent:
    """A CrossExamAgent wired to the mock index."""
    index = MockIndex.from_fixture(FIXTURE)
    return CrossExamAgent(index, top_k=3, alpha=0.8)


class FakeChatMessage:
    """Minimal stand-in for a LiveKit ChatMessage exposing ``text_content``."""

    def __init__(self, text: str) -> None:
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
