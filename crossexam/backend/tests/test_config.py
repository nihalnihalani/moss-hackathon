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
