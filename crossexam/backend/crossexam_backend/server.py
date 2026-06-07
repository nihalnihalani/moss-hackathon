"""LiveKit worker entrypoint for the CrossExam voice agent.

Run with::

    python -m crossexam_backend.server dev

When ``livekit-agents`` is installed and LiveKit credentials are configured the
module wires up a real worker (STT -> LLM -> TTS) whose agent grounds each turn
via the retrieval index. When LiveKit is not installed it prints a helpful
message instead of crashing, so the package remains importable and runnable for
development and testing without the full real-time stack.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from crossexam_backend.agent import LIVEKIT_AVAILABLE, CrossExamAgent
from crossexam_backend.config import Settings, get_settings
from crossexam_backend.retrieval.base import RetrievalIndex
from crossexam_backend.retrieval.factory import get_index
from crossexam_backend.tracing import get_tracer

logger = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    """Configure structured-ish stdlib logging at the configured level."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def build_index(settings: Settings) -> RetrievalIndex:
    """Build the retrieval index for the worker (Moss or mock)."""
    return get_index(settings)


def build_agent(
    settings: Settings, index: RetrievalIndex, *, session_id: str = "default"
) -> CrossExamAgent:
    """Build the :class:`CrossExamAgent` from settings and an index.

    ``session_id`` scopes the agent's :class:`ConversationMemory` (the live
    worker passes the room name, so a repeated citation within a room is recalled
    rather than re-snapped).
    """
    return CrossExamAgent(
        index,
        top_k=settings.top_k,
        alpha=settings.alpha,
        speculative_enabled=settings.speculative_enabled,
        proactive_enabled=settings.proactive_enabled,
        multihop_enabled=settings.multihop_enabled,
        faithfulness_threshold=settings.faithfulness_threshold,
        session_id=session_id,
        tracer=get_tracer(settings),
    )


def main() -> int:
    """Process entrypoint. Returns a POSIX exit code."""
    settings = get_settings()
    configure_logging(settings)

    index = build_index(settings)
    logger.info(
        "crossexam.boot use_mocks=%s livekit_available=%s index=%s",
        settings.use_mocks,
        LIVEKIT_AVAILABLE,
        type(index).__name__,
    )

    if not LIVEKIT_AVAILABLE:
        print(
            "livekit-agents is not installed, so the real-time voice worker "
            "cannot start.\n"
            "Install it with:  pip install 'livekit-agents'\n"
            f"Retrieval index ready: {type(index).__name__} "
            f"(use_mocks={settings.use_mocks}).",
            file=sys.stderr,
        )
        return 0

    return _run_livekit_worker(settings, index)


class ProviderConfigError(RuntimeError):
    """Raised when a configured STT/LLM/TTS provider cannot be constructed.

    This makes a missing plugin package or API key a CLEAR, actionable error
    instead of a silent empty :class:`AgentSession` that never speaks.
    """


def _build_stt(settings: Settings) -> object:
    """Construct the STT plugin selected by ``settings.stt_provider``.

    Imports are local and guarded so a missing plugin or key surfaces a clear
    :class:`ProviderConfigError` rather than an import-time crash.
    """
    provider = settings.stt_provider.lower()
    if provider == "deepgram":
        if not settings.deepgram_api_key:
            raise ProviderConfigError(
                "STT_PROVIDER=deepgram requires DEEPGRAM_API_KEY to be set."
            )
        try:
            from livekit.plugins import deepgram
        except ImportError as exc:  # pragma: no cover - needs the plugin installed
            raise ProviderConfigError(
                "STT_PROVIDER=deepgram requires the 'livekit-plugins-deepgram' "
                "package. Install it with: pip install 'crossexam-backend[voice]'."
            ) from exc
        return deepgram.STT(
            api_key=settings.deepgram_api_key,
            model=settings.deepgram_model,
            language=settings.deepgram_language,
        )

    raise ProviderConfigError(
        f"Unsupported STT_PROVIDER={settings.stt_provider!r}. Supported: deepgram."
    )


def _build_llm(settings: Settings) -> object:
    """Construct the LLM plugin selected by ``settings.llm_provider``."""
    provider = settings.llm_provider.lower()
    if provider == "openai":
        if not settings.openai_api_key:
            raise ProviderConfigError(
                "LLM_PROVIDER=openai requires OPENAI_API_KEY to be set."
            )
        try:
            from livekit.plugins import openai
        except ImportError as exc:  # pragma: no cover - needs the plugin installed
            raise ProviderConfigError(
                "LLM_PROVIDER=openai requires the 'livekit-plugins-openai' "
                "package. Install it with: pip install 'crossexam-backend[voice]'."
            ) from exc
        return openai.LLM(api_key=settings.openai_api_key, model=settings.openai_model)

    raise ProviderConfigError(
        f"Unsupported LLM_PROVIDER={settings.llm_provider!r}. Supported: openai."
    )


def _build_tts(settings: Settings) -> object:
    """Construct the TTS plugin selected by ``settings.tts_provider``."""
    provider = settings.tts_provider.lower()
    if provider == "cartesia":
        if not settings.cartesia_api_key:
            raise ProviderConfigError(
                "TTS_PROVIDER=cartesia requires CARTESIA_API_KEY to be set."
            )
        try:
            from livekit.plugins import cartesia
        except ImportError as exc:  # pragma: no cover - needs the plugin installed
            raise ProviderConfigError(
                "TTS_PROVIDER=cartesia requires the 'livekit-plugins-cartesia' "
                "package. Install it with: pip install 'crossexam-backend[voice]'."
            ) from exc
        return cartesia.TTS(
            api_key=settings.cartesia_api_key,
            model=settings.cartesia_model,
            voice=settings.cartesia_voice,
            language=settings.cartesia_language,
        )

    raise ProviderConfigError(
        f"Unsupported TTS_PROVIDER={settings.tts_provider!r}. Supported: cartesia."
    )


def _build_vad() -> object | None:
    """Load Silero VAD for end-of-turn detection + interruptions, or ``None``.

    AgentSession needs a VAD to detect speech boundaries (when the user starts /
    stops talking). Without it, end-of-turn detection and barge-in never fire.
    ``livekit-plugins-silero`` ships ``VAD.load()`` which loads the Silero model
    weights once; we call it here (the worker calls this in prewarm so the model
    is warm before the first job). Imported lazily and guarded: when the plugin
    is not installed (mock/test env) this returns ``None`` and the caller starts
    the session without a VAD rather than crashing at import time.

    Verified surface (livekit-plugins-silero 1.x): ``silero.VAD.load()``.
    """
    try:  # pragma: no cover - needs the silero plugin installed
        from livekit.plugins import silero

        vad: object = silero.VAD.load()
        return vad
    except ImportError:  # pragma: no cover - plugin not in mock/test env
        logger.info(
            "silero VAD plugin not installed; session starts without a VAD "
            "(end-of-turn detection + interruptions disabled)."
        )
        return None


def _build_turn_detection() -> object | None:
    """Construct LiveKit's model-based turn detector, or ``None`` if absent.

    Uses the ``livekit-plugins-turn-detector`` multilingual model to decide
    end-of-turn from the transcript context rather than raw silence. Imported
    lazily and guarded: when the plugin is not installed this returns ``None``
    and the session falls back to VAD-based turn detection. This ONLY runs
    inside a live LiveKit worker session.
    """
    try:  # pragma: no cover - needs the turn-detector plugin installed
        from livekit.plugins.turn_detector.multilingual import MultilingualModel

        model: object = MultilingualModel()
        return model
    except ImportError:  # pragma: no cover - plugin not in mock/test env
        logger.info(
            "turn-detector plugin not installed; session uses VAD turn detection."
        )
        return None


def _run_livekit_worker(settings: Settings, index: RetrievalIndex) -> int:
    """Start the LiveKit worker. Imported lazily so the module stays portable."""
    # Imports are local on purpose: they only exist when livekit is installed.
    # mypy: ignore_missing_imports handles the optional ``livekit-agents`` dep,
    # so no per-import ignore is needed here.
    from livekit.agents import (
        AgentSession,
        JobContext,
        TurnHandlingOptions,
        WorkerOptions,
        cli,
    )

    async def entrypoint(ctx: JobContext) -> None:
        """Per-room LiveKit job: connect, prewarm, and run the voice session."""
        await ctx.connect()
        await index.prewarm()
        # Scope conversation memory to the room so repeated citations within the
        # same session are recalled (feat 5) rather than re-snapped.
        room_name = getattr(ctx.room, "name", None) or settings.livekit_default_room
        agent = build_agent(settings, index, session_id=room_name)
        # Wire the room handle so on_user_turn_completed can publish citations
        # to the frontend over the data channel.
        agent.room = ctx.room
        # Register the INBOUND data-channel handler so a typed Cmd+K question
        # ({type:"ask"}) or Space push-to-talk ({type:"ptt"}) published by the
        # frontend reaches the backend: an "ask" runs the same route as a spoken
        # turn (retrieve / multi-hop -> publish a real citations/contradiction
        # frame). Guarded — a no-op without a live room.
        agent.register_inbound_handlers(ctx.room)

        # Build the STT/LLM/TTS providers from settings. A misconfigured or
        # missing provider raises a CLEAR ProviderConfigError here instead of
        # silently producing a no-op session.
        stt = _build_stt(settings)
        llm = _build_llm(settings)
        tts = _build_tts(settings)

        # TURN DETECTION + BARGE-IN. The session uses LiveKit's model-based
        # multilingual turn detector to decide when the user has truly finished
        # speaking (beats raw VAD silence on conversational pauses). Barge-in
        # (interruptions) is on by DEFAULT in livekit-agents 1.5.x, so we no
        # longer pass the deprecated ``allow_interruptions=`` kwarg. These kwargs
        # are assembled into a dict and only attached when the plugin / support
        # is present — they ONLY activate in a real LiveKit session (never in
        # mock/test, where this whole function is not reached).
        session_kwargs: dict[str, object] = {
            "stt": stt,
            "llm": llm,
            "tts": tts,
        }
        # VAD: required for end-of-turn detection + interruptions. Prefer a VAD
        # warmed in prewarm (stashed on proc.userdata); fall back to loading it
        # here if prewarm did not run (e.g. a fresh dispatch). Attached only when
        # the silero plugin is present.
        vad = getattr(getattr(ctx, "proc", None), "userdata", {}).get("vad")
        if vad is None:
            vad = _build_vad()
        if vad is not None:
            session_kwargs["vad"] = vad
        # Turn detection is configured via the non-deprecated TurnHandlingOptions
        # (livekit-agents 1.5.x): the bare ``turn_detection=`` kwarg is deprecated
        # in favour of ``turn_handling=TurnHandlingOptions(turn_detection=...)``.
        turn_detection = _build_turn_detection()
        if turn_detection is not None:
            session_kwargs["turn_handling"] = TurnHandlingOptions(
                turn_detection=turn_detection
            )

        session = AgentSession(**session_kwargs)
        await session.start(agent=agent, room=ctx.room)

        # SPECULATIVE PREFETCH WIRING. Feed ASR interim (non-final) transcripts
        # into the agent's SpeculativeRetriever so the citation for the final
        # transcript is frequently already cached by the time the user's turn
        # ends. AgentSession emits "user_input_transcribed" with an event object
        # carrying ``transcript`` and ``is_final`` (livekit-agents 1.5.x). This
        # is the LiveKit-side interim hook — there is no agent-level one — and it
        # ONLY runs in a live session. Defensive throughout (getattr, guarded).
        def _on_transcript(ev: object) -> None:
            if getattr(ev, "is_final", True):
                return
            text = getattr(ev, "transcript", "") or ""
            if not text:
                return
            try:
                asyncio.get_running_loop().create_task(agent.prefetch_partial(text))
            except RuntimeError:
                pass

        # Register via a plain call (not the @session.on decorator) so mypy does
        # not flag an untyped decorator on _on_transcript; behaviour is identical.
        session.on("user_input_transcribed", _on_transcript)

    async def prewarm(proc: object) -> None:
        """Worker prewarm: eagerly load the index + Silero VAD before serving jobs.

        Loading the Silero VAD weights here (once per process) keeps the first
        job's first turn fast. The loaded VAD is stashed on ``proc.userdata`` so
        the entrypoint reuses it instead of reloading. Guarded — a missing silero
        plugin simply leaves no VAD stashed and the entrypoint starts without one.
        """
        await index.prewarm()
        vad = _build_vad()
        if vad is not None:
            userdata = getattr(proc, "userdata", None)
            if isinstance(userdata, dict):
                userdata["vad"] = vad

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
