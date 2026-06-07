"""Tests for :mod:`crossexam_backend.config`."""

from __future__ import annotations

import pytest

from crossexam_backend.config import Settings


def test_use_mocks_auto_true_when_no_moss_keys() -> None:
    """With no Moss credentials, mocks should auto-enable."""
    settings = Settings(
        moss_project_id=None,
        moss_project_key=None,
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.use_mocks is True
    assert settings.has_moss_credentials is False


def test_use_mocks_auto_false_when_moss_keys_present() -> None:
    """With both Moss credentials, mocks should auto-disable."""
    settings = Settings(
        moss_project_id="proj-123",
        moss_project_key="key-abc",
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.use_mocks is False
    assert settings.has_moss_credentials is True


def test_use_mocks_explicit_override_wins() -> None:
    """An explicit ``use_mocks=True`` overrides credential presence."""
    settings = Settings(
        moss_project_id="proj-123",
        moss_project_key="key-abc",
        use_mocks=True,
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.use_mocks is True


def test_defaults_for_top_k_and_alpha() -> None:
    """Defaults match the spec: top_k=5, alpha=0.8."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.top_k == 5
    assert settings.alpha == pytest.approx(0.8)


def test_local_dev_cors_allows_localhost_and_loopback() -> None:
    """Both common Vite dev origins should be accepted by default."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.cors_origin_list == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_has_livekit_credentials_requires_all_three() -> None:
    """LiveKit credentials are only complete when all three are present."""
    partial = Settings(
        livekit_url="wss://x", livekit_api_key="k", _env_file=None  # type: ignore[call-arg]
    )
    assert partial.has_livekit_credentials is False
    full = Settings(
        livekit_url="wss://x",
        livekit_api_key="k",
        livekit_api_secret="s",
        _env_file=None,  # type: ignore[call-arg]
    )
    assert full.has_livekit_credentials is True


def test_alpha_out_of_range_rejected() -> None:
    """Alpha must be within [0, 1]."""
    with pytest.raises(ValueError):
        Settings(alpha=1.5, _env_file=None)  # type: ignore[call-arg]


def test_technical_depth_feature_defaults() -> None:
    """Speculative + proactive default on; faithfulness threshold defaults 0.5."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.speculative_enabled is True
    assert settings.proactive_enabled is True
    assert settings.faithfulness_threshold == pytest.approx(0.5)


def test_multihop_defaults_on() -> None:
    """Multi-hop routing defaults on with a sane per-hop k."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.multihop_enabled is True
    assert settings.multihop_per_hop_k == 5


def test_obs_defaults_off_and_no_secrets() -> None:
    """Observability is off by default with no baked-in Langfuse secrets."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.obs_enabled is False
    assert settings.langfuse_public_key is None
    assert settings.langfuse_secret_key is None
    assert settings.has_langfuse_credentials is False


def test_has_langfuse_credentials_requires_both() -> None:
    """Langfuse credentials are only complete when both keys are present."""
    partial = Settings(langfuse_public_key="pk", _env_file=None)  # type: ignore[call-arg]
    assert partial.has_langfuse_credentials is False
    full = Settings(
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
        _env_file=None,  # type: ignore[call-arg]
    )
    assert full.has_langfuse_credentials is True


def test_faithfulness_threshold_out_of_range_rejected() -> None:
    """The faithfulness threshold must be within [0, 1]."""
    with pytest.raises(ValueError):
        Settings(faithfulness_threshold=2.0, _env_file=None)  # type: ignore[call-arg]


def test_voice_model_pins_have_expected_defaults() -> None:
    """The live voice stack pins models explicitly (no silent plugin drift).

    The defaults are pinned in settings so a dependency bump cannot silently
    change the deployed model/voice. gpt-4.1 stays the realtime default, while
    newer OpenAI models remain an env override after live latency validation.
    """
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.deepgram_model == "nova-3"
    assert settings.deepgram_language == "en-US"
    assert settings.openai_model == "gpt-4.1"
    assert settings.cartesia_model == "sonic-3"
    assert settings.cartesia_language == "en"
    # The Cartesia voice is a non-empty UUID-ish identifier.
    assert settings.cartesia_voice
    assert isinstance(settings.cartesia_voice, str)


def test_voice_model_pins_overridable_via_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each voice model/voice/language pin is overridable via its env var."""
    monkeypatch.setenv("DEEPGRAM_MODEL", "nova-2")
    monkeypatch.setenv("DEEPGRAM_LANGUAGE", "fr")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    monkeypatch.setenv("CARTESIA_MODEL", "sonic-2")
    monkeypatch.setenv("CARTESIA_VOICE", "custom-voice-id")
    monkeypatch.setenv("CARTESIA_LANGUAGE", "es")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.deepgram_model == "nova-2"
    assert settings.deepgram_language == "fr"
    assert settings.openai_model == "gpt-4o"
    assert settings.cartesia_model == "sonic-2"
    assert settings.cartesia_voice == "custom-voice-id"
    assert settings.cartesia_language == "es"
