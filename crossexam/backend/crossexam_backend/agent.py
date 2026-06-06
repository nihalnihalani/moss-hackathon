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

import json
import logging
from typing import Any, Protocol, runtime_checkable

from crossexam_backend.models import Citation, RetrievalResult
from crossexam_backend.proactive import (
    ClaimDetector,
    SurfaceThresholds,
    should_surface,
)
from crossexam_backend.retrieval.base import RetrievalIndex
from crossexam_backend.speculative import SpeculativeRetriever
from crossexam_backend.tracing import NoOpTracer, OTelTracer
from crossexam_backend.verify import verify_faithfulness

logger = logging.getLogger(__name__)

# Reason code published (with ``citation: null``) when the cited chunk does not
# support the agent's answer — the faithfulness gate refuses to highlight.
REASON_NOT_FOUND = "not_found_in_document"


# --------------------------------------------------------------------------- #
# Frontend bridge: structured citation payload                                #
# --------------------------------------------------------------------------- #
def build_citation_payload(
    result: RetrievalResult,
    answer_text: str | None = None,
    *,
    proactive: bool = False,
    faithfulness_threshold: float = 0.5,
) -> dict[str, Any] | None:
    """Build the JSON citation frame the frontend consumes, with a faithful gate.

    The returned dict matches the EXACT shape ``frontend/src/hooks/useCrossExam.ts``
    (``isCitation``) parses off the LiveKit data channel: a top-level ``citation``
    object whose ``bbox`` carries ``page, x0, y0, x1, y1`` plus
    ``page_width``/``page_height`` (snake_case, matching ``BBox`` in
    ``frontend/src/types.ts``), and whose top level carries ``id``, ``text``,
    ``confidence`` and ``score`` — now plus a ``faithfulness`` object
    ``{supported, score, method}``.

    The FAITHFULNESS GATE: before highlighting, the answer (``answer_text`` or,
    as a fallback, the top chunk's own text) is verified against the cited chunk
    via :func:`verify_faithfulness`. If the chunk does NOT support the answer the
    function returns ``{"citation": null, "reason": "not_found_in_document"}``
    instead of pointing the user at a wrong box. The top-level ``latencyMs`` is
    always carried (from the retrieval result).

    This is a pure function (no LiveKit dependency) so it can be unit-tested
    without ``livekit-agents`` installed.

    Args:
        result: The retrieval result for the just-completed user turn.
        answer_text: The agent's drafted answer to verify against the chunk; when
            ``None`` the top chunk's own text is used (vacuously supported).
        proactive: When ``True`` mark the frame as an unprompted surfacing.
        faithfulness_threshold: Support threshold for the gate.

    Returns:
        A wire-frame dict ready to ``json.dumps`` and publish, or ``None`` when
        the result has no citations at all (nothing to consider).
    """
    if not result.citations:
        return None

    top = result.citations[0]
    chunk = top.chunk
    bbox = chunk.bbox

    verdict = verify_faithfulness(
        answer_text if answer_text is not None else chunk.text,
        chunk.text,
        threshold=faithfulness_threshold,
    )

    if not verdict.supported:
        # Refuse to highlight a box that does not back up what was said.
        return {
            "citation": None,
            "reason": REASON_NOT_FOUND,
            "latencyMs": result.latency_ms,
        }

    frame: dict[str, Any] = {
        "citation": {
            "id": chunk.id,
            "text": chunk.text,
            "bbox": {
                "page": bbox.page,
                "x0": bbox.x0,
                "y0": bbox.y0,
                "x1": bbox.x1,
                "y1": bbox.y1,
                "page_width": bbox.page_width,
                "page_height": bbox.page_height,
            },
            "confidence": chunk.confidence,
            "score": top.score,
            "faithfulness": {
                "supported": verdict.supported,
                "score": verdict.score,
                "method": verdict.method,
            },
        },
        "latencyMs": result.latency_ms,
    }
    if proactive:
        frame["proactive"] = True
    return frame


# --------------------------------------------------------------------------- #
# Guarded LiveKit imports + shims                                             #
# --------------------------------------------------------------------------- #
# The optional LiveKit ``Agent`` base, held as ``type | None`` so the guarded
# import needs no per-line type-ignore in either environment (with or without
# ``livekit-agents`` installed). See _agent_base() below.
_LiveKitAgent: type | None
try:  # pragma: no cover - exercised only when livekit is installed
    from livekit.agents import Agent as _ImportedLiveKitAgent

    _LiveKitAgent = _ImportedLiveKitAgent
    LIVEKIT_AVAILABLE = True
except ImportError:  # pragma: no cover - default path in this environment
    _LiveKitAgent = None
    LIVEKIT_AVAILABLE = False


@runtime_checkable
class TurnContext(Protocol):
    """Structural type for LiveKit's turn / chat context.

    We only depend on the ability to append a system message. LiveKit's
    ``ChatContext`` exposes ``add_message(role=..., content=...)``; the shim
    below provides the same surface for tests.
    """

    # ANN401: return is Any because LiveKit's ChatContext.add_message return
    # type is unknown without the optional ``livekit-agents`` dep (shim pattern).
    def add_message(self, *, role: str, content: str) -> Any:  # noqa: ANN401
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
        speculative_enabled: bool = True,
        proactive_enabled: bool = True,
        faithfulness_threshold: float = 0.5,
        surface_thresholds: SurfaceThresholds | None = None,
        tracer: NoOpTracer | OTelTracer | None = None,
        # ANN401: forwarded verbatim to LiveKit's Agent.__init__, whose kwargs
        # are untyped here without the optional ``livekit-agents`` dep.
        **agent_kwargs: Any,  # noqa: ANN401
    ) -> None:
        """Create the agent.

        Args:
            index: The retrieval index to query on each turn.
            top_k: Number of citations to retrieve per turn.
            alpha: Hybrid-search weight passed through to the index.
            instructions: System prompt / persona for the LLM.
            speculative_enabled: Enable prefetch-on-ASR-partials caching.
            proactive_enabled: Enable unprompted citation surfacing on claims.
            faithfulness_threshold: Support threshold for the highlight gate.
            surface_thresholds: Confidence gates for proactive surfacing.
            tracer: Optional observability tracer; defaults to a no-op tracer
                so retrieve/publish spans cost nothing unless obs is configured.
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
        self._tracer: NoOpTracer | OTelTracer = tracer or NoOpTracer()
        self._faithfulness_threshold = faithfulness_threshold
        self._proactive_enabled = proactive_enabled
        self._latest_result: RetrievalResult | None = None
        # Optional LiveKit room handle, set by the session entrypoint, used to
        # publish structured citations to the frontend over the data channel.
        # Typed ``Any`` so this module imports without ``livekit`` installed.
        self._room: Any | None = None

        # Speculative retrieval: prefetch on interim transcripts so the citation
        # is ready before the turn ends. The query closure threads top_k/alpha.
        self._claim_detector = ClaimDetector()
        self._surface_thresholds = surface_thresholds or SurfaceThresholds()
        self._speculative: SpeculativeRetriever | None = (
            SpeculativeRetriever(self._speculative_query)
            if speculative_enabled
            else None
        )

    async def _speculative_query(self, text: str) -> RetrievalResult:
        """Query closure used by the speculative retriever (threads top_k/alpha)."""
        return await self._index.query(text, top_k=self._top_k, alpha=self._alpha)

    # -- LiveKit room wiring (set by the session entrypoint) ----------------
    # ANN401 on the room handle below is intrinsic to the optional-import shim:
    # the LiveKit ``Room`` type is unavailable without the ``livekit-agents``
    # dep, so the handle is typed ``Any`` to keep this module importable.
    @property
    def room(self) -> Any | None:  # noqa: ANN401 - LiveKit Room type optional
        """The LiveKit room handle used for publishing citations, if wired."""
        return self._room

    @room.setter
    def room(self, room: Any | None) -> None:  # noqa: ANN401 - LiveKit Room optional
        self._room = room

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

    # -- speculative retrieval on ASR partials ------------------------------
    async def prefetch_partial(self, partial_text: str) -> None:
        """Speculatively retrieve for an interim (partial) ASR transcript.

        Wired to LiveKit's interim-transcript stream so that, by the time the
        user's turn ends, the citation for the final transcript is frequently
        already cached. A guarded no-op when speculative retrieval is disabled.
        """
        if self._speculative is None:
            return
        await self._speculative.prefetch(partial_text)

    # -- core retrieval logic (pure, unit-testable) -------------------------
    async def retrieve(self, text: str) -> RetrievalResult:
        """Return citations for ``text``, using the speculative cache if warm.

        If an interim partial already prefetched this turn (a cached prefix
        confirmed by the final ``text``), the cached result is returned with no
        new query — the citation is ready before the turn ends. Otherwise the
        index is queried normally. Either way the result is cached as
        :attr:`latest_result`.

        Args:
            text: The user-turn text.

        Returns:
            The retrieval result (also stored as :attr:`latest_result`).
        """
        with self._tracer.span(
            "retrieve", query=text, top_k=self._top_k
        ) as span:
            result: RetrievalResult | None = None
            speculative_hit = False
            if self._speculative is not None:
                result = self._speculative.take(text)
                speculative_hit = result is not None
            if result is None:
                result = await self._index.query(
                    text, top_k=self._top_k, alpha=self._alpha
                )
            span.set_attribute("latency_ms", result.latency_ms)
            span.set_attribute("speculative_hit", speculative_hit)
        self._latest_result = result
        logger.info(
            "agent.retrieve query=%r citations=%d latency_ms=%.3f speculative=%s",
            text,
            len(result.citations),
            result.latency_ms,
            speculative_hit,
        )
        return result

    # -- frontend publishing -------------------------------------------------
    async def publish_citation(
        self,
        result: RetrievalResult,
        room: Any | None = None,  # noqa: ANN401 - LiveKit Room type is optional
        *,
        answer_text: str | None = None,
        proactive: bool = False,
    ) -> bool:
        """Publish the (faithfulness-gated) top citation to the data channel.

        Builds the JSON frame via :func:`build_citation_payload` — which runs the
        faithfulness gate against ``answer_text`` (or the chunk's own text) — and
        writes it to the room's local-participant data channel as a UTF-8 JSON
        frame, exactly the shape ``useCrossExam.ts`` parses. When the cited chunk
        does not support the answer, the published frame is
        ``{citation:null, reason:"not_found_in_document"}`` rather than a wrong
        box. All LiveKit usage is guarded so the method is a safe no-op
        (returning ``False``) when no room is wired or LiveKit is unavailable.

        Args:
            result: The retrieval result whose top citation to publish.
            room: Optional explicit room handle; falls back to ``self.room``.
            answer_text: The agent's drafted answer to faithfulness-check.
            proactive: Mark the frame as unprompted (ambient) surfacing.

        Returns:
            ``True`` if a frame was published, ``False`` otherwise.
        """
        target_room = room if room is not None else self._room
        if target_room is None:
            logger.debug("agent.publish_citation no room wired; skipping")
            return False

        with self._tracer.span("publish", proactive=proactive) as span:
            payload = build_citation_payload(
                result,
                answer_text,
                proactive=proactive,
                faithfulness_threshold=self._faithfulness_threshold,
            )
            if payload is None:
                logger.debug("agent.publish_citation no citations; skipping")
                return False

            cit = payload.get("citation")
            if isinstance(cit, dict):
                fth = cit.get("faithfulness")
                if isinstance(fth, dict):
                    span.set_attribute("faithfulness.score", fth.get("score"))
            span.set_attribute("latency_ms", payload.get("latencyMs"))

            data = json.dumps(payload).encode("utf-8")
            try:
                await target_room.local_participant.publish_data(data)
            except Exception:  # noqa: BLE001 - never break the turn on publish failure
                logger.exception("agent.publish_citation failed; turn continues")
                return False

        logger.debug(
            "agent.publish_citation published citation=%s reason=%s bytes=%d",
            payload.get("citation") is not None,
            payload.get("reason"),
            len(data),
        )
        return True

    @staticmethod
    # ANN401: LiveKit's ChatMessage type is unavailable without the optional
    # dep; this accepts it OR a plain str and normalises both.
    def _message_text(new_message: Any) -> str:  # noqa: ANN401
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
        # ANN401: this is LiveKit's ChatMessage, untyped here without the
        # optional ``livekit-agents`` dep (the runtime passes it in verbatim).
        new_message: Any,  # noqa: ANN401
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

        # PROACTIVE / AMBIENT SURFACING: when the just-completed turn is a spoken
        # CLAIM (a declarative assertion, not a question) and the document
        # confidently supports it, publish the citation UNPROMPTED with
        # ``proactive: true`` — the user need not ask. Confidence-gated by
        # retrieval score AND faithfulness so we never push a weak highlight.
        if self._proactive_enabled and self._is_proactive_claim(query_text, result):
            await self.publish_citation(
                result, answer_text=query_text, proactive=True
            )
            return

        # Otherwise publish the top citation (faithfulness-gated) for the answer.
        # No-op when no room is wired. ``answer_text`` defaults to the chunk in
        # publish path until the LLM's drafted answer is available.
        await self.publish_citation(result)

    def _is_proactive_claim(self, text: str, result: RetrievalResult) -> bool:
        """Whether ``text`` is a claim the document confidently supports.

        Combines the :class:`~crossexam_backend.proactive.ClaimDetector` (is this
        an assertion, not a question?) with the confidence gate
        :func:`~crossexam_backend.proactive.should_surface` (do retrieval and
        faithfulness both clear their thresholds?).
        """
        if not self._claim_detector.is_claim(text):
            return False
        if not result.citations:
            return False
        verdict = verify_faithfulness(
            text,
            result.top_text,
            threshold=self._faithfulness_threshold,
        )
        return should_surface(result, verdict, self._surface_thresholds)
