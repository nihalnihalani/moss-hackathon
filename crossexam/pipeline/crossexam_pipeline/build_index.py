"""Build or refresh a Moss index from parsed chunks.

When Moss credentials (``MOSS_PROJECT_ID``, ``MOSS_PROJECT_KEY``,
``MOSS_INDEX_NAME``) are present, this module creates or upserts a Moss index
via the in-process Moss SDK (``inferedge-moss``).  Moss is an in-process SDK,
not a REST service — there is no network endpoint to POST to.

**Job lifecycle.**  ``create_index`` and ``add_docs`` both return a
``MutationResult`` whose ``.job_id`` identifies an asynchronous background job.
The index is **not queryable** until that job reaches ``JobStatus.COMPLETED``.
After submitting, the pipeline polls ``get_job_status(job_id)`` until the job
completes, fails, or a configurable wall-clock timeout is exceeded.

Create-vs-upsert decision:

* ``list_indexes()`` is called first; if the target index name appears in the
  returned list, ``add_docs(name, docs, MutationOptions(upsert=True))`` is used
  — this is safe for both empty and populated existing indexes.
* If the name is **absent** from the list, ``create_index(name, docs, model_id)``
  is called.
* If ``list_indexes()`` itself raises, the error is **re-raised** rather than
  silently falling through to ``create_index`` — a transient network/auth error
  must not destroy an existing index's documents.

The async entry point is :func:`build_index_async`.  The sync CLI wrapper
:func:`build_index` drives it via :func:`_run_async` so the typer CLI and the
backend ``api.py`` caller both stay synchronous.

When Moss credentials are absent (the default for the offline demo), the module
writes the backend-compatible chunk JSON to disk instead.  That file is loaded
by the CrossExam backend mock index, so the same artifact serves both modes.

The disk write is idempotent: re-running with the same input produces a
byte-identical file (chunks are sorted by page then id, JSON is
sorted/indented).
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import os
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from crossexam_pipeline.models import ParsedChunk, chunks_to_index_records

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)

ENV_PROJECT_ID = "MOSS_PROJECT_ID"
# Use the SAME var name the backend reads (crossexam_backend.config.Settings.moss_project_key)
# so a single filled .env drives both the pipeline upsert and the runtime query.
ENV_API_KEY = "MOSS_PROJECT_KEY"
ENV_INDEX_NAME = "MOSS_INDEX_NAME"
ENV_MODEL_ID = "MOSS_MODEL_ID"
DEFAULT_MODEL_ID = "moss-minilm"

# Job-polling configuration — override via environment variables.
ENV_JOB_TIMEOUT_S = "MOSS_JOB_TIMEOUT_S"
ENV_JOB_POLL_INTERVAL_S = "MOSS_JOB_POLL_INTERVAL_S"
DEFAULT_JOB_TIMEOUT_S = 300.0
DEFAULT_JOB_POLL_INTERVAL_S = 1.0

# Job phases at/after which the index artifact is built and cloud-queryable.
# Reaching any of these is treated as build success: the remaining phases only
# finalize the downloadable copy used by local load_index, not cloud queryability,
# so a long server-side finalize must not raise a false timeout.
_READY_PHASES: frozenset[str] = frozenset({"building_index", "uploading", "cleanup"})

# Import-package names to try, in priority order. The current PyPI distribution
# is ``moss`` (import name ``moss``, >=1.4.0); ``inferedge_moss`` is the legacy
# 1.0.0b19 distribution (now gone from PyPI), kept only as a fallback. Mirrors
# the backend's _MOSS_IMPORT_CANDIDATES order.
_MOSS_IMPORT_CANDIDATES: tuple[str, ...] = ("moss", "inferedge_moss")

# Default location the backend mock index reads from.
_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
_CROSSEXAM_ROOT = _PIPELINE_ROOT.parent
DEFAULT_CHUNKS_OUT = _CROSSEXAM_ROOT / "backend" / "fixtures" / "sample_chunks.json"


class MossConfig:
    """Resolved Moss configuration read from the environment.

    Attributes:
        project_id: Moss project id, or ``None`` if unset.
        api_key: Moss API key, or ``None`` if unset.
        index_name: Moss index name, or ``None`` if unset.
    """

    def __init__(
        self,
        project_id: str | None,
        api_key: str | None,
        index_name: str | None,
    ) -> None:
        """Store Moss configuration, coercing empty strings to ``None``."""
        self.project_id = project_id or None
        self.api_key = api_key or None
        self.index_name = index_name or None

    @classmethod
    def from_env(cls) -> MossConfig:
        """Read Moss configuration from environment variables.

        Returns:
            A :class:`MossConfig` with whatever values were present.
        """
        return cls(
            project_id=os.environ.get(ENV_PROJECT_ID, "").strip() or None,
            api_key=os.environ.get(ENV_API_KEY, "").strip() or None,
            index_name=os.environ.get(ENV_INDEX_NAME, "").strip() or None,
        )

    @property
    def is_complete(self) -> bool:
        """Whether all required Moss credentials are present."""
        return bool(self.project_id and self.api_key and self.index_name)


def _sorted_chunks(chunks: list[ParsedChunk]) -> list[ParsedChunk]:
    """Return chunks in a stable order (page, then id) for idempotency."""
    return sorted(chunks, key=lambda c: (c.page, c.id))


def write_chunks_json(chunks: list[ParsedChunk], out_path: Path | str) -> Path:
    """Write backend-compatible chunk records to disk idempotently.

    Args:
        chunks: Parsed chunks to serialize.
        out_path: Destination JSON path. Parent dirs are created.

    Returns:
        The resolved path written to.
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = chunks_to_index_records(_sorted_chunks(chunks))
    serialized = json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False)
    path.write_text(serialized + "\n", encoding="utf-8")
    logger.info("Wrote %d chunk record(s) to %s", len(records), path)
    return path


def _chunk_metadata(c: ParsedChunk) -> dict[str, str]:
    """Build the string-only Moss metadata dict for one chunk.

    Moss metadata values MUST all be ``str`` -- nested dicts/ints/floats/lists/
    bools are not accepted -- so structured fields (bbox, words, quads) are
    JSON-encoded and scalars are stringified.

    This is the SHARED CONTRACT the backend's ``MossIndex._to_citation`` parses
    back out. ``documentId`` lives ONLY here (no top-level field on the SDK's
    ``DocumentInfo``); the frontend's ``isCitation`` requires it to be a string,
    so it is always emitted. ``scanned``/``page``/``confidence``/``bbox``/
    ``words`` are always present; ``documentTitle``/``quads`` only when present.

    Per-word ``confidence`` inside the ``words`` JSON is also stringified for
    internal consistency (all metadata values, including those inside
    JSON-encoded sub-fields, are ``str``).

    Args:
        c: A parsed chunk.

    Returns:
        A flat ``dict[str, str]`` ready to hand to ``DocumentInfo(metadata=...)``.
    """
    metadata: dict[str, str] = {
        "documentId": str(c.document_id),
        "scanned": "true" if c.scanned else "false",
        "page": str(c.page),
        "confidence": str(c.confidence),
        "bbox": json.dumps(
            {
                "page": c.bbox.page,
                "x0": c.bbox.x0,
                "y0": c.bbox.y0,
                "x1": c.bbox.x1,
                "y1": c.bbox.y1,
                "page_width": c.bbox.page_width,
                "page_height": c.bbox.page_height,
            }
        ),
        "words": json.dumps(
            [
                {
                    "text": w.text,
                    "bbox": {
                        "page": w.bbox.page,
                        "x0": w.bbox.x0,
                        "y0": w.bbox.y0,
                        "x1": w.bbox.x1,
                        "y1": w.bbox.y1,
                        "page_width": w.bbox.page_width,
                        "page_height": w.bbox.page_height,
                    },
                    "confidence": str(w.confidence),
                }
                for w in c.words
            ]
        ),
    }
    if c.document_title is not None:
        metadata["documentTitle"] = str(c.document_title)
    if c.quads:
        metadata["quads"] = json.dumps(
            [
                {
                    "page": q.page,
                    "x0": q.x0,
                    "y0": q.y0,
                    "x1": q.x1,
                    "y1": q.y1,
                    "page_width": q.page_width,
                    "page_height": q.page_height,
                }
                for q in c.quads
            ]
        )
    if c.quad_texts:
        metadata["quadTexts"] = json.dumps(list(c.quad_texts))
    return metadata


def _load_moss_module() -> ModuleType | None:
    """Import the Moss SDK if installed, else return ``None``.

    Tries each name in :data:`_MOSS_IMPORT_CANDIDATES`. Kept lazy so the whole
    module (and the offline disk path) imports fine without ``inferedge-moss``.

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
    logger.debug("moss SDK not installed (tried %s)", ", ".join(_MOSS_IMPORT_CANDIDATES))
    return None


def _run_async(coro: Coroutine[Any, Any, Any]) -> Any:  # noqa: ANN401 - SDK return is dynamic
    """Run an awaitable from sync code, loop-running or not.

    ``MossClient`` methods are async but this module (and both its callers --
    the typer CLI and the backend ``api.py``) are synchronous. If we are already
    inside a running event loop (e.g. a FastAPI request thread), ``asyncio.run``
    would raise; in that case we run the coroutine to completion on a fresh
    thread with its own loop. Otherwise we just use ``asyncio.run`` directly.

    Args:
        coro: The coroutine to run to completion.

    Returns:
        Whatever the coroutine returns.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread: safe to drive it directly.
        return asyncio.run(coro)

    # A loop is already running here; offload to a private thread + loop.
    import threading

    result: dict[str, Any] = {}

    def _worker() -> None:
        result["value"] = asyncio.run(coro)

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join()
    return result["value"]


def _create_index_arity(moss_client_cls: Any) -> bool | None:  # noqa: ANN401 - SDK class is untyped
    """Detect whether ``MossClient.create_index`` accepts a third ``model_id`` arg.

    Uses ``inspect.signature`` for Python-level methods; falls back to ``None``
    (unknown) for C-extension / non-introspectable callables.

    Args:
        moss_client_cls: The ``MossClient`` class from the loaded SDK module.

    Returns:
        ``True`` if the 3-arg form ``(name, docs, model_id)`` is supported,
        ``False`` if only the 2-arg form ``(name, docs)`` is, ``None`` if the
        signature cannot be determined.
    """
    try:
        sig = inspect.signature(moss_client_cls.create_index)
        non_self = [p for p in sig.parameters.values() if p.name != "self"]
        return len(non_self) >= 3 or any(p.name == "model_id" for p in non_self)
    except (ValueError, TypeError):
        return None


def _is_completed(status: object, completed_value: str) -> bool:
    """Return True if *status* equals the COMPLETED sentinel.

    Tolerates the status being the enum-like class-attribute string, a plain
    string, or any object whose ``str()`` or ``.value`` attribute matches
    (defensive for SDK versions that change the representation -- moss 1.4.0
    returns a ``JobStatus`` object whose ``str()`` is its repr but whose
    ``.value`` is ``"completed"``).

    Args:
        status: The ``.status`` field of a ``JobStatusResponse``.
        completed_value: The string value of ``JobStatus.COMPLETED``.

    Returns:
        ``True`` when the job is complete.
    """
    value_attr = getattr(status, "value", None)
    return (
        status == completed_value
        or str(status) == completed_value
        or (value_attr is not None and value_attr == completed_value)
    )


def _is_failed(status: object, failed_value: str) -> bool:
    """Return True if *status* equals the FAILED sentinel.

    Args:
        status: The ``.status`` field of a ``JobStatusResponse``.
        failed_value: The string value of ``JobStatus.FAILED``.

    Returns:
        ``True`` when the job has failed.
    """
    value_attr = getattr(status, "value", None)
    return (
        status == failed_value
        or str(status) == failed_value
        or (value_attr is not None and value_attr == failed_value)
    )


async def _poll_job(
    client: object,
    job_id: str,
    completed_value: str,
    failed_value: str,
    timeout_s: float,
    poll_interval_s: float,
) -> None:
    """Poll ``get_job_status`` until the job completes, fails, or times out.

    Elapsed time is measured with :func:`asyncio.get_running_loop().time` (a
    monotonic clock) so that tests injecting a zero ``poll_interval_s`` do not
    loop forever: even with no sleep the clock still advances between iterations
    because each ``get_job_status`` call and each ``asyncio.sleep(0)`` yield
    control to the event loop, allowing other tasks to run and time to advance.

    ``asyncio.get_running_loop()`` is used (not the deprecated
    ``asyncio.get_event_loop()``) because this function is always called from
    inside a running coroutine; the running-loop form is correct, non-deprecated,
    and eliminates ``DeprecationWarning`` on Python 3.10+ / errors on 3.12+.

    Args:
        client: A ``MossClient`` instance (or compatible fake) with an async
            ``get_job_status(job_id)`` method.
        job_id: The job identifier returned by the mutation.
        completed_value: String value of ``JobStatus.COMPLETED``.
        failed_value: String value of ``JobStatus.FAILED``.
        timeout_s: Wall-clock seconds before a :class:`RuntimeError` is raised.
        poll_interval_s: Seconds to sleep between polls (0 is valid for tests).

    Raises:
        RuntimeError: When the job fails (includes the error message from the
            response) or when the timeout is exceeded.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        resp = await client.get_job_status(job_id)  # type: ignore[attr-defined]
        status = resp.status
        if _is_completed(status, completed_value):
            logger.debug("Job %s completed.", job_id)
            return
        # Once the build reaches an index-ready phase the artifact is already
        # cloud-queryable; only upload/cleanup finalization remains (which does
        # not affect queryability). Treat that as success to avoid a false
        # timeout while the server finishes finalizing a large corpus.
        phase = getattr(resp, "current_phase", None)
        if phase is not None and str(phase) in _READY_PHASES:
            logger.info(
                "Job %s reached phase %r; index is queryable, treating as done.",
                job_id,
                phase,
            )
            return
        if _is_failed(status, failed_value):
            error_detail = getattr(resp, "error", None) or "no error detail"
            raise RuntimeError(
                f"Moss job {job_id!r} failed. Status: {status!r}. Error: {error_detail}"
            )
        elapsed = loop.time() - (deadline - timeout_s)
        logger.debug(
            "Job %s status=%r (phase=%r); sleeping %.1fs (elapsed=%.1fs/%.1fs).",
            job_id,
            status,
            getattr(resp, "current_phase", None),
            poll_interval_s,
            elapsed,
            timeout_s,
        )
        await asyncio.sleep(poll_interval_s)

    raise RuntimeError(
        f"Moss job {job_id!r} did not complete within {timeout_s:.0f}s "
        f"(MOSS_JOB_TIMEOUT_S). Check the Moss dashboard for status."
    )


async def _create_or_update_moss_index_async(
    chunks: list[ParsedChunk],
    config: MossConfig,
    *,
    module: ModuleType | None = None,
) -> int:
    """Create or upsert a Moss index from chunks, then poll to completion.

    All network I/O (SDK calls, job polling) is async. The caller is responsible
    for running this coroutine on an event loop — use :func:`build_index_async`
    as the public entry or :func:`_run_async` from sync code.

    **Create vs upsert decision**: calls ``list_indexes()`` and checks for the
    target name in the returned list.  If present, ``add_docs(upsert=True)`` is
    used; if absent, ``create_index`` is called.  If ``list_indexes()`` raises,
    the exception is re-raised immediately — a transient error must never
    silently fall through to ``create_index``, which would destroy an existing
    index's documents.

    **Thread/loop safety**: ``MossClient`` is constructed *inside* this
    coroutine so that both construction and the awaited mutations share the same
    thread and event loop (Moss is an in-process/WASM runtime that may bind to
    the thread it is created on).

    Args:
        chunks: Parsed chunks to index.
        config: Complete Moss configuration (all three credentials present).
        module: Optional pre-loaded SDK module (used in tests to inject a fake).
            When ``None``, the SDK is imported lazily.

    Returns:
        The number of documents indexed (``MutationResult.doc_count`` when
        available, else ``len(chunks)``).

    Raises:
        RuntimeError: When the Moss SDK is not installed, the job fails, or the
            job polling timeout is exceeded.
    """
    assert config.index_name is not None  # guaranteed by is_complete

    moss = module if module is not None else _load_moss_module()
    if moss is None:
        raise RuntimeError(
            "The Moss SDK is not installed. Install it with: pip install '.[moss]' "
            "(distribution 'inferedge-moss'). Or unset Moss credentials to use the "
            "offline disk/mock path."
        )

    moss_client_cls = moss.MossClient
    document_info_cls = moss.DocumentInfo
    mutation_options_cls = moss.MutationOptions
    job_status_cls = moss.JobStatus

    completed_value: str = str(job_status_cls.COMPLETED)
    failed_value: str = str(job_status_cls.FAILED)

    # Read polling config from env (allow injection via monkeypatch in tests).
    timeout_s = float(os.environ.get(ENV_JOB_TIMEOUT_S, "") or DEFAULT_JOB_TIMEOUT_S)
    poll_interval_s = float(
        os.environ.get(ENV_JOB_POLL_INTERVAL_S, "") or DEFAULT_JOB_POLL_INTERVAL_S
    )

    model_id = os.environ.get(ENV_MODEL_ID, "").strip() or DEFAULT_MODEL_ID
    index_name = config.index_name
    sorted_chunks = _sorted_chunks(chunks)
    n_chunks = len(sorted_chunks)

    # Determine create_index arity ONCE (inspect works on the Python wrapper;
    # falls back to None for C-extension code paths).
    use_model_id: bool | None = _create_index_arity(moss_client_cls)

    # Construct MossClient INSIDE the coroutine so construction and the awaited
    # mutations run on the same thread/loop (Moss may bind to its creation thread).
    client = moss_client_cls(config.project_id, config.api_key)

    documents = [
        document_info_cls(id=str(c.id), text=str(c.text), metadata=_chunk_metadata(c))
        for c in sorted_chunks
    ]

    # --- Decide: create a new index or upsert into an existing one. ---
    # Use list_indexes() for the membership check so that ANY exception from the
    # SDK (network, auth, transient) propagates immediately rather than being
    # silently treated as "not found" — which would cause create_index to destroy
    # an existing index.  The Rust extension raises RuntimeError for both "not
    # found" and real errors; there is no typed NotFoundError to catch narrowly.
    existing_indexes = await client.list_indexes()
    index_exists = any(getattr(idx, "name", None) == index_name for idx in existing_indexes)
    if index_exists:
        logger.info("Index %r found in list_indexes; will upsert docs.", index_name)
    else:
        logger.info("Index %r absent from list_indexes; will create.", index_name)

    if index_exists:
        mutation_result = await client.add_docs(
            index_name, documents, mutation_options_cls(upsert=True)
        )
    else:
        # 3-arg form preferred; graceful fallback for older 2-arg SDK builds.
        if use_model_id is True:
            mutation_result = await client.create_index(index_name, documents, model_id)
        elif use_model_id is False:
            mutation_result = await client.create_index(index_name, documents)
        else:
            # Signature not introspectable (C-extension build): try 3-arg first;
            # retry 2-arg ONLY when the TypeError message names the third param so
            # that real indexing errors (bad doc type, internal invariant) propagate.
            try:
                mutation_result = await client.create_index(index_name, documents, model_id)
            except TypeError as exc:
                msg = str(exc).lower()
                if "model_id" in msg or "positional argument" in msg:
                    mutation_result = await client.create_index(index_name, documents)
                else:
                    raise

    # --- Poll the async job to completion. ---
    job_id: str | None = getattr(mutation_result, "job_id", None) or None
    if job_id:
        logger.info(
            "Moss job %r submitted for index %r; polling every %.1fs (timeout=%.0fs).",
            job_id,
            index_name,
            poll_interval_s,
            timeout_s,
        )
        await _poll_job(
            client,
            job_id,
            completed_value=completed_value,
            failed_value=failed_value,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
    else:
        logger.info("Moss mutation for index %r returned no job_id; treating as done.", index_name)

    count = getattr(mutation_result, "doc_count", None)
    if not isinstance(count, int):
        count = n_chunks
    logger.info(
        "%s Moss index %r with %d document(s).",
        "Upserted" if index_exists else "Created",
        index_name,
        count,
    )
    return count


async def build_index_async(
    chunks: list[ParsedChunk],
    index_name: str | None = None,
    out_path: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a Moss index (async entry), or write chunks JSON for the mock.

    This is the **public async entry point** — the backend ``api.py`` can
    ``await`` this directly on its own event loop.  The sync CLI wrapper
    :func:`build_index` drives it through :func:`_run_async`.

    Resolution order:

    * ``dry_run=True`` -> always write JSON to disk (never touches the SDK).
    * Moss credentials complete -> create/upsert the Moss index via the
      in-process SDK, then poll the async job to completion.
    * Otherwise -> write JSON to disk and log that Moss was skipped.

    Args:
        chunks: Parsed chunks to index.
        index_name: Override for the Moss index name. Falls back to
            ``MOSS_INDEX_NAME``.
        out_path: Override for the JSON output path (disk modes). Falls back to
            the backend's ``fixtures/sample_chunks.json``.
        dry_run: Force the offline disk path regardless of credentials.

    Returns:
        A summary dict with keys ``mode`` (``"moss"``/``"disk"``/``"dry-run"``),
        ``chunk_count``, and either ``path`` (disk modes) or ``index`` (moss).
    """
    config = MossConfig.from_env()
    if index_name:
        config = MossConfig(config.project_id, config.api_key, index_name)

    target_out = Path(out_path) if out_path else DEFAULT_CHUNKS_OUT

    if dry_run:
        path = write_chunks_json(chunks, target_out)
        logger.info("Dry-run: skipped Moss; wrote backend-compatible chunks to disk.")
        return {"mode": "dry-run", "chunk_count": len(chunks), "path": str(path)}

    if config.is_complete:
        count = await _create_or_update_moss_index_async(chunks, config)
        return {"mode": "moss", "chunk_count": count, "index": config.index_name}

    path = write_chunks_json(chunks, target_out)
    logger.warning(
        "Moss credentials incomplete (%s/%s/%s); wrote chunks to %s for the mock "
        "index instead. Set all three env vars to upsert to Moss.",
        ENV_PROJECT_ID,
        ENV_API_KEY,
        ENV_INDEX_NAME,
        path,
    )
    return {"mode": "disk", "chunk_count": len(chunks), "path": str(path)}


def build_index(
    chunks: list[ParsedChunk],
    index_name: str | None = None,
    out_path: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a Moss index synchronously, or write chunks JSON for the mock.

    Sync wrapper around :func:`build_index_async` for the typer CLI and the
    backend ``api.py`` caller.  The signature and return contract are identical
    to :func:`build_index_async`; this function simply drives the coroutine
    through :func:`_run_async` (which handles both "inside a running event loop"
    and "no loop yet" call sites).

    Args:
        chunks: Parsed chunks to index.
        index_name: Override for the Moss index name. Falls back to
            ``MOSS_INDEX_NAME``.
        out_path: Override for the JSON output path (disk modes). Falls back to
            the backend's ``fixtures/sample_chunks.json``.
        dry_run: Force the offline disk path regardless of credentials.

    Returns:
        A summary dict with keys ``mode`` (``"moss"``/``"disk"``/``"dry-run"``),
        ``chunk_count``, and either ``path`` (disk modes) or ``index`` (moss).
    """
    result: dict[str, Any] = _run_async(  # noqa: ANN401
        build_index_async(chunks, index_name=index_name, out_path=out_path, dry_run=dry_run)
    )
    return result
