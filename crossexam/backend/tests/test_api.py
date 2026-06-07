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
import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crossexam_backend.api import _index_to_mock_fixture, create_app
from crossexam_backend.config import Settings
from crossexam_backend.retrieval.mock_index import MockIndex

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
        uploaded_pdf_dir=str(tmp_path / "uploads"),
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


def _fixture_record(chunk_id: str, document_id: str, text: str) -> dict[str, object]:
    return {
        "id": chunk_id,
        "text": text,
        "page": 1,
        "bbox": {
            "page": 1,
            "x0": 72.0,
            "y0": 100.0,
            "x1": 220.0,
            "y1": 120.0,
            "page_width": 612.0,
            "page_height": 792.0,
        },
        "confidence": 1.0,
        "documentId": document_id,
        "documentTitle": f"Document {document_id}",
        "quads": None,
        "scanned": False,
    }


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


def test_mock_fixture_reupload_replaces_same_document_id(tmp_path: Path) -> None:
    """Repeated fallback uploads replace a document instead of duplicating it."""
    settings = _mock_settings(tmp_path)
    fixture = Path(settings.mock_fixture_path)

    _index_to_mock_fixture(
        settings,
        [
            _fixture_record("same-old-1", "same-doc", "old text one"),
            _fixture_record("same-old-2", "same-doc", "old text two"),
            _fixture_record("other-1", "other-doc", "other text"),
        ],
    )
    written, _index = _index_to_mock_fixture(
        settings,
        [_fixture_record("same-new-1", "same-doc", "new text")],
    )

    records = json.loads(fixture.read_text(encoding="utf-8"))
    assert written == 1
    assert {record["id"] for record in records} == {"same-new-1", "other-1"}


def test_mock_fixture_reupload_replaces_same_source_hash(tmp_path: Path) -> None:
    """Fallback uploads with the same source hash cannot grow the fixture."""
    settings = _mock_settings(tmp_path)
    fixture = Path(settings.mock_fixture_path)
    old = _fixture_record("old-doc-p1", "old-doc", "old text")
    old["sourceHash"] = "same-hash"
    other = _fixture_record("other-doc-p1", "other-doc", "other text")
    other["sourceHash"] = "other-hash"
    new = _fixture_record("new-doc-p1", "new-doc", "new text")
    new["sourceHash"] = "same-hash"

    _index_to_mock_fixture(settings, [old, other])
    written, _index = _index_to_mock_fixture(settings, [new])

    records = json.loads(fixture.read_text(encoding="utf-8"))
    assert written == 1
    assert {record["id"] for record in records} == {"new-doc-p1", "other-doc-p1"}


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


def test_token_creates_named_agent_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/token creates the room with a named RoomAgentDispatch attached.

    Replaces the old list_dispatch/create_dispatch path. The new mechanism uses
    an idempotent create_room call so the room is created AND the named agent is
    attached in one step — avoiding the 404 that list_dispatch raised when called
    before the room existed.
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
            return f"fake.jwt.for.{self.identity}"

    class _FakeCreateRoomRequest:
        def __init__(self, *, name: str, agents: list[object]) -> None:
            self.name = name
            self.agents = agents

    class _FakeRoomAgentDispatch:
        def __init__(self, *, agent_name: str) -> None:
            self.agent_name = agent_name

    calls: dict[str, object] = {}

    class _FakeRoomService:
        async def create_room(self, req: _FakeCreateRoomRequest) -> object:
            calls["created_room"] = req.name
            calls["agents"] = [getattr(a, "agent_name", None) for a in req.agents]
            return types.SimpleNamespace(name=req.name)

    class _FakeLiveKitAPI:
        def __init__(self, url: str | None, key: str, secret: str) -> None:
            calls["url"] = url
            calls["key"] = key
            calls["secret"] = secret
            self.room = _FakeRoomService()

        async def __aenter__(self) -> _FakeLiveKitAPI:
            return self

        async def __aexit__(self, *_: object) -> None:
            calls["closed"] = True

    fake_livekit = types.ModuleType("livekit")
    fake_api = types.ModuleType("livekit.api")
    fake_api.AccessToken = _FakeAccessToken  # type: ignore[attr-defined]
    fake_api.VideoGrants = _FakeGrants  # type: ignore[attr-defined]
    fake_api.LiveKitAPI = _FakeLiveKitAPI  # type: ignore[attr-defined]
    fake_api.CreateRoomRequest = _FakeCreateRoomRequest  # type: ignore[attr-defined]
    fake_api.RoomAgentDispatch = _FakeRoomAgentDispatch  # type: ignore[attr-defined]
    fake_livekit.api = fake_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    monkeypatch.setitem(sys.modules, "livekit.api", fake_api)

    settings = Settings(
        livekit_url="wss://x.livekit.cloud",
        livekit_api_key="key",
        livekit_api_secret="secret",
        livekit_agent_name="crossexam-agent",
        mock_fixture_path=str(tmp_path / "chunks.json"),
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        resp = client.post("/token", json={"room": "room-1", "identity": "alice"})
    assert resp.status_code == 200
    assert calls["url"] == "https://x.livekit.cloud"
    assert calls["key"] == "key"
    assert calls["secret"] == "secret"
    assert calls["created_room"] == "room-1"
    assert calls["agents"] == ["crossexam-agent"]
    assert calls["closed"] is True


def test_token_room_ensure_idempotent_on_existing_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/token create_room is called every time — idempotent on the live API.

    The old reuse-check (list_dispatch) is gone. create_room is idempotent:
    calling it on an existing room is a no-op server-side, so we simply always
    call it. This test confirms the token endpoint returns 200 regardless of
    how many times /token is called for the same room.
    """

    class _FakeAccessToken:
        def __init__(self, key: str, secret: str) -> None:
            pass

        def with_identity(self, identity: str) -> _FakeAccessToken:
            return self

        def with_grants(self, grants: object) -> _FakeAccessToken:
            return self

        def to_jwt(self) -> str:
            return "fake.jwt"

    calls: dict[str, int] = {"create_room_calls": 0}

    class _FakeRoomService:
        async def create_room(self, req: object) -> object:
            calls["create_room_calls"] += 1
            return types.SimpleNamespace(name=getattr(req, "name", "room-1"))

    class _FakeLiveKitAPI:
        def __init__(self, url: str | None, key: str, secret: str) -> None:
            self.room = _FakeRoomService()

        async def __aenter__(self) -> _FakeLiveKitAPI:
            return self

        async def __aexit__(self, *_: object) -> None:
            pass

    fake_livekit = types.ModuleType("livekit")
    fake_api = types.ModuleType("livekit.api")
    fake_api.AccessToken = _FakeAccessToken  # type: ignore[attr-defined]
    fake_api.VideoGrants = lambda **_: object()  # type: ignore[attr-defined]
    fake_api.LiveKitAPI = _FakeLiveKitAPI  # type: ignore[attr-defined]
    fake_api.CreateRoomRequest = lambda **kwargs: types.SimpleNamespace(**kwargs)  # type: ignore[attr-defined]
    fake_api.RoomAgentDispatch = lambda **kwargs: types.SimpleNamespace(**kwargs)  # type: ignore[attr-defined]
    fake_livekit.api = fake_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    monkeypatch.setitem(sys.modules, "livekit.api", fake_api)

    settings = Settings(
        livekit_url="wss://x.livekit.cloud",
        livekit_api_key="key",
        livekit_api_secret="secret",
        livekit_agent_name="crossexam-agent",
        mock_fixture_path=str(tmp_path / "chunks.json"),
        _env_file=None,  # type: ignore[call-arg]
    )
    with TestClient(create_app(settings)) as client:
        r1 = client.post("/token", json={"room": "room-1"})
        r2 = client.post("/token", json={"room": "room-1"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    # create_room called once per /token hit (idempotent on server side)
    assert calls["create_room_calls"] == 2


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
    assert body["pdf_url"] == f"/documents/{body['document_id']}/pdf"
    pdf_resp = client.get(body["pdf_url"])
    assert pdf_resp.status_code == 200
    assert pdf_resp.content == pdf_bytes
    fixture_path = Path(client.app.state.settings.mock_fixture_path)  # type: ignore[attr-defined]
    records = json.loads(fixture_path.read_text(encoding="utf-8"))
    uploaded = [r for r in records if r["id"].startswith(body["document_id"])]
    assert uploaded
    assert {r["documentId"] for r in uploaded} == {body["document_id"]}
    assert {r["documentTitle"] for r in uploaded} == {"sample.pdf"}
    assert {r["sourceHash"] for r in uploaded}


@pytest.mark.skipif(
    not (_HAVE_REPORTLAB and _HAVE_PDFPLUMBER),
    reason="needs reportlab + pdfplumber",
)
def test_documents_reupload_same_pdf_reuses_document_id(client: TestClient) -> None:
    """The same PDF bytes should replace one uploaded corpus, not append another."""
    pdf_bytes = _make_pdf_bytes()

    first = client.post(
        "/documents",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
    )
    second = client.post(
        "/documents",
        files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_body = first.json()
    second_body = second.json()
    assert first_body["document_id"] == second_body["document_id"]
    fixture_path = Path(client.app.state.settings.mock_fixture_path)  # type: ignore[attr-defined]
    records = json.loads(fixture_path.read_text(encoding="utf-8"))
    uploaded = [r for r in records if r["documentId"] == first_body["document_id"]]
    assert len(uploaded) == first_body["chunks_indexed"]


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


# --------------------------------------------------------------------------- #
# /documents — live (Moss) path                                               #
# --------------------------------------------------------------------------- #
class _FakeLiveIndex:
    """Stand-in live MossIndex that records prewarm/refresh invocations."""

    def __init__(self) -> None:
        self.prewarm_calls = 0
        self.refresh_calls = 0

    async def prewarm(self) -> None:
        self.prewarm_calls += 1

    async def refresh_document_ids(self) -> None:
        self.refresh_calls += 1


def _live_settings(tmp_path: Path) -> Settings:
    """Settings that resolve to the live Moss path (creds set, mocks off)."""
    return Settings(
        moss_project_id="proj",
        moss_project_key="key",
        moss_index_name="crossexam-test",
        use_mocks=False,
        mock_fixture_path=str(tmp_path / "chunks.json"),
        uploaded_pdf_dir=str(tmp_path / "uploads"),
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.mark.skipif(
    not (_HAVE_REPORTLAB and _HAVE_PDFPLUMBER),
    reason="needs reportlab (to synthesize a PDF) and pdfplumber (to parse it)",
)
def test_documents_live_moss_path_invokes_prewarm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful real-Moss upsert reports mode=moss and reloads the index.

    Patches ``_moss_import_available`` so the live path is taken without the SDK
    installed, replaces ``build_index_async`` with an async fake returning a moss
    summary, and asserts the live MossIndex's prewarm/refresh were invoked.
    """
    import crossexam_backend.api as api_mod

    monkeypatch.setattr(api_mod, "_moss_import_available", lambda: "inferedge_moss")

    captured: dict[str, object] = {}

    async def _fake_build_index_async(
        chunks: list[object], index_name: str | None = None, **_: object
    ) -> dict[str, object]:
        captured["index_name"] = index_name
        captured["n"] = len(chunks)
        return {"mode": "moss", "chunk_count": len(chunks)}

    fake_pipeline = types.ModuleType("crossexam_pipeline")
    fake_build = types.ModuleType("crossexam_pipeline.build_index")
    fake_build.build_index_async = _fake_build_index_async  # type: ignore[attr-defined]
    fake_models = types.ModuleType("crossexam_pipeline.models")

    class _ParsedChunk:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

        @classmethod
        def model_validate(cls, rec: dict[str, object]) -> _ParsedChunk:
            return cls(**rec)

    fake_models.ParsedChunk = _ParsedChunk  # type: ignore[attr-defined]
    fake_pipeline.build_index = fake_build  # type: ignore[attr-defined]
    fake_pipeline.models = fake_models  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "crossexam_pipeline", fake_pipeline)
    monkeypatch.setitem(sys.modules, "crossexam_pipeline.build_index", fake_build)
    monkeypatch.setitem(sys.modules, "crossexam_pipeline.models", fake_models)

    app = create_app(_live_settings(tmp_path))
    fake_index = _FakeLiveIndex()
    app.state.index = fake_index  # type: ignore[attr-defined]

    with TestClient(app) as c:
        resp = c.post(
            "/documents",
            files={"file": ("sample.pdf", _make_pdf_bytes(), "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "moss"
    assert body["chunks_indexed"] > 0
    assert captured["index_name"] == "crossexam-test"
    # The live index must be reloaded so the freshly-upserted doc is queryable.
    assert fake_index.prewarm_calls == 1
    assert fake_index.refresh_calls == 1


@pytest.mark.skipif(
    not (_HAVE_REPORTLAB and _HAVE_PDFPLUMBER),
    reason="needs reportlab + pdfplumber",
)
def test_documents_live_moss_failure_degrades_to_mock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising build_index_async degrades to mock (HTTP 200), never 500s."""
    import crossexam_backend.api as api_mod

    monkeypatch.setattr(api_mod, "_moss_import_available", lambda: "inferedge_moss")

    async def _boom(
        chunks: list[object], index_name: str | None = None, **_: object
    ) -> dict[str, object]:
        raise RuntimeError("simulated Moss SDK runtime error")

    fake_pipeline = types.ModuleType("crossexam_pipeline")
    fake_build = types.ModuleType("crossexam_pipeline.build_index")
    fake_build.build_index_async = _boom  # type: ignore[attr-defined]
    fake_models = types.ModuleType("crossexam_pipeline.models")

    class _ParsedChunk:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)

        @classmethod
        def model_validate(cls, rec: dict[str, object]) -> _ParsedChunk:
            return cls(**rec)

    fake_models.ParsedChunk = _ParsedChunk  # type: ignore[attr-defined]
    fake_pipeline.build_index = fake_build  # type: ignore[attr-defined]
    fake_pipeline.models = fake_models  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "crossexam_pipeline", fake_pipeline)
    monkeypatch.setitem(sys.modules, "crossexam_pipeline.build_index", fake_build)
    monkeypatch.setitem(sys.modules, "crossexam_pipeline.models", fake_models)

    app = create_app(_live_settings(tmp_path))
    with TestClient(app) as c:
        resp = c.post(
            "/documents",
            files={"file": ("sample.pdf", _make_pdf_bytes(), "application/pdf")},
        )
    # Graceful degrade: 200 with mode=mock, NOT a 500.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "mock"
    assert body["chunks_indexed"] > 0
    assert isinstance(app.state.index, MockIndex)  # type: ignore[attr-defined]
