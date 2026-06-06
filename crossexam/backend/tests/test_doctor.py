"""Tests for the preflight doctor (:mod:`crossexam_backend.doctor`).

These assert the doctor runs without network access and reports MOCK mode when
no keys are configured.
"""

from __future__ import annotations

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


def test_moss_missing_when_creds_set_but_no_sdk() -> None:
    """Credentials present but SDK absent -> Moss row is MISSING (visible)."""
    settings = Settings(
        moss_project_id="p",
        moss_project_key="k",
        use_mocks=False,
        _env_file=None,  # type: ignore[call-arg]
    )
    checks = {c.name: c for c in run_checks(settings)}
    # The real SDK is not installed in this environment.
    assert checks["Moss retrieval"].status is Status.MISSING


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


def test_off_rows_do_not_fail_overall_mode() -> None:
    """OFF rows are not MISSING; mock mode still resolves cleanly."""
    checks = run_checks(_no_keys_settings())
    assert overall_mode(checks) == "MOCK"
    assert not any(c.status is Status.MISSING for c in checks)


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
