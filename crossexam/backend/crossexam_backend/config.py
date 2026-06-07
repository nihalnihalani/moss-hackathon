"""Application configuration via ``pydantic-settings``.

Settings are loaded from environment variables (and a ``.env`` file if present).
When the credentials required to talk to the real Moss service are missing, the
backend automatically falls back to an in-memory mock index so that the worker
and the test-suite run with no external dependencies and no real API keys.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Runtime configuration for the CrossExam backend.

    Attributes:
        moss_project_id: Moss project identifier. Required for the real client.
        moss_project_key: Moss API key. Required for the real client.
        moss_index_name: Name of the Moss index to query.
        livekit_url: LiveKit server URL (e.g. ``wss://...``).
        livekit_api_key: LiveKit API key.
        livekit_api_secret: LiveKit API secret.
        top_k: Default number of citations to retrieve per turn.
        alpha: Hybrid-search weight; ``1.0`` = pure semantic, ``0.0`` = pure
            keyword. Defaults to ``0.8`` (semantic-leaning).
        use_mocks: When ``True`` the mock index is used regardless of keys. When
            left unset it is auto-derived: ``True`` if the Moss credentials are
            missing, ``False`` otherwise.
        mock_fixture_path: Path to the JSON fixture the mock index loads.
        multihop_enabled: Route contradiction / multi-hop questions through the
            MultiHopRetriever (decompose -> per-hop retrieve -> fuse -> flag
            contradiction). Defaults to ``True``.
        multihop_per_hop_k: Max citations retrieved per decomposed sub-query.
        log_level: Root log level for structured logging.
    """

    model_config = SettingsConfigDict(
        # Support both direct package runs from `crossexam/backend` and the
        # documented project-root `crossexam/.env` used by Makefile/Compose.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Moss ---------------------------------------------------------------
    moss_project_id: str | None = Field(default=None)
    moss_project_key: str | None = Field(default=None)
    moss_index_name: str = Field(default="crossexam-documents")
    # Embedding model the Moss index is built/queried with. Default is Moss's
    # edge model; "moss-mediumlm" trades latency for accuracy. Pipeline (build)
    # and backend (query) must agree, so both read this single value.
    moss_model_id: str = Field(default="moss-minilm")
    # load_index tuning: when auto_refresh is on, Moss re-pulls the index from
    # the cloud every refresh_interval_s so a live-upserted doc becomes
    # queryable without an explicit reload. Off by default (deterministic demo).
    moss_auto_refresh: bool = Field(default=False)
    moss_refresh_interval_s: int = Field(default=600, ge=30, le=86400)
    # create_index / add_docs return an async JOB; we poll get_job_status until
    # COMPLETED. These bound that poll loop (timeout + interval, seconds).
    moss_job_timeout_s: float = Field(default=120.0, ge=1.0, le=3600.0)
    # ge=0.0 so a 0 interval is valid (poll as fast as the deadline allows —
    # used by tests; the deadline still bounds the loop). The pipeline reads the
    # MOSS_JOB_POLL_INTERVAL_S env var directly, so this bound must not reject 0.
    moss_job_poll_interval_s: float = Field(default=1.0, ge=0.0, le=60.0)
    # Bound live index warmup so a broken/corrupt local Moss model cannot block
    # LiveKit worker initialization forever. On timeout we use MockIndex fallback.
    moss_load_timeout_s: float = Field(default=15.0, ge=1.0, le=300.0)

    # --- LiveKit ------------------------------------------------------------
    livekit_url: str | None = Field(default=None)
    livekit_api_key: str | None = Field(default=None)
    livekit_api_secret: str | None = Field(default=None)
    # Dispatch name for the LiveKit worker. A named worker is explicitly
    # dispatched from /token, which is more reliable than automatic room dispatch
    # during local restarts and reconnects.
    livekit_agent_name: str = Field(default="crossexam-agent")

    # --- Voice pipeline providers (STT / LLM / TTS) -------------------------
    # Which plugin to use for each leg of the real-time voice loop. The defaults
    # (deepgram / openai / cartesia) are the common LiveKit Agents stack. Each
    # provider needs its corresponding API key below to actually start.
    stt_provider: str = Field(default="deepgram")
    llm_provider: str = Field(default="openai")
    tts_provider: str = Field(default="cartesia")

    # Provider API keys. Blank in mock/offline mode; required for the matching
    # provider when a live voice session starts.
    deepgram_api_key: str | None = Field(default=None)
    openai_api_key: str | None = Field(default=None)
    cartesia_api_key: str | None = Field(default=None)

    # Explicit model/voice pins for the live voice stack. Pinned (not left to
    # plugin defaults) so a dependency bump can't silently change the model.
    # `gpt-4.1` is LiveKit's direct-OpenAI example model and OpenAI's strongest
    # non-reasoning model; for deeper but slower/costlier reasoning, override
    # OPENAI_MODEL to a current GPT-5.x model after live latency testing.
    deepgram_model: str = Field(default="nova-3")
    deepgram_language: str = Field(default="en-US")
    openai_model: str = Field(default="gpt-4.1")
    cartesia_model: str = Field(default="sonic-3")
    # Cartesia "Katie" — a voice Cartesia documents as tuned for voice agents.
    cartesia_voice: str = Field(default="f786b574-daa5-4673-aa0c-cbe3e8534c02")
    cartesia_language: str = Field(default="en")
    # Optional local LiveKit turn-detector model. Disabled by default because
    # it requires downloaded model weights / inference setup; Silero VAD remains
    # the reliable local end-of-turn path.
    turn_detector_enabled: bool = Field(default=False)

    # --- Retrieval tuning ---------------------------------------------------
    top_k: int = Field(default=5, ge=1, le=50)
    alpha: float = Field(default=0.8, ge=0.0, le=1.0)

    # --- Technical-depth features (speculative / faithfulness / proactive) --
    # Speculative retrieval on ASR partials: prefetch the query on interim
    # transcripts so the citation is ready before the turn ends. On by default.
    speculative_enabled: bool = Field(default=True)
    # Faithfulness gate: minimum support score for the agent to highlight a
    # citation. Below this the agent publishes {citation:null,
    # reason:"not_found_in_document"} instead of a wrong box.
    faithfulness_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    # Proactive / ambient surfacing: publish a citation unprompted when a spoken
    # CLAIM (not a question) is confidently supported by the document.
    proactive_enabled: bool = Field(default=True)
    # Multi-hop routing (depth-v2 feat 1): route contradiction / multi-hop
    # questions through the MultiHopRetriever (decompose -> per-hop retrieve ->
    # fuse -> flag contradiction) so the frame carries hops[] + contradiction
    # and citations spanning pages/docs. On by default.
    multihop_enabled: bool = Field(default=True)
    # Max citations retrieved per decomposed sub-query (multi-hop per-hop k).
    multihop_per_hop_k: int = Field(default=5, ge=1, le=50)

    # --- Observability (OpenTelemetry -> Langfuse) --------------------------
    # Tracing is OFF unless obs_enabled AND the [obs] extra is installed AND the
    # Langfuse keys below are present. No secrets ship with these defaults.
    obs_enabled: bool = Field(default=False)
    langfuse_public_key: str | None = Field(default=None)
    langfuse_secret_key: str | None = Field(default=None)
    langfuse_host: str = Field(default="https://cloud.langfuse.com")

    # --- Mock / runtime -----------------------------------------------------
    use_mocks: bool | None = Field(default=None)
    mock_fixture_path: str = Field(default="fixtures/sample_chunks.json")

    log_level: str = Field(default="INFO")

    # --- HTTP API service ---------------------------------------------------
    # The FastAPI service (crossexam_backend.api) mints LiveKit tokens and
    # ingests documents. These have sane local-dev defaults.
    api_host: str = Field(default="0.0.0.0")  # noqa: S104 - bind-all is intended for the dev/container service  # noqa: E501
    api_port: int = Field(default=8000, ge=1, le=65535)
    # Comma-separated list of allowed CORS origins for the browser frontend.
    cors_origins: str = Field(default="http://localhost:5173,http://127.0.0.1:5173")
    # Default room a /token request joins when the body omits ``room``.
    livekit_default_room: str = Field(default="crossexam")
    # Token TTL in seconds for minted LiveKit access tokens.
    token_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    # Max accepted upload size for POST /documents, in megabytes.
    max_upload_mb: int = Field(default=25, ge=1, le=200)
    # Directory where POST /documents persists source PDFs for later rendering.
    uploaded_pdf_dir: str = Field(default="runtime/uploads")

    @property
    def cors_origin_list(self) -> list[str]:
        """Return ``cors_origins`` split into a clean list of origins."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_moss_credentials(self) -> bool:
        """Return ``True`` when both Moss credentials are configured."""
        return bool(self.moss_project_id) and bool(self.moss_project_key)

    @property
    def has_langfuse_credentials(self) -> bool:
        """Return ``True`` when both Langfuse keys are configured."""
        return bool(self.langfuse_public_key) and bool(self.langfuse_secret_key)

    @property
    def has_livekit_credentials(self) -> bool:
        """Return ``True`` when all LiveKit credentials are configured."""
        return bool(self.livekit_url) and bool(self.livekit_api_key) and bool(
            self.livekit_api_secret
        )

    @model_validator(mode="after")
    def _resolve_use_mocks(self) -> Settings:
        """Auto-enable mocks when ``use_mocks`` is unset and Moss keys missing."""
        if self.use_mocks is None:
            self.use_mocks = not self.has_moss_credentials
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance loaded from the environment."""
    settings = Settings()
    logger.debug(
        "settings.loaded use_mocks=%s has_moss=%s has_livekit=%s top_k=%s alpha=%s",
        settings.use_mocks,
        settings.has_moss_credentials,
        settings.has_livekit_credentials,
        settings.top_k,
        settings.alpha,
    )
    return settings
