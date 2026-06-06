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
        log_level: Root log level for structured logging.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Moss ---------------------------------------------------------------
    moss_project_id: str | None = Field(default=None)
    moss_project_key: str | None = Field(default=None)
    moss_index_name: str = Field(default="crossexam-documents")

    # --- LiveKit ------------------------------------------------------------
    livekit_url: str | None = Field(default=None)
    livekit_api_key: str | None = Field(default=None)
    livekit_api_secret: str | None = Field(default=None)

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

    # --- Retrieval tuning ---------------------------------------------------
    top_k: int = Field(default=5, ge=1, le=50)
    alpha: float = Field(default=0.8, ge=0.0, le=1.0)

    # --- Mock / runtime -----------------------------------------------------
    use_mocks: bool | None = Field(default=None)
    mock_fixture_path: str = Field(default="fixtures/sample_chunks.json")

    log_level: str = Field(default="INFO")

    @property
    def has_moss_credentials(self) -> bool:
        """Return ``True`` when both Moss credentials are configured."""
        return bool(self.moss_project_id) and bool(self.moss_project_key)

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
