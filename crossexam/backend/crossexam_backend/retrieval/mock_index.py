"""In-memory mock retrieval index.

Implements a deterministic hybrid (semantic-ish + keyword) ranking over a list
of :class:`Chunk` objects loaded from a JSON fixture. It carries real bounding
boxes and page numbers through to the citations and reports a realistic sub-ms
latency, so the rest of the system behaves exactly as it would against Moss.

The "semantic" component here is a lightweight token-overlap cosine that needs
no model weights — it is intentionally simple, deterministic and dependency
free, which is what makes it a faithful test double for Moss.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import Counter
from pathlib import Path

from crossexam_backend.models import Chunk, Citation, RetrievalResult
from crossexam_backend.retrieval.base import RetrievalIndex

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Extremely common words that should not drive keyword matching.
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "of", "on", "in", "at", "to", "and", "or", "for",
        "was", "is", "were", "are", "be", "been", "did", "do", "does", "that",
        "this", "it", "as", "by", "with", "from", "what", "when", "where",
        "who", "how", "why", "i", "you", "he", "she", "they", "we", "me",
        "my", "your", "his", "her", "their", "there", "had", "has", "have",
    }
)


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip stopwords and split ``text`` into alphanumeric tokens."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


class MockIndex(RetrievalIndex):
    """A deterministic in-memory retrieval index over fixture chunks."""

    def __init__(self, chunks: list[Chunk]) -> None:
        """Build the index from an in-memory list of chunks.

        Args:
            chunks: The corpus to search over.
        """
        self._chunks: list[Chunk] = list(chunks)
        self._doc_vectors: list[Counter[str]] = [
            Counter(_tokenize(c.text)) for c in self._chunks
        ]
        # Inverse document frequency, used to weight the semantic cosine.
        self._idf: dict[str, float] = self._compute_idf(self._doc_vectors)
        logger.info("mock_index.loaded chunks=%d", len(self._chunks))

    # -- construction --------------------------------------------------------
    @classmethod
    def from_fixture(cls, path: str | Path) -> MockIndex:
        """Load a :class:`MockIndex` from a JSON fixture file.

        Args:
            path: Path to a JSON file containing a list of chunk dicts.

        Returns:
            A populated :class:`MockIndex`.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
            ValueError: If the fixture is not a JSON list of chunks.
        """
        fixture_path = Path(path)
        if not fixture_path.is_file():
            raise FileNotFoundError(f"Fixture not found: {fixture_path}")
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("Fixture must be a JSON list of chunk objects")
        chunks = [Chunk.model_validate(item) for item in raw]
        return cls(chunks)

    @staticmethod
    def _compute_idf(doc_vectors: list[Counter[str]]) -> dict[str, float]:
        """Compute smoothed inverse document frequency for every term."""
        n_docs = max(len(doc_vectors), 1)
        df: Counter[str] = Counter()
        for vec in doc_vectors:
            df.update(vec.keys())
        return {
            term: math.log((1.0 + n_docs) / (1.0 + count)) + 1.0
            for term, count in df.items()
        }

    # -- scoring -------------------------------------------------------------
    def _semantic_score(self, query_vec: Counter[str], doc_idx: int) -> float:
        """TF-IDF weighted cosine similarity between query and a document."""
        doc_vec = self._doc_vectors[doc_idx]
        if not doc_vec or not query_vec:
            return 0.0
        shared = set(query_vec) & set(doc_vec)
        if not shared:
            return 0.0
        dot = sum(
            query_vec[t] * doc_vec[t] * (self._idf.get(t, 1.0) ** 2) for t in shared
        )
        q_norm = math.sqrt(
            sum((cnt * self._idf.get(t, 1.0)) ** 2 for t, cnt in query_vec.items())
        )
        d_norm = math.sqrt(
            sum((cnt * self._idf.get(t, 1.0)) ** 2 for t, cnt in doc_vec.items())
        )
        if q_norm == 0.0 or d_norm == 0.0:
            return 0.0
        return dot / (q_norm * d_norm)

    @staticmethod
    def _keyword_score(query_tokens: list[str], doc_vec: Counter[str]) -> float:
        """Fraction of distinct query tokens present in the document."""
        if not query_tokens:
            return 0.0
        distinct = set(query_tokens)
        hits = sum(1 for t in distinct if doc_vec.get(t, 0) > 0)
        return hits / len(distinct)

    # -- public API ----------------------------------------------------------
    async def query(
        self,
        text: str,
        top_k: int = 5,
        alpha: float = 0.8,
    ) -> RetrievalResult:
        """Return the top-``k`` citations for ``text`` (see base class)."""
        start = time.perf_counter()

        query_tokens = _tokenize(text)
        query_vec: Counter[str] = Counter(query_tokens)

        scored: list[tuple[float, int]] = []
        for idx in range(len(self._chunks)):
            semantic = self._semantic_score(query_vec, idx)
            keyword = self._keyword_score(query_tokens, self._doc_vectors[idx])
            blended = alpha * semantic + (1.0 - alpha) * keyword
            if blended > 0.0:
                scored.append((blended, idx))

        # Sort by score desc, then by chunk id for fully deterministic ties.
        scored.sort(key=lambda pair: (-pair[0], self._chunks[pair[1]].id))

        citations: list[Citation] = []
        for score, idx in scored[:top_k]:
            chunk = self._chunks[idx]
            citations.append(Citation(chunk=chunk, score=min(score, 1.0)))

        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.debug(
            "mock_index.query text=%r hits=%d latency_ms=%.3f",
            text,
            len(citations),
            latency_ms,
        )
        return RetrievalResult(query=text, citations=citations, latency_ms=latency_ms)
