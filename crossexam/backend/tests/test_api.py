"""Tests for the FastAPI HTTP service (:mod:`crossexam_backend.api`).

Every test runs WITHOUT live keys or optional deps:

* ``/healthz`` reports mock mode when no keys are set.
* ``/config`` never leaks secrets.
* ``/token`` returns 503 with a clear message when keys/lib are absent, and a
  200 path is covered by faking the ``livekit.api`` module + creds.
* ``/documents`` indexes a tiny generated PDF in mock mode (chunks_indexed > 0)
  and rejects a non-PDF upload.

The PDF tests build a real born-digital PDF with a text layer using
``reportlab`` when available, and are skipped otherwise (the parse path needs a
text layer; ``reportlab`` is the only way to synthesize one here without
shipping a binary fixture).
"""

from __future__ import annotations

import io
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crossexam_backend.api import create_app
from crossexam_backend.config import Settings

try:
    from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

    _HAVE_REPORTLAB = True
except ImportError:  # pragma: no cover - reportlab is optional in CI
    _HAVE_REPORTLAB = False

try:
    import pdfplumber  # type: ignore[import-untyped]  # noqa: F401

    _HAVE_PDFPLUMBER = True
except ImportError:  # pragma: no cover
    _HAVE_PDFPLUMBER = False


def _mock_settings(tmp_path: Path) -> Settings:
    """Settings with no keys and an isolated, throwaway fixture path."""
    fixture = tmp_path / "chunks.json"
    return Settings(
        mock_fixture_path=str(fixture),
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A TestClient over an app in pure mock mode with an isolated fixture."""
    app = create_app(_mock_settings(tmp_path))
    with TestClient(app) as c:
        yield c


def _make_pdf_bytes() -> bytes:
    """Build a tiny born-digital PDF with an extractable text layer."""
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    pdf.drawString(72, 720, "The witness confirmed the warehouse was illuminated.")
    pdf.drawString(72, 700, "Q. Where were you on the night of the 14th?")
    pdf.showPage()
    pdf.drawString(72, 720, "A. I was at the Harbor Street warehouse past midnight.")
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# /healthz                                                                    #
# --------------------------------------------------------------------------- #
def test_healthz_mock_mode_with_no_keys(client: TestClient) -> None:
    """No keys -> status ok, mock mode, neither integration configured."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mode"] == "mock"
    assert body["livekit_configured"] is False
    assert body["moss_configured"] is False


# --------------------------------------------------------------------------- #
# /config                                                                     #
# --------------------------------------------------------------------------- #
def test_config_never_leaks_secrets(tmp_path: Path) -> None:
    """/config returns only livekit_url + live; never any secret value."""
    settings = Settings(
        livekit_url="wss://example.livekit.cloud",
        livekit_api_key="SECRET_KEY_VALUE",
        livekit_api_secret="SECRET_SECRET_VALUE",
        moss_project_id="proj",
        moss_project_key="MOSS_SECRET_VALUE",
        mock_fixture_path=str(tmp_path / "chunks.json"),
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        resp = client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"livekit_url", "live"}
    assert body["livekit_url"] == "wss://example.livekit.cloud"
    serialized = resp.text
    for secret in ("SECRET_KEY_VALUE", "SECRET_SECRET_VALUE", "MOSS_SECRET_VALUE"):
        assert secret not in serialized


# --------------------------------------------------------------------------- #
# /token                                                                      #
# --------------------------------------------------------------------------- #
def test_token_503_when_keys_absent(client: TestClient) -> None:
    """No LiveKit creds -> 503 with a clear, actionable message."""
    resp = client.post("/token", json={})
    assert resp.status_code == 503
    assert "LiveKit is not configured" in resp.json()["detail"]


def test_token_503_when_lib_missing_but_keys_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creds present but livekit-api not importable -> 503 mentioning the lib."""
    # Ensure importing `livekit` fails even if it happens to be installed.
    monkeypatch.setitem(sys.modules, "livekit", None)
    settings = Settings(
        livekit_url="wss://x",
        livekit_api_key="k",
        livekit_api_secret="s",
        mock_fixture_path=str(tmp_path / "chunks.json"),
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        resp = client.post("/token", json={"room": "r", "identity": "i"})
    assert resp.status_code == 503
    assert "livekit-api" in resp.json()["detail"]


def test_token_200_with_fake_signer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With creds + a faked livekit.api signer, /token returns a 200 token."""

    class _FakeGrants:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class _FakeAccessToken:
        def __init__(self, key: str, secret: str) -> None:
            self.key = key
            self.secret = secret

        def with_identity(self, identity: str) -> _FakeAccessToken:
            self.identity = identity
            return self

        def with_grants(self, grants: object) -> _FakeAccessToken:
            self.grants = grants
            return self

        def to_jwt(self) -> str:
            return f"fake.jwt.for.{self.identity}"

    fake_livekit = types.ModuleType("livekit")
    fake_api = types.ModuleType("livekit.api")
    fake_api.AccessToken = _FakeAccessToken  # type: ignore[attr-defined]
    fake_api.VideoGrants = _FakeGrants  # type: ignore[attr-defined]
    fake_livekit.api = fake_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    monkeypatch.setitem(sys.modules, "livekit.api", fake_api)

    settings = Settings(
        livekit_url="wss://x",
        livekit_api_key="key",
        livekit_api_secret="secret",
        mock_fixture_path=str(tmp_path / "chunks.json"),
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        resp = client.post("/token", json={"room": "room-1", "identity": "alice"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] == "fake.jwt.for.alice"
    assert body["room"] == "room-1"
    assert body["identity"] == "alice"
    # `url` is the field the frontend reads; `livekit_url` is the back-compat
    # alias. Both must carry the LiveKit ws URL.
    assert body["url"] == "wss://x"
    assert body["livekit_url"] == "wss://x"
    # The secret must not leak into the response.
    assert "secret" not in resp.text


def test_token_response_includes_url_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/token MUST include `url` (the field the frontend connects with).

    A missing `url` is why live mode never engaged; this locks the contract.
    """

    class _FakeGrants:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class _FakeAccessToken:
        def __init__(self, key: str, secret: str) -> None:
            self.key = key
            self.secret = secret

        def with_identity(self, identity: str) -> _FakeAccessToken:
            self.identity = identity
            return self

        def with_grants(self, grants: object) -> _FakeAccessToken:
            self.grants = grants
            return self

        def to_jwt(self) -> str:
            return "fake.jwt"

    fake_livekit = types.ModuleType("livekit")
    fake_api = types.ModuleType("livekit.api")
    fake_api.AccessToken = _FakeAccessToken  # type: ignore[attr-defined]
    fake_api.VideoGrants = _FakeGrants  # type: ignore[attr-defined]
    fake_livekit.api = fake_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    monkeypatch.setitem(sys.modules, "livekit.api", fake_api)

    settings = Settings(
        livekit_url="wss://room.example",
        livekit_api_key="key",
        livekit_api_secret="secret",
        mock_fixture_path=str(tmp_path / "chunks.json"),
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        resp = client.post("/token", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert "url" in body
    assert body["url"] == "wss://room.example"
    assert body["livekit_url"] == body["url"]


def test_token_defaults_room_and_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty body -> default room from settings + a generated identity."""

    class _FakeAccessToken:
        def __init__(self, key: str, secret: str) -> None:
            pass

        def with_identity(self, identity: str) -> _FakeAccessToken:
            self._id = identity
            return self

        def with_grants(self, grants: object) -> _FakeAccessToken:
            return self

        def to_jwt(self) -> str:
            return "jwt"

    fake_api = types.ModuleType("livekit.api")
    fake_api.AccessToken = _FakeAccessToken  # type: ignore[attr-defined]
    fake_api.VideoGrants = lambda **k: object()  # type: ignore[attr-defined]
    fake_livekit = types.ModuleType("livekit")
    fake_livekit.api = fake_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    monkeypatch.setitem(sys.modules, "livekit.api", fake_api)

    settings = Settings(
        livekit_url="wss://x",
        livekit_api_key="key",
        livekit_api_secret="secret",
        livekit_default_room="my-default-room",
        mock_fixture_path=str(tmp_path / "chunks.json"),
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        resp = client.post("/token", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["room"] == "my-default-room"
    assert body["identity"].startswith("user-")


# --------------------------------------------------------------------------- #
# /documents                                                                  #
# --------------------------------------------------------------------------- #
def test_documents_rejects_non_pdf(client: TestClient) -> None:
    """A non-PDF upload is rejected with 415."""
    resp = client.post(
        "/documents",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    assert resp.status_code == 415


def test_documents_rejects_pdf_named_file_that_is_not_pdf(client: TestClient) -> None:
    """A .pdf-named upload without the %PDF- header is rejected."""
    resp = client.post(
        "/documents",
        files={"file": ("fake.pdf", b"not really a pdf", "application/pdf")},
    )
    assert resp.status_code == 415


def test_documents_rejects_empty_upload(client: TestClient) -> None:
    """An empty upload is rejected with 400."""
    resp = client.post(
        "/documents",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert resp.status_code == 400


@pytest.mark.skipif(
    not (_HAVE_REPORTLAB and _HAVE_PDFPLUMBER),
    reason="needs reportlab (to synthesize a PDF) and pdfplumber (to parse it)",
)
def test_documents_accepts_pdf_and_indexes_in_mock_mode(client: TestClient) -> None:
    """A small PDF is parsed + indexed; chunks_indexed > 0 in mock mode."""
    pdf_bytes = _make_pdf_bytes()
    resp = client.post(
        "/documents",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "mock"
    assert body["pages"] == 2
    assert body["chunks_indexed"] > 0
    assert body["document_id"].startswith("doc-")


@pytest.mark.skipif(
    not (_HAVE_REPORTLAB and _HAVE_PDFPLUMBER),
    reason="needs reportlab + pdfplumber",
)
async def test_documents_then_queryable_via_index(client: TestClient) -> None:
    """After ingest the app's shared index can retrieve the new content."""
    pdf_bytes = _make_pdf_bytes()
    resp = client.post(
        "/documents",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    index = client.app.state.index  # type: ignore[attr-defined]
    result = await index.query("Harbor Street warehouse", top_k=3)
    assert len(result.citations) > 0
