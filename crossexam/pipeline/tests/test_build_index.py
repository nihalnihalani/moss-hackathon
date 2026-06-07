"""Tests for build_index: dry-run/disk JSON, the Moss SDK job lifecycle.

Also covers the real-SDK DocumentInfo contract.

Structure
---------
* Disk / dry-run path — unchanged behaviour, no SDK needed.
* Metadata contract — _chunk_metadata produces flat str-only dicts.
* Job-lifecycle tests — fake-client tests (no SDK) for COMPLETED, FAILED,
  timeout, existing-index (add_docs upsert), new-index (create_index).
* Fix-1/2 regression tests — arity detection and TypeError propagation;
  client construction thread.
* Real-SDK contract test — guarded by pytest.importorskip("inferedge_moss");
  builds real DocumentInfo objects from sample chunks and checks the metadata
  contract without making any network calls.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from crossexam_pipeline import build_index as bi
from crossexam_pipeline.build_index import (
    MossConfig,
    _chunk_metadata,
    _create_or_update_moss_index_async,
    _poll_job,
    build_index,
    build_index_async,
    write_chunks_json,
)
from crossexam_pipeline.fallback import DeterministicParser
from crossexam_pipeline.models import BBox, ParsedChunk


# --------------------------------------------------------------------------- #
# Shared helpers                                                               #
# --------------------------------------------------------------------------- #
def _full_chunk() -> ParsedChunk:
    """A chunk exercising every optional metadata field."""
    bbox = BBox(page=2, x0=1, y0=2, x1=3, y1=4, page_width=612, page_height=792)
    return ParsedChunk(
        id="c1",
        text="hello world",
        page=2,
        bbox=bbox,
        confidence=0.97,
        document_id="doc-xyz",
        document_title="My Doc",
        scanned=True,
        quads=[bbox],
    )


def _complete_config() -> MossConfig:
    """A MossConfig with all three credentials present."""
    return MossConfig(project_id="proj", api_key="key", index_name="crossexam-demo")


# --------------------------------------------------------------------------- #
# Fake Moss SDK surface                                                        #
# --------------------------------------------------------------------------- #
class FakeDocumentInfo:
    """Stub mirroring the verified ``DocumentInfo(id, text, metadata)`` shape."""

    def __init__(
        self,
        id: str,  # noqa: A002 - mirrors the SDK's DocumentInfo(id=...) kwarg
        text: str,
        metadata: dict[str, str],
        embedding: object = None,
    ) -> None:
        """Record the document fields exactly as passed."""
        self.id = id
        self.text = text
        self.metadata = metadata
        self.embedding = embedding


class FakeMutationOptions:
    """Stub for MutationOptions(upsert=True)."""

    def __init__(self, upsert: bool | None = None) -> None:
        """Record the upsert flag."""
        self.upsert = upsert


class FakeJobStatus:
    """Stub for JobStatus class-attribute string constants (mirrors real SDK)."""

    COMPLETED = "completed"
    FAILED = "failed"
    BUILDING = "building"
    PENDING_UPLOAD = "pending_upload"
    UPLOADING = "uploading"


def _make_mutation_result(
    job_id: str | None = "job-123",
    doc_count: int = 1,
    index_name: str = "crossexam-demo",
) -> SimpleNamespace:
    """Build a MutationResult-like object."""
    return SimpleNamespace(job_id=job_id, doc_count=doc_count, index_name=index_name)


def _job_status_resp(status: str, error: str | None = None) -> SimpleNamespace:
    """Build a JobStatusResponse-like object."""
    return SimpleNamespace(
        job_id="job-123",
        status=status,
        current_phase=None,
        progress=None,
        error=error,
        created_at=None,
        updated_at=None,
        completed_at=None,
    )


class FakeMossClient:
    """Recorded Moss client implementing the verified async create surface.

    Records the calls made so tests can assert the adapter forwards them
    correctly.  Configurable for the create/upsert decision and job-polling
    scenarios.

    Uses ``list_indexes()`` for the create-vs-upsert decision (mirroring the
    production code) rather than ``get_index`` so that tests cover the safe
    path that re-raises transient errors instead of silently creating.
    """

    last_instance: FakeMossClient | None = None

    def __init__(
        self,
        project_id: str | None,
        project_key: str | None,
        *,
        index_exists: bool = False,
        list_indexes_raises: bool = False,
        job_statuses: list[str] | None = None,
        job_error: str | None = None,
        mutation_job_id: str | None = "job-123",
        mutation_doc_count: int = 1,
    ) -> None:
        """Configure the fake's behaviour for a test scenario.

        Args:
            project_id: Moss project id (passed through; not validated by the fake).
            project_key: Moss project key (passed through; not validated).
            index_exists: When ``True``, ``list_indexes`` returns a list that
                contains the target index name, triggering the upsert path.
            list_indexes_raises: When ``True``, ``list_indexes`` raises a
                transient RuntimeError (tests the non-destructive re-raise path).
            job_statuses: Sequence of status strings returned by successive
                ``get_job_status`` calls.  Defaults to ``["completed"]``.
            job_error: Value placed on the ``error`` attribute of the job
                response when status is ``"failed"``.
            mutation_job_id: ``job_id`` returned by ``create_index``/``add_docs``.
            mutation_doc_count: ``doc_count`` returned by those same calls.
        """
        self.project_id = project_id
        self.project_key = project_key
        self._index_exists = index_exists
        self._list_indexes_raises = list_indexes_raises
        self._job_statuses = list(job_statuses or ["completed"])
        self._job_error = job_error
        self._mutation_job_id = mutation_job_id
        self._mutation_doc_count = mutation_doc_count
        self._status_idx = 0

        self.create_calls: list[tuple[str, list[FakeDocumentInfo], object]] = []
        self.add_docs_calls: list[tuple[str, list[FakeDocumentInfo], object]] = []
        self.get_job_status_calls: list[str] = []
        FakeMossClient.last_instance = self

    async def list_indexes(self) -> list[SimpleNamespace]:
        """Return a list containing the target index, or raise, per config."""
        if self._list_indexes_raises:
            raise RuntimeError("transient network error")
        if self._index_exists:
            return [SimpleNamespace(name="crossexam-demo")]
        return []

    async def create_index(
        self,
        name: str,
        documents: list[FakeDocumentInfo],
        model_id: object = "__unset__",
    ) -> object:
        """Record the call and return a MutationResult-like."""
        self.create_calls.append((name, documents, model_id))
        return _make_mutation_result(
            job_id=self._mutation_job_id,
            doc_count=self._mutation_doc_count,
        )

    async def add_docs(
        self,
        name: str,
        documents: list[FakeDocumentInfo],
        options: object = None,
    ) -> object:
        """Record the call and return a MutationResult-like."""
        self.add_docs_calls.append((name, documents, options))
        return _make_mutation_result(
            job_id=self._mutation_job_id,
            doc_count=self._mutation_doc_count,
        )

    async def get_job_status(self, job_id: str) -> SimpleNamespace:
        """Return successive statuses from the configured list."""
        self.get_job_status_calls.append(job_id)
        idx = min(self._status_idx, len(self._job_statuses) - 1)
        status = self._job_statuses[idx]
        self._status_idx += 1
        error = self._job_error if status == FakeJobStatus.FAILED else None
        return _job_status_resp(status, error=error)


def _fake_moss_module(
    client_cls: type | None = None,
    *,
    index_exists: bool = False,
    list_indexes_raises: bool = False,
    job_statuses: list[str] | None = None,
    job_error: str | None = None,
    mutation_job_id: str | None = "job-123",
    mutation_doc_count: int = 1,
) -> ModuleType:
    """Build a fake SDK module with a pre-configured FakeMossClient."""
    if client_cls is None:
        _ie = index_exists
        _lir = list_indexes_raises
        _js = job_statuses
        _je = job_error
        _jid = mutation_job_id
        _dc = mutation_doc_count

        class _Configured(FakeMossClient):
            def __init__(self, pid: str | None, pkey: str | None) -> None:
                super().__init__(
                    pid,
                    pkey,
                    index_exists=_ie,
                    list_indexes_raises=_lir,
                    job_statuses=_js,
                    job_error=_je,
                    mutation_job_id=_jid,
                    mutation_doc_count=_dc,
                )

        client_cls = _Configured

    module = ModuleType("fake_moss")
    module.MossClient = client_cls  # type: ignore[attr-defined]
    module.DocumentInfo = FakeDocumentInfo  # type: ignore[attr-defined]
    module.MutationOptions = FakeMutationOptions  # type: ignore[attr-defined]
    module.JobStatus = FakeJobStatus  # type: ignore[attr-defined]
    return module


# --------------------------------------------------------------------------- #
# Disk / dry-run path (unchanged behaviour)                                    #
# --------------------------------------------------------------------------- #
def test_dry_run_writes_backend_compatible_chunks(tmp_path: Path) -> None:
    """Dry-run writes the exact backend mock-index JSON shape."""
    chunks = DeterministicParser().parse()
    out = tmp_path / "sample_chunks.json"

    summary = build_index(chunks, index_name="crossexam-demo", out_path=out, dry_run=True)

    assert summary["mode"] == "dry-run"
    assert summary["chunk_count"] == len(chunks)
    assert out.exists()

    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data

    for rec in data:
        assert {"id", "text", "page", "bbox", "confidence", "documentId"} <= set(rec)
        assert set(rec.keys()) <= {
            "id",
            "text",
            "page",
            "bbox",
            "confidence",
            "documentId",
            "documentTitle",
            "scanned",
            "quads",
        }
        assert set(rec["bbox"].keys()) == {
            "page",
            "x0",
            "y0",
            "x1",
            "y1",
            "page_width",
            "page_height",
        }
        assert isinstance(rec["page"], int)
        assert isinstance(rec["confidence"], float)
        ParsedChunk.model_validate(rec)


def test_disk_mode_when_no_moss_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without Moss credentials, build_index falls back to disk mode."""
    for var in ("MOSS_PROJECT_ID", "MOSS_PROJECT_KEY", "MOSS_INDEX_NAME"):
        monkeypatch.delenv(var, raising=False)
    chunks = DeterministicParser().parse()
    out = tmp_path / "chunks.json"

    summary = build_index(chunks, index_name="x", out_path=out, dry_run=False)

    assert summary["mode"] == "disk"
    assert out.exists()


def test_write_is_idempotent(tmp_path: Path) -> None:
    """Writing the same chunks in any order yields identical bytes."""
    chunks = DeterministicParser().parse()
    out = tmp_path / "chunks.json"
    write_chunks_json(chunks, out)
    first = out.read_text(encoding="utf-8")
    write_chunks_json(list(reversed(chunks)), out)
    second = out.read_text(encoding="utf-8")
    assert first == second


def test_output_round_trips_to_parsed_chunks(tmp_path: Path) -> None:
    """Written JSON re-validates back into ParsedChunk records."""
    chunks = DeterministicParser().parse()
    out = tmp_path / "chunks.json"
    write_chunks_json(chunks, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    loaded = [ParsedChunk.model_validate(rec) for rec in data]
    assert len(loaded) == len(chunks)


# --------------------------------------------------------------------------- #
# Metadata contract (string-only)                                             #
# --------------------------------------------------------------------------- #
def test_chunk_metadata_values_are_all_strings() -> None:
    """EVERY Moss metadata value must be a str (no dicts/ints/floats/bools)."""
    meta = _chunk_metadata(_full_chunk())
    assert all(isinstance(v, str) for v in meta.values())


def test_chunk_metadata_documentid_only_in_metadata() -> None:
    """The documentId lives in metadata (DocumentInfo has no top-level field)."""
    meta = _chunk_metadata(_full_chunk())
    assert meta["documentId"] == "doc-xyz"
    assert meta["scanned"] == "true"
    assert meta["page"] == "2"
    assert meta["confidence"] == "0.97"
    assert meta["documentTitle"] == "My Doc"


def test_chunk_metadata_bbox_words_quads_round_trip() -> None:
    """bbox/words/quads are JSON strings that load back to structured data."""
    meta = _chunk_metadata(_full_chunk())
    bbox = json.loads(meta["bbox"])
    assert set(bbox.keys()) == {"page", "x0", "y0", "x1", "y1", "page_width", "page_height"}
    quads = json.loads(meta["quads"])
    assert isinstance(quads, list) and len(quads) == 1
    words = json.loads(meta["words"])
    assert isinstance(words, list)
    # Per-word confidence is serialised as a string for internal consistency.
    if words:
        assert isinstance(words[0]["confidence"], str)


def test_chunk_metadata_omits_optional_when_absent() -> None:
    """documentTitle/quads are omitted when unset; scanned=false stringified."""
    bbox = BBox(page=1, x0=0, y0=0, x1=1, y1=1, page_width=612, page_height=792)
    chunk = ParsedChunk(
        id="c2",
        text="plain chunk",
        page=1,
        bbox=bbox,
        confidence=1.0,
        document_id="doc-1",
        document_title=None,
        scanned=False,
        quads=[],
    )
    meta = _chunk_metadata(chunk)
    assert meta["documentId"] == "doc-1"
    assert meta["scanned"] == "false"
    assert "documentTitle" not in meta
    assert "quads" not in meta


# --------------------------------------------------------------------------- #
# Job lifecycle — fake-client tests (no SDK needed)                           #
# --------------------------------------------------------------------------- #
def test_job_polls_to_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Polling resolves when get_job_status returns COMPLETED."""
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "10")
    module = _fake_moss_module(
        job_statuses=["building", "building", "completed"],
        mutation_doc_count=3,
    )
    count = asyncio.run(
        _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
    )
    assert count == 3
    client = FakeMossClient.last_instance
    assert client is not None
    assert client.get_job_status_calls == ["job-123", "job-123", "job-123"]
    assert len(client.create_calls) == 1


def test_job_failed_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A FAILED job status raises RuntimeError including the error detail."""
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "10")
    module = _fake_moss_module(
        job_statuses=["building", "failed"],
        job_error="embedding model crashed",
    )
    with pytest.raises(RuntimeError, match="embedding model crashed"):
        asyncio.run(
            _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
        )


def test_job_timeout_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job stuck at BUILDING times out and raises RuntimeError.

    Uses timeout_s=0 so the deadline is already past before the first poll,
    guaranteeing the timeout path fires without relying on wall-clock precision.
    """
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "0")
    module = _fake_moss_module(job_statuses=["building"] * 100)
    with pytest.raises(RuntimeError, match="did not complete within"):
        asyncio.run(
            _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
        )


def test_existing_index_uses_add_docs_upsert(monkeypatch: pytest.MonkeyPatch) -> None:
    """When list_indexes returns the target name, add_docs(upsert=True) is used."""
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "10")
    module = _fake_moss_module(index_exists=True, job_statuses=["completed"])
    asyncio.run(
        _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
    )
    client = FakeMossClient.last_instance
    assert client is not None
    assert len(client.create_calls) == 0, "create_index must NOT be called for existing index"
    assert len(client.add_docs_calls) == 1
    _name, _docs, options = client.add_docs_calls[0]
    assert options.upsert is True


def test_new_index_uses_create_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """When list_indexes returns an empty list, create_index is called (not add_docs)."""
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "10")
    module = _fake_moss_module(index_exists=False, job_statuses=["completed"])
    asyncio.run(
        _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
    )
    client = FakeMossClient.last_instance
    assert client is not None
    assert len(client.add_docs_calls) == 0, "add_docs must NOT be called for new index"
    assert len(client.create_calls) == 1


def test_transient_list_indexes_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient list_indexes error propagates — must NOT silently create the index.

    This is the data-loss guard: a network/auth error on list_indexes must NOT
    be treated as "index absent -> create_index", which would destroy documents
    in an existing index.
    """
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "10")
    module = _fake_moss_module(list_indexes_raises=True)
    with pytest.raises(RuntimeError, match="transient network error"):
        asyncio.run(
            _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
        )
    # Confirm neither mutation was called.
    client = FakeMossClient.last_instance
    assert client is not None
    assert client.create_calls == [], "create_index must NOT be called after list_indexes error"
    assert client.add_docs_calls == [], "add_docs must NOT be called after list_indexes error"


def test_no_job_id_skips_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """When MutationResult.job_id is None/falsy, polling is skipped entirely."""
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "0")  # timeout=0 → would fail if polled
    module = _fake_moss_module(mutation_job_id=None)
    # Should succeed without raising a timeout error.
    asyncio.run(
        _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
    )
    client = FakeMossClient.last_instance
    assert client is not None
    assert client.get_job_status_calls == [], "no polling when job_id is falsy"


def test_build_index_async_moss_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_index_async with creds reports moss mode and SDK doc count."""
    chunks = DeterministicParser().parse()
    for var, val in (
        ("MOSS_PROJECT_ID", "proj"),
        ("MOSS_PROJECT_KEY", "key"),
        ("MOSS_INDEX_NAME", "crossexam-demo"),
        ("MOSS_JOB_POLL_INTERVAL_S", "0"),
        ("MOSS_JOB_TIMEOUT_S", "10"),
    ):
        monkeypatch.setenv(var, val)
    monkeypatch.setattr(
        bi,
        "_load_moss_module",
        lambda: _fake_moss_module(job_statuses=["completed"], mutation_doc_count=len(chunks)),
    )
    summary = asyncio.run(build_index_async(chunks, dry_run=False))
    assert summary["mode"] == "moss"
    assert summary["index"] == "crossexam-demo"
    assert summary["chunk_count"] == len(chunks)


def test_build_index_sync_wraps_async(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """build_index (sync) wraps build_index_async; disk path still works."""
    for var in ("MOSS_PROJECT_ID", "MOSS_PROJECT_KEY", "MOSS_INDEX_NAME"):
        monkeypatch.delenv(var, raising=False)
    chunks = DeterministicParser().parse()
    out = tmp_path / "chunks.json"
    summary = build_index(chunks, out_path=out, dry_run=True)
    assert summary["mode"] == "dry-run"
    assert out.exists()


def test_create_moss_index_defaults_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_id defaults to 'moss-minilm' when MOSS_MODEL_ID is unset."""
    monkeypatch.delenv("MOSS_MODEL_ID", raising=False)
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "10")
    module = _fake_moss_module(job_statuses=["completed"])
    asyncio.run(
        _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
    )
    client = FakeMossClient.last_instance
    assert client is not None
    assert len(client.create_calls) == 1
    _name, _docs, model_id = client.create_calls[0]
    assert model_id == "moss-minilm"


def test_create_moss_index_reads_model_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """MOSS_MODEL_ID overrides the default model id."""
    monkeypatch.setenv("MOSS_MODEL_ID", "moss-mediumlm")
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "10")
    module = _fake_moss_module(job_statuses=["completed"])
    asyncio.run(
        _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
    )
    client = FakeMossClient.last_instance
    assert client is not None
    _name, _docs, model_id = client.create_calls[0]
    assert model_id == "moss-mediumlm"


def test_create_moss_index_passes_positional_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MossClient is constructed with positional (project_id, project_key)."""
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "10")
    module = _fake_moss_module(job_statuses=["completed"])
    asyncio.run(
        _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
    )
    client = FakeMossClient.last_instance
    assert client is not None
    assert client.project_id == "proj"
    assert client.project_key == "key"


# --------------------------------------------------------------------------- #
# SDK-missing path                                                            #
# --------------------------------------------------------------------------- #
def test_create_moss_index_raises_when_sdk_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clear RuntimeError tells the user to pip install '.[moss]'."""
    monkeypatch.setattr(bi, "_load_moss_module", lambda: None)
    with pytest.raises(RuntimeError, match=r"pip install"):
        asyncio.run(_create_or_update_moss_index_async([_full_chunk()], _complete_config()))


# --------------------------------------------------------------------------- #
# Fix-1 regression: TypeError propagation                                     #
# --------------------------------------------------------------------------- #
class _RealTypeErrorClient:
    """Client whose create_index raises a TypeError unrelated to model_id."""

    def __init__(self, _pid: str | None, _pkey: str | None) -> None:
        """Accept positional credentials."""

    async def list_indexes(self) -> list[object]:
        """Return empty list to trigger the create path."""
        return []

    async def create_index(
        self, _name: str, _docs: list[object], _model_id: object = None
    ) -> object:
        """Raise a TypeError that is NOT about model_id."""
        raise TypeError("unhashable type: 'list'")

    async def add_docs(self, *_args: object, **_kwargs: object) -> object:
        """Not used in this test path."""
        raise AssertionError("add_docs should not be called")  # noqa: TRY301


def test_real_type_error_propagates_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A TypeError unrelated to model_id must propagate, not trigger a 2-arg retry."""
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "10")

    module = ModuleType("fake_rte")
    module.MossClient = _RealTypeErrorClient  # type: ignore[attr-defined]
    module.DocumentInfo = FakeDocumentInfo  # type: ignore[attr-defined]
    module.MutationOptions = FakeMutationOptions  # type: ignore[attr-defined]
    module.JobStatus = FakeJobStatus  # type: ignore[attr-defined]

    with pytest.raises(TypeError, match="unhashable"):
        asyncio.run(
            _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
        )


# --------------------------------------------------------------------------- #
# Fix-2 regression: client constructed inside the coroutine                  #
# --------------------------------------------------------------------------- #
class _ThreadTrackingClient:
    """Records which thread __init__ and create_index run on."""

    init_thread: str = ""
    call_thread: str = ""

    def __init__(self, _pid: str | None, _pkey: str | None) -> None:
        """Record the thread __init__ runs on."""
        import threading

        _ThreadTrackingClient.init_thread = threading.current_thread().name

    async def list_indexes(self) -> list[object]:
        """Return empty list to trigger the create path."""
        return []

    async def create_index(
        self, _name: str, _docs: list[object], _model_id: object = None
    ) -> object:
        """Record the thread create_index runs on."""
        import threading

        _ThreadTrackingClient.call_thread = threading.current_thread().name
        return _make_mutation_result(job_id=None)

    async def add_docs(self, *_args: object, **_kwargs: object) -> object:
        """Not used in this test path."""
        raise AssertionError("add_docs should not be called")  # noqa: TRY301


def test_client_constructed_inside_coroutine(monkeypatch: pytest.MonkeyPatch) -> None:
    """MossClient.__init__ and create_index execute on the same thread."""
    monkeypatch.setenv("MOSS_JOB_POLL_INTERVAL_S", "0")
    monkeypatch.setenv("MOSS_JOB_TIMEOUT_S", "10")

    module = ModuleType("fake_thread")
    module.MossClient = _ThreadTrackingClient  # type: ignore[attr-defined]
    module.DocumentInfo = FakeDocumentInfo  # type: ignore[attr-defined]
    module.MutationOptions = FakeMutationOptions  # type: ignore[attr-defined]
    module.JobStatus = FakeJobStatus  # type: ignore[attr-defined]

    asyncio.run(
        _create_or_update_moss_index_async([_full_chunk()], _complete_config(), module=module)
    )
    assert _ThreadTrackingClient.init_thread != ""
    assert _ThreadTrackingClient.init_thread == _ThreadTrackingClient.call_thread


# --------------------------------------------------------------------------- #
# _poll_job unit tests                                                        #
# --------------------------------------------------------------------------- #
class _PollClient:
    """Minimal client that serves a canned list of statuses to _poll_job."""

    def __init__(self, statuses: list[str], error: str | None = None) -> None:
        """Initialise with the sequence of statuses to return."""
        self._statuses = statuses
        self._error = error
        self._idx = 0
        self.calls: list[str] = []

    async def get_job_status(self, job_id: str) -> SimpleNamespace:
        """Return the next status in the canned sequence."""
        self.calls.append(job_id)
        status = self._statuses[min(self._idx, len(self._statuses) - 1)]
        self._idx += 1
        return _job_status_resp(status, error=self._error if status == "failed" else None)


def test_poll_job_completes_immediately() -> None:
    """_poll_job resolves on the first poll if already COMPLETED."""
    client = _PollClient(["completed"])
    asyncio.run(_poll_job(client, "j1", "completed", "failed", timeout_s=5.0, poll_interval_s=0.0))
    assert client.calls == ["j1"]


def test_poll_job_retries_then_completes() -> None:
    """_poll_job waits through BUILDING states before COMPLETED."""
    client = _PollClient(["building", "building", "completed"])
    asyncio.run(_poll_job(client, "j2", "completed", "failed", timeout_s=5.0, poll_interval_s=0.0))
    assert len(client.calls) == 3


def test_poll_job_failed_carries_error() -> None:
    """_poll_job raises RuntimeError with the error field on FAILED."""
    client = _PollClient(["failed"], error="disk full")
    with pytest.raises(RuntimeError, match="disk full"):
        asyncio.run(
            _poll_job(client, "j3", "completed", "failed", timeout_s=5.0, poll_interval_s=0.0)
        )


def test_poll_job_timeout() -> None:
    """_poll_job raises RuntimeError when timeout_s=0 (deadline already passed)."""
    client = _PollClient(["building"] * 50)
    with pytest.raises(RuntimeError, match="did not complete within"):
        asyncio.run(
            _poll_job(client, "j4", "completed", "failed", timeout_s=0.0, poll_interval_s=0.0)
        )
    # With timeout_s=0 the loop body is never entered; no polls should occur.
    assert client.calls == []


# --------------------------------------------------------------------------- #
# Real-SDK contract test (requires inferedge_moss; skipped otherwise)        #
# --------------------------------------------------------------------------- #
def test_real_document_info_metadata_contract() -> None:
    """Real DocumentInfo objects carry str-only metadata and parseable JSON fields.

    Uses the actual inferedge_moss.DocumentInfo from the installed SDK to
    confirm our _chunk_metadata output is accepted as-is.  No network calls.
    """
    inferedge_moss = pytest.importorskip("inferedge_moss")

    chunks = DeterministicParser().parse()
    for c in chunks[:5]:  # spot-check the first five
        meta = _chunk_metadata(c)

        # Every top-level metadata value must be a plain str.
        bad = {k: type(v) for k, v in meta.items() if not isinstance(v, str)}
        assert not bad, f"Non-str metadata for chunk {c.id}: {bad}"

        # The real SDK must accept the metadata dict without raising.
        doc = inferedge_moss.DocumentInfo(id=str(c.id), text=str(c.text), metadata=meta)
        assert doc.id == str(c.id)
        assert doc.text == str(c.text)
        assert doc.metadata == meta

        # Structured fields must round-trip through json.loads.
        bbox = json.loads(meta["bbox"])
        assert set(bbox.keys()) >= {"page", "x0", "y0", "x1", "y1"}

        words = json.loads(meta["words"])
        assert isinstance(words, list)
        for w in words:
            assert isinstance(w.get("confidence"), str), (
                f"per-word confidence must be str, got {type(w.get('confidence'))}"
            )

        if "quads" in meta:
            quads = json.loads(meta["quads"])
            assert isinstance(quads, list) and len(quads) > 0
