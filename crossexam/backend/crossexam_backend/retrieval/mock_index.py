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

# Discourse / role words that frame *who is speaking* or *the act of testifying*
# rather than the factual content of the testimony. In a cross-examination
# query ("Did the witness admit ...") these point at the deposition machinery,
# not at the passage that answers the question -- the witness's own answer
# rarely repeats the word "witness" or "admit". They still carry a little
# signal, so we down-weight rather than drop them. This is a general retrieval
# refinement (query-term weighting by discourse class), not a per-fixture hack.
_DISCOURSE_TERMS = frozenset(
    {
        "witness", "witnesses", "admit", "admits", "admitted", "admitting",
        "say", "says", "said", "deny", "denies", "denied",
    }
)
_DISCOURSE_WEIGHT = 0.3

# Relative weights of the two order-sensitive lexical refinements that are
# layered on top of the alpha-blended semantic+keyword base score.
_PHRASE_WEIGHT = 0.45
_PROXIMITY_WEIGHT = 0.45


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip stopwords and split ``text`` into alphanumeric tokens."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _raw_tokens(text: str) -> list[str]:
    """Split ``text`` into alphanumeric tokens *keeping* stopwords.

    Used for verbatim phrase matching, where stopwords (``at the``, ``on the
    night of``) carry the word-order signal that token-overlap scoring throws
    away.
    """
    return _TOKEN_RE.findall(text.lower())


def _query_term_weight(term: str) -> float:
    """Per-term query weight: down-weight discourse/role words (see above)."""
    return _DISCOURSE_WEIGHT if term in _DISCOURSE_TERMS else 1.0


class MockIndex(RetrievalIndex):
    """A deterministic in-memory retrieval index over fixture chunks."""

    def __init__(self, chunks: list[Chunk]) -> None:
        """Build the index from an in-memory list of chunks.

        Args:
            chunks: The corpus to search over.
        """
        self._chunks: list[Chunk] = list(chunks)
        self._doc_tokens: list[list[str]] = [_tokenize(c.text) for c in self._chunks]
        self._doc_vectors: list[Counter[str]] = [
            Counter(toks) for toks in self._doc_tokens
        ]
        # Raw token streams (stopwords retained) power verbatim phrase matching.
        self._doc_raw_text: list[str] = [
            " " + " ".join(_raw_tokens(c.text)) + " " for c in self._chunks
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
        """TF-IDF weighted cosine similarity between query and a document.

        Query terms are scaled by :func:`_query_term_weight` so discourse/role
        words contribute less than substantive content terms.
        """
        doc_vec = self._doc_vectors[doc_idx]
        if not doc_vec or not query_vec:
            return 0.0
        shared = set(query_vec) & set(doc_vec)
        if not shared:
            return 0.0
        dot = sum(
            query_vec[t]
            * _query_term_weight(t)
            * doc_vec[t]
            * (self._idf.get(t, 1.0) ** 2)
            for t in shared
        )
        q_norm = math.sqrt(
            sum(
                (cnt * _query_term_weight(t) * self._idf.get(t, 1.0)) ** 2
                for t, cnt in query_vec.items()
            )
        )
        d_norm = math.sqrt(
            sum((cnt * self._idf.get(t, 1.0)) ** 2 for t, cnt in doc_vec.items())
        )
        if q_norm == 0.0 or d_norm == 0.0:
            return 0.0
        return dot / (q_norm * d_norm)

    @staticmethod
    def _keyword_score(query_tokens: list[str], doc_vec: Counter[str]) -> float:
        """Weighted fraction of distinct query tokens present in the document."""
        if not query_tokens:
            return 0.0
        distinct = set(query_tokens)
        total = sum(_query_term_weight(t) for t in distinct)
        if total == 0.0:
            return 0.0
        hits = sum(_query_term_weight(t) for t in distinct if doc_vec.get(t, 0) > 0)
        return hits / total

    def _phrase_score(self, query_raw: list[str], doc_idx: int) -> float:
        """Length of the longest verbatim query phrase found in the document.

        Returns the longest run of *consecutive* raw query tokens (stopwords
        included) that appears contiguously in the document, normalised so a
        full-query match scores ``1.0`` and a lone bigram scores just above
        ``0``. This rewards passages that echo the query's word order -- e.g.
        the deposition answer that contains the question verbatim -- which pure
        bag-of-words overlap cannot distinguish.
        """
        n = len(query_raw)
        if n < 2:
            return 0.0
        doc_text = self._doc_raw_text[doc_idx]
        longest = 0
        for start in range(n):
            # Try the longest phrase starting here first, then shrink.
            for end in range(n, start + 1, -1):
                if end - start <= longest:
                    break
                phrase = " " + " ".join(query_raw[start:end]) + " "
                if phrase in doc_text:
                    longest = end - start
                    break
        if longest < 2:
            return 0.0
        return min((longest - 1) / (n - 1), 1.0)

    def _proximity_score(self, query_tokens: list[str], doc_idx: int) -> float:
        """IDF-weighted tightness of the matched query terms in the document.

        Finds the smallest document window containing every distinct query term
        that occurs in the document, then scores it by how tightly those terms
        cluster (ideal = perfectly adjacent) times the share of the query's IDF
        mass they cover. A passage where ``warehouse``/``night``/``14th`` sit a
        few tokens apart beats one where they are scattered across a paragraph.
        """
        distinct = set(query_tokens)
        if len(distinct) < 2:
            return 0.0
        positions: dict[str, list[int]] = {}
        matched: set[str] = set()
        for pos, term in enumerate(self._doc_tokens[doc_idx]):
            if term in distinct:
                positions.setdefault(term, []).append(pos)
                matched.add(term)
        if len(matched) < 2:
            return 0.0
        occurrences = sorted(
            (pos, term) for term in matched for pos in positions[term]
        )
        need = len(matched)
        window: Counter[str] = Counter()
        left = 0
        covered = 0
        best_span: int | None = None
        for right_pos, right_term in occurrences:
            if window[right_term] == 0:
                covered += 1
            window[right_term] += 1
            while covered == need:
                span = right_pos - occurrences[left][0]
                if best_span is None or span < best_span:
                    best_span = span
                left_term = occurrences[left][1]
                window[left_term] -= 1
                if window[left_term] == 0:
                    covered -= 1
                left += 1
        if not best_span:
            tightness = 1.0
        else:
            tightness = min((len(matched) - 1) / best_span, 1.0)
        idf_mass = sum(
            _query_term_weight(t) * self._idf.get(t, 1.0) for t in matched
        ) / sum(_query_term_weight(t) * self._idf.get(t, 1.0) for t in distinct)
        return idf_mass * tightness

    # -- public API ----------------------------------------------------------
    async def query(
        self,
        text: str,
        top_k: int = 5,
        alpha: float = 0.8,
    ) -> RetrievalResult:
        """Return the top-``k`` citations for ``text`` (see base class).

        The score blends four deterministic, dependency-free signals:

        * an ``alpha``-weighted mix of the TF-IDF cosine (semantic) and the
          query-term-coverage (keyword) scores -- preserving the existing
          ``alpha`` contract, and
        * two order-sensitive lexical refinements -- a verbatim phrase match
          and an IDF-weighted proximity score -- that reward passages echoing
          the query's wording and clustering its salient terms.

        Scores are normalised by the best hit, so the top citation reports
        ``1.0`` and the rest fall in ``(0, 1]``.
        """
        start = time.perf_counter()

        query_tokens = _tokenize(text)
        query_raw = _raw_tokens(text)
        query_vec: Counter[str] = Counter(query_tokens)

        query_term_set = set(query_tokens)
        scored: list[tuple[float, int]] = []
        for idx in range(len(self._chunks)):
            # Cheap pruning: a chunk sharing no query terms scores 0 on every
            # component, so skip the (relatively pricey) phrase/proximity work.
            if query_term_set.isdisjoint(self._doc_vectors[idx]):
                continue
            semantic = self._semantic_score(query_vec, idx)
            keyword = self._keyword_score(query_tokens, self._doc_vectors[idx])
            base = alpha * semantic + (1.0 - alpha) * keyword
            phrase = self._phrase_score(query_raw, idx)
            proximity = self._proximity_score(query_tokens, idx)
            blended = (
                base
                + _PHRASE_WEIGHT * phrase
                + _PROXIMITY_WEIGHT * proximity
            )
            if blended > 0.0:
                scored.append((blended, idx))

        # Sort by score desc, then by chunk id for fully deterministic ties.
        scored.sort(key=lambda pair: (-pair[0], self._chunks[pair[1]].id))

        # Normalise so the best hit is 1.0 and scores stay in (0, 1].
        best = scored[0][0] if scored else 1.0

        citations: list[Citation] = []
        for score, idx in scored[:top_k]:
            chunk = self._chunks[idx]
            citations.append(Citation(chunk=chunk, score=min(score / best, 1.0)))

        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.debug(
            "mock_index.query text=%r hits=%d latency_ms=%.3f",
            text,
            len(citations),
            latency_ms,
        )
        return RetrievalResult(query=text, citations=citations, latency_ms=latency_ms)
