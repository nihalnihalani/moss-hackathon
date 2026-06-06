"""Tests for :mod:`crossexam_backend.speculative` (prefix-cache hit/miss)."""

from __future__ import annotations

from crossexam_backend.models import BBox, Chunk, Citation, RetrievalResult
from crossexam_backend.speculative import SpeculativeRetriever, normalize_prefix


def _result(query: str, *, with_citation: bool = False) -> RetrievalResult:
    """A minimal result tagged by query (optionally with one citation)."""
    citations = []
    if with_citation:
        citations = [
            Citation(
                chunk=Chunk(
                    id="c1",
                    text="x",
                    page=1,
                    bbox=BBox(page=1, x0=0.0, y0=0.0, x1=1.0, y1=1.0),
                ),
                score=1.0,
            )
        ]
    return RetrievalResult(query=query, citations=citations, latency_ms=1.0)


class _CountingQuery:
    """An async query fn that records every text it was called with."""

    def __init__(self) -> None:
        """Initialise the call log."""
        self.calls: list[str] = []

    async def __call__(self, text: str) -> RetrievalResult:
        """Record ``text`` and return a tagged result with one citation."""
        self.calls.append(text)
        return _result(text, with_citation=True)


def test_normalize_prefix_lowercases_and_strips() -> None:
    """Normalisation lowercases, collapses whitespace and trims punctuation."""
    assert normalize_prefix("  Where  were   you?? ") == "where were you"
    assert normalize_prefix("WAREHOUSE keys.") == "warehouse keys"


async def test_prefix_cache_hit() -> None:
    """A prefetched partial is returned for a final transcript that extends it."""
    q = _CountingQuery()
    spec = SpeculativeRetriever(q)
    await spec.prefetch("where were you on the")
    # The final transcript starts with the cached prefix -> hit, no re-query.
    hit = spec.take("Where were you on the night of the 14th?")
    assert hit is not None
    assert hit.query == "where were you on the"
    assert q.calls == ["where were you on the"]
    # The entry was consumed.
    assert spec.take("where were you on the night of the 14th") is None


async def test_prefix_cache_miss_unrelated_final() -> None:
    """An unrelated final transcript does not match any cached prefix."""
    q = _CountingQuery()
    spec = SpeculativeRetriever(q)
    await spec.prefetch("indemnification clause")
    assert spec.take("what is the termination notice period") is None


async def test_longest_prefix_wins() -> None:
    """When several prefixes match, the most complete (longest) one is used."""
    q = _CountingQuery()
    spec = SpeculativeRetriever(q)
    await spec.prefetch("where were")
    await spec.prefetch("where were you on the night")
    hit = spec.take("where were you on the night of the 14th")
    assert hit is not None
    assert hit.query == "where were you on the night"


async def test_short_partial_not_prefetched() -> None:
    """A too-short partial is skipped (no wasted query)."""
    q = _CountingQuery()
    spec = SpeculativeRetriever(q, min_prefix_chars=4)
    assert await spec.prefetch("the") is None
    assert q.calls == []
    assert spec.size == 0


async def test_duplicate_prefetch_not_requeried() -> None:
    """Prefetching the same normalised prefix twice runs only one query."""
    q = _CountingQuery()
    spec = SpeculativeRetriever(q)
    await spec.prefetch("warehouse keys")
    await spec.prefetch("Warehouse  keys.")  # normalises to the same key
    assert q.calls == ["warehouse keys"]
    assert spec.size == 1


async def test_lru_eviction_bounds_cache() -> None:
    """The cache evicts the oldest entry beyond ``max_entries``."""
    q = _CountingQuery()
    spec = SpeculativeRetriever(q, max_entries=2)
    await spec.prefetch("alpha one")
    await spec.prefetch("bravo two")
    await spec.prefetch("charlie three")
    assert spec.size == 2
    # "alpha one" was evicted.
    assert spec.take("alpha one extended") is None
    assert spec.take("charlie three extended") is not None


def test_disabled_via_none_query_take_empty() -> None:
    """take() on an empty cache returns None for empty/blank finals."""
    q = _CountingQuery()
    spec = SpeculativeRetriever(q)
    assert spec.take("") is None
    assert spec.take("   ") is None
