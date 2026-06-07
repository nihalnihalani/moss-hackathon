"""Factory that selects the concrete retrieval index for the current config."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from crossexam_backend.config import Settings
from crossexam_backend.models import RetrievalResult
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


class MossWithMockFallback(RetrievalIndex):
    """Moss primary index with a local fixture fallback.

    This keeps live mode honest during demos: when Moss credentials are present
    but local model loading or cloud query fallback fails, the API/worker still
    answer from the same on-disk fixture that `/documents` writes in degraded
    mode. The fixture is reloaded when its mtime changes, so a worker process can
    pick up API-side upload fallbacks without restarting.
    """

    def __init__(self, primary: MossIndex, settings: Settings) -> None:
        """Create the fallback wrapper around a real Moss primary index."""
        self._primary = primary
        self._settings = settings
        self._fallback: MockIndex | None = None
        self._fixture_mtime_ns: int | None = None
        self._prefer_fallback = False

    @property
    def document_ids(self) -> list[str]:
        """Distinct document IDs from the active index."""
        if not self._prefer_fallback:
            ids = getattr(self._primary, "document_ids", [])
            if ids:
                return list(ids)
        return self._load_fallback().document_ids

    def _load_fallback(self) -> MockIndex:
        """Load or reload the mock fixture when it changes on disk."""
        fixture_path = _resolve_fixture_path(self._settings)
        try:
            mtime_ns = fixture_path.stat().st_mtime_ns
        except FileNotFoundError:
            if self._fallback is None:
                self._fallback = MockIndex([])
                self._fixture_mtime_ns = None
            return self._fallback

        if self._fallback is None or self._fixture_mtime_ns != mtime_ns:
            self._fallback = MockIndex.from_fixture(fixture_path)
            self._fixture_mtime_ns = mtime_ns
            logger.info(
                "retrieval.factory loaded MockIndex fallback fixture=%s",
                fixture_path,
            )
        return self._fallback

    async def prewarm(self) -> None:
        """Prewarm Moss, then prepare the local fallback."""
        try:
            await asyncio.wait_for(
                self._primary.prewarm(),
                timeout=self._settings.moss_load_timeout_s,
            )
        except TimeoutError:
            self._prefer_fallback = True
            logger.warning(
                "retrieval.factory Moss prewarm timed out after %.1fs; "
                "using MockIndex fallback",
                self._settings.moss_load_timeout_s,
            )
        except Exception:  # noqa: BLE001 - fallback keeps the app serving
            self._prefer_fallback = True
            logger.exception(
                "retrieval.factory Moss prewarm failed; using MockIndex fallback"
            )
        self._prefer_fallback = not self._primary.is_loaded
        if self._prefer_fallback:
            logger.warning(
                "retrieval.factory Moss prewarm failed; using MockIndex fallback"
            )
        await self._load_fallback().prewarm()

    async def query(
        self,
        text: str,
        top_k: int = 5,
        alpha: float = 0.8,
    ) -> RetrievalResult:
        """Query Moss first, then the local fallback on failure."""
        if not self._prefer_fallback:
            try:
                return await self._primary.query(text, top_k=top_k, alpha=alpha)
            except Exception:  # noqa: BLE001 - degraded mode should keep serving
                self._prefer_fallback = True
                logger.exception(
                    "retrieval.factory Moss query failed; using MockIndex fallback"
                )
        return await self._load_fallback().query(text, top_k=top_k, alpha=alpha)

    async def query_multi(
        self,
        text: str,
        top_k: int = 5,
        alpha: float = 0.8,
        *,
        doc_ids: list[str] | None = None,
    ) -> RetrievalResult:
        """Query across documents, falling back to the local fixture."""
        if not self._prefer_fallback:
            try:
                return await self._primary.query_multi(
                    text, top_k=top_k, alpha=alpha, doc_ids=doc_ids
                )
            except Exception:  # noqa: BLE001 - degraded mode should keep serving
                self._prefer_fallback = True
                logger.exception(
                    "retrieval.factory Moss query_multi failed; using MockIndex fallback"
                )
        return await self._load_fallback().query_multi(
            text, top_k=top_k, alpha=alpha, doc_ids=doc_ids
        )


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
            return MossWithMockFallback(MossIndex(settings), settings)
        except MossClientUnavailableError:
            logger.warning(
                "retrieval.factory Moss requested but SDK unavailable; "
                "falling back to MockIndex"
            )

    fixture_path = _resolve_fixture_path(settings)
    logger.info("retrieval.factory selecting MockIndex fixture=%s", fixture_path)
    return MockIndex.from_fixture(fixture_path)
