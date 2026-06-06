"""The CrossExam voice agent.

The agent runs LiveKit's real-time voice loop (STT -> LLM -> TTS). On every user
turn we override LiveKit Agents' ``on_user_turn_completed()`` hook to query the
retrieval index (Moss in production, the mock otherwise) and inject the top-k
results as a ``role="system"`` message into the turn context, so the LLM always
has grounding context before it speaks ("no dead air").

IMPORTANT: ``on_user_turn_completed`` is a **LiveKit Agents** lifecycle hook, not
a Moss feature. LiveKit invokes it after a user's turn is transcribed and before
the LLM is asked to respond, which is exactly the right place to enrich the
context with retrieved passages.

LiveKit imports are guarded so this module imports cleanly even when
``livekit-agents`` is not installed (e.g. in unit tests). When LiveKit is
absent we fall back to a thin local shim that mirrors the parts of the API we
depend on.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from crossexam_backend.models import Citation, RetrievalResult
from crossexam_backend.retrieval.base import RetrievalIndex

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Guarded LiveKit imports + shims                                             #
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - exercised only when livekit is installed
    from livekit.agents import Agent as _LiveKitAgent
    from livekit.agents.llm import ChatContext as _LiveKitChatContext

    LIVEKIT_AVAILABLE = True
except ImportError:  # pragma: no cover - default path in this environment
    _LiveKitAgent = None  # type: ignore[assignment]
    _LiveKitChatContext = None  # type: ignore[assignment]
    LIVEKIT_AVAILABLE = False


@runtime_checkable
class TurnContext(Protocol):
    """Structural type for LiveKit's turn / chat context.

    We only depend on the ability to append a system message. LiveKit's
    ``ChatContext`` exposes ``add_message(role=..., content=...)``; the shim
    below provides the same surface for tests.
    """

    def add_message(self, *, role: str, content: str) -> Any:
        """Append a message with ``role`` and ``content`` to the context."""
        ...


class ShimChatContext:
    """Minimal stand-in for LiveKit's ``ChatContext`` used when LiveKit is absent.

    Stores appended messages in a plain list so tests can assert on them.
    """

    def __init__(self) -> None:
        """Initialise an empty message buffer."""
        self.messages: list[dict[str, str]] = []

    def add_message(self, *, role: str, content: str) -> dict[str, str]:
        """Append and return a ``{"role", "content"}`` message dict."""
        message = {"role": role, "content": content}
        self.messages.append(message)
        return message


def _agent_base() -> type:
    """Return the LiveKit ``Agent`` base class, or ``object`` as a shim base."""
    if LIVEKIT_AVAILABLE and _LiveKitAgent is not None:
        return _LiveKitAgent
    return object


_DEFAULT_INSTRUCTIONS = (
    "You are CrossExam, a meticulous examiner of documents. You answer questions "
    "strictly from the supplied document passages and always cite the page "
    "number. If passages contradict each other, point out the contradiction "
    "explicitly. If the document does not support an answer, say so."
)


class CrossExamAgent(_agent_base()):  # type: ignore[misc]
    """A LiveKit voice agent that grounds every turn in document retrieval."""

    def __init__(
        self,
        index: RetrievalIndex,
        *,
        top_k: int = 5,
        alpha: float = 0.8,
        instructions: str = _DEFAULT_INSTRUCTIONS,
        **agent_kwargs: Any,
    ) -> None:
        """Create the agent.

        Args:
            index: The retrieval index to query on each turn.
            top_k: Number of citations to retrieve per turn.
            alpha: Hybrid-search weight passed through to the index.
            instructions: System prompt / persona for the LLM.
            **agent_kwargs: Forwarded to LiveKit's ``Agent.__init__`` when
                LiveKit is installed (ignored by the shim base).
        """
        if LIVEKIT_AVAILABLE:
            super().__init__(instructions=instructions, **agent_kwargs)
        else:  # shim base is ``object`` — store what we need ourselves
            super().__init__()
            self._instructions = instructions

        self._index = index
        self._top_k = top_k
        self._alpha = alpha
        self._latest_result: RetrievalResult | None = None

    # -- accessors for the frontend bridge ----------------------------------
    @property
    def latest_result(self) -> RetrievalResult | None:
        """The most recent :class:`RetrievalResult`, or ``None`` before any turn."""
        return self._latest_result

    @property
    def latest_citations(self) -> list[Citation]:
        """Citations from the latest turn (page + bbox + score) for the UI."""
        if self._latest_result is None:
            return []
        return self._latest_result.citations

    # -- core retrieval logic (pure, unit-testable) -------------------------
    async def retrieve(self, text: str) -> RetrievalResult:
        """Query the index for ``text`` and cache the result.

        Args:
            text: The user-turn text.

        Returns:
            The retrieval result (also stored as :attr:`latest_result`).
        """
        result = await self._index.query(text, top_k=self._top_k, alpha=self._alpha)
        self._latest_result = result
        logger.info(
            "agent.retrieve query=%r citations=%d latency_ms=%.3f",
            text,
            len(result.citations),
            result.latency_ms,
        )
        return result

    @staticmethod
    def _message_text(new_message: Any) -> str:
        """Extract plain text from a LiveKit ``ChatMessage`` or a plain string."""
        if isinstance(new_message, str):
            return new_message
        # LiveKit ChatMessage exposes either `.text_content` or `.content`.
        text_content = getattr(new_message, "text_content", None)
        if isinstance(text_content, str) and text_content:
            return text_content
        content = getattr(new_message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, (list, tuple)):
            return " ".join(str(part) for part in content)
        return str(new_message) if new_message is not None else ""

    # -- LiveKit Agents lifecycle hook --------------------------------------
    async def on_user_turn_completed(  # noqa: D401
        self,
        turn_ctx: TurnContext,
        new_message: Any,
    ) -> None:
        """LiveKit hook: enrich the turn with retrieved document passages.

        This is invoked by the **LiveKit Agents** runtime after the user's
        speech has been transcribed and before the LLM generates a reply. We
        retrieve the most relevant passages from the index and inject them as a
        ``role="system"`` message into ``turn_ctx`` so the model can ground and
        cite its answer with zero added latency for the user.

        Args:
            turn_ctx: The current turn / chat context to enrich.
            new_message: The user's just-completed message (string or LiveKit
                ``ChatMessage``).
        """
        query_text = self._message_text(new_message).strip()
        if not query_text:
            logger.debug("agent.on_user_turn_completed empty message; skipping")
            return

        result = await self.retrieve(query_text)
        system_content = result.to_system_prompt()

        try:
            turn_ctx.add_message(role="system", content=system_content)
        except Exception:  # noqa: BLE001 - never break the turn on injection
            logger.exception("agent.inject_context failed; turn continues ungrounded")
            return

        logger.debug(
            "agent.on_user_turn_completed injected system message chars=%d",
            len(system_content),
        )
