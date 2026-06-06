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

from crossexam_backend.memory import ConversationMemory
from crossexam_backend.models import (
    BBox,
    Citation,
    HopTrace,
    MemoryRef,
    MultiHopResult,
    RetrievalResult,
    Speaker,
)
from crossexam_backend.proactive import (
    ClaimDetector,
    SurfaceThresholds,
    should_surface,
)
from crossexam_backend.retrieval.base import RetrievalIndex
from crossexam_backend.retrieval.multihop import MultiHopRetriever, QueryDecomposer
from crossexam_backend.speculative import SpeculativeRetriever
from crossexam_backend.tracing import NoOpTracer, OTelTracer
from crossexam_backend.verify import FaithfulnessVerdict, verify_faithfulness

logger = logging.getLogger(__name__)

# Reason code published (with ``citations: []``) when no cited chunk supports
# the agent's answer — the faithfulness gate refuses to highlight.
REASON_NOT_FOUND = "not_found_in_document"


# --------------------------------------------------------------------------- #
# Frontend bridge: structured multi-citation wire frame (depth-v2 contract)   #
# --------------------------------------------------------------------------- #
def _bbox_dict(bbox: BBox) -> dict[str, Any]:
    """Serialise a :class:`BBox` to the snake_case wire shape the frontend reads."""
    return {
        "page": bbox.page,
        "x0": bbox.x0,
        "y0": bbox.y0,
        "x1": bbox.x1,
        "y1": bbox.y1,
        "page_width": bbox.page_width,
        "page_height": bbox.page_height,
    }


def _citation_dict(citation: Citation, verdict: FaithfulnessVerdict) -> dict[str, Any]:
    """Serialise one (faithfulness-checked) :class:`Citation` to the wire shape.

    Carries the depth-v2 fields through verbatim: ``quads`` (per-line boxes),
    ``documentId``/``documentTitle`` (multi-doc switcher) and ``scanned`` (OCR
    badge), plus the attached ``faithfulness`` verdict.
    """
    chunk = citation.chunk
    payload: dict[str, Any] = {
        "id": chunk.id,
        "text": chunk.text,
        "bbox": _bbox_dict(chunk.bbox),
        "confidence": chunk.confidence,
        "score": citation.score,
        "documentId": citation.documentId,
        "faithfulness": {
            "supported": verdict.supported,
            "score": verdict.score,
            "method": verdict.method,
        },
    }
    if citation.documentTitle is not None:
        payload["documentTitle"] = citation.documentTitle
    if citation.quads:
        payload["quads"] = [_bbox_dict(q) for q in citation.quads]
    if citation.scanned:
        payload["scanned"] = True
    return payload


def _memory_dict(ref: MemoryRef) -> dict[str, Any]:
    """Serialise a :class:`MemoryRef` recall to the wire shape (feat 5)."""
    return {
        "kind": ref.kind,
        "citationId": ref.citationId,
        "documentId": ref.documentId,
        "page": ref.page,
        "note": ref.note,
    }


def _hop_dict(hop: HopTrace) -> dict[str, Any]:
    """Serialise a :class:`HopTrace` decomposition step to the wire shape."""
    return {"subQuery": hop.subQuery, "citationIds": list(hop.citationIds)}


def _speaker_dict(speaker: Speaker) -> dict[str, Any]:
    """Serialise a :class:`Speaker` (meeting mode) to the wire shape (feat 4)."""
    return {"id": speaker.id, "label": speaker.label}


def build_frame(
    result: RetrievalResult | MultiHopResult,
    *,
    answer_text: str | None = None,
    proactive: bool = False,
    speaker: Speaker | None = None,
    memory_refs: list[MemoryRef] | None = None,
    faithfulness_threshold: float = 0.5,
) -> dict[str, Any]:
    """Build the depth-v2 multi-citation wire frame the frontend consumes.

    Produces the contract frame::

        { citations: Citation[], primaryId?, contradiction?, hops?, memory?,
          speaker?, proactive?, latencyMs?, reason?, ... }

    Every candidate citation is faithfulness-checked against ``answer_text`` (or,
    when ``None``, against its own chunk text, i.e. vacuously supported). The
    FAITHFULNESS GATE drops any citation the answer is not supported by; if none
    survive the gate the frame is the honest-silence
    ``{citations: [], reason: "not_found_in_document", latencyMs}`` rather than a
    wrong box. Surviving citations carry ``quads``/``documentId``/``scanned`` and
    an attached ``faithfulness`` verdict (feats 1-3). ``primaryId`` is the id to
    page-jump to first; for a :class:`MultiHopResult` it comes from the
    contradiction pair (when flagged) and the ``hops``/``contradiction`` trail is
    carried through (feat 1). ``memory_refs`` are emitted as ``memory[]`` recalls
    (feat 5) and ``speaker`` is threaded through for meeting mode (feat 4).

    This is a pure function (no LiveKit dependency), unit-testable without
    ``livekit-agents`` installed.

    Args:
        result: The (single-hop or multi-hop) retrieval result for the turn.
        answer_text: The agent's drafted answer to faithfulness-check each
            citation against; ``None`` uses each chunk's own text.
        proactive: Mark the frame as an unprompted (ambient) surfacing.
        speaker: Who triggered the turn (meeting mode), threaded onto the frame.
        memory_refs: Recalls of citations already surfaced this session, emitted
            as ``memory[]`` instead of re-snapping the box.
        faithfulness_threshold: Support threshold for the highlight gate.

    Returns:
        A wire-frame dict ready to ``json.dumps`` and publish.
    """
    candidates: list[Citation] = list(result.citations)
    latency_ms = result.latency_ms

    kept: list[dict[str, Any]] = []
    kept_ids: list[str] = []
    for citation in candidates:
        chunk_text = citation.chunk.text
        verdict = verify_faithfulness(
            answer_text if answer_text is not None else chunk_text,
            chunk_text,
            threshold=faithfulness_threshold,
        )
        if not verdict.supported:
            # Drop a box that does not back up what was said.
            continue
        kept.append(_citation_dict(citation, verdict))
        kept_ids.append(citation.chunk.id)

    frame: dict[str, Any] = {
        "citations": kept,
        "latencyMs": latency_ms,
    }

    # primaryId: prefer the multi-hop primary (e.g. the contradiction anchor)
    # when it survived the gate, else the best surviving citation.
    multihop_primary: str | None = None
    if isinstance(result, MultiHopResult):
        if result.primary_id is not None and result.primary_id in kept_ids:
            multihop_primary = result.primary_id
        if result.contradiction and len(kept) > 1:
            frame["contradiction"] = True
        if result.hops:
            frame["hops"] = [_hop_dict(h) for h in result.hops]

    if not kept:
        # Honest silence: no supported citation to highlight this turn.
        frame["reason"] = REASON_NOT_FOUND
    else:
        frame["primaryId"] = multihop_primary or kept_ids[0]

    if memory_refs:
        frame["memory"] = [_memory_dict(r) for r in memory_refs]
    if speaker is not None:
        frame["speaker"] = _speaker_dict(speaker)
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
        multihop_enabled: bool = True,
        faithfulness_threshold: float = 0.5,
        surface_thresholds: SurfaceThresholds | None = None,
        session_id: str = "default",
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
            multihop_enabled: Route contradiction/multi-hop questions through the
                :class:`MultiHopRetriever` (decompose -> per-hop retrieve ->
                fuse -> flag contradiction). When ``False`` every turn uses the
                single-query path.
            faithfulness_threshold: Support threshold for the highlight gate.
            surface_thresholds: Confidence gates for proactive surfacing.
            session_id: Scopes :class:`ConversationMemory` so a repeated citation
                in the same session is recalled rather than re-snapped.
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

        # Multi-hop routing (feat 1): a QueryDecomposer signals whether a turn is
        # a contradiction/multi-hop question; if so the MultiHopRetriever
        # decomposes, retrieves per sub-query across documents, fuses and flags
        # contradictions. Otherwise the cheaper single-query path is used.
        self._multihop_enabled = multihop_enabled
        self._decomposer = QueryDecomposer()
        self._multihop = MultiHopRetriever(index, self._decomposer)

        # Conversation memory (feat 5): tracks citations surfaced this session so
        # a repeat is recalled ("as we saw on page N") instead of re-snapped.
        self._memory = ConversationMemory(session_id=session_id)
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

    @property
    def memory(self) -> ConversationMemory:
        """The per-session :class:`ConversationMemory` (feat 5 dedupe/recall)."""
        return self._memory

    @property
    def multihop_enabled(self) -> bool:
        """Whether contradiction/multi-hop routing is active for this agent."""
        return self._multihop_enabled

    def is_multihop_question(self, text: str) -> bool:
        """Whether ``text`` should route through the multi-hop retriever.

        Uses the :class:`QueryDecomposer` signal (contradiction/multi-hop cues),
        gated on :attr:`multihop_enabled`.
        """
        return self._multihop_enabled and self._decomposer.is_multihop(text)

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

    async def retrieve_multihop(self, text: str) -> MultiHopResult:
        """Decompose ``text`` into hops, retrieve across docs and fuse (feat 1).

        Used for contradiction/multi-hop questions (see
        :meth:`is_multihop_question`). Records the decomposition trail (``hops``)
        and the cross-document ``contradiction`` flag, and caches the projected
        single-hop view as :attr:`latest_result` so the rest of the agent (and
        the frontend accessors) keep working unchanged.
        """
        with self._tracer.span("retrieve_multihop", query=text) as span:
            result = await self._multihop.retrieve(
                text, top_k=self._top_k, per_hop_k=self._top_k, alpha=self._alpha
            )
            span.set_attribute("latency_ms", result.latency_ms)
            span.set_attribute("hops", len(result.hops))
            span.set_attribute("contradiction", result.contradiction)
        self._latest_result = result.to_retrieval_result()
        logger.info(
            "agent.retrieve_multihop query=%r hops=%d citations=%d contradiction=%s",
            text,
            len(result.hops),
            len(result.citations),
            result.contradiction,
        )
        return result

    # -- frontend publishing -------------------------------------------------
    def _dedupe_for_publish(
        self, result: RetrievalResult | MultiHopResult
    ) -> tuple[RetrievalResult | MultiHopResult, list[MemoryRef]]:
        """Split ``result``'s citations into fresh ones and session recalls.

        Runs :meth:`ConversationMemory.dedupe`: a citation already surfaced this
        session becomes a :class:`MemoryRef` recall (so the UI says "as we saw on
        page N" instead of re-snapping), the rest pass through fresh and are
        recorded as surfaced. Returns a copy of ``result`` carrying only the fresh
        citations (preserving hops/contradiction/primary on a multi-hop result)
        plus the recalls to emit as ``memory[]``.
        """
        fresh, recalls = self._memory.dedupe(list(result.citations))
        scoped: RetrievalResult | MultiHopResult
        if isinstance(result, MultiHopResult):
            fresh_ids = {c.chunk.id for c in fresh}
            primary = result.primary_id if result.primary_id in fresh_ids else (
                fresh[0].chunk.id if fresh else None
            )
            scoped = result.model_copy(
                update={"citations": fresh, "primary_id": primary}
            )
        else:
            scoped = result.model_copy(update={"citations": fresh})
        return scoped, recalls

    async def publish_frame(
        self,
        result: RetrievalResult | MultiHopResult,
        room: Any | None = None,  # noqa: ANN401 - LiveKit Room type is optional
        *,
        answer_text: str | None = None,
        proactive: bool = False,
        speaker: Speaker | None = None,
        dedupe: bool = True,
    ) -> bool:
        """Publish the depth-v2 multi-citation wire frame to the data channel.

        Before building the frame, runs the session :class:`ConversationMemory`
        dedupe (when ``dedupe`` is set): citations already surfaced this session
        are emitted as ``memory[]`` recalls rather than re-snapped, and fresh ones
        pass through and are recorded as surfaced. The frame is then built by
        :func:`build_frame`, which runs the faithfulness gate per citation (drops
        unsupported ones; emits ``{citations:[], reason:"not_found_in_document"}``
        when none survive) and threads through ``hops``/``contradiction``/
        ``primaryId`` (multi-hop), ``speaker`` (meeting mode) and ``proactive``.

        All LiveKit usage is guarded so the method is a safe no-op (returning
        ``False``) when no room is wired or LiveKit is unavailable.

        Args:
            result: The single-hop or multi-hop retrieval result for the turn.
            room: Optional explicit room handle; falls back to ``self.room``.
            answer_text: The agent's drafted answer to faithfulness-check.
            proactive: Mark the frame as unprompted (ambient) surfacing.
            speaker: Who triggered the turn (meeting mode), threaded onto frame.
            dedupe: Run conversation-memory dedupe (recall repeats). Disable to
                publish every citation as fresh (e.g. a forced re-surface).

        Returns:
            ``True`` if a frame was published, ``False`` otherwise.
        """
        target_room = room if room is not None else self._room
        if target_room is None:
            logger.debug("agent.publish_frame no room wired; skipping")
            return False

        recalls: list[MemoryRef] = []
        scoped: RetrievalResult | MultiHopResult = result
        if dedupe:
            scoped, recalls = self._dedupe_for_publish(result)

        with self._tracer.span("publish", proactive=proactive) as span:
            payload = build_frame(
                scoped,
                answer_text=answer_text,
                proactive=proactive,
                speaker=speaker,
                memory_refs=recalls,
                faithfulness_threshold=self._faithfulness_threshold,
            )
            span.set_attribute("citations", len(payload.get("citations", [])))
            span.set_attribute("recalls", len(recalls))
            span.set_attribute("latency_ms", payload.get("latencyMs"))

            data = json.dumps(payload).encode("utf-8")
            try:
                await target_room.local_participant.publish_data(data)
            except Exception:  # noqa: BLE001 - never break the turn on publish failure
                logger.exception("agent.publish_frame failed; turn continues")
                return False

        logger.debug(
            "agent.publish_frame published citations=%d recalls=%d reason=%s bytes=%d",
            len(payload.get("citations", [])),
            len(recalls),
            payload.get("reason"),
            len(data),
        )
        return True

    # Back-compat alias: the single-citation publisher is now a multi-citation
    # frame publisher. Existing callers keep working.
    async def publish_citation(
        self,
        result: RetrievalResult | MultiHopResult,
        room: Any | None = None,  # noqa: ANN401 - LiveKit Room type is optional
        *,
        answer_text: str | None = None,
        proactive: bool = False,
        speaker: Speaker | None = None,
        dedupe: bool = True,
    ) -> bool:
        """Deprecated alias for :meth:`publish_frame` (kept for back-compat)."""
        return await self.publish_frame(
            result,
            room,
            answer_text=answer_text,
            proactive=proactive,
            speaker=speaker,
            dedupe=dedupe,
        )

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
        *,
        speaker: Speaker | None = None,
    ) -> None:
        """LiveKit hook: enrich the turn with retrieved document passages.

        This is invoked by the **LiveKit Agents** runtime after the user's
        speech has been transcribed and before the LLM generates a reply. We
        retrieve the most relevant passages from the index and inject them as a
        ``role="system"`` message into ``turn_ctx`` so the model can ground and
        cite its answer with zero added latency for the user.

        ROUTING (feat 1): when the turn reads as a contradiction / multi-hop
        question (per :meth:`is_multihop_question`) it is routed through the
        :class:`MultiHopRetriever` so the published frame carries the ``hops``
        trail, the ``contradiction`` flag and citations spanning pages/docs;
        otherwise the single-query path is used. The retrieval result (multi-hop
        projected down) is what grounds the LLM and is published.

        Args:
            turn_ctx: The current turn / chat context to enrich.
            new_message: The user's just-completed message (string or LiveKit
                ``ChatMessage``).
            speaker: Optional meeting-mode speaker who triggered the turn (feat
                4); threaded onto every frame published for this turn.
        """
        query_text = self._message_text(new_message).strip()
        if not query_text:
            logger.debug("agent.on_user_turn_completed empty message; skipping")
            return

        # Decide the retrieval path: multi-hop for contradiction/compound asks.
        publish_result: RetrievalResult | MultiHopResult
        if self.is_multihop_question(query_text):
            multi = await self.retrieve_multihop(query_text)
            publish_result = multi
            system_result = multi.to_retrieval_result()
        else:
            single = await self.retrieve(query_text)
            publish_result = single
            system_result = single

        system_content = system_result.to_system_prompt()

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
        # Speaker-aware: the surfacing is attributed to whoever made the claim.
        if self._proactive_enabled and self._is_proactive_claim(
            query_text, system_result
        ):
            await self.publish_frame(
                publish_result,
                answer_text=query_text,
                proactive=True,
                speaker=speaker,
            )
            return

        # Otherwise publish the turn's frame (faithfulness-gated, memory-deduped).
        # No-op when no room is wired. ``answer_text`` defaults to the chunk in
        # publish path until the LLM's drafted answer is available.
        await self.publish_frame(publish_result, speaker=speaker)

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
