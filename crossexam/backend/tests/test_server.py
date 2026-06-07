"""Tests for :mod:`crossexam_backend.server` provider-construction guards.

These cover the api-key-absent branch of ``_build_stt``/``_build_llm``/
``_build_tts``, which raises :class:`ProviderConfigError` *before* importing any
livekit plugin. That makes the path safe to exercise without the (uninstalled)
``livekit-plugins-*`` packages: the missing-key check short-circuits first.
"""

from __future__ import annotations

import pytest

from crossexam_backend.config import Settings
from crossexam_backend.server import (
    ProviderConfigError,
    _build_llm,
    _build_stt,
    _build_tts,
)


def _settings(**overrides: object) -> Settings:
    """Build a Settings with env/.env ignored so the test is hermetic."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_build_stt_missing_key_raises_provider_config_error() -> None:
    """Deepgram STT with no DEEPGRAM_API_KEY raises before importing the plugin."""
    settings = _settings(stt_provider="deepgram", deepgram_api_key=None)
    with pytest.raises(ProviderConfigError, match="DEEPGRAM_API_KEY"):
        _build_stt(settings)


def test_build_llm_missing_key_raises_provider_config_error() -> None:
    """OpenAI LLM with no OPENAI_API_KEY raises before importing the plugin."""
    settings = _settings(llm_provider="openai", openai_api_key=None)
    with pytest.raises(ProviderConfigError, match="OPENAI_API_KEY"):
        _build_llm(settings)


def test_build_tts_missing_key_raises_provider_config_error() -> None:
    """Cartesia TTS with no CARTESIA_API_KEY raises before importing the plugin."""
    settings = _settings(tts_provider="cartesia", cartesia_api_key=None)
    with pytest.raises(ProviderConfigError, match="CARTESIA_API_KEY"):
        _build_tts(settings)


def test_build_stt_unsupported_provider_raises() -> None:
    """An unknown STT provider raises a clear ProviderConfigError."""
    settings = _settings(stt_provider="nope")
    with pytest.raises(ProviderConfigError, match="Unsupported STT_PROVIDER"):
        _build_stt(settings)


def test_build_llm_unsupported_provider_raises() -> None:
    """An unknown LLM provider raises a clear ProviderConfigError."""
    settings = _settings(llm_provider="nope")
    with pytest.raises(ProviderConfigError, match="Unsupported LLM_PROVIDER"):
        _build_llm(settings)


def test_build_tts_unsupported_provider_raises() -> None:
    """An unknown TTS provider raises a clear ProviderConfigError."""
    settings = _settings(tts_provider="nope")
    with pytest.raises(ProviderConfigError, match="Unsupported TTS_PROVIDER"):
        _build_tts(settings)
