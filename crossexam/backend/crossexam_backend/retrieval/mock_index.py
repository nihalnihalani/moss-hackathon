"""In-memory mock retrieval index.

Implements a deterministic hybrid (semantic + lexical) ranking over a list of
:class:`Chunk` objects loaded from a JSON fixture. It carries real bounding
boxes and page numbers through to the citations and reports a realistic sub-ms
latency, so the rest of the system behaves exactly as it would against Moss.

The hybrid score fuses two first-stage rankers with Reciprocal Rank Fusion:

* a "semantic" leg -- a lightweight TF-IDF cosine that needs no model weights,
  and
* a lexical leg -- an in-process Okapi BM25 scorer (:mod:`.bm25`).

Both are intentionally simple, deterministic and dependency-free, which is what
makes this a faithful test double for Moss's hybrid retrieval. The ``alpha``
knob is threaded into the fusion as the dense/lexical weight (``1.0`` = pure
semantic, ``0.0`` = pure BM25). Two order-sensitive lexical refinements (a
verbatim phrase match and an IDF-weighted proximity score) are layered on top of
the fused base, preserving the canonical demo ranking. An optional second-stage
reranker (:mod:`.rerank`) can be enabled per-query.
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
from crossexam_backend.retrieval.bm25 import BM25Okapi
from crossexam_backend.retrieval.fusion import (
    DEFAULT_RRF_K,
    reciprocal_rank_fusion,
)
from crossexam_backend.retrieval.rerank import LexicalReranker, Reranker

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Extremely common words that should not drive keyword matching. Includes the
# modal/auxiliary and generic verbs ("will", "like", "be", ...) that carry no
# content signal: a query like "what *will* the weather *be* *like*" must not
# match a passage merely because it also contains "will" — these words appear
# everywhere and only inflate spurious overlap, so they are dropped before
# scoring (mirrors the verify.py content-token list).
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "of", "on", "in", "at", "to", "and", "or", "for",
        "was", "is", "were", "are", "be", "been", "did", "do", "does", "that",
        "this", "it", "as", "by", "with", "from", "what", "when", "where",
        "who", "how", "why", "i", "you", "he", "she", "they", "we", "me",
        "my", "your", "his", "her", "their", "there", "had", "has", "have",
        "will", "would", "could", "should", "shall", "may", "might", "must",
        "can", "like", "about", "not", "no",
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
# layered on top of the RRF-fused semantic+BM25 base score. The fused base is
# normalised into [0, 1] before these are added, so these weights set how much
# verbatim word-order and term-clustering can outweigh first-stage rank.
_PHRASE_WEIGHT = 0.45
_PROXIMITY_WEIGHT = 0.45

# How many first-stage candidates to feed the optional second-stage reranker.
_RERANK_CANDIDATES = 20

# Common legal transcript phrasing differs from how users naturally ask
# questions. Expand only the query side so "who represented..." can match
# "On behalf of the Witness" / "representing Elizabeth Holmes", and "when was
# testimony taken" can match front-matter DATE lines.
_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "deposition": ("testimony", "examination", "witness", "counsel"),
    "involvement": ("involved", "overseeing", "engage", "engaged", "role"),
    "overseeing": (
        "lab",
        "laboratory",
        "software",
        "product",
        "development",
        "operations",
    ),
    "represented": (
        "represent",
        "representing",
        "represents",
        "counsel",
        "behalf",
        "appearance",
        "appearances",
        "witness",
        "esq",
    ),
    "representing": (
        "represent",
        "represented",
        "represents",
        "counsel",
        "behalf",
        "appearance",
        "appearances",
        "witness",
        "esq",
    ),
    "taken": ("date", "time", "place"),
    "testimony": ("date", "examination", "transcript", "witness"),
}
_REPRESENTATION_QUERY_TERMS = frozenset(
    {"represented", "representing", "represent", "represents", "counsel"}
)
_REPRESENTATION_DOC_CUES = frozenset(
    {
        "represented",
        "representing",
        "represent",
        "counsel",
        "behalf",
        "esq",
        "appearance",
        "appearances",
    }
)
_INVOLVEMENT_QUERY_TERMS = frozenset({"involvement", "role"})
_INVOLVEMENT_DOC_CUES = frozenset(
    {"involved", "overseeing", "engage", "engaged", "role"}
)
_TESTIMONY_HEADER_DOC_CUES = frozenset({"date", "witness"})
_LEGAL_INTENT_BOOST = 0.65
_HEADER_INTENT_BOOST = 1.1


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip stopwords and split ``text`` into alphanumeric tokens."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _expand_query_tokens(tokens: list[str]) -> list[str]:
    """Add legal-transcript synonyms to user-query tokens."""
    expanded = list(tokens)
    seen = set(expanded)
    for token in tokens:
        for synonym in _QUERY_EXPANSIONS.get(token, ()):
            if synonym not in seen:
                expanded.append(synonym)
                seen.add(synonym)
    return expanded


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


def _chunk_to_citation(chunk: Chunk, score: float) -> Citation:
    """Build a :class:`Citation` from a chunk, carrying the depth-v2 fields.

    Propagates the chunk's ``documentId`` / ``documentTitle`` / ``quads`` /
    ``scanned`` onto the citation so multi-doc, quad-highlight and scanned-source
    metadata survive retrieval (see the shared contract).
    """
    return Citation(
        chunk=chunk,
        score=score,
        quads=chunk.quads,
        documentId=chunk.documentId,
        documentTitle=chunk.documentTitle,
        scanned=chunk.scanned,
    )


class MockIndex(RetrievalIndex):
    """A deterministic in-memory retrieval index over fixture chunks."""

    def __init__(
        self, chunks: list[Chunk], *, reranker: Reranker | None = None
    ) -> None:
        """Build the index from an in-memory list of chunks.

        Args:
            chunks: The corpus to search over.
            reranker: Optional second-stage reranker used when a query passes
                ``rerank=True``. Defaults to a deterministic
                :class:`~crossexam_backend.retrieval.rerank.LexicalReranker`.
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
        # Lexical leg of the hybrid: an in-process Okapi BM25 over the same
        # stopword-stripped tokens the semantic cosine uses.
        self._bm25 = BM25Okapi(self._doc_tokens)
        self._reranker: Reranker = reranker or LexicalReranker()
        logger.info("mock_index.loaded chunks=%d", len(self._chunks))

    # -- construction --------------------------------------------------------
    @classmethod
    def from_fixture(
        cls, path: str | Path, *, reranker: Reranker | None = None
    ) -> MockIndex:
        """Load a :class:`MockIndex` from a JSON fixture file.

        Args:
            path: Path to a JSON file containing a list of chunk dicts.
            reranker: Optional second-stage reranker (see :meth:`__init__`).

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
        return cls(chunks, reranker=reranker)

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

    def _legal_intent_boost(
        self, original_query_tokens: set[str], doc_idx: int
    ) -> float:
        """Boost transcript chunks that match legal-query intent cues."""
        doc_terms = set(self._doc_vectors[doc_idx])
        boost = 0.0
        if (
            original_query_tokens & _REPRESENTATION_QUERY_TERMS
            and doc_terms & _REPRESENTATION_DOC_CUES
        ):
            boost += _LEGAL_INTENT_BOOST
        if (
            original_query_tokens & _INVOLVEMENT_QUERY_TERMS
            and doc_terms & _INVOLVEMENT_DOC_CUES
        ):
            boost += _LEGAL_INTENT_BOOST
        if (
            {"witness", "taken"} <= original_query_tokens
            and _TESTIMONY_HEADER_DOC_CUES <= doc_terms
        ):
            boost += _HEADER_INTENT_BOOST
        return boost

    # -- hybrid fusion -------------------------------------------------------
    def _candidate_indices(
        self, query_term_set: set[str], doc_ids: set[str] | None = None
    ) -> list[int]:
        """Chunks sharing at least one query term (cheap recall pruning).

        A chunk with no overlapping term scores 0 on the semantic cosine, 0 on
        BM25, and 0 on both refinements, so it can never rank — skipping it keeps
        the per-query work proportional to the matched postings, not the corpus.
        When ``doc_ids`` is given, only chunks from those documents are kept (the
        cross-document filter).
        """
        return [
            idx
            for idx in range(len(self._chunks))
            if not query_term_set.isdisjoint(self._doc_vectors[idx])
            and (doc_ids is None or self._chunks[idx].documentId in doc_ids)
        ]

    @property
    def document_ids(self) -> list[str]:
        """Distinct document ids present in the index, in first-seen order."""
        seen: list[str] = []
        for chunk in self._chunks:
            if chunk.documentId not in seen:
                seen.append(chunk.documentId)
        return seen

    def _fused_base(
        self,
        candidates: list[int],
        query_vec: Counter[str],
        query_tokens: list[str],
        alpha: float,
    ) -> dict[int, float]:
        """RRF-fuse the semantic and BM25 rankings over ``candidates``.

        Builds two best-first rankings -- one by the TF-IDF cosine (the dense /
        semantic leg) and one by Okapi BM25 (the lexical leg) -- and fuses them
        with Reciprocal Rank Fusion, weighting the semantic leg by ``alpha`` and
        the lexical leg by ``1 - alpha``. The resulting fused scores are scaled
        so the best candidate is ``1.0`` (and the empty case yields ``{}``),
        giving the order-sensitive refinements a stable scale to add onto.
        """
        semantic_scores = {
            idx: self._semantic_score(query_vec, idx) for idx in candidates
        }
        bm25_scores = {idx: self._bm25.score(query_tokens, idx) for idx in candidates}

        semantic_ranking = sorted(
            candidates, key=lambda i: (-semantic_scores[i], self._chunks[i].id)
        )
        bm25_ranking = sorted(
            candidates, key=lambda i: (-bm25_scores[i], self._chunks[i].id)
        )

        fused = reciprocal_rank_fusion(
            [semantic_ranking, bm25_ranking],
            weights=[alpha, 1.0 - alpha],
            k=DEFAULT_RRF_K,
        )
        if not fused:
            return {}
        best = max(fused.values())
        if best <= 0.0:
            return dict.fromkeys(fused, 0.0)
        return {idx: value / best for idx, value in fused.items()}

    # -- public API ----------------------------------------------------------
    async def query(
        self,
        text: str,
        top_k: int = 5,
        alpha: float = 0.8,
        *,
        rerank: bool = False,
        doc_ids: list[str] | None = None,
    ) -> RetrievalResult:
        """Return the top-``k`` citations for ``text`` (see base class).

        The score combines deterministic, dependency-free signals:

        * a hybrid base -- the TF-IDF cosine (semantic) ranking and the Okapi
          BM25 (lexical) ranking fused with Reciprocal Rank Fusion, with
          ``alpha`` weighting the dense leg and ``1 - alpha`` the lexical leg,
          then normalised so the best candidate is ``1.0``; and
        * two order-sensitive lexical refinements -- a verbatim phrase match and
          an IDF-weighted proximity score -- layered on top to reward passages
          that echo the query's wording and cluster its salient terms.

        When ``rerank`` is ``True`` the top ``_RERANK_CANDIDATES`` first-stage
        hits are re-scored by the configured second-stage
        :class:`~crossexam_backend.retrieval.rerank.Reranker` before truncating
        to ``top_k``; reranking never runs on the default path.

        Scores are normalised by the best hit, so the top citation reports
        ``1.0`` and the rest fall in ``(0, 1]``.
        """
        start = time.perf_counter()

        original_query_tokens = _tokenize(text)
        query_tokens = _expand_query_tokens(original_query_tokens)
        query_raw = _raw_tokens(text)
        query_vec: Counter[str] = Counter(query_tokens)
        query_term_set = set(query_tokens)
        original_query_term_set = set(original_query_tokens)

        doc_filter = set(doc_ids) if doc_ids is not None else None
        candidates = self._candidate_indices(query_term_set, doc_filter)
        fused_base = self._fused_base(candidates, query_vec, query_tokens, alpha)

        scored: list[tuple[float, int]] = []
        for idx in candidates:
            base = fused_base.get(idx, 0.0)
            phrase = self._phrase_score(query_raw, idx)
            proximity = self._proximity_score(query_tokens, idx)
            blended = (
                base
                + _PHRASE_WEIGHT * phrase
                + _PROXIMITY_WEIGHT * proximity
                + self._legal_intent_boost(original_query_term_set, idx)
            )
            if blended > 0.0:
                scored.append((blended, idx))

        # Sort by score desc, then by chunk id for fully deterministic ties.
        scored.sort(key=lambda pair: (-pair[0], self._chunks[pair[1]].id))

        if rerank and scored:
            scored = self._apply_rerank(text, scored)

        # Normalise so the best hit is 1.0 and scores stay in (0, 1].
        best = scored[0][0] if scored else 1.0

        citations: list[Citation] = []
        for score, idx in scored[:top_k]:
            chunk = self._chunks[idx]
            citations.append(_chunk_to_citation(chunk, min(score / best, 1.0)))

        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.debug(
            "mock_index.query text=%r hits=%d rerank=%s latency_ms=%.3f",
            text,
            len(citations),
            rerank,
            latency_ms,
        )
        return RetrievalResult(query=text, citations=citations, latency_ms=latency_ms)

    async def query_multi(
        self,
        text: str,
        top_k: int = 5,
        alpha: float = 0.8,
        *,
        doc_ids: list[str] | None = None,
        rerank: bool = False,
    ) -> RetrievalResult:
        """Query across one or more documents, optionally filtered to ``doc_ids``.

        A thin contract-named wrapper over :meth:`query`: with ``doc_ids=None``
        it searches every document in the index; otherwise it restricts the
        candidate set to those documents. Citations carry their ``documentId`` so
        callers can fuse results spanning documents.
        """
        return await self.query(
            text, top_k=top_k, alpha=alpha, rerank=rerank, doc_ids=doc_ids
        )

    def _apply_rerank(
        self, text: str, scored: list[tuple[float, int]]
    ) -> list[tuple[float, int]]:
        """Re-order the top first-stage hits with the second-stage reranker.

        Only the top ``_RERANK_CANDIDATES`` are re-scored (the rest keep their
        first-stage order appended after, so ``top_k`` slicing is unaffected for
        small ``k``). The reranker's scores fully determine the new order of the
        candidate window; ties fall back to chunk id for determinism.
        """
        window = scored[:_RERANK_CANDIDATES]
        tail = scored[_RERANK_CANDIDATES:]
        documents = [self._chunks[idx].text for _, idx in window]
        rerank_scores = self._reranker.score(text, documents)
        reordered = sorted(
            zip(rerank_scores, (idx for _, idx in window), strict=True),
            key=lambda pair: (-pair[0], self._chunks[pair[1]].id),
        )
        return [(float(score), idx) for score, idx in reordered] + tail
