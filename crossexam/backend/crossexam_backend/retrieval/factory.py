"""Factory that selects the concrete retrieval index for the current config."""

from __future__ import annotations

import logging
from pathlib import Path

from crossexam_backend.config import Settings
from crossexam_backend.retrieval.base import RetrievalIndex
from crossexam_backend.retrieval.mock_index import MockIndex
from crossexam_backend.retrieval.moss_client import (
    MossClientUnavailableError,
    MossIndex,
)

logger = logging.getLogger(__name__)


def _resolve_fixture_path(settings: Settings) -> Path:
    """Resolve the fixture path relative to the package root if not absolute."""
    candidate = Path(settings.mock_fixture_path)
    if candidate.is_absolute() and candidate.is_file():
        return candidate
    if candidate.is_file():
        return candidate
    # Fall back to the fixture shipped alongside the backend package.
    backend_root = Path(__file__).resolve().parents[2]
    return backend_root / settings.mock_fixture_path


def get_index(settings: Settings) -> RetrievalIndex:
    """Return the retrieval index appropriate for ``settings``.

    Selects :class:`MossIndex` when mocks are disabled and Moss credentials are
    present; otherwise (and as a safe fallback) returns a :class:`MockIndex`
    loaded from the JSON fixture.

    Args:
        settings: The resolved application settings.

    Returns:
        A ready-to-use :class:`RetrievalIndex`.
    """
    if not settings.use_mocks and settings.has_moss_credentials:
        try:
            logger.info("retrieval.factory selecting MossIndex")
            return MossIndex(settings)
        except MossClientUnavailableError:
            logger.warning(
                "retrieval.factory Moss requested but SDK unavailable; "
                "falling back to MockIndex"
            )

    fixture_path = _resolve_fixture_path(settings)
    logger.info("retrieval.factory selecting MockIndex fixture=%s", fixture_path)
    return MockIndex.from_fixture(fixture_path)
