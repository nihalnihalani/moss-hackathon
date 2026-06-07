"""FastAPI HTTP service for CrossExam live mode.

The LiveKit *agent worker* (``crossexam_backend.server``) speaks audio inside a
room, but the browser needs two things the worker cannot provide:

* a **LiveKit access token** so it can join a room, and
* a way to **upload a document** so it is parsed and indexed for retrieval.

This module is that small service. Endpoints:

* ``GET  /healthz``    -> liveness + resolved mode + which integrations are wired.
* ``GET  /config``     -> public connection info for the frontend (no secrets).
* ``POST /token``      -> mint a real LiveKit join token (503 if not configured).
* ``POST /documents``  -> upload a PDF; parse + index it; returns chunk counts.

Everything imports and tests WITHOUT live keys or optional deps: the LiveKit
token library import is guarded (missing lib/keys -> a clear 503), and the
ingest path uses the offline fixture + :class:`MockIndex` when Moss is absent.

Run it with::

    python -m crossexam_backend.api          # or: crossexam-api
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from crossexam_backend import ingest
from crossexam_backend.config import Settings, get_settings
from crossexam_backend.doctor import _moss_import_available  # noqa: PLC2701 - reuse doctor's check
from crossexam_backend.retrieval.base import RetrievalIndex
from crossexam_backend.retrieval.factory import _resolve_fixture_path, get_index
from crossexam_backend.retrieval.mock_index import MockIndex

logger = logging.getLogger(__name__)

# Accepted upload content types for PDF (browsers/curl vary on the exact value).
_PDF_CONTENT_TYPES = frozenset(
    {"application/pdf", "application/x-pdf", "application/octet-stream"}
)
_PDF_MAGIC = b"%PDF-"


# --------------------------------------------------------------------------- #
# Request / response models (no secrets ever appear in a response model)      #
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    """Liveness + resolved-mode report for ``GET /healthz``."""

    status: str = Field(description="Always 'ok' when the service is up.")
    mode: str = Field(description="'live' when Moss+LiveKit wired, else 'mock'.")
    livekit_configured: bool = Field(description="LiveKit creds + token lib present.")
    moss_configured: bool = Field(description="Moss creds present and SDK importable.")


class ConfigResponse(BaseModel):
    """Public connection info for the frontend (``GET /config``)."""

    livekit_url: str | None = Field(description="LiveKit ws URL, or null if unset.")
    live: bool = Field(description="Whether the backend is in full live mode.")


class TokenRequest(BaseModel):
    """Body for ``POST /token``."""

    room: str | None = Field(default=None, description="Room to join; defaults configured.")
    identity: str | None = Field(default=None, description="Participant identity.")


class TokenResponse(BaseModel):
    """Minted LiveKit access token (``POST /token``).

    ``url`` is the field the frontend reads to connect (LiveKit JS expects
    ``url``); ``livekit_url`` is kept as an alias for back-compat. Both carry the
    same value. Without ``url`` the frontend never engages live mode.
    """

    token: str
    room: str
    identity: str
    url: str | None = Field(description="LiveKit ws URL the frontend connects to.")
    livekit_url: str | None = Field(
        description="Alias of ``url`` kept for back-compat."
    )


class DocumentResponse(BaseModel):
    """Result of ingesting a document (``POST /documents``)."""

    document_id: str
    pages: int
    chunks_indexed: int
    mode: str = Field(description="'moss' when upserted to Moss, else 'mock'.")


# --------------------------------------------------------------------------- #
# Indexing helpers                                                            #
# --------------------------------------------------------------------------- #
def _livekit_configured(settings: Settings) -> bool:
    """Return ``True`` when LiveKit creds are set AND the token lib imports."""
    if not settings.has_livekit_credentials:
        return False
    try:
        import livekit.api  # noqa: F401
    except ImportError:
        return False
    return True


def _moss_configured(settings: Settings) -> bool:
    """Return ``True`` when Moss creds are present and the SDK is importable."""
    return settings.has_moss_credentials and _moss_import_available() is not None


def _livekit_http_url(livekit_url: str | None) -> str | None:
    """Convert a LiveKit websocket URL into the HTTP API URL."""
    if livekit_url is None:
        return None
    if livekit_url.startswith("wss://"):
        return "https://" + livekit_url[len("wss://") :]
    if livekit_url.startswith("ws://"):
        return "http://" + livekit_url[len("ws://") :]
    return livekit_url


def _enum_value(enum: object, name: str) -> int | None:
    """Return a protobuf enum value by name, tolerating SDK/test fakes."""
    value = getattr(enum, "Value", None)
    if not callable(value):
        return None
    try:
        return int(value(name))
    except Exception:  # noqa: BLE001 - enum wrappers/fakes vary by SDK version
        return None


def _dispatch_is_active(dispatch: object, lk_api: object) -> bool:
    """Return ``True`` when an existing LiveKit agent dispatch can be reused."""
    state = getattr(dispatch, "state", None)
    if state is None:
        return True
    deleted_at = int(getattr(state, "deleted_at", 0) or 0)
    if deleted_at > 0:
        return False

    jobs = list(getattr(state, "jobs", []) or [])
    if not jobs:
        # Newly-created dispatches can exist before a job is attached.
        return True

    job_status = getattr(lk_api, "JobStatus", None)
    pending = _enum_value(job_status, "JS_PENDING")
    running = _enum_value(job_status, "JS_RUNNING")
    success = _enum_value(job_status, "JS_SUCCESS")
    failed = _enum_value(job_status, "JS_FAILED")
    active_statuses = {s for s in (pending, running) if s is not None}
    terminal_statuses = {s for s in (success, failed) if s is not None}

    for job in jobs:
        job_state = getattr(job, "state", None)
        if job_state is None:
            return True
        status = getattr(job_state, "status", None)
        if status in active_statuses:
            return True
        ended_at = int(getattr(job_state, "ended_at", 0) or 0)
        if ended_at <= 0 and status not in terminal_statuses:
            return True
    return False


async def _ensure_agent_dispatch(
    settings: Settings, lk_api: object, room: str
) -> str | None:
    """Create or reuse the named LiveKit agent dispatch for ``room``.

    LiveKit's current guidance favors explicit dispatch for production control.
    The browser joins with a participant token, then this backend makes sure the
    named worker has a job for that room. Older/minimal test fakes may not expose
    the management client; in that case token minting still works.
    """
    agent_name = settings.livekit_agent_name.strip()
    if not agent_name:
        return None

    livekit_api_cls = getattr(lk_api, "LiveKitAPI", None)
    create_request_cls = getattr(lk_api, "CreateAgentDispatchRequest", None)
    if not callable(livekit_api_cls) or create_request_cls is None:
        return None

    client = livekit_api_cls(
        _livekit_http_url(settings.livekit_url),
        settings.livekit_api_key,
        settings.livekit_api_secret,
    )
    try:
        agent_dispatch = getattr(client, "agent_dispatch", None)
        if agent_dispatch is None:
            return None

        list_dispatch = getattr(agent_dispatch, "list_dispatch", None)
        if callable(list_dispatch):
            for dispatch in await list_dispatch(room):
                if getattr(dispatch, "agent_name", None) == agent_name and _dispatch_is_active(
                    dispatch, lk_api
                ):
                    dispatch_id = str(getattr(dispatch, "id", "") or "")
                    logger.info(
                        "token.agent_dispatch_reused room=%s agent=%s dispatch=%s",
                        room,
                        agent_name,
                        dispatch_id,
                    )
                    return dispatch_id

        create_dispatch = getattr(agent_dispatch, "create_dispatch", None)
        if not callable(create_dispatch):
            return None
        dispatch = await create_dispatch(
            create_request_cls(room=room, agent_name=agent_name)
        )
        dispatch_id = str(getattr(dispatch, "id", "") or "")
        logger.info(
            "token.agent_dispatch_created room=%s agent=%s dispatch=%s",
            room,
            agent_name,
            dispatch_id,
        )
        return dispatch_id
    finally:
        close = getattr(client, "aclose", None)
        if callable(close):
            await close()


def _initial_index(settings: Settings) -> RetrievalIndex:
    """Build the startup retrieval index, tolerating a missing fixture.

    In live (Moss) mode this returns the Moss-backed index. In offline mode it
    loads the fixture if present, else starts with an EMPTY :class:`MockIndex`
    so the service comes up cleanly before any document has been uploaded.
    """
    try:
        return get_index(settings)
    except FileNotFoundError:
        logger.info("api.index no fixture yet; starting with an empty MockIndex")
        return MockIndex([])


def _index_to_mock_fixture(
    settings: Settings, records: list[dict[str, Any]]
) -> tuple[int, RetrievalIndex]:
    """Append ``records`` to the fixture JSON and reload a fresh MockIndex.

    Existing chunks for the same uploaded ``documentId`` are replaced before new
    records are merged. The parser generates fresh chunk IDs for every upload,
    so ID-only dedupe would append the same PDF on every retry/restart and make
    the fallback fixture grow without bound.

    Returns:
        ``(chunks_indexed, new_index)`` where ``chunks_indexed`` is the number of
        records contributed by this upload.
    """
    fixture_path = _resolve_fixture_path(settings)
    existing: list[dict[str, Any]] = []
    if fixture_path.is_file():
        loaded = json.loads(fixture_path.read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            existing = loaded
    incoming_document_ids = {
        str(rec.get("documentId")) for rec in records if rec.get("documentId") is not None
    }
    retained = [
        rec
        for rec in existing
        if str(rec.get("documentId")) not in incoming_document_ids
    ]
    by_id: dict[str, dict[str, Any]] = {str(r["id"]): r for r in retained}
    for rec in records:
        by_id[str(rec["id"])] = rec
    merged = list(by_id.values())
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(
        json.dumps(merged, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info(
        "ingest.mock_fixture wrote=%d total=%d path=%s",
        len(records),
        len(merged),
        fixture_path,
    )
    new_index = MockIndex.from_fixture(fixture_path)
    return len(records), new_index


async def _index_to_moss(
    settings: Settings, records: list[dict[str, Any]]
) -> tuple[int, str]:
    """Upsert ``records`` into Moss via the pipeline's real ``build_index_async``.

    RECOMMENDED path: pre-build the Moss index OFFLINE via ``crossexam-pipeline``
    and keep live-upload as best-effort enrichment only. Live-upload is provided
    for hackathon demos where offline pre-build is impractical.

    Imported lazily and guarded: if the pipeline package is not installed we
    raise a clear 503 rather than silently falling back, because the caller has
    already decided (real Moss creds present) that this is the live path.

    ``build_index_async`` is natively async — it ``await``s the in-process Moss
    SDK (create-or-upsert, then polls the job to COMPLETED) without ever spinning
    its own event loop — so we ``await`` it DIRECTLY on the FastAPI event loop.
    This is the production-correct fix for the prior thread/WASM concern: the
    earlier ``asyncio.to_thread(build_index, ...)`` hop ran the SDK off-loop,
    which could clash with the SDK's in-process (WASM-backed) runtime. There is
    no blocking work to offload, so no thread is needed.

    If ``build_index_async`` raises (e.g. SDK runtime error) we log the failure
    and DEGRADE gracefully to the on-disk fixture path — the endpoint returns
    ``mode="mock"`` instead of 500-ing, so the demo stays up. The caller reloads
    the mock index from the fixture, meaning the freshly-uploaded document is
    still searchable offline.

    ``build_index_async`` itself may also fall back internally when its own
    credential check fails. We read the summary's ``mode`` and surface it
    honestly rather than always reporting ``"moss"``.

    Returns:
        ``(chunks_indexed, mode)`` where ``mode`` is ``"moss"`` only when the
        pipeline actually upserted to Moss; ``"mock"`` when it wrote the fixture.
    """
    try:
        from crossexam_pipeline.build_index import build_index_async
        from crossexam_pipeline.models import ParsedChunk
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Moss credentials are set but the crossexam-pipeline package "
                "(real upsert path) is not installed. Install it to index into "
                "Moss, or unset Moss credentials to use the offline mock index."
            ),
        ) from exc
    chunks = [ParsedChunk.model_validate(r) for r in records]
    # build_index_async is natively async (awaits the in-process SDK and polls
    # the job to completion) -> await it directly; no thread hop, no nested loop.
    try:
        summary = await build_index_async(chunks, index_name=settings.moss_index_name)
    except Exception:  # noqa: BLE001 - Moss runtime error must not 500 the upload
        logger.exception(
            "documents.moss_upsert_failed index=%s chunk_count=%d; "
            "degrading to on-disk fixture (mode=mock)",
            settings.moss_index_name,
            len(chunks),
        )
        # Degrade: write the fixture and let the caller reload MockIndex.
        count, _new_index = _index_to_mock_fixture(settings, records)
        return count, "mock"
    count = int(summary.get("chunk_count", len(records)))
    # build_index_async reports mode in {"moss", "disk", "dry-run"}; only "moss"
    # is a real upsert. Anything else means it wrote the disk fixture as fallback.
    mode = "moss" if summary.get("mode") == "moss" else "mock"
    return count, mode


async def _reload_live_index(index: RetrievalIndex) -> None:
    """Re-prewarm a live MossIndex so a freshly-upserted document is queryable.

    A loaded Moss index does not automatically see documents upserted after it
    was warmed, so we call ``await index.prewarm()`` (which re-runs
    ``load_index`` and re-seeds the document-id set) followed by
    ``refresh_document_ids()`` to snapshot the new corpus.

    Guarded and best-effort: only runs when ``index`` actually exposes a
    ``prewarm`` coroutine (the mock path does not need it), and any failure is
    logged and swallowed so a reload error never 500s the upload — the document
    is already upserted server-side and will surface on the next refresh.
    """
    prewarm = getattr(index, "prewarm", None)
    if not callable(prewarm):
        return
    try:
        await prewarm()
        refresh = getattr(index, "refresh_document_ids", None)
        if callable(refresh):
            await refresh()
    except Exception:  # noqa: BLE001 - reload failure must not 500 the upload
        logger.exception(
            "documents.live_reload_failed; upserted doc will surface on next "
            "refresh cycle (upload still succeeds)"
        )


# --------------------------------------------------------------------------- #
# App factory                                                                 #
# --------------------------------------------------------------------------- #
def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app.

    Args:
        settings: Optional settings override (tests inject a no-env instance).
            Defaults to the cached :func:`get_settings`.

    Returns:
        A configured :class:`FastAPI` application.
    """
    settings = settings or get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    app = FastAPI(title="CrossExam API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Shared, mutable retrieval index. /documents swaps it out after an offline
    # ingest so subsequent queries see the new corpus. Held on app.state so tests
    # and the worker can inspect it.
    app.state.settings = settings
    app.state.index = _initial_index(settings)

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        """Liveness + which integrations are wired (no secrets)."""
        live = _livekit_configured(settings) and _moss_configured(settings)
        return HealthResponse(
            status="ok",
            mode="live" if live else "mock",
            livekit_configured=_livekit_configured(settings),
            moss_configured=_moss_configured(settings),
        )

    @app.get("/config", response_model=ConfigResponse)
    async def config() -> ConfigResponse:
        """Public connection info for the frontend. Never returns secrets."""
        live = _livekit_configured(settings) and _moss_configured(settings)
        return ConfigResponse(livekit_url=settings.livekit_url, live=live)

    @app.post("/token", response_model=TokenResponse)
    async def token(body: TokenRequest) -> TokenResponse:
        """Mint a real LiveKit join token. 503 if the lib/keys are missing."""
        if not settings.has_livekit_credentials:
            raise HTTPException(
                status_code=503,
                detail=(
                    "LiveKit is not configured. Set LIVEKIT_URL, LIVEKIT_API_KEY "
                    "and LIVEKIT_API_SECRET to mint tokens."
                ),
            )
        try:
            from livekit import api as lk_api
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "The 'livekit-api' package is not installed. Install it with "
                    "pip install 'crossexam-backend[api]' to mint LiveKit tokens."
                ),
            ) from exc

        room = body.room or settings.livekit_default_room
        identity = body.identity or f"user-{uuid.uuid4().hex[:12]}"
        try:
            grant = lk_api.VideoGrants(room_join=True, room=room)
            access = (
                lk_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
                .with_identity(identity)
                .with_grants(grant)
            )
            # with_ttl exists on the real SDK; guard so a minimal fake signer
            # (tests) without it still works.
            with_ttl = getattr(access, "with_ttl", None)
            if callable(with_ttl):
                from datetime import timedelta

                access = with_ttl(timedelta(seconds=settings.token_ttl_seconds))
            jwt = access.to_jwt()
        except Exception as exc:  # noqa: BLE001 - surface signing failures clearly
            logger.exception("token.mint_failed room=%s", room)
            raise HTTPException(
                status_code=500, detail="Failed to mint LiveKit token."
            ) from exc
        try:
            await _ensure_agent_dispatch(settings, lk_api, room)
        except Exception:  # noqa: BLE001 - dispatch must not block token minting
            logger.exception(
                "token.agent_dispatch_failed room=%s agent=%s",
                room,
                settings.livekit_agent_name,
            )
        logger.info("token.minted room=%s identity=%s", room, identity)
        return TokenResponse(
            token=jwt,
            room=room,
            identity=identity,
            url=settings.livekit_url,
            livekit_url=settings.livekit_url,
        )

    @app.post("/documents", response_model=DocumentResponse)
    async def documents(file: UploadFile = File(...)) -> DocumentResponse:  # noqa: B008 - FastAPI dependency default
        """Upload a PDF, parse its text layer, and index the chunks."""
        content_type = (file.content_type or "").split(";")[0].strip().lower()
        filename = file.filename or ""
        if content_type not in _PDF_CONTENT_TYPES and not filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported content type {content_type!r}; expected a PDF.",
            )

        max_bytes = settings.max_upload_mb * 1024 * 1024
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        if len(data) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {settings.max_upload_mb} MB upload limit.",
            )
        if not data.startswith(_PDF_MAGIC):
            raise HTTPException(
                status_code=415,
                detail="File does not look like a PDF (missing %PDF- header).",
            )

        document_id = f"doc-{uuid.uuid4().hex[:12]}"
        tmp_path = Path(tempfile.gettempdir()) / f"{document_id}.pdf"
        tmp_path.write_bytes(data)
        try:
            records = ingest.parse_pdf_to_records(tmp_path, id_prefix=document_id)
        except ingest.PdfDependencyError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ingest.PdfParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            tmp_path.unlink(missing_ok=True)

        pages = ingest.page_count(records)
        if _moss_configured(settings):
            chunks_indexed, mode = await _index_to_moss(settings, records)
            if mode == "moss":
                # Refresh the live index so the worker queries the new docs.
                # The running app.state.index is a live MossIndex whose loaded
                # copy won't see the freshly-upserted document until it reloads,
                # so re-prewarm it (load_index + re-seed document ids) in place.
                # Best-effort: a reload failure must NOT 500 the upload — the doc
                # is already upserted server-side and becomes queryable on the
                # next refresh cycle.
                await _reload_live_index(app.state.index)
            else:
                # build_index fell back to the disk fixture (it did NOT upsert to
                # Moss). Reload the mock index from that fixture so queries see
                # the new docs, and report the honest "mock" mode.
                app.state.index = MockIndex.from_fixture(_resolve_fixture_path(settings))
        else:
            chunks_indexed, new_index = _index_to_mock_fixture(settings, records)
            app.state.index = new_index
            mode = "mock"

        logger.info(
            "documents.indexed id=%s pages=%d chunks=%d mode=%s",
            document_id,
            pages,
            chunks_indexed,
            mode,
        )
        return DocumentResponse(
            document_id=document_id,
            pages=pages,
            chunks_indexed=chunks_indexed,
            mode=mode,
        )

    return app


def main() -> None:
    """Entrypoint for ``python -m crossexam_backend.api`` / ``crossexam-api``."""
    import uvicorn

    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    logger.info(
        "crossexam-api starting host=%s port=%s cors=%s",
        settings.api_host,
        settings.api_port,
        settings.cors_origin_list,
    )
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
