"""In-process Okapi BM25 lexical scorer.

A clean, dependency-free implementation of the classic Okapi BM25 ranking
function. It is intentionally small and deterministic so it can serve as the
lexical leg of the hybrid retrieval pipeline (fused with the semantic cosine via
Reciprocal Rank Fusion) without pulling in ``rank_bm25`` or any other external
package — the whole offline/mock path stays zero-dependency.

BM25 scores a document ``d`` against a query ``q`` as::

    score(d, q) = sum_{t in q} IDF(t) * (f(t, d) * (k1 + 1))
                                       / (f(t, d) + k1 * (1 - b + b * |d| / avgdl))

where ``f(t, d)`` is the term frequency of ``t`` in ``d``, ``|d|`` the document
length in tokens, ``avgdl`` the average document length, and ``IDF(t)`` the
Robertson/Sparck-Jones inverse document frequency with the usual ``+0.5``
smoothing, floored at a small positive value so a term occurring in (almost)
every document never yields a negative contribution.
"""

from __future__ import annotations

import math
from collections import Counter

# Standard Okapi BM25 hyper-parameters. ``k1`` controls term-frequency
# saturation; ``b`` controls length normalisation. These are the canonical
# defaults used across the IR literature and by libraries such as Lucene and
# rank_bm25, so the scorer behaves the way practitioners expect out of the box.
_DEFAULT_K1 = 1.5
_DEFAULT_B = 0.75

# Floor for the IDF so that very common terms (present in > half the corpus,
# where the raw Robertson-Sparck-Jones IDF would go negative) still contribute a
# small non-negative weight rather than actively penalising a document.
_IDF_FLOOR = 1e-9


class BM25Okapi:
    """A deterministic in-process Okapi BM25 index over pre-tokenised documents.

    The corpus is supplied as a list of token lists (the caller owns tokenisation
    / stop-word handling so BM25 stays orthogonal to the semantic scorer's
    vocabulary). All statistics are computed once at construction time, so each
    :meth:`get_scores` call is ``O(query_terms * postings)``.
    """

    def __init__(
        self,
        corpus: list[list[str]],
        *,
        k1: float = _DEFAULT_K1,
        b: float = _DEFAULT_B,
    ) -> None:
        """Build the BM25 index.

        Args:
            corpus: One token list per document, in document order.
            k1: Term-frequency saturation parameter (Okapi default ``1.5``).
            b: Length-normalisation parameter (Okapi default ``0.75``).
        """
        self._k1 = k1
        self._b = b
        self._corpus_size = len(corpus)
        self._doc_freqs: list[Counter[str]] = [Counter(doc) for doc in corpus]
        self._doc_len: list[int] = [len(doc) for doc in corpus]
        total_len = sum(self._doc_len)
        self._avgdl = (total_len / self._corpus_size) if self._corpus_size else 0.0
        self._idf = self._compute_idf(self._doc_freqs, self._corpus_size)

    @staticmethod
    def _compute_idf(
        doc_freqs: list[Counter[str]], corpus_size: int
    ) -> dict[str, float]:
        """Compute the Robertson/Sparck-Jones IDF for every corpus term."""
        df: Counter[str] = Counter()
        for freqs in doc_freqs:
            df.update(freqs.keys())
        idf: dict[str, float] = {}
        for term, n_t in df.items():
            # Classic BM25 IDF with +0.5 smoothing; floored to stay non-negative.
            value = math.log((corpus_size - n_t + 0.5) / (n_t + 0.5) + 1.0)
            idf[term] = max(value, _IDF_FLOOR)
        return idf

    def idf(self, term: str) -> float:
        """Return the IDF of ``term`` (``0.0`` for an out-of-vocabulary term)."""
        return self._idf.get(term, 0.0)

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        """Score a single document ``doc_idx`` against ``query_tokens``."""
        if not (0 <= doc_idx < self._corpus_size):
            return 0.0
        freqs = self._doc_freqs[doc_idx]
        doc_len = self._doc_len[doc_idx]
        if self._avgdl == 0.0:
            return 0.0
        norm = self._k1 * (1.0 - self._b + self._b * doc_len / self._avgdl)
        total = 0.0
        for term in set(query_tokens):
            tf = freqs.get(term, 0)
            if tf == 0:
                continue
            total += self.idf(term) * (tf * (self._k1 + 1.0)) / (tf + norm)
        return total

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        """Score every document in the corpus against ``query_tokens``.

        Args:
            query_tokens: The tokenised query.

        Returns:
            A list of BM25 scores, one per document, in corpus order.
        """
        return [self.score(query_tokens, idx) for idx in range(self._corpus_size)]
