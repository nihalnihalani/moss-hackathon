"""Tests for :mod:`crossexam_backend.server` provider-construction guards.

These cover the api-key-absent branch of ``_build_stt``/``_build_llm``/
``_build_tts``, which raises :class:`ProviderConfigError` *before* importing any
livekit plugin. That makes the path safe to exercise without the (uninstalled)
``livekit-plugins-*`` packages: the missing-key check short-circuits first.
"""

from __future__ import annotations

import sys
import types

import pytest

from crossexam_backend.config import Settings
from crossexam_backend.server import (
    ProviderConfigError,
    _build_llm,
    _build_stt,
    _build_tts,
    _build_turn_detection,
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


def test_build_llm_prefers_openai_responses_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct OpenAI usage prefers LiveKit's recommended Responses API class."""

    class _ResponsesLLM:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class _ChatLLM:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    fake_livekit = types.ModuleType("livekit")
    fake_plugins = types.ModuleType("livekit.plugins")
    fake_openai = types.ModuleType("livekit.plugins.openai")
    fake_openai.responses = types.SimpleNamespace(LLM=_ResponsesLLM)  # type: ignore[attr-defined]
    fake_openai.LLM = _ChatLLM  # type: ignore[attr-defined]
    fake_plugins.openai = fake_openai  # type: ignore[attr-defined]
    fake_livekit.plugins = fake_plugins  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    monkeypatch.setitem(sys.modules, "livekit.plugins", fake_plugins)
    monkeypatch.setitem(sys.modules, "livekit.plugins.openai", fake_openai)

    settings = _settings(openai_api_key="sk-test", openai_model="gpt-4.1")
    llm = _build_llm(settings)

    assert isinstance(llm, _ResponsesLLM)
    assert llm.kwargs == {"api_key": "sk-test", "model": "gpt-4.1"}


def test_build_llm_falls_back_to_chat_completions_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older livekit-plugins-openai builds without responses.LLM still work."""

    class _ChatLLM:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    fake_livekit = types.ModuleType("livekit")
    fake_plugins = types.ModuleType("livekit.plugins")
    fake_openai = types.ModuleType("livekit.plugins.openai")
    fake_openai.LLM = _ChatLLM  # type: ignore[attr-defined]
    fake_plugins.openai = fake_openai  # type: ignore[attr-defined]
    fake_livekit.plugins = fake_plugins  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    monkeypatch.setitem(sys.modules, "livekit.plugins", fake_plugins)
    monkeypatch.setitem(sys.modules, "livekit.plugins.openai", fake_openai)

    settings = _settings(openai_api_key="sk-test", openai_model="gpt-4.1")
    llm = _build_llm(settings)

    assert isinstance(llm, _ChatLLM)
    assert llm.kwargs == {"api_key": "sk-test", "model": "gpt-4.1"}


def test_build_turn_detection_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local turn detector stays off unless explicitly enabled."""

    class _Model:
        pass

    fake_multilingual = types.ModuleType("livekit.plugins.turn_detector.multilingual")
    fake_multilingual.MultilingualModel = _Model  # type: ignore[attr-defined]
    monkeypatch.setitem(
        sys.modules,
        "livekit.plugins.turn_detector.multilingual",
        fake_multilingual,
    )

    assert _build_turn_detection(_settings()) is None


def test_build_turn_detection_enabled_uses_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The turn detector is constructed only when explicitly enabled."""

    class _Model:
        pass

    fake_multilingual = types.ModuleType("livekit.plugins.turn_detector.multilingual")
    fake_multilingual.MultilingualModel = _Model  # type: ignore[attr-defined]
    monkeypatch.setitem(
        sys.modules,
        "livekit.plugins.turn_detector.multilingual",
        fake_multilingual,
    )

    assert isinstance(
        _build_turn_detection(_settings(turn_detector_enabled=True)),
        _Model,
    )
