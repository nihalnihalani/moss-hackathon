"""Moss-backed retrieval index.

Moss provides sub-10ms in-process semantic + keyword (hybrid) retrieval. This
module wraps the real Moss client *if it is installed and credentials are
present*; otherwise the factory never instantiates it. The import of the Moss
SDK is guarded so this module is always importable (e.g. in tests) even without
the dependency.

============================================================================
VERIFIED MOSS SDK SURFACE (researched 2026-06-04)
============================================================================
Sources:
  - PyPI:    https://pypi.org/project/inferedge-moss/
  - Docs:    https://moss-docs-seven.vercel.app/
  - GitHub:  https://github.com/usemoss/moss
  - Web:     https://www.moss.dev/

PACKAGE-NAME INCONSISTENCY (documented per task requirement):
  - The PyPI *distribution* is published as ``inferedge-moss``
    (``pip install inferedge-moss``).
  - The *import package* is reported as ``inferedge_moss`` by the docs site,
    BUT the GitHub README (usemoss/moss) shows ``from moss import MossClient``
    and ``pip install moss``. The npm sibling is ``@inferedge/moss``.
  - The naming is genuinely inconsistent across the upstream sources, so we try
    BOTH import names (``inferedge_moss`` first, then ``moss``) in
    :func:`_load_moss_module`. On-site this becomes a one-line change if a
    third name appears.

VERIFIED API SHAPE (high confidence — consistent across PyPI + docs + GitHub):
    from inferedge_moss import MossClient, QueryOptions   # or: from moss import ...

    client = MossClient("your_project_id", "your_project_key")  # POSITIONAL args
    await client.load_index("index-name")
    results = await client.query(
        "index-name",
        "query text",
        QueryOptions(top_k=3, alpha=0.6),   # alpha default 0.8; 0.0=keyword, 1.0=semantic
    )
    for doc in results.docs:                # results.docs is the ranked list
        doc.id, doc.text, doc.score         # per-document fields
    results.time_taken_ms                   # server-measured latency

COULD-NOT-VERIFY (kept defensive, locked by recorded test):
  - bbox / page / page_width / page_height: NONE of the public sources document
    geometry fields on a Moss document. ``DocumentInfo`` is documented as
    ``id`` + ``text`` + optional ``metadata`` (a dict). For PDF citations we
    therefore assume bbox/page live inside ``doc.metadata`` (or as top-level
    attributes if a future version adds them). :meth:`_to_citation` reads both
    locations defensively. The exact key names are an ASSUMPTION and are locked
    by ``tests/test_moss_adapter.py`` so a real-SDK swap is a fixture update,
    not a rewrite.
  - Whether ``query`` is sync or async: docs show ``await client.query(...)``,
    so we treat it as awaitable but still tolerate a sync return.

ALL real-SDK touch points are isolated behind small methods
(:func:`_load_moss_module`, :meth:`_make_client`, :meth:`_make_query_options`,
:meth:`_raw_query`) so adapting to the exact installed version touches exactly
those methods.
============================================================================
"""

from __future__ import annotations

import importlib
import logging
import time
from types import ModuleType
from typing import TYPE_CHECKING, Any

from crossexam_backend.config import Settings
from crossexam_backend.models import BBox, Chunk, Citation, RetrievalResult
from crossexam_backend.retrieval.base import RetrievalIndex

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Import-package names to try, in priority order. See the module docstring for
# why there are several: the upstream naming is inconsistent.
_MOSS_IMPORT_CANDIDATES: tuple[str, ...] = ("inferedge_moss", "moss")


class MossClientUnavailableError(RuntimeError):
    """Raised when a real Moss client is requested but cannot be created."""


class MossQueryError(RuntimeError):
    """Raised when a Moss query fails and the index is in *strict* mode.

    In *lenient* mode the failure is logged and an empty
    :class:`~crossexam_backend.models.RetrievalResult` is returned instead, so a
    single bad turn never crashes the voice loop.
    """


def _load_moss_module() -> ModuleType | None:
    """Import the Moss SDK if available, else return ``None``.

    Tries each name in :data:`_MOSS_IMPORT_CANDIDATES` (the distribution name is
    ``inferedge-moss`` but the import name is reported inconsistently upstream).

    Returns:
        The imported module, or ``None`` when no candidate is installed.
    """
    for name in _MOSS_IMPORT_CANDIDATES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        logger.info("moss SDK imported as %r", name)
        return module
    logger.debug(
        "moss SDK not installed (tried %s)", ", ".join(_MOSS_IMPORT_CANDIDATES)
    )
    return None


class MossIndex(RetrievalIndex):
    """Retrieval index backed by the real Moss service.

    Args:
        settings: Application settings carrying the Moss credentials and tuning.
        client: Optional pre-constructed Moss client (used for testing / DI).
            When provided, the real SDK is not imported.
        strict: When ``True`` (the default when real Moss credentials are
            present) a query failure raises :class:`MossQueryError` so a broken
            integration is *visible*. When ``False`` a failed query is logged
            and returns an empty result so the turn continues gracefully.

    Raises:
        MossClientUnavailableError: If no ``client`` is provided and the Moss
            SDK is not installed.
    """

    def __init__(
        self,
        settings: Settings,
        client: object | None = None,
        *,
        strict: bool | None = None,
    ) -> None:
        """Create a Moss-backed index (see the class docstring for args)."""
        self._settings = settings
        self._index_name = settings.moss_index_name
        # Default to strict whenever real credentials are configured: if the
        # operator wired up Moss, a broken query should be loud, not silent.
        self._strict = settings.has_moss_credentials if strict is None else strict
        self._module: ModuleType | None = None
        # Distinct documentIds observed so far. The Moss SDK does not document a
        # "list all documents in an index" call, so we MAINTAIN this set: it is
        # seeded from the loaded index when the SDK exposes a doc listing (see
        # :meth:`prewarm`) and otherwise grows as query results stream in. This
        # backs the :attr:`document_ids` property that multi-hop anchor-expansion
        # reads (multihop.py) — without it, cross-document expansion silently
        # finds no other docs to reach into.
        self._seen_document_ids: set[str] = set()

        if client is not None:
            self._client = client
        else:
            module = _load_moss_module()
            if module is None:
                raise MossClientUnavailableError(
                    "moss SDK is not installed; cannot create a real MossIndex. "
                    f"Install with: pip install inferedge-moss (tried imports: "
                    f"{', '.join(_MOSS_IMPORT_CANDIDATES)})"
                )
            self._module = module
            self._client = self._make_client(module, settings)
        logger.info(
            "moss_index.init index=%s strict=%s", self._index_name, self._strict
        )

    @property
    def document_ids(self) -> list[str]:
        """Distinct document ids known to this index, in sorted order.

        Multi-hop anchor-expansion (``retrieval/multihop.py``) reads this via
        ``getattr(index, "document_ids", [])`` to reach across OTHER documents
        for a cross-document counter. The Moss SDK does not document an
        enumerate-all-documents call, so this returns the ids OBSERVED so far
        (seeded from the loaded index in :meth:`prewarm` when possible, plus any
        seen as query results stream). It is therefore best-effort: it will be
        complete once the relevant docs have been touched, which is sufficient
        for the expansion heuristic. See the module docstring (COULD-NOT-VERIFY).
        """
        return sorted(self._seen_document_ids)

    def _record_document_ids(self, citations: Sequence[Citation]) -> None:
        """Add each citation's documentId to the observed-id set (best-effort)."""
        for c in citations:
            if c.documentId:
                self._seen_document_ids.add(c.documentId)

    # -- real-SDK touch points (the ONLY places that know the SDK shape) -----
    @staticmethod
    def _make_client(module: ModuleType, settings: Settings) -> object:
        """Construct the real ``MossClient`` (verified positional-arg surface).

        Verified: ``MossClient("project_id", "project_key")``. Isolated so the
        constructor surface lives in exactly one place.
        """
        client_cls = getattr(module, "MossClient", None) or getattr(
            module, "Client", None
        )
        if client_cls is None:
            raise MossClientUnavailableError(
                "moss SDK exposes neither MossClient nor Client"
            )
        return client_cls(settings.moss_project_id, settings.moss_project_key)

    def _make_query_options(
        self, top_k: int, alpha: float, *, doc_ids: list[str] | None = None
    ) -> Any | None:  # noqa: ANN401
        """Build a ``QueryOptions(top_k=, alpha=)`` if the SDK exposes it.

        Verified surface: ``QueryOptions(top_k=3, alpha=0.6)``. Returns ``None``
        when no module is loaded (DI/test path) so the caller can fall back to
        keyword args on ``query``. Typed ``Any`` because the SDK class is not
        importable at type-check time (optional dependency).

        DOC FILTER (ASSUMED, not verified upstream): when ``doc_ids`` is given we
        attempt to add a server-side candidate filter so Moss only ranks the
        allowed documents (a real filter, not over-fetch). The exact kwarg name
        is undocumented, so we try ``filter`` then ``where`` and DROP the filter
        if neither is accepted by ``QueryOptions`` — the caller then falls back
        to over-fetch + post-filter. The filter shape assumes a documentId-in
        predicate; adjust here once the SDK's filter surface is confirmed.
        """
        if self._module is None:
            return None
        options_cls = getattr(self._module, "QueryOptions", None)
        if options_cls is None:
            return None
        if not doc_ids:
            return options_cls(top_k=top_k, alpha=alpha)
        # Best-effort server-side filter. Try documented-ish kwarg names; on a
        # TypeError (unknown kwarg) fall back to an unfiltered options object so
        # query_multi can over-fetch + post-filter instead.
        predicate = {"documentId": {"$in": list(doc_ids)}}
        for kw in ("filter", "where", "metadata_filter"):
            try:
                return options_cls(top_k=top_k, alpha=alpha, **{kw: predicate})
            except TypeError:
                continue
        return options_cls(top_k=top_k, alpha=alpha)

    def _supports_server_filter(self) -> bool:
        """Whether ``QueryOptions`` accepts a doc filter kwarg (assumed surface).

        Used by :meth:`query_multi` to decide between a real server-side filter
        and the over-fetch + post-filter fallback. Returns ``False`` whenever no
        module is loaded (DI/test path) or the SDK does not accept any of the
        candidate filter kwargs, so behaviour is unchanged on the verified path.
        """
        if self._module is None:
            return False
        options_cls = getattr(self._module, "QueryOptions", None)
        if options_cls is None:
            return False
        predicate = {"documentId": {"$in": ["__probe__"]}}
        for kw in ("filter", "where", "metadata_filter"):
            try:
                options_cls(top_k=1, alpha=0.5, **{kw: predicate})
                return True
            except TypeError:
                continue
        return False

    async def _raw_query(
        self, text: str, top_k: int, alpha: float, *, doc_ids: list[str] | None = None
    ) -> tuple[Sequence[object], float | None]:
        """Call the underlying Moss client; return ``(docs, server_latency_ms)``.

        Verified surface:
            ``results = await client.query(index, text, QueryOptions(...))``
            then iterate ``results.docs`` and read ``results.time_taken_ms``.

        Isolated so the SDK-specific call lives in exactly one place. Tolerates
        a sync return, a ``.docs``/``.matches`` wrapper, or a bare list. When
        ``doc_ids`` is given AND the SDK accepts a filter kwarg, the candidate
        filter is pushed into ``QueryOptions`` (see :meth:`_make_query_options`).
        """
        query_fn = getattr(self._client, "query", None) or getattr(
            self._client, "search", None
        )
        if not callable(query_fn):
            raise MossClientUnavailableError(
                "Moss client exposes neither query() nor search()"
            )

        options = self._make_query_options(top_k, alpha, doc_ids=doc_ids)
        if options is not None:
            result = query_fn(self._index_name, text, options)
        else:
            # DI/test path or older SDK: fall back to keyword args.
            result = query_fn(
                self._index_name, text, top_k=top_k, alpha=alpha
            )

        if hasattr(result, "__await__"):
            result = await result

        server_latency = self._server_latency_ms(result)
        # Verified wrapper attr is ``docs``; tolerate ``matches`` and bare lists.
        docs = getattr(result, "docs", None)
        if docs is None:
            docs = getattr(result, "matches", result)
        return list(docs), server_latency

    @staticmethod
    def _server_latency_ms(result: object) -> float | None:
        """Return Moss's server-measured ``time_taken_ms`` if present."""
        value = getattr(result, "time_taken_ms", None)
        if value is None and isinstance(result, dict):
            value = result.get("time_taken_ms")
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # -- response mapping (pure, fully unit-testable) -----------------------
    # ANN401: a Moss document is a genuinely dynamic payload (dict- or
    # attribute-style, fields vary by SDK version), so this accessor returns
    # ``Any`` on purpose. Callers immediately coerce to str/int/float.
    @staticmethod
    def _get(obj: Any, key: str, default: Any = None) -> Any:  # noqa: ANN401
        """Read ``key`` from a dict-style or attribute-style payload."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @classmethod
    def _to_citation(cls, match: object) -> Citation:
        """Convert one raw Moss document into a :class:`Citation`.

        Tolerant of attribute-style and dict-style payloads. bbox/page geometry
        is UNVERIFIED upstream (see module docstring): we look for it as a
        top-level field first, then inside ``metadata``. The points + page dims
        are carried straight through to :class:`BBox`.
        """
        get = cls._get
        meta = get(match, "metadata", {}) or {}

        # bbox may be top-level or nested in metadata; accept either.
        bbox_raw = get(match, "bbox", None)
        if bbox_raw is None:
            bbox_raw = get(meta, "bbox", {}) or {}

        page = int(
            get(match, "page", get(meta, "page", get(bbox_raw, "page", 1)))
        )
        bbox = BBox(
            page=page,
            x0=float(get(bbox_raw, "x0", 0.0)),
            y0=float(get(bbox_raw, "y0", 0.0)),
            x1=float(get(bbox_raw, "x1", 0.0)),
            y1=float(get(bbox_raw, "y1", 0.0)),
            page_width=float(get(bbox_raw, "page_width", 612.0)),
            page_height=float(get(bbox_raw, "page_height", 792.0)),
        )
        # Depth-v2 multi-doc / scanned-source metadata (optional; defaults keep
        # single-doc back-compat). documentId may be top-level or in metadata.
        document_id = get(match, "documentId", get(meta, "documentId", None))
        document_title = get(match, "documentTitle", get(meta, "documentTitle", None))
        scanned = bool(get(match, "scanned", get(meta, "scanned", False)))

        chunk_kwargs: dict[str, Any] = {
            "id": str(get(match, "id", get(meta, "id", "unknown"))),
            "text": str(get(match, "text", get(meta, "text", ""))),
            "page": page,
            "bbox": bbox,
            "confidence": float(
                get(match, "confidence", get(meta, "confidence", 1.0))
            ),
            "documentTitle": document_title,
            "scanned": scanned,
        }
        if document_id is not None:
            chunk_kwargs["documentId"] = str(document_id)
        chunk = Chunk(**chunk_kwargs)
        raw_score = float(get(match, "score", 0.0))
        # Clamp into [0, 1] in case Moss returns distances/logits.
        score = max(0.0, min(raw_score, 1.0))
        return Citation(
            chunk=chunk,
            score=score,
            documentId=chunk.documentId,
            documentTitle=chunk.documentTitle,
            scanned=chunk.scanned,
        )

    # -- lifecycle ----------------------------------------------------------
    async def prewarm(self) -> None:
        """Open / warm the Moss index ahead of the first query.

        Verified surface is ``await client.load_index(name)``; we also accept a
        ``warm`` method for forward/backward compatibility. Prewarm never
        crashes the worker — a warm failure degrades to a cold first query.
        """
        warm = (
            getattr(self._client, "load_index", None)
            or getattr(self._client, "warm", None)
        )
        if callable(warm):
            try:
                result = warm(self._index_name)
                if hasattr(result, "__await__"):
                    result = await result
            except Exception:  # noqa: BLE001 - prewarm must never crash worker
                logger.exception("moss_index.prewarm failed; continuing")
            else:
                # Best-effort: seed document_ids from the loaded index when the
                # SDK exposes a doc list on the load result (ASSUMED — the SDK
                # does not document an enumerate-all call; see the property
                # docstring). Any shape mismatch is ignored; ids still accrue
                # from query results.
                self._seed_document_ids(result)

    def _seed_document_ids(self, loaded: object) -> None:
        """Seed observed-doc ids from a loaded-index handle, if it lists docs.

        Looks for a ``documents``/``docs`` collection on the load result and
        records each entry's documentId (top-level or in ``metadata``). Purely
        best-effort and exception-safe: this is an ASSUMED SDK surface.
        """
        if loaded is None:
            return
        try:
            docs = self._get(loaded, "documents", None)
            if docs is None:
                docs = self._get(loaded, "docs", None)
            if not docs:
                return
            for d in docs:
                meta = self._get(d, "metadata", {}) or {}
                doc_id = self._get(d, "documentId", self._get(meta, "documentId", None))
                if doc_id:
                    self._seen_document_ids.add(str(doc_id))
        except Exception:  # noqa: BLE001 - seeding is best-effort only
            logger.debug("moss_index.seed_document_ids skipped (unrecognized shape)")

    async def query(
        self,
        text: str,
        top_k: int = 5,
        alpha: float = 0.8,
    ) -> RetrievalResult:
        """Query Moss and adapt the response (see base class).

        In *strict* mode a query failure raises :class:`MossQueryError`. In
        *lenient* mode it is logged and an empty result is returned so the turn
        continues. ``latency_ms`` prefers Moss's server-measured
        ``time_taken_ms`` and falls back to a wall-clock measurement.
        """
        start = time.perf_counter()
        try:
            docs, server_latency = await self._raw_query(text, top_k=top_k, alpha=alpha)
        except MossClientUnavailableError:
            # A structurally-broken client is always loud, regardless of mode.
            raise
        except Exception as exc:  # noqa: BLE001 - mode decides raise vs degrade
            logger.exception(
                "moss_index.query failed text=%r strict=%s", text, self._strict
            )
            if self._strict:
                raise MossQueryError(
                    f"Moss query failed for index {self._index_name!r}: {exc}"
                ) from exc
            latency_ms = (time.perf_counter() - start) * 1000.0
            return RetrievalResult(query=text, citations=[], latency_ms=latency_ms)

        citations = [self._to_citation(m) for m in list(docs)[:top_k]]
        # Grow the observed-doc set so document_ids (and thus cross-document
        # anchor-expansion) reflects the docs Moss has actually returned.
        self._record_document_ids(citations)

        # Prefer Moss's server-measured latency; fall back to wall-clock.
        wall_ms = (time.perf_counter() - start) * 1000.0
        latency_ms = server_latency if server_latency is not None else wall_ms
        logger.debug(
            "moss_index.query text=%r hits=%d latency_ms=%.3f",
            text,
            len(citations),
            latency_ms,
        )
        return RetrievalResult(query=text, citations=citations, latency_ms=latency_ms)

    async def query_multi(
        self,
        text: str,
        top_k: int = 5,
        alpha: float = 0.8,
        *,
        doc_ids: list[str] | None = None,
    ) -> RetrievalResult:
        """Query Moss across one or more documents (see base class).

        When the SDK's ``QueryOptions`` accepts a candidate-filter kwarg (ASSUMED
        surface — see :meth:`_make_query_options`), the documentId allow-list is
        pushed SERVER-SIDE so Moss only ranks the allowed docs (a real filter).
        When it does not (the verified-only path, DI/test, or older SDK), we fall
        back to over-fetching and filtering the returned citations by
        ``documentId`` client-side. With ``doc_ids=None`` it is identical to
        :meth:`query`.
        """
        if doc_ids is None:
            return await self.query(text, top_k=top_k, alpha=alpha)
        allow = set(doc_ids)

        if self._supports_server_filter():
            # Real server-side candidate filter: ask Moss for exactly top_k from
            # the allow-set. Still post-filter defensively in case the assumed
            # filter shape is a no-op on some SDK version.
            start = time.perf_counter()
            try:
                docs, server_latency = await self._raw_query(
                    text, top_k=top_k, alpha=alpha, doc_ids=list(doc_ids)
                )
            except MossClientUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001 - mode decides raise vs degrade
                logger.exception(
                    "moss_index.query_multi failed text=%r strict=%s",
                    text,
                    self._strict,
                )
                if self._strict:
                    raise MossQueryError(
                        f"Moss query failed for index {self._index_name!r}: {exc}"
                    ) from exc
                latency_ms = (time.perf_counter() - start) * 1000.0
                return RetrievalResult(query=text, citations=[], latency_ms=latency_ms)
            citations = [self._to_citation(m) for m in list(docs)]
            self._record_document_ids(citations)
            filtered = [c for c in citations if c.documentId in allow][:top_k]
            wall_ms = (time.perf_counter() - start) * 1000.0
            latency_ms = server_latency if server_latency is not None else wall_ms
            return RetrievalResult(
                query=text, citations=filtered, latency_ms=latency_ms
            )

        # Fallback (verified-only path): over-fetch so post-filtering still
        # yields up to top_k from the allow-set.
        wide = await self.query(text, top_k=max(top_k * 4, top_k), alpha=alpha)
        filtered = [c for c in wide.citations if c.documentId in allow][:top_k]
        return RetrievalResult(
            query=text, citations=filtered, latency_ms=wide.latency_ms
        )

    async def aclose(self) -> None:
        """Close the underlying client if it exposes a ``close`` method."""
        close = getattr(self._client, "close", None)
        if callable(close):
            try:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # noqa: BLE001 - close must never crash shutdown
                logger.exception("moss_index.close failed")
