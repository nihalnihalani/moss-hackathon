"""Recorded-response tests for the Moss adapter (:mod:`...retrieval.moss_client`).

These tests run WITHOUT the real Moss SDK or any API keys. They drive
:class:`MossIndex` with a FAKE client that implements the *verified* Moss
surface (``query(index, text, QueryOptions)`` -> object with ``.docs`` and
``.time_taken_ms``; per-doc ``id``/``text``/``score`` plus the assumed
``metadata.bbox`` geometry), and assert the adapter maps responses into
``RetrievalResult`` / ``Citation`` with the correct bbox (points + page dims),
score, and latency_ms.

They lock the adapter's *shape* so that swapping in the real SDK on-site is a
fixture update, not a rewrite.
"""

from __future__ import annotations

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


def _recorded_result() -> FakeResult:
    """A two-document recorded response with bbox geometry in metadata."""
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
    """A MossIndex wired to the recorded fake client (no real SDK)."""
    client = FakeMossClient(_recorded_result())
    # Inject QueryOptions so the adapter exercises the verified positional call.
    idx = MossIndex(_settings(), client=client)
    idx._module = type("M", (), {"QueryOptions": FakeQueryOptions})()  # noqa: SLF001
    return idx


# --------------------------------------------------------------------------- #
# Response-mapping tests                                                       #
# --------------------------------------------------------------------------- #
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
    idx = MossIndex(_settings(), client=FakeMossClient(_recorded_result()))
    assert idx._strict is True  # noqa: SLF001 - white-box assertion on the adapter


# --------------------------------------------------------------------------- #
# Lifecycle + unavailability                                                  #
# --------------------------------------------------------------------------- #
async def test_prewarm_loads_index() -> None:
    """prewarm() calls the verified load_index(name) surface."""
    client = FakeMossClient(_recorded_result())
    idx = MossIndex(_settings(), client=client)
    await idx.prewarm()
    assert client.loaded == ["crossexam-documents"]


async def test_aclose_closes_client() -> None:
    """aclose() calls the client's close() when present."""
    client = FakeMossClient(_recorded_result())
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
            return _recorded_result()

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
