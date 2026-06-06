"""Speculative retrieval on ASR partials.

LiveKit's STT emits *interim* (partial) transcripts while the user is still
speaking, then a *final* transcript when the turn ends. We exploit that: as
partials stream in we speculatively run the retrieval query and cache the
result keyed by a normalised transcript PREFIX. By the time the final
transcript arrives, the citation is frequently already in hand, so the agent
can publish it with near-zero added latency ("no dead air").

This module is pure and dependency-free (no LiveKit, no keys), so it is fully
unit-testable. The agent wires it behind a guarded interim-transcript hook.

The cache is a bounded, prefix-keyed store: ``prefetch(partial)`` normalises the
partial and stores the query result under that prefix; ``take(final)`` returns
the cached result whose key is a prefix of (or equal to) the normalised final
text — i.e. a speculative prefetch that the final turn confirms — else ``None``.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from crossexam_backend.models import RetrievalResult

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")

# A retrieval coroutine: ``query(text) -> RetrievalResult``. The agent passes a
# closure over ``index.query(text, top_k=..., alpha=...)`` so the speculative
# retriever stays decoupled from the index API.
QueryFn = Callable[[str], Awaitable[RetrievalResult]]


def normalize_prefix(text: str) -> str:
    """Normalise a transcript fragment to a stable cache key.

    Lowercases, collapses whitespace and strips surrounding punctuation/space so
    that an interim partial and the final transcript that extends it share a
    common prefix. A trailing partial word is left intact (we cannot know the
    user is done with it), which is fine — the final transcript that completes
    the word simply produces a longer key whose prefix still matches.
    """
    cleaned = _WS_RE.sub(" ", text.lower()).strip()
    return cleaned.strip(" .,?!;:")


class SpeculativeRetriever:
    """Prefix-keyed speculative cache over retrieval queries.

    Args:
        query_fn: Async ``(text) -> RetrievalResult`` used to run a prefetch.
        max_entries: Maximum cached prefixes (LRU-evicted) to bound memory.
        min_prefix_chars: Skip prefetching fragments shorter than this; very
            short partials ("uh", "the") are noisy and waste a query.
    """

    def __init__(
        self,
        query_fn: QueryFn,
        *,
        max_entries: int = 16,
        min_prefix_chars: int = 4,
    ) -> None:
        """Initialise the speculative cache (see class docstring)."""
        self._query_fn = query_fn
        self._max_entries = max(1, max_entries)
        self._min_prefix_chars = max(0, min_prefix_chars)
        # prefix -> result, in insertion/use order for LRU eviction.
        self._cache: OrderedDict[str, RetrievalResult] = OrderedDict()

    @property
    def size(self) -> int:
        """Number of cached prefixes currently held."""
        return len(self._cache)

    def _store(self, prefix: str, result: RetrievalResult) -> None:
        """Insert ``result`` under ``prefix`` with LRU eviction."""
        self._cache[prefix] = result
        self._cache.move_to_end(prefix)
        while len(self._cache) > self._max_entries:
            evicted, _ = self._cache.popitem(last=False)
            logger.debug("speculative.evict prefix=%r", evicted)

    async def prefetch(self, partial_text: str) -> RetrievalResult | None:
        """Speculatively query for ``partial_text`` and cache the result.

        Returns the result (also cached) or ``None`` when the partial is too
        short / empty to be worth a query, or already cached (no re-query).
        """
        prefix = normalize_prefix(partial_text)
        if len(prefix) < self._min_prefix_chars:
            return None
        if prefix in self._cache:
            self._cache.move_to_end(prefix)
            return self._cache[prefix]
        result = await self._query_fn(prefix)
        self._store(prefix, result)
        logger.debug(
            "speculative.prefetch prefix=%r citations=%d",
            prefix,
            len(result.citations),
        )
        return result

    def take(self, final_text: str) -> RetrievalResult | None:
        """Return a cached result speculatively prefetched for this final turn.

        A cache hit is any cached prefix that is a prefix of (or equal to) the
        normalised final text — i.e. a partial we already retrieved for that the
        final transcript confirms. The longest matching prefix wins (the most
        complete speculative query). The entry is consumed (removed) so a later
        unrelated turn cannot accidentally reuse it.
        """
        final = normalize_prefix(final_text)
        if not final:
            return None
        best_key: str | None = None
        for key in self._cache:
            if final.startswith(key) and (best_key is None or len(key) > len(best_key)):
                best_key = key
        if best_key is None:
            return None
        result = self._cache.pop(best_key)
        logger.debug(
            "speculative.take hit final=%r matched_prefix=%r", final, best_key
        )
        return result

    def clear(self) -> None:
        """Drop all cached prefixes (e.g. on session reset)."""
        self._cache.clear()
