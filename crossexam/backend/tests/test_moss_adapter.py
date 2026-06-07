"""Recorded-response tests for the Moss adapter (:mod:`...retrieval.moss_client`).

These tests run WITHOUT the real Moss SDK or any API keys. They drive
:class:`MossIndex` with a FAKE client that implements the *verified* Moss
surface (``query(index, text, QueryOptions)`` -> object with ``.docs`` and
``.time_taken_ms``; per-doc ``id``/``text``/``score`` plus the assumed
``metadata.bbox`` geometry), and assert the adapter maps responses into
``RetrievalResult`` / ``Citation`` with the correct bbox (points + page dims),
score, and latency_ms.

The PRIMARY default fixture (``_real_string_result`` / ``index``) uses the REAL
Moss metadata contract (all values are strings; geometry is JSON-encoded) so that
CI catches regressions in the string-parsing path, which is what the live SDK
actually returns.  A separate ``_di_fake_result`` fixture uses native Python
types (dict/int/float) to cover the DI / test-fake path (dual-tolerance).

They lock the adapter's *shape* so that swapping in the real SDK on-site is a
fixture update, not a rewrite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from crossexam_backend.config import Settings
from crossexam_backend.retrieval.moss_client import (
    MossClientUnavailableError,
    MossIndex,
    MossQueryError,
)


# --------------------------------------------------------------------------- #
# Fake / recorded Moss SDK surface                                            #
# --------------------------------------------------------------------------- #
@dataclass
class FakeQueryOptions:
    """Stub mirroring the verified ``QueryOptions(top_k=, alpha=)`` surface."""

    top_k: int = 5
    alpha: float = 0.8


@dataclass
class FakeDoc:
    """A single recorded Moss document (verified id/text/score + metadata)."""

    id: str
    text: str
    score: float
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class FakeResult:
    """Verified result wrapper: ``.docs`` list + ``.time_taken_ms``."""

    docs: list[FakeDoc]
    time_taken_ms: float


class FakeMossClient:
    """A recorded Moss client implementing the verified async surface.

    Records the exact (index, text, options) it was called with so tests can
    assert the adapter forwards top_k / alpha correctly.
    """

    def __init__(self, result: FakeResult) -> None:
        """Record the canned ``result`` to return from every query."""
        self._result = result
        self.calls: list[tuple[str, str, FakeQueryOptions]] = []
        self.loaded: list[str] = []
        self.closed = False

    async def load_index(self, name: str) -> None:
        """Record an index-load prewarm call by name."""
        self.loaded.append(name)

    async def query(
        self, index: str, text: str, options: FakeQueryOptions
    ) -> FakeResult:
        """Record the call args and return the canned result."""
        self.calls.append((index, text, options))
        return self._result

    async def close(self) -> None:
        """Mark the client closed."""
        self.closed = True


class FakeMossClientWithQueryFilter(FakeMossClient):
    """Fake client for the public ``query(..., filter=predicate)`` SDK shape."""

    def __init__(self, result: FakeResult) -> None:
        """Initialise with a canned result and record query filters."""
        super().__init__(result)
        self.filters: list[object] = []

    async def query(
        self,
        index: str,
        text: str,
        options: FakeQueryOptions,
        *,
        filter: object = None,  # noqa: A002 - matches the SDK kwarg
    ) -> FakeResult:
        """Record the public query-level filter kwarg and return the fixture."""
        self.calls.append((index, text, options))
        self.filters.append(filter)
        return self._result


def _real_string_result() -> FakeResult:
    """PRIMARY fixture: two-doc response in the REAL Moss string contract.

    All metadata values are strings; bbox/words geometry is JSON-encoded.
    This mirrors exactly what the live Moss SDK returns (pipeline contract).
    The ``index`` pytest fixture uses this so the default test path exercises
    the string-parsing branch of ``_to_citation``.
    """
    bbox12 = {
        "page": 12,
        "x0": 72.0,
        "y0": 120.5,
        "x1": 540.0,
        "y1": 156.25,
        "page_width": 612.0,
        "page_height": 792.0,
    }
    bbox41 = {
        "page": 41,
        "x0": 80.0,
        "y0": 300.0,
        "x1": 500.0,
        "y1": 330.0,
        "page_width": 612.0,
        "page_height": 792.0,
    }
    return FakeResult(
        docs=[
            FakeDoc(
                id="chunk-12",
                text="The witness stated they remained at the warehouse past midnight.",
                score=0.92,
                metadata={
                    "page": "12",
                    "confidence": "0.97",
                    "scanned": "false",
                    "bbox": json.dumps(bbox12),
                },
            ),
            FakeDoc(
                id="chunk-41",
                text="On cross, the witness conceded leaving before 8pm.",
                score=0.81,
                metadata={
                    "page": "41",
                    "bbox": json.dumps(bbox41),
                },
            ),
        ],
        time_taken_ms=7.3,
    )


def _di_fake_result() -> FakeResult:
    """DI/test-fake fixture: same two docs but with native Python types.

    Uses int/float/dict metadata (the shape test doubles and older SDK paths
    return).  Covers the dual-tolerance (non-string) branch of ``_to_citation``.
    """
    return FakeResult(
        docs=[
            FakeDoc(
                id="chunk-12",
                text="The witness stated they remained at the warehouse past midnight.",
                score=0.92,
                metadata={
                    "page": 12,
                    "confidence": 0.97,
                    "bbox": {
                        "page": 12,
                        "x0": 72.0,
                        "y0": 120.5,
                        "x1": 540.0,
                        "y1": 156.25,
                        "page_width": 612.0,
                        "page_height": 792.0,
                    },
                },
            ),
            FakeDoc(
                id="chunk-41",
                text="On cross, the witness conceded leaving before 8pm.",
                score=0.81,
                metadata={
                    "page": 41,
                    "bbox": {
                        "page": 41,
                        "x0": 80.0,
                        "y0": 300.0,
                        "x1": 500.0,
                        "y1": 330.0,
                    },
                },
            ),
        ],
        time_taken_ms=7.3,
    )


def _settings() -> Settings:
    """Settings with Moss credentials present (so default mode is strict)."""
    return Settings(
        moss_project_id="proj-test",
        moss_project_key="key-test",
        moss_index_name="crossexam-documents",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture()
def index() -> MossIndex:
    """A MossIndex wired to the PRIMARY string-contract fixture (real Moss shape).

    Uses ``_real_string_result`` so the default test suite exercises the
    string-parsing path of ``_to_citation``.  All assertions remain identical —
    the adapter must coerce string metadata to the same typed values.
    """
    client = FakeMossClient(_real_string_result())
    # Inject QueryOptions so the adapter exercises the verified positional call.
    idx = MossIndex(_settings(), client=client)
    idx._module = type("M", (), {"QueryOptions": FakeQueryOptions})()  # noqa: SLF001
    return idx


# --------------------------------------------------------------------------- #
# Response-mapping tests (default fixture = real Moss STRING contract)        #
# --------------------------------------------------------------------------- #
async def test_default_fixture_exercises_string_contract(index: MossIndex) -> None:
    """The default ``index`` fixture drives the real Moss string-metadata path.

    This is the CANARY test: if the adapter stops accepting string page/confidence
    /bbox it will fail here, before the DI-fake tests further below.
    """
    result = await index.query("warehouse", top_k=5)
    assert len(result.citations) == 2
    c0, c1 = result.citations
    # page coerced from "12" / "41" (strings)
    assert c0.chunk.page == 12
    assert c1.chunk.page == 41
    # confidence coerced from "0.97" (string) / absent -> default 1.0
    assert c0.chunk.confidence == pytest.approx(0.97)
    assert c1.chunk.confidence == pytest.approx(1.0)
    # bbox parsed from JSON string
    assert c0.chunk.bbox.x0 == pytest.approx(72.0)
    assert c0.chunk.bbox.page_width == pytest.approx(612.0)
    # scanned coerced from "false" (string) -> False
    assert c0.scanned is False


async def test_query_maps_docs_to_citations(index: MossIndex) -> None:
    """Recorded docs map to citations preserving id, text and score order."""
    result = await index.query("warehouse night of the 14th", top_k=5, alpha=0.6)
    assert [c.chunk.id for c in result.citations] == ["chunk-12", "chunk-41"]
    assert result.citations[0].chunk.text.startswith("The witness stated")
    assert result.citations[0].score == pytest.approx(0.92)
    assert result.citations[1].score == pytest.approx(0.81)


async def test_bbox_points_and_page_dims_carried_through(index: MossIndex) -> None:
    """The bbox points + page dimensions map straight into the BBox model."""
    result = await index.query("warehouse", top_k=5)
    bbox = result.citations[0].chunk.bbox
    assert bbox.page == 12
    assert bbox.x0 == pytest.approx(72.0)
    assert bbox.y0 == pytest.approx(120.5)
    assert bbox.x1 == pytest.approx(540.0)
    assert bbox.y1 == pytest.approx(156.25)
    assert bbox.page_width == pytest.approx(612.0)
    assert bbox.page_height == pytest.approx(792.0)
    # Normalized form derives from the carried page dims.
    assert bbox.normalized["x0"] == pytest.approx(72.0 / 612.0)


async def test_missing_page_dims_default(index: MossIndex) -> None:
    """A doc without page_width/height falls back to US-Letter defaults."""
    result = await index.query("cross examination", top_k=5)
    bbox = result.citations[1].chunk.bbox
    assert bbox.page == 41
    assert bbox.page_width == pytest.approx(612.0)
    assert bbox.page_height == pytest.approx(792.0)


async def test_confidence_from_metadata(index: MossIndex) -> None:
    """The confidence is read from metadata when present, else defaults to 1.0."""
    result = await index.query("warehouse", top_k=5)
    assert result.citations[0].chunk.confidence == pytest.approx(0.97)
    assert result.citations[1].chunk.confidence == pytest.approx(1.0)


async def test_latency_prefers_server_time_taken_ms(index: MossIndex) -> None:
    """latency_ms uses Moss's server-measured time_taken_ms when present."""
    result = await index.query("warehouse", top_k=5)
    assert result.latency_ms == pytest.approx(7.3)


async def test_query_forwards_top_k_and_alpha(index: MossIndex) -> None:
    """top_k and alpha are forwarded into QueryOptions on the verified call."""
    await index.query("anything", top_k=3, alpha=0.25)
    client = index._client  # noqa: SLF001 - white-box assertion on the adapter
    assert isinstance(client, FakeMossClient)
    last_index, last_text, last_options = client.calls[-1]
    assert last_index == "crossexam-documents"
    assert last_text == "anything"
    assert last_options.top_k == 3
    assert last_options.alpha == pytest.approx(0.25)


async def test_top_k_truncates_results(index: MossIndex) -> None:
    """No more than top_k citations are returned even if Moss sends more."""
    result = await index.query("warehouse", top_k=1)
    assert len(result.citations) == 1


async def test_score_clamped_to_unit_interval() -> None:
    """Out-of-range scores (distances/logits) are clamped into [0, 1]."""
    weird = FakeResult(
        docs=[FakeDoc(id="x", text="t", score=4.2, metadata={"page": 1})],
        time_taken_ms=1.0,
    )
    idx = MossIndex(_settings(), client=FakeMossClient(weird))
    idx._module = type("M", (), {"QueryOptions": FakeQueryOptions})()  # noqa: SLF001
    result = await idx.query("q", top_k=5)
    assert result.citations[0].score == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# DI / test-fake native-typed contract (dual-tolerance, non-string path)     #
# --------------------------------------------------------------------------- #
async def test_di_fake_native_types_still_parsed() -> None:
    """Native int/float/dict metadata (DI/test-fake path) parses identically.

    Covers the dual-tolerance branch of ``_to_citation``: the same assertions
    that the string-contract ``index`` fixture passes must also hold when the
    metadata uses native Python types instead of encoded strings.
    """
    idx = MossIndex(_settings(), client=FakeMossClient(_di_fake_result()))
    idx._module = type("M", (), {"QueryOptions": FakeQueryOptions})()  # noqa: SLF001
    result = await idx.query("warehouse", top_k=5)
    c0, c1 = result.citations
    assert c0.chunk.page == 12
    assert c0.chunk.confidence == pytest.approx(0.97)
    assert c0.chunk.bbox.x0 == pytest.approx(72.0)
    assert c0.chunk.bbox.page_width == pytest.approx(612.0)
    assert c1.chunk.page == 41
    assert c1.chunk.confidence == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Real Moss STRING-metadata contract (additional / edge-case coverage)        #
# --------------------------------------------------------------------------- #
def _string_contract_result() -> FakeResult:
    """A recorded response in the REAL Moss contract: all metadata are strings.

    Geometry (bbox/words/quads) is JSON-ENCODED; page/confidence/scanned are
    string scalars. This mirrors exactly what the pipeline writes into Moss.
    """
    bbox = {
        "page": 3,
        "x0": 72.0,
        "y0": 120.5,
        "x1": 540.0,
        "y1": 156.25,
        "page_width": 612.0,
        "page_height": 792.0,
    }
    words = [{"text": "warehouse", "bbox": bbox, "confidence": 0.99}]
    return FakeResult(
        docs=[
            FakeDoc(
                id="real-1",
                text="The witness remained past midnight.",
                score=0.88,
                metadata={
                    "documentId": "depo-001",
                    "documentTitle": "Holloway Deposition",
                    "scanned": "true",
                    "page": "3",
                    "confidence": "0.97",
                    "bbox": json.dumps(bbox),
                    "words": json.dumps(words),
                },
            ),
        ],
        time_taken_ms=4.2,
    )


@pytest.fixture()
def string_index() -> MossIndex:
    """A MossIndex wired to a fake client returning the real string contract."""
    client = FakeMossClient(_string_contract_result())
    idx = MossIndex(_settings(), client=client)
    idx._module = type("M", (), {"QueryOptions": FakeQueryOptions})()  # noqa: SLF001
    return idx


async def test_string_contract_bbox_parsed_from_json(string_index: MossIndex) -> None:
    """A JSON-string bbox is decoded into a typed BBox with point values."""
    result = await string_index.query("warehouse", top_k=5)
    bbox = result.citations[0].chunk.bbox
    assert bbox.page == 3
    assert bbox.x0 == pytest.approx(72.0)
    assert bbox.y1 == pytest.approx(156.25)
    assert bbox.page_width == pytest.approx(612.0)
    assert bbox.page_height == pytest.approx(792.0)


async def test_string_contract_page_and_confidence(string_index: MossIndex) -> None:
    """String ``page`` and ``confidence`` are coerced to int/float."""
    result = await string_index.query("warehouse", top_k=5)
    chunk = result.citations[0].chunk
    assert chunk.page == 3
    assert chunk.confidence == pytest.approx(0.97)


async def test_string_contract_scanned_and_doc_fields(string_index: MossIndex) -> None:
    """String ``scanned="true"`` -> True; documentId/Title read from metadata."""
    result = await string_index.query("warehouse", top_k=5)
    cit = result.citations[0]
    assert cit.scanned is True
    assert cit.documentId == "depo-001"
    assert cit.documentTitle == "Holloway Deposition"


async def test_malformed_json_bbox_falls_back_to_default() -> None:
    """A malformed JSON bbox string never crashes; bbox uses safe defaults."""
    bad = FakeResult(
        docs=[
            FakeDoc(
                id="bad-1",
                text="t",
                score=0.5,
                metadata={"page": "2", "bbox": "{not valid json", "confidence": "x"},
            ),
        ],
        time_taken_ms=1.0,
    )
    idx = MossIndex(_settings(), client=FakeMossClient(bad))
    idx._module = type("M", (), {"QueryOptions": FakeQueryOptions})()  # noqa: SLF001
    result = await idx.query("q", top_k=5)
    bbox = result.citations[0].chunk.bbox
    assert bbox.page == 2  # page still coerced from "2"
    assert bbox.x0 == pytest.approx(0.0)  # malformed bbox -> {} -> defaults
    assert bbox.page_width == pytest.approx(612.0)
    assert result.citations[0].chunk.confidence == pytest.approx(1.0)  # bad -> default


async def test_scanned_false_string_is_false() -> None:
    """``scanned="false"`` coerces to False (only true-ish strings are True)."""
    res = FakeResult(
        docs=[
            FakeDoc(
                id="s-0",
                text="t",
                score=0.5,
                metadata={"page": "1", "scanned": "false"},
            ),
        ],
        time_taken_ms=1.0,
    )
    idx = MossIndex(_settings(), client=FakeMossClient(res))
    idx._module = type("M", (), {"QueryOptions": FakeQueryOptions})()  # noqa: SLF001
    result = await idx.query("q", top_k=5)
    assert result.citations[0].scanned is False


# --------------------------------------------------------------------------- #
# Strict vs lenient error handling                                            #
# --------------------------------------------------------------------------- #
class BoomClient:
    """A client whose query() always raises, to exercise error modes."""

    async def query(self, *_args: object, **_kwargs: object) -> object:
        """Always raise to exercise strict/lenient error handling."""
        raise RuntimeError("moss backend exploded")


async def test_strict_mode_raises_on_query_failure() -> None:
    """With credentials present (strict default), a failure raises MossQueryError."""
    idx = MossIndex(_settings(), client=BoomClient(), strict=True)
    with pytest.raises(MossQueryError):
        await idx.query("q", top_k=5)


async def test_lenient_mode_returns_empty_on_query_failure() -> None:
    """In lenient mode a failure degrades to an empty result, never raising."""
    idx = MossIndex(_settings(), client=BoomClient(), strict=False)
    result = await idx.query("q", top_k=5)
    assert result.citations == []
    assert result.query == "q"
    assert result.latency_ms >= 0.0


async def test_strict_default_follows_credentials() -> None:
    """The strict flag defaults to True when Moss credentials are configured."""
    idx = MossIndex(_settings(), client=FakeMossClient(_di_fake_result()))
    assert idx._strict is True  # noqa: SLF001 - white-box assertion on the adapter


# --------------------------------------------------------------------------- #
# Lifecycle + unavailability                                                  #
# --------------------------------------------------------------------------- #
async def test_prewarm_loads_index() -> None:
    """prewarm() calls the verified load_index(name) surface."""
    client = FakeMossClient(_di_fake_result())
    idx = MossIndex(_settings(), client=client)
    await idx.prewarm()
    assert client.loaded == ["crossexam-documents"]


async def test_aclose_closes_client() -> None:
    """aclose() calls the client's close() when present."""
    client = FakeMossClient(_di_fake_result())
    idx = MossIndex(_settings(), client=client)
    await idx.aclose()
    assert client.closed is True


def test_missing_sdk_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no client and no SDK installed, construction raises a clear error."""
    monkeypatch.setattr(
        "crossexam_backend.retrieval.moss_client._load_moss_module",
        lambda: None,
    )
    with pytest.raises(MossClientUnavailableError):
        MossIndex(_settings())


async def test_keyword_arg_fallback_when_no_query_options() -> None:
    """When the SDK has no QueryOptions, the adapter falls back to kwargs."""

    class KwargClient:
        def __init__(self) -> None:
            """Initialise an empty record of the last call's kwargs."""
            self.kwargs: dict[str, object] = {}

        async def query(
            self, index: str, text: str, *, top_k: int, alpha: float
        ) -> FakeResult:
            """Record the keyword-arg call and return a canned result."""
            self.kwargs = {"index": index, "text": text, "top_k": top_k, "alpha": alpha}
            return _di_fake_result()

    client = KwargClient()
    # No _module set -> _make_query_options returns None -> kwargs path.
    idx = MossIndex(_settings(), client=client)
    result = await idx.query("hello", top_k=4, alpha=0.5)
    assert client.kwargs == {
        "index": "crossexam-documents",
        "text": "hello",
        "top_k": 4,
        "alpha": 0.5,
    }
    assert len(result.citations) == 2


# --------------------------------------------------------------------------- #
# document_ids + query_multi doc filter (anchor-expansion round-trip)         #
# --------------------------------------------------------------------------- #
def _multi_doc_result() -> FakeResult:
    """A two-document response with distinct documentIds in metadata."""
    return FakeResult(
        docs=[
            FakeDoc(
                id="a-1",
                text="Clause 4.2 forbids subcontracting.",
                score=0.9,
                metadata={"page": 1, "documentId": "contract-acme"},
            ),
            FakeDoc(
                id="b-1",
                text="We subcontracted the work in March.",
                score=0.8,
                metadata={"page": 1, "documentId": "email-thread"},
            ),
        ],
        time_taken_ms=5.0,
    )


async def test_document_ids_grows_as_query_results_stream() -> None:
    """MossIndex.document_ids reflects the docs observed across queries."""
    client = FakeMossClient(_multi_doc_result())
    idx = MossIndex(_settings(), client=client)
    idx._module = type("M", (), {"QueryOptions": FakeQueryOptions})()  # noqa: SLF001
    assert idx.document_ids == []  # nothing observed yet
    await idx.query("subcontract", top_k=5)
    assert idx.document_ids == ["contract-acme", "email-thread"]


async def test_query_multi_post_filters_when_no_server_filter() -> None:
    """Without a filter-capable QueryOptions, query_multi post-filters by doc."""
    client = FakeMossClient(_multi_doc_result())
    idx = MossIndex(_settings(), client=client)
    idx._module = type("M", (), {"QueryOptions": FakeQueryOptions})()  # noqa: SLF001
    result = await idx.query_multi("subcontract", top_k=5, doc_ids=["email-thread"])
    assert [c.documentId for c in result.citations] == ["email-thread"]


async def test_query_multi_uses_server_filter_when_supported() -> None:
    """A filter-capable QueryOptions makes query_multi push the filter server-side."""

    class FilterQueryOptions:
        """QueryOptions stub that accepts a server-side ``filter`` kwarg."""

        def __init__(
            self, top_k: int = 5, alpha: float = 0.8, filter: object = None  # noqa: A002
        ) -> None:
            self.top_k = top_k
            self.alpha = alpha
            self.filter = filter

    client = FakeMossClient(_multi_doc_result())
    idx = MossIndex(_settings(), client=client)
    idx._module = type("M", (), {"QueryOptions": FilterQueryOptions})()  # noqa: SLF001
    assert idx._supports_server_filter() is True  # noqa: SLF001
    result = await idx.query_multi("subcontract", top_k=5, doc_ids=["contract-acme"])
    # A single id collapses to the verified single-field $eq predicate.
    _index, _text, options = client.calls[-1]
    assert options.filter == {
        "field": "documentId",
        "condition": {"$eq": "contract-acme"},
    }
    # Post-filter still applies defensively, yielding only the allowed doc.
    assert [c.documentId for c in result.citations] == ["contract-acme"]


async def test_query_multi_prefers_public_query_filter_kwarg() -> None:
    """Moss public docs show filtering as query(..., QueryOptions, filter=...)."""
    client = FakeMossClientWithQueryFilter(_multi_doc_result())
    idx = MossIndex(_settings(), client=client)
    idx._module = type("M", (), {"QueryOptions": FakeQueryOptions})()  # noqa: SLF001

    result = await idx.query_multi("subcontract", top_k=5, doc_ids=["email-thread"])

    assert client.filters[-1] == {
        "field": "documentId",
        "condition": {"$eq": "email-thread"},
    }
    assert [c.documentId for c in result.citations] == ["email-thread"]


async def test_query_multi_multi_id_filter_uses_and_or_eq() -> None:
    """Multiple doc_ids build the verified $and-wrapped $or of per-id $eq."""

    class FilterQueryOptions:
        """QueryOptions stub that accepts a server-side ``filter`` kwarg."""

        def __init__(
            self, top_k: int = 5, alpha: float = 0.8, filter: object = None  # noqa: A002
        ) -> None:
            self.top_k = top_k
            self.alpha = alpha
            self.filter = filter

    client = FakeMossClient(_multi_doc_result())
    idx = MossIndex(_settings(), client=client)
    idx._module = type("M", (), {"QueryOptions": FilterQueryOptions})()  # noqa: SLF001
    await idx.query_multi(
        "subcontract", top_k=5, doc_ids=["contract-acme", "email-thread"]
    )
    _index, _text, options = client.calls[-1]
    assert options.filter == {
        "$and": [
            {
                "$or": [
                    {"field": "documentId", "condition": {"$eq": "contract-acme"}},
                    {"field": "documentId", "condition": {"$eq": "email-thread"}},
                ]
            }
        ]
    }


def test_build_doc_filter_shapes() -> None:
    """The verified filter builder: single id -> $eq; many -> $and/$or/$eq."""
    assert MossIndex._build_doc_filter(["only"]) == {  # noqa: SLF001
        "field": "documentId",
        "condition": {"$eq": "only"},
    }
    assert MossIndex._build_doc_filter(["a", "b"]) == {  # noqa: SLF001
        "$and": [
            {
                "$or": [
                    {"field": "documentId", "condition": {"$eq": "a"}},
                    {"field": "documentId", "condition": {"$eq": "b"}},
                ]
            }
        ]
    }


# --------------------------------------------------------------------------- #
# prewarm + document_ids seeding via get_docs (fake-client tests)            #
# --------------------------------------------------------------------------- #
class FakeMossClientWithGetDocs(FakeMossClient):
    """Extended fake that also implements get_docs for prewarm seeding tests."""

    def __init__(self, result: FakeResult, doc_infos: list[FakeDoc]) -> None:
        """Initialise with a query result and a separate get_docs doc list."""
        super().__init__(result)
        self._doc_infos = doc_infos
        self.get_docs_calls: list[tuple[str, object]] = []

    async def get_docs(self, name: str, options: object = None) -> list[FakeDoc]:
        """Return the canned doc list; record the call."""
        self.get_docs_calls.append((name, options))
        return self._doc_infos


async def test_prewarm_seeds_document_ids_via_get_docs() -> None:
    """prewarm() calls load_index then enumerates ids via get_docs."""
    doc_infos = [
        FakeDoc(id="contract-001", text="t", score=0.0, metadata={}),
        FakeDoc(id="email-001", text="t", score=0.0, metadata={}),
    ]
    client = FakeMossClientWithGetDocs(_di_fake_result(), doc_infos)
    idx = MossIndex(_settings(), client=client)
    # Wire a fake module so GetDocumentsOptions probe path is exercised.
    idx._module = type(  # noqa: SLF001
        "M", (), {"QueryOptions": FakeQueryOptions, "GetDocumentsOptions": None}
    )()
    assert idx.document_ids == []
    await idx.prewarm()
    # load_index called with the index name.
    assert "crossexam-documents" in client.loaded
    # get_docs was called and ids are now in document_ids.
    assert client.get_docs_calls, "get_docs should have been called"
    assert idx.document_ids == ["contract-001", "email-001"]


async def test_prewarm_load_index_kwarg_fallback() -> None:
    """prewarm() falls back to load_index(name) when kwargs raise TypeError."""

    class KwargRejectingClient:
        """Client whose load_index only accepts a positional name arg."""

        def __init__(self) -> None:
            """Initialise with empty call records."""
            self.plain_calls: list[str] = []
            self.kwarg_calls: list[str] = []

        async def load_index(self, name: str) -> str:
            """Record a plain load_index call (no kwargs) and return status."""
            self.plain_calls.append(name)
            return "loaded"

    client = KwargRejectingClient()
    idx = MossIndex(_settings(), client=client)
    await idx.prewarm()
    # The plain (no-kwargs) path should have been used.
    assert client.plain_calls == ["crossexam-documents"]


async def test_refresh_document_ids_replaces_existing() -> None:
    """refresh_document_ids() clears and re-seeds the id set from get_docs."""
    doc_infos_v1 = [FakeDoc(id="doc-a", text="t", score=0.0, metadata={})]
    client = FakeMossClientWithGetDocs(_di_fake_result(), doc_infos_v1)
    idx = MossIndex(_settings(), client=client)
    # Manually pre-populate with a stale id.
    idx._seen_document_ids.add("stale-doc")  # noqa: SLF001
    await idx.refresh_document_ids()
    # stale-doc gone; doc-a now present.
    assert idx.document_ids == ["doc-a"]


# --------------------------------------------------------------------------- #
# query_multi server-filter fallback on raise                                 #
# --------------------------------------------------------------------------- #
async def test_query_multi_falls_back_to_overfetch_when_server_filter_raises() -> None:
    """query_multi degrades gracefully when the server-side filter raises."""

    class FilterRaisingClient:
        """Client that raises on filtered queries but succeeds on unfiltered."""

        def __init__(self, result: FakeResult) -> None:
            """Initialise with a canned result for unfiltered queries."""
            self._result = result
            self.calls: list[object] = []

        async def query(
            self, index: str, text: str, options: object
        ) -> FakeResult:
            """Raise if a filter is set; return the canned result otherwise."""
            self.calls.append(options)
            flt = getattr(options, "filter", None)
            if flt is not None:
                raise RuntimeError("$or not supported by this server version")
            return self._result

    class FilterQueryOptions:
        """QueryOptions stub accepting a filter kwarg."""

        def __init__(
            self,
            top_k: int = 5,
            alpha: float = 0.8,
            filter: object = None,  # noqa: A002
        ) -> None:
            self.top_k = top_k
            self.alpha = alpha
            self.filter = filter

    client = FilterRaisingClient(_multi_doc_result())
    idx = MossIndex(_settings(), client=client, strict=False)
    idx._module = type("M", (), {"QueryOptions": FilterQueryOptions})()  # noqa: SLF001
    # Server filter is "supported" (QueryOptions accepts filter kwarg).
    assert idx._supports_server_filter() is True  # noqa: SLF001
    # query_multi should NOT raise; it falls back to over-fetch + post-filter.
    result = await idx.query_multi(
        "subcontract", top_k=5, doc_ids=["contract-acme"]
    )
    # Post-filter is authoritative: only contract-acme in the allow-set.
    assert all(c.documentId == "contract-acme" for c in result.citations)
    # At least 2 calls: one filtered (raises) + at least one unfiltered.
    assert len(client.calls) >= 2


# --------------------------------------------------------------------------- #
# Real SDK tests (skipped automatically when moss is not installed) #
# --------------------------------------------------------------------------- #
def test_real_sdk_query_result_document_info_to_citation() -> None:
    """_to_citation works against REAL moss.QueryResultDocumentInfo.

    Skipped when moss is not installed so the no-SDK CI path is
    unaffected.  When the SDK IS present this test exercises the live type:
    confirms that the coercion chain handles real string-typed metadata and
    produces a correctly-typed Citation/BBox.
    """
    im = pytest.importorskip("moss")

    # Build a real QueryResultDocumentInfo from the pipeline's string contract.
    bbox_dict = {
        "page": 7,
        "x0": 60.0,
        "y0": 100.0,
        "x1": 500.0,
        "y1": 140.0,
        "page_width": 612.0,
        "page_height": 792.0,
    }
    # QueryResultDocumentInfo is a Rust-backed type; construct via the real SDK.
    # It only accepts keyword args matching its fields: id, text, metadata, score.
    qrdi = im.QueryResultDocumentInfo(
        id="sdk-chunk-7",
        text="The plaintiff signed on page seven.",
        metadata={
            "documentId": "contract-xyz",
            "documentTitle": "Purchase Agreement",
            "scanned": "false",
            "page": "7",
            "confidence": "0.95",
            "bbox": json.dumps(bbox_dict),
        },
        score=0.87,
    )

    citation = MossIndex._to_citation(qrdi)  # noqa: SLF001

    # id / text / score pass through correctly.
    assert citation.chunk.id == "sdk-chunk-7"
    assert citation.chunk.text == "The plaintiff signed on page seven."
    assert citation.score == pytest.approx(0.87)

    # page coerced from "7" (string metadata).
    assert citation.chunk.page == 7
    assert citation.chunk.bbox.page == 7

    # bbox decoded from JSON string.
    assert citation.chunk.bbox.x0 == pytest.approx(60.0)
    assert citation.chunk.bbox.y1 == pytest.approx(140.0)
    assert citation.chunk.bbox.page_width == pytest.approx(612.0)

    # confidence coerced from "0.95".
    assert citation.chunk.confidence == pytest.approx(0.95)

    # scanned coerced from "false" string -> False.
    assert citation.scanned is False

    # documentId / documentTitle from string metadata.
    assert citation.documentId == "contract-xyz"
    assert citation.documentTitle == "Purchase Agreement"


def test_real_sdk_query_result_document_info_missing_bbox() -> None:
    """_to_citation against a real SDK doc with no bbox never crashes."""
    im = pytest.importorskip("moss")

    qrdi = im.QueryResultDocumentInfo(
        id="no-bbox",
        text="Short passage.",
        metadata={"page": "2"},
        score=0.5,
    )
    citation = MossIndex._to_citation(qrdi)  # noqa: SLF001
    assert citation.chunk.page == 2
    assert citation.chunk.bbox.x0 == pytest.approx(0.0)
    assert citation.chunk.bbox.page_width == pytest.approx(612.0)
    assert citation.score == pytest.approx(0.5)


def test_real_sdk_document_info_id_accessible() -> None:
    """DocumentInfo.id is accessible for get_docs seeding logic.

    Verifies that the attribute read in _seed_document_ids_from_get_docs
    (``self._get(d, "id", None)``) works against the real SDK type.
    """
    im = pytest.importorskip("moss")

    di = im.DocumentInfo(
        id="di-001",
        text="Some text.",
        metadata={"page": "1"},
        embedding=None,
    )
    # The adapter reads via _get which calls getattr; confirm the attr exists.
    assert MossIndex._get(di, "id") == "di-001"  # noqa: SLF001
    assert MossIndex._get(di, "text") == "Some text."  # noqa: SLF001
