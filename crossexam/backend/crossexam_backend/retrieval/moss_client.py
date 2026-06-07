"""Moss-backed retrieval index.

Moss provides sub-10ms in-process semantic + keyword (hybrid) retrieval. This
module wraps the real Moss client *if it is installed and credentials are
present*; otherwise the factory never instantiates it. The import of the Moss
SDK is guarded so this module is always importable (e.g. in tests) even without
the dependency.

============================================================================
VERIFIED MOSS SDK SURFACE (moss 1.4.0; API-compatible with legacy inferedge_moss 1.0.0b19)
============================================================================
Sources:
  - PyPI:    https://pypi.org/project/inferedge-moss/
  - Docs:    https://moss-docs-seven.vercel.app/
  - GitHub:  https://github.com/usemoss/moss
  - Web:     https://www.moss.dev/

PACKAGE NAME (verified by import introspection):
  - The current PyPI SDK is ``moss`` (>=1.4.0), import name ``moss``. The legacy
    ``inferedge_moss`` (1.0.0b19) distribution is gone from PyPI and is retained
    only as an import fallback. The candidates order now prefers ``moss``.

VERIFIED API SHAPE (introspected from inferedge_moss 1.0.0b19):

    from inferedge_moss import (
        MossClient, QueryOptions, GetDocumentsOptions,
        SearchResult, QueryResultDocumentInfo, DocumentInfo,
    )

    client = MossClient("project_id", "project_key")  # POSITIONAL args

    # load_index: downloads the index into memory for ~1-10ms in-process queries.
    # Without it queries fall back to the cloud API (~100-500ms).
    status: str = await client.load_index(
        name,
        auto_refresh=False,           # bool — poll for updates
        polling_interval_in_seconds=600,  # int — polling cadence
    )
    # Returns a str (status), NOT a document list.

    # query: SearchResult.docs is list[QueryResultDocumentInfo]
    results: SearchResult = await client.query(
        name, query_text,
        QueryOptions(top_k=5, alpha=0.8, filter=...),
    )
    for doc in results.docs:          # QueryResultDocumentInfo
        doc.id, doc.text, doc.score   # str, str, float
        doc.metadata                  # dict[str, str]  — ALL VALUES ARE STRINGS
    results.time_taken_ms             # float — server-measured latency

    # get_docs: REAL enumeration of all documents in an index.
    docs: list[DocumentInfo] = await client.get_docs(
        name,
        GetDocumentsOptions(doc_ids=None),  # None -> all docs
    )
    for d in docs:
        d.id, d.text, d.metadata      # DocumentInfo attrs

    QueryOptions fields: top_k, alpha, embedding
    GetDocumentsOptions fields: doc_ids (list[str] | None)
    SearchResult fields: docs, query, index_name, time_taken_ms
    QueryResultDocumentInfo fields: id, text, metadata, score
    DocumentInfo fields: id, text, metadata, embedding

VERIFIED METADATA CONTRACT (confirmed from the running pipeline + SDK):
  - Moss document metadata values are ALL STRINGS. The pipeline writes the
    shared contract into ``doc.metadata`` with every value string-encoded:
    ``documentId``, ``documentTitle`` (optional), ``scanned`` ("true"/"false"),
    ``page`` (e.g. "3"), ``confidence`` (e.g. "0.97"), and the geometry
    (``bbox``, ``words``, optional ``quads``) as JSON-ENCODED STRINGS.
  - :meth:`_to_citation` is DUAL-TOLERANT: it parses BOTH the new string
    contract (real Moss) AND the older nested-dict form used by DI / test
    fakes. JSON-string geometry is ``json.loads``-decoded (malformed value
    falls back to a safe default so a single bad turn never crashes the voice
    loop); page/confidence/scanned are coerced tolerantly.

VERIFIED FILTER GRAMMAR (from ``query`` docstring in the SDK):
  - The public docs show the ``filter`` kwarg passed to ``client.query``:
    ``client.query(name, text, QueryOptions(...), filter=predicate)``.
  - Older/introspected SDK builds also accept ``filter`` on ``QueryOptions``:
      ``{"$and": [{"field": "f", "condition": {"$eq": "v"}}, ...]}``
    Confirmed operators: ``$and``, ``$eq``, ``$lt``.
  - ``$or`` is NOT confirmed in the SDK docstring (only ``$and``/``$eq``/
    ``$lt`` are shown). We therefore fall back to over-fetch + client-side
    post-filter if the server raises on our ``$or``-based compound filter.
  - The ``filter`` kwarg is ``filter=`` (singular, verified).

ALL real-SDK touch points are isolated behind small methods
(:func:`_load_moss_module`, :meth:`_make_client`, :meth:`_make_query_options`,
:meth:`_raw_query`, :meth:`prewarm`, :meth:`refresh_document_ids`) so adapting
to the exact installed version touches exactly those methods.
============================================================================
"""

from __future__ import annotations

import importlib
import json
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

# Import-package names to try, in priority order. The current PyPI distribution
# is "moss" (>=1.4.0); "inferedge_moss" is the legacy 1.0.0b19 distribution (now
# gone from PyPI), kept only as a fallback.
_MOSS_IMPORT_CANDIDATES: tuple[str, ...] = ("moss", "inferedge_moss")


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

    Tries each name in :data:`_MOSS_IMPORT_CANDIDATES`. The current SDK is
    ``moss`` (>=1.4.0); legacy ``inferedge_moss`` (1.0.0b19) is the fallback.

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
        self._loaded = False
        self._last_prewarm_error: Exception | None = None
        # Distinct documentIds observed so far. Seeded in prewarm() via the
        # real get_docs() enumeration, then grown by query results. Backs the
        # document_ids property that multi-hop anchor-expansion reads.
        self._seen_document_ids: set[str] = set()

        if client is not None:
            self._client = client
        else:
            module = _load_moss_module()
            if module is None:
                raise MossClientUnavailableError(
                    "moss SDK is not installed; cannot create a real MossIndex. "
                    f"Install with: pip install 'crossexam-backend[moss]' (tried: "
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
        ``getattr(index, "document_ids", [])`` to reach across OTHER documents.
        Seeded by :meth:`prewarm` via the real ``get_docs`` enumeration, then
        grown by query results as a best-effort fallback. Call
        :meth:`refresh_document_ids` to re-enumerate at any point.
        """
        return sorted(self._seen_document_ids)

    @property
    def is_loaded(self) -> bool:
        """Whether the last ``load_index`` prewarm completed successfully."""
        return self._loaded

    @property
    def last_prewarm_error(self) -> Exception | None:
        """The last ``load_index`` failure captured during prewarm, if any."""
        return self._last_prewarm_error

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

    @staticmethod
    def _build_doc_filter(doc_ids: list[str]) -> dict[str, Any]:
        """Build the Moss filter predicate for a documentId allow-list.

        Filter grammar (from SDK query docstring):
          - ``$and`` / ``$eq`` / ``$lt`` are confirmed operators.
          - ``$or`` is NOT confirmed, so multi-id filters use the same
            ``$and``-wrapped ``$or`` shape, but callers must tolerate a server
            rejection and fall back to client-side post-filter.

          single id:  ``{"field": "documentId", "condition": {"$eq": id}}``
          multi  ids: ``{"$and": [{"$or": [<per-id $eq clauses>]}]}``
        """
        clauses = [
            {"field": "documentId", "condition": {"$eq": i}} for i in doc_ids
        ]
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": [{"$or": clauses}]}

    def _make_query_options(
        self, top_k: int, alpha: float, *, doc_ids: list[str] | None = None
    ) -> Any | None:  # noqa: ANN401
        """Build ``QueryOptions(top_k=, alpha=)`` if the SDK exposes it.

        Verified fields: ``top_k``, ``alpha``, ``embedding``. The current public
        docs put metadata filtering on ``client.query(..., filter=predicate)``;
        ``QueryOptions(filter=...)`` is retained as an older-SDK fallback.
        Returns ``None`` when no module is loaded (DI/test path) so the caller
        falls back to keyword args on ``query``. Typed ``Any`` because the SDK
        class is not importable at type-check time (optional dependency).

        When ``doc_ids`` is given, attaches the documentId filter
        (see :meth:`_build_doc_filter`). Falls back to an unfiltered options
        object on ``TypeError`` (older SDK without ``filter``).
        """
        if self._module is None:
            return None
        options_cls = getattr(self._module, "QueryOptions", None)
        if options_cls is None:
            return None
        if not doc_ids:
            return options_cls(top_k=top_k, alpha=alpha)
        predicate = self._build_doc_filter(list(doc_ids))
        try:
            return options_cls(top_k=top_k, alpha=alpha, filter=predicate)
        except TypeError:
            # Older SDK or bad filter kwarg: degrade to unfiltered.
            return options_cls(top_k=top_k, alpha=alpha)

    def _supports_server_filter(self) -> bool:
        """Whether ``QueryOptions`` accepts the ``filter`` kwarg.

        Used by :meth:`query_multi` to decide between attempting a server-side
        filter (latency optimisation) and the over-fetch + post-filter fallback.
        Returns ``False`` when no module is loaded (DI/test path).
        """
        if self._module is None:
            return False
        options_cls = getattr(self._module, "QueryOptions", None)
        if options_cls is None:
            return False
        predicate = self._build_doc_filter(["__probe__"])
        try:
            options_cls(top_k=1, alpha=0.5, filter=predicate)
            return True
        except TypeError:
            return False

    async def _raw_query(
        self, text: str, top_k: int, alpha: float, *, doc_ids: list[str] | None = None
    ) -> tuple[Sequence[object], float | None]:
        """Call the underlying Moss client; return ``(docs, server_latency_ms)``.

        Verified surface (inferedge_moss 1.0.0b19):
            ``result: SearchResult = await client.query(name, text, QueryOptions(...))``
            public docs show metadata filtering as
            ``client.query(name, text, QueryOptions(...), filter=predicate)``
            ``result.docs`` — list of ``QueryResultDocumentInfo``
            ``result.time_taken_ms`` — float

        Per-doc fields on ``QueryResultDocumentInfo``: id, text, metadata, score.
        Tolerates a sync return, a ``.docs``/``.matches`` wrapper, or a bare list.
        """
        query_fn = getattr(self._client, "query", None) or getattr(
            self._client, "search", None
        )
        if not callable(query_fn):
            raise MossClientUnavailableError(
                "Moss client exposes neither query() nor search()"
            )

        predicate = self._build_doc_filter(list(doc_ids)) if doc_ids else None
        # Prefer the public SDK docs path for metadata filtering:
        # query(index, text, QueryOptions(...), filter=predicate). Keep the
        # older/introspected QueryOptions(filter=...) path as a fallback.
        options = self._make_query_options(top_k, alpha)
        if options is not None:
            if predicate is not None:
                try:
                    result = query_fn(self._index_name, text, options, filter=predicate)
                except TypeError:
                    if self._supports_server_filter():
                        options = self._make_query_options(
                            top_k, alpha, doc_ids=doc_ids
                        )
                        result = query_fn(self._index_name, text, options)
                    else:
                        raise
            else:
                result = query_fn(self._index_name, text, options)
        else:
            # DI/test path or older SDK: fall back to keyword args.
            if predicate is not None:
                result = query_fn(
                    self._index_name,
                    text,
                    top_k=top_k,
                    alpha=alpha,
                    filter=predicate,
                )
            else:
                result = query_fn(self._index_name, text, top_k=top_k, alpha=alpha)

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

    # -- dual-tolerant coercion (real string contract OR nested dicts) ------
    # The real Moss SDK returns ALL metadata values as STRINGS (geometry is
    # JSON-encoded); DI/test fakes and older shapes use native dicts/ints. Each
    # coercer accepts BOTH and never raises on a malformed value — a single bad
    # turn must never crash the voice loop.
    @staticmethod
    def _coerce_json(value: Any, default: Any) -> Any:  # noqa: ANN401
        """Decode a JSON-string container, pass through a dict/list, else default.

        ``bbox``/``words``/``quads`` arrive as JSON strings on the real SDK and
        as native dict/list on the test/DI path. A malformed JSON string falls
        back to ``default`` (never raises) so geometry parsing is non-fatal.
        """
        if value is None:
            return default
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                logger.debug("moss_index.coerce_json malformed value; using default")
                return default
        if isinstance(value, (dict, list)):
            return value
        return default

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:  # noqa: ANN401
        """Coerce ``"3"`` / ``3`` / ``3.0`` to an int; ``default`` on failure."""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:  # noqa: ANN401
        """Coerce ``"0.97"`` / ``0.97`` to a float; ``default`` on failure."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _coerce_bool(value: Any) -> bool:  # noqa: ANN401
        """True for ``True``/``"true"``/``"True"``/``"1"``; False otherwise."""
        return value in (True, "true", "True", "1")

    @classmethod
    def _to_citation(cls, match: object) -> Citation:
        """Convert one raw Moss document into a :class:`Citation`.

        DUAL-TOLERANT: parses BOTH the real Moss string contract
        (``QueryResultDocumentInfo``: id/text/score are typed, metadata is
        ``dict[str, str]`` with all values string-encoded; bbox/words/quads are
        JSON-encoded strings) AND the older nested-dict form used by DI / test
        fakes. Geometry, page, confidence and scanned are coerced tolerantly so
        a malformed value never crashes the voice loop.
        """
        get = cls._get
        meta = get(match, "metadata", {}) or {}

        # bbox may be top-level or nested in metadata, and either a JSON string
        # (real SDK) or a native dict (test/DI). Coerce to a dict either way.
        bbox_value = get(match, "bbox", None)
        if bbox_value is None:
            bbox_value = get(meta, "bbox", None)
        bbox_raw = cls._coerce_json(bbox_value, {})
        if not isinstance(bbox_raw, dict):
            bbox_raw = {}

        page = cls._coerce_int(
            get(match, "page", get(meta, "page", get(bbox_raw, "page", 1))), 1
        )
        bbox = BBox(
            page=page,
            x0=cls._coerce_float(get(bbox_raw, "x0", 0.0), 0.0),
            y0=cls._coerce_float(get(bbox_raw, "y0", 0.0), 0.0),
            x1=cls._coerce_float(get(bbox_raw, "x1", 0.0), 0.0),
            y1=cls._coerce_float(get(bbox_raw, "y1", 0.0), 0.0),
            page_width=cls._coerce_float(get(bbox_raw, "page_width", 612.0), 612.0),
            page_height=cls._coerce_float(get(bbox_raw, "page_height", 792.0), 792.0),
        )
        # Depth-v2 multi-doc / scanned-source metadata (optional; defaults keep
        # single-doc back-compat). documentId may be top-level or in metadata.
        document_id = get(match, "documentId", get(meta, "documentId", None))
        document_title = get(match, "documentTitle", get(meta, "documentTitle", None))
        scanned = cls._coerce_bool(get(match, "scanned", get(meta, "scanned", False)))

        quads: list[BBox] = []
        quads_value = get(match, "quads", get(meta, "quads", None))
        quads_raw = cls._coerce_json(quads_value, [])
        if isinstance(quads_raw, list):
            for item in quads_raw:
                if not isinstance(item, dict):
                    continue
                try:
                    quads.append(
                        BBox(
                            page=cls._coerce_int(get(item, "page", page), page),
                            x0=cls._coerce_float(get(item, "x0", 0.0), 0.0),
                            y0=cls._coerce_float(get(item, "y0", 0.0), 0.0),
                            x1=cls._coerce_float(get(item, "x1", 0.0), 0.0),
                            y1=cls._coerce_float(get(item, "y1", 0.0), 0.0),
                            page_width=cls._coerce_float(
                                get(item, "page_width", bbox.page_width),
                                bbox.page_width,
                            ),
                            page_height=cls._coerce_float(
                                get(item, "page_height", bbox.page_height),
                                bbox.page_height,
                            ),
                        )
                    )
                except ValueError:
                    logger.debug("moss_index.to_citation skipped malformed quad")

        quad_texts: list[str] | None = None
        quad_texts_value = get(match, "quadTexts", get(meta, "quadTexts", None))
        quad_texts_raw = cls._coerce_json(quad_texts_value, [])
        if isinstance(quad_texts_raw, list):
            quad_texts = [str(item) for item in quad_texts_raw]
        if quads and quad_texts is not None and len(quad_texts) != len(quads):
            logger.debug(
                "moss_index.to_citation dropped mismatched quadTexts length"
            )
            quad_texts = None

        chunk_kwargs: dict[str, Any] = {
            "id": str(get(match, "id", get(meta, "id", "unknown"))),
            "text": str(get(match, "text", get(meta, "text", ""))),
            "page": page,
            "bbox": bbox,
            "confidence": cls._coerce_float(
                get(match, "confidence", get(meta, "confidence", 1.0)), 1.0
            ),
            "documentTitle": document_title,
            "scanned": scanned,
        }
        if document_id is not None:
            chunk_kwargs["documentId"] = str(document_id)
        if quads:
            chunk_kwargs["quads"] = quads
        if quad_texts is not None:
            chunk_kwargs["quadTexts"] = quad_texts
        chunk = Chunk(**chunk_kwargs)
        raw_score = cls._coerce_float(get(match, "score", 0.0), 0.0)
        # Clamp into [0, 1] in case Moss returns distances/logits.
        score = max(0.0, min(raw_score, 1.0))
        return Citation(
            chunk=chunk,
            score=score,
            quads=chunk.quads,
            documentId=chunk.documentId,
            documentTitle=chunk.documentTitle,
            scanned=chunk.scanned,
        )

    # -- lifecycle ----------------------------------------------------------

    async def prewarm(self) -> None:
        """Open / warm the Moss index ahead of the first query.

        Calls ``await client.load_index(name, auto_refresh=...,
        polling_interval_in_seconds=...)`` using the settings values.
        ``load_index`` returns a ``str`` (status) — NOT a document list.

        Falls back to ``load_index(name)`` only on ``TypeError`` so older SDK
        versions without the keyword args still work.

        After loading, seeds ``_seen_document_ids`` via the real
        ``get_docs(name, GetDocumentsOptions(doc_ids=None))`` enumeration (all
        documents in the index). Any failure is caught and logged — prewarm must
        never crash the worker.
        """
        load_fn = getattr(self._client, "load_index", None) or getattr(
            self._client, "warm", None
        )
        if not callable(load_fn):
            return

        # --- step 1: load_index ---
        try:
            try:
                result = load_fn(
                    self._index_name,
                    auto_refresh=self._settings.moss_auto_refresh,
                    polling_interval_in_seconds=self._settings.moss_refresh_interval_s,
                )
            except TypeError:
                # Older signature without keyword args.
                logger.debug(
                    "moss_index.prewarm load_index kwargs rejected; retrying plain"
                )
                result = load_fn(self._index_name)
            if hasattr(result, "__await__"):
                result = await result
            logger.info(
                "moss_index.prewarm loaded index=%s status=%r",
                self._index_name,
                result,
            )
            self._loaded = True
            self._last_prewarm_error = None
        except Exception as exc:  # noqa: BLE001 - prewarm must never crash worker
            self._loaded = False
            self._last_prewarm_error = exc
            logger.exception(
                "moss_index.prewarm load_index failed index=%s; continuing cold",
                self._index_name,
            )
            return

        # --- step 2: seed document_ids via real get_docs enumeration ---
        await self._seed_document_ids_from_get_docs()

    async def _seed_document_ids_from_get_docs(self) -> None:
        """Enumerate all documents in the index via ``get_docs`` and seed ids.

        Uses the REAL ``client.get_docs(name, GetDocumentsOptions(doc_ids=None))``
        call (verified in inferedge_moss 1.0.0b19). Each returned
        ``DocumentInfo.id`` is added to ``_seen_document_ids``. Best-effort
        and exception-safe: if the SDK version does not expose ``get_docs`` or
        ``GetDocumentsOptions``, we skip silently.
        """
        get_docs_fn = getattr(self._client, "get_docs", None)
        if not callable(get_docs_fn):
            logger.debug("moss_index.seed_docs get_docs not available; skipping")
            return

        # GetDocumentsOptions(doc_ids=None) -> all docs in the index.
        options: object | None = None
        if self._module is not None:
            gdo_cls = getattr(self._module, "GetDocumentsOptions", None)
            if gdo_cls is not None:
                try:
                    options = gdo_cls(doc_ids=None)
                except Exception:  # noqa: BLE001 - keep best-effort
                    logger.debug("moss_index.seed_docs GetDocumentsOptions() failed")

        try:
            if options is not None:
                docs_result = get_docs_fn(self._index_name, options)
            else:
                docs_result = get_docs_fn(self._index_name)
            if hasattr(docs_result, "__await__"):
                docs_result = await docs_result
            if docs_result:
                for d in docs_result:
                    doc_id = self._get(d, "id", None)
                    if doc_id:
                        self._seen_document_ids.add(str(doc_id))
            logger.info(
                "moss_index.seed_docs seeded %d document ids from get_docs",
                len(self._seen_document_ids),
            )
        except Exception:  # noqa: BLE001 - seeding is always best-effort
            logger.debug(
                "moss_index.seed_docs get_docs enumeration failed; ids will "
                "accumulate from query results",
                exc_info=True,
            )

    async def refresh_document_ids(self) -> None:
        """Re-enumerate all documents via ``get_docs`` and repopulate the id set.

        Clears ``_seen_document_ids`` and re-seeds from the real index, giving an
        accurate snapshot after an offline pipeline build or a document deletion.
        Best-effort: on failure the existing set is left intact and a warning is
        logged so the multi-hop expansion path degrades gracefully.
        """
        self._seen_document_ids.clear()
        await self._seed_document_ids_from_get_docs()

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
        ``time_taken_ms`` (``SearchResult.time_taken_ms``) and falls back to a
        wall-clock measurement.
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

        Strategy:
          1. ATTEMPT a server-side documentId filter using the public SDK shape
             ``query(..., QueryOptions(...), filter=predicate)``. Older SDKs
             that only accept ``QueryOptions(filter=...)`` are also supported.
             The ``$or`` operator is not confirmed everywhere, so the filtered
             query is wrapped in a broad ``except`` that falls through to
             over-fetch rather than raising on a filter-shape rejection.
          2. CLIENT-SIDE POST-FILTER is ALWAYS applied as the authoritative
             correctness guarantee, regardless of whether the server filter
             succeeded. Post-filter is the truth; server-filter is a speed hint.
          3. If no server-filter support or the filtered query raises ANY
             exception, fall back to over-fetching (top_k * 4) and post-filtering
             client-side. MossClientUnavailableError always re-raises.

        With ``doc_ids=None`` this is identical to :meth:`query`.
        """
        if doc_ids is None:
            return await self.query(text, top_k=top_k, alpha=alpha)
        allow = set(doc_ids)

        start = time.perf_counter()
        try:
            docs, server_latency = await self._raw_query(
                text, top_k=top_k, alpha=alpha, doc_ids=list(doc_ids)
            )
        except MossClientUnavailableError:
            raise
        except Exception:  # noqa: BLE001
            # Server filter rejected or network error — fall through to
            # over-fetch path. Do NOT propagate: $or may not be supported.
            logger.debug(
                "moss_index.query_multi server filter failed; "
                "falling back to over-fetch text=%r",
                text,
            )
        else:
            # Server-filtered result received. Post-filter is still applied as
            # the AUTHORITATIVE correctness guarantee (server filter is a
            # latency optimisation; may be a no-op on some SDK versions).
            citations = [self._to_citation(m) for m in list(docs)]
            self._record_document_ids(citations)
            filtered = [c for c in citations if c.documentId in allow][:top_k]
            wall_ms = (time.perf_counter() - start) * 1000.0
            latency_ms = server_latency if server_latency is not None else wall_ms
            return RetrievalResult(query=text, citations=filtered, latency_ms=latency_ms)

        # Over-fetch + client-side post-filter (verified-safe path): fetch
        # top_k * 4 to ensure the allow-set has enough candidates after filtering.
        # CLIENT-SIDE POST-FILTER is authoritative — it is applied here too.
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
