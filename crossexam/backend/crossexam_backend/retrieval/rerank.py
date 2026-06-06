"""Optional second-stage rerankers for the retrieval pipeline.

A reranker re-scores a small candidate set (typically the top ~20 from first-
stage hybrid retrieval) with a more expensive, more accurate relevance model and
returns them re-ordered. This module provides:

* :class:`Reranker` -- the structural protocol every reranker satisfies.
* :class:`LexicalReranker` -- the default, deterministic, zero-dependency
  reranker. It scores each candidate by query-term coverage and proximity, which
  reliably promotes passages that contain *more* of the query's salient terms
  *closer together* — a strong relevance signal that pure first-stage ranking
  can miss.
* :class:`CrossEncoderReranker` -- a stub that wraps a sentence-transformers
  cross-encoder when that optional dependency is installed (guarded import). It
  falls back to raising a clear error at construction time when the dep is
  absent, so the default path never imports it.

Reranking only runs when the index ``query(..., rerank=True)`` path is taken, so
it never adds latency to the default hot path.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Lowercase and split ``text`` into alphanumeric tokens."""
    return _TOKEN_RE.findall(text.lower())


@runtime_checkable
class Reranker(Protocol):
    """Structural type for a second-stage reranker.

    A reranker takes the query and a list of candidate document texts and returns
    a parallel list of relevance scores (higher is better). The caller is
    responsible for sorting / truncating using these scores, so a reranker never
    needs to know about ``top_k`` or citations.
    """

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Return a relevance score for each document, parallel to ``documents``."""
        ...


class LexicalReranker:
    """Deterministic lexical reranker: query-term coverage + proximity.

    The score for each document is::

        coverage + _PROXIMITY_WEIGHT * proximity

    where ``coverage`` is the fraction of distinct query terms present in the
    document and ``proximity`` rewards those matched terms appearing in a tight
    window. This deterministically promotes passages that mention more of the
    query, clustered together — exactly the behaviour a human would expect from a
    "more careful second look". No model weights, no dependencies.
    """

    _PROXIMITY_WEIGHT = 0.5

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Score each document by query-term coverage and proximity."""
        query_terms = set(_tokens(query))
        if not query_terms:
            return [0.0] * len(documents)
        return [self._score_one(query_terms, doc) for doc in documents]

    def _score_one(self, query_terms: set[str], document: str) -> float:
        """Coverage + weighted proximity for a single document."""
        doc_tokens = _tokens(document)
        if not doc_tokens:
            return 0.0
        positions: dict[str, list[int]] = {}
        for pos, term in enumerate(doc_tokens):
            if term in query_terms:
                positions.setdefault(term, []).append(pos)
        matched = set(positions)
        if not matched:
            return 0.0
        coverage = len(matched) / len(query_terms)
        proximity = self._proximity(positions, matched)
        return coverage + self._PROXIMITY_WEIGHT * proximity

    @staticmethod
    def _proximity(positions: dict[str, list[int]], matched: set[str]) -> float:
        """Tightness of the smallest window covering every matched term.

        Returns ``0`` when fewer than two distinct terms matched (no
        meaningful window), otherwise ``(n_matched - 1) / best_span`` clamped to
        ``1.0`` — perfectly adjacent terms score ``1.0``.
        """
        if len(matched) < 2:
            return 0.0
        occurrences = sorted(
            (pos, term) for term in matched for pos in positions[term]
        )
        need = len(matched)
        window: dict[str, int] = {}
        covered = 0
        left = 0
        best_span: int | None = None
        for right_pos, right_term in occurrences:
            if window.get(right_term, 0) == 0:
                covered += 1
            window[right_term] = window.get(right_term, 0) + 1
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
            return 1.0
        return min((len(matched) - 1) / best_span, 1.0)


class CrossEncoderReranker:
    """Cross-encoder reranker backed by ``sentence-transformers`` (optional).

    The cross-encoder is imported lazily and guarded: constructing this class
    raises :class:`RuntimeError` with an actionable message when
    ``sentence-transformers`` is not installed, so nothing on the default path
    ever triggers the import. Install with ``pip install
    'crossexam-backend[rerank]'``.

    Args:
        model_name: A sentence-transformers cross-encoder model id. The default
            is the small, fast MS-MARCO MiniLM reranker commonly used for this
            task.
    """

    _DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    def __init__(self, model_name: str | None = None) -> None:
        """Load the cross-encoder model, or raise if the dep is unavailable."""
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - exercised only w/o dep
            raise RuntimeError(
                "CrossEncoderReranker requires sentence-transformers; install "
                "with: pip install 'crossexam-backend[rerank]'"
            ) from exc
        self._model = CrossEncoder(model_name or self._DEFAULT_MODEL)

    def score(self, query: str, documents: list[str]) -> list[float]:
        """Score each ``(query, document)`` pair with the cross-encoder."""
        if not documents:
            return []
        pairs = [(query, doc) for doc in documents]
        raw = self._model.predict(pairs)
        return [float(value) for value in raw]
