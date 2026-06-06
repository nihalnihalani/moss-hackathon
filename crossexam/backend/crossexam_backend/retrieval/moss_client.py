"""Moss-backed retrieval index.

Moss provides sub-10ms in-process semantic retrieval. This module wraps the real
Moss client *if it is installed and credentials are present*; otherwise the
factory will never instantiate it. The import of the Moss SDK is guarded so this
module is always importable (e.g. in tests) even without the dependency.

NOTE: The exact Moss SDK surface may differ between versions; the call site is
isolated in :meth:`MossIndex._raw_query` so it is the single place to adapt.
"""

from __future__ import annotations

import importlib
import logging
import time
from typing import Any

from crossexam_backend.config import Settings
from crossexam_backend.models import BBox, Chunk, Citation, RetrievalResult
from crossexam_backend.retrieval.base import RetrievalIndex

logger = logging.getLogger(__name__)


def _load_moss_module() -> Any | None:
    """Import the Moss SDK if available, else return ``None``."""
    try:
        return importlib.import_module("moss")
    except ModuleNotFoundError:
        logger.debug("moss SDK not installed")
        return None


class MossClientUnavailableError(RuntimeError):
    """Raised when a real Moss client is requested but cannot be created."""


class MossIndex(RetrievalIndex):
    """Retrieval index backed by the real Moss service."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        """Create a Moss-backed index.

        Args:
            settings: Application settings carrying the Moss credentials.
            client: Optional pre-constructed Moss client (used for testing).

        Raises:
            MossClientUnavailableError: If no client is provided and the Moss
                SDK is not installed.
        """
        self._settings = settings
        self._index_name = settings.moss_index_name
        if client is not None:
            self._client = client
        else:
            moss = _load_moss_module()
            if moss is None:
                raise MossClientUnavailableError(
                    "moss SDK is not installed; cannot create a real MossIndex"
                )
            # The constructor name is isolated here so it is trivial to adapt
            # to the exact SDK version in use.
            self._client = moss.Client(  # type: ignore[attr-defined]
                project_id=settings.moss_project_id,
                project_key=settings.moss_project_key,
            )
        logger.info("moss_index.init index=%s", self._index_name)

    async def prewarm(self) -> None:
        """Open / warm the Moss index ahead of the first query."""
        warm = getattr(self._client, "warm", None) or getattr(
            self._client, "load_index", None
        )
        if callable(warm):
            try:
                result = warm(self._index_name)
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # noqa: BLE001 - prewarm must never crash worker
                logger.exception("moss_index.prewarm failed; continuing")

    async def _raw_query(self, text: str, top_k: int, alpha: float) -> list[Any]:
        """Call the underlying Moss client and return raw match objects.

        Isolated so the SDK-specific surface lives in exactly one place.
        """
        query_fn = getattr(self._client, "query", None) or getattr(
            self._client, "search", None
        )
        if not callable(query_fn):
            raise MossClientUnavailableError(
                "Moss client exposes neither query() nor search()"
            )
        result = query_fn(
            index=self._index_name,
            text=text,
            top_k=top_k,
            alpha=alpha,
        )
        if hasattr(result, "__await__"):
            result = await result
        # Accept either a bare list or a wrapper with `.matches`.
        matches = getattr(result, "matches", result)
        return list(matches)

    @staticmethod
    def _to_citation(match: Any) -> Citation:
        """Convert a single raw Moss match into a :class:`Citation`.

        Tolerant of both attribute-style and dict-style match payloads.
        """

        def get(obj: Any, key: str, default: Any = None) -> Any:
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        meta = get(match, "metadata", {}) or {}
        bbox_raw = get(match, "bbox", None) or get(meta, "bbox", {}) or {}
        page = int(get(match, "page", get(meta, "page", get(bbox_raw, "page", 1))))

        bbox = BBox(
            page=page,
            x0=float(get(bbox_raw, "x0", 0.0)),
            y0=float(get(bbox_raw, "y0", 0.0)),
            x1=float(get(bbox_raw, "x1", 0.0)),
            y1=float(get(bbox_raw, "y1", 0.0)),
            page_width=float(get(bbox_raw, "page_width", 612.0)),
            page_height=float(get(bbox_raw, "page_height", 792.0)),
        )
        chunk = Chunk(
            id=str(get(match, "id", get(meta, "id", "unknown"))),
            text=str(get(match, "text", get(meta, "text", ""))),
            page=page,
            bbox=bbox,
            confidence=float(get(match, "confidence", get(meta, "confidence", 1.0))),
        )
        raw_score = float(get(match, "score", 0.0))
        # Clamp into [0, 1] in case Moss returns distances/logits.
        score = max(0.0, min(raw_score, 1.0))
        return Citation(chunk=chunk, score=score)

    async def query(
        self,
        text: str,
        top_k: int = 5,
        alpha: float = 0.8,
    ) -> RetrievalResult:
        """Query Moss and adapt the response (see base class)."""
        start = time.perf_counter()
        try:
            matches = await self._raw_query(text, top_k=top_k, alpha=alpha)
        except MossClientUnavailableError:
            raise
        except Exception:  # noqa: BLE001 - surface as empty, never crash the turn
            logger.exception("moss_index.query failed text=%r", text)
            latency_ms = (time.perf_counter() - start) * 1000.0
            return RetrievalResult(query=text, citations=[], latency_ms=latency_ms)

        citations = [self._to_citation(m) for m in matches[:top_k]]
        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.debug(
            "moss_index.query text=%r hits=%d latency_ms=%.3f",
            text,
            len(citations),
            latency_ms,
        )
        return RetrievalResult(query=text, citations=citations, latency_ms=latency_ms)

    async def aclose(self) -> None:
        """Close the underlying client if it exposes a ``close`` method."""
        close = getattr(self._client, "close", None)
        if callable(close):
            try:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # noqa: BLE001
                logger.exception("moss_index.close failed")
