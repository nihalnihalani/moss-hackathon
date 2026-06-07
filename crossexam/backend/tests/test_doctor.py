"""Tests for the preflight doctor (:mod:`crossexam_backend.doctor`).

These assert the doctor runs without network access and reports MOCK mode when
no keys are configured.
"""

from __future__ import annotations

import types

import pytest

from crossexam_backend import doctor
from crossexam_backend.config import Settings, get_settings
from crossexam_backend.doctor import Status, overall_mode, run_checks


def _no_keys_settings() -> Settings:
    """Settings with no credentials at all -> everything mock/missing."""
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_reports_mock_mode_when_no_keys() -> None:
    """With no Moss/LiveKit keys, the overall mode is MOCK."""
    checks = run_checks(_no_keys_settings())
    assert overall_mode(checks) == "MOCK"


def test_retrieval_is_mock_when_no_keys() -> None:
    """The retrieval row is MOCK (MockIndex) when no Moss credentials are set."""
    checks = {c.name: c for c in run_checks(_no_keys_settings())}
    assert checks["Moss retrieval"].status is Status.MOCK


def test_all_rows_present() -> None:
    """The table covers retrieval, runtime, all three provider legs and VAD."""
    names = {c.name for c in run_checks(_no_keys_settings())}
    assert "Moss retrieval" in names
    assert "LiveKit runtime" in names
    assert any(n.startswith("STT provider") for n in names)
    assert any(n.startswith("LLM provider") for n in names)
    assert any(n.startswith("TTS provider") for n in names)
    assert any(n.startswith("VAD") for n in names)


def test_moss_missing_when_creds_set_but_no_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credentials present but SDK absent -> Moss row is MISSING (visible).

    Forces the SDK-absent case by patching ``_moss_import_available`` so the test
    is deterministic whether or not ``inferedge_moss`` is installed in the env.
    """
    monkeypatch.setattr(doctor, "_moss_import_available", lambda: None)
    settings = Settings(
        moss_project_id="p",
        moss_project_key="k",
        use_mocks=False,
        _env_file=None,  # type: ignore[call-arg]
    )
    checks = {c.name: c for c in run_checks(settings)}
    assert checks["Moss retrieval"].status is Status.MISSING


def test_moss_ready_unverified_when_creds_and_sdk_present_no_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creds + SDK present, no --probe -> READY (index existence unverified).

    Patches ``_moss_import_available`` to force the SDK-present case so the test
    passes regardless of ambient install state.
    """
    monkeypatch.setattr(doctor, "_moss_import_available", lambda: "inferedge_moss")
    settings = Settings(
        moss_project_id="p",
        moss_project_key="k",
        use_mocks=False,
        _env_file=None,  # type: ignore[call-arg]
    )
    row = {c.name: c for c in run_checks(settings)}["Moss retrieval"]
    assert row.status is Status.READY
    assert row.status is not Status.MISSING
    assert "unverified" in row.detail.lower()


def test_tracing_and_proactive_rows_present() -> None:
    """The table reports tracing + proactive readiness rows."""
    names = {c.name for c in run_checks(_no_keys_settings())}
    assert "Tracing (Langfuse)" in names
    assert "Proactive surfacing" in names


def test_tracing_off_and_proactive_ready_by_default() -> None:
    """No obs config -> tracing OFF; proactive defaults on -> READY."""
    checks = {c.name: c for c in run_checks(_no_keys_settings())}
    assert checks["Tracing (Langfuse)"].status is Status.OFF
    assert checks["Proactive surfacing"].status is Status.READY


def test_multihop_and_memory_rows_present_and_ready() -> None:
    """The table reports multi-hop routing + conversation memory readiness."""
    checks = {c.name: c for c in run_checks(_no_keys_settings())}
    assert "Multi-hop routing" in checks
    assert "Conversation memory" in checks
    # Both default-on / always-available -> READY, never MISSING.
    assert checks["Multi-hop routing"].status is Status.READY
    assert checks["Conversation memory"].status is Status.READY


def test_off_rows_do_not_fail_overall_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No creds -> mock mode resolves cleanly with NO MISSING rows.

    The Moss row must be MOCK (never MISSING) when no Moss creds are set, even if
    ``inferedge_moss`` is installed in the env. Patch ``_moss_import_available``
    to SDK-present to prove the absence of creds — not the absence of the SDK —
    is what keeps the row out of MISSING.
    """
    monkeypatch.setattr(doctor, "_moss_import_available", lambda: "inferedge_moss")
    checks = run_checks(_no_keys_settings())
    assert overall_mode(checks) == "MOCK"
    # With no Moss creds the Moss row must be MOCK (never MISSING), regardless of
    # whether the SDK is installed. (LiveKit/VAD rows are env-coupled to whether
    # livekit-agents/silero are installed and are out of this fix's scope.)
    moss_row = {c.name: c for c in checks}["Moss retrieval"]
    assert moss_row.status is Status.MOCK
    assert moss_row.status is not Status.MISSING


def test_probe_reports_index_status_and_doc_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--probe reports the real index status + doc_count from a fake client.

    Patches the SDK-present check and the module importer so the probe runs
    against a fake ``MossClient`` whose async lifecycle returns IndexInfo-like
    objects, and asserts the detail string reflects status/doc_count and that the
    Ready status maps to a READY row.
    """

    class _FakeStatus:
        value = "Ready"

    class _FakeIndexInfo:
        def __init__(self, name: str) -> None:
            self.name = name
            self.status = _FakeStatus()
            self.doc_count = 42

    class _FakeClient:
        def __init__(self, project_id: str, project_key: str) -> None:
            self.project_id = project_id

        async def list_indexes(self) -> list[_FakeIndexInfo]:
            return [_FakeIndexInfo("crossexam")]

        async def get_index(self, name: str) -> _FakeIndexInfo:
            return _FakeIndexInfo(name)

        async def load_index(self, name: str) -> str:
            return "Ready"

        async def close(self) -> None:
            return None

    fake_module = types.SimpleNamespace(MossClient=_FakeClient)
    monkeypatch.setattr(doctor, "_moss_import_available", lambda: "inferedge_moss")
    monkeypatch.setattr(
        doctor.importlib, "import_module", lambda name: fake_module
    )

    settings = Settings(
        moss_project_id="p",
        moss_project_key="k",
        moss_index_name="crossexam",
        use_mocks=False,
        _env_file=None,  # type: ignore[call-arg]
    )
    row = {c.name: c for c in run_checks(settings, probe=True)}["Moss retrieval"]
    assert row.status is Status.READY
    assert "status=Ready" in row.detail
    assert "docs=42" in row.detail


def test_probe_missing_when_index_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """--probe -> MISSING with a clear detail when the index is not found."""

    class _FakeOther:
        name = "some-other-index"

    class _FakeClient:
        def __init__(self, project_id: str, project_key: str) -> None:
            pass

        async def list_indexes(self) -> list[_FakeOther]:
            return [_FakeOther()]

        async def close(self) -> None:
            return None

    fake_module = types.SimpleNamespace(MossClient=_FakeClient)
    monkeypatch.setattr(doctor, "_moss_import_available", lambda: "inferedge_moss")
    monkeypatch.setattr(
        doctor.importlib, "import_module", lambda name: fake_module
    )

    settings = Settings(
        moss_project_id="p",
        moss_project_key="k",
        moss_index_name="crossexam",
        use_mocks=False,
        _env_file=None,  # type: ignore[call-arg]
    )
    row = {c.name: c for c in run_checks(settings, probe=True)}["Moss retrieval"]
    assert row.status is Status.MISSING
    assert "not found" in row.detail.lower()


def test_format_table_is_text() -> None:
    """The rendered table is a non-empty multi-line string with a header."""
    table = doctor.format_table(run_checks(_no_keys_settings()))
    assert "SUBSYSTEM" in table
    assert "STATUS" in table
    assert table.count("\n") >= 5


def test_main_runs_and_returns_int(capsys: pytest.CaptureFixture[str]) -> None:
    """main() prints the table and returns an int exit code without network."""
    get_settings.cache_clear()
    try:
        code = doctor.main()
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "CrossExam backend preflight" in out
    assert "resolved mode:" in out
    assert isinstance(code, int)
