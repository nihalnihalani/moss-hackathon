"""Multi-hop retrieval, query decomposition and cross-document contradiction.

This module implements feature 1 of the depth-v2 contract: an agentic retrieval
path that

1. **decomposes** a complex / contradiction-seeking question into several
   focused sub-queries (:class:`QueryDecomposer`),
2. **retrieves** each sub-query across one or more documents and **fuses +
   dedupes** the hits into a single ranked ``citations`` list, recording the
   decomposition trail as ``hops`` (:class:`MultiHopRetriever`), and
3. **detects contradiction** — two high-confidence citations (on different
   pages or in different documents) that assert mutually-exclusive facts.

Everything here is pure, deterministic and dependency-free on the default path.
The decomposer exposes a guarded LLM hook (``llm_fn``) that is only used when a
caller injects one; with no hook it falls back to a heuristic splitter. The
contradiction detector reuses the *content-token* idea from
:mod:`crossexam_backend.verify`: it compares the substantive tokens of two
candidate citations and looks for opposing predicates over a shared subject
(opposing location / time / polarity), i.e. an "both cannot be true" check.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence

from crossexam_backend.models import (
    Citation,
    HopTrace,
    MultiHopResult,
)
from crossexam_backend.retrieval.base import RetrievalIndex
from crossexam_backend.retrieval.fusion import reciprocal_rank_fusion

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Words that frame the *act* of contradicting rather than the facts in conflict.
# Their presence in a question is a strong signal that the asker wants a
# multi-hop, contradiction-seeking retrieval.
_CONTRADICTION_CUES = frozenset(
    {
        "contradict",
        "contradicted",
        "contradiction",
        "conflict",
        "conflicting",
        "inconsistent",
        "inconsistency",
        "discrepancy",
        "change",
        "changed",
        "recant",
        "recanted",
        "differ",
        "different",
        "versus",
    }
)

# Cues that a question spans more than one fact / hop and is worth decomposing
# even without an explicit contradiction word (e.g. "and", "both", "compare").
_MULTIHOP_CUES = frozenset({"and", "both", "compare", "versus", "vs"})

# Stopwords (mirrors verify.py / mock_index.py) so the decomposer and the
# contradiction detector reason over content tokens only.
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "of", "on", "in", "at", "to", "and", "or", "for",
        "was", "is", "were", "are", "be", "been", "did", "do", "does", "that",
        "this", "it", "as", "by", "with", "from", "what", "when", "where",
        "who", "how", "why", "i", "you", "he", "she", "they", "we", "me",
        "my", "your", "his", "her", "their", "there", "had", "has", "have",
        "not", "no", "yes", "so", "but", "if", "then", "than", "into", "out",
        "up", "down", "over", "under", "about", "would", "could", "should",
        "will", "can", "may", "might", "must", "am", "being", "having", "doing",
        "himself", "herself", "themselves",
    }
)

# Antonym / opposing-predicate pairs used by the contradiction detector. Each
# frozenset is a class of mutually-exclusive predicates: if one citation matches
# a term from the left bucket and the other matches the right bucket (over a
# shared subject), the two cannot both be true. These cover the common
# deposition contradictions — presence vs. absence, staying vs. leaving, and
# opposing time-of-day anchors.
_OPPOSING_PREDICATES: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    # stayed / remained  vs  left / departed
    (
        frozenset({"stayed", "remained", "present", "there", "midnight", "past"}),
        frozenset({"left", "departed", "leaving", "home", "away", "absent"}),
    ),
    # affirmation  vs  negation of an action
    (
        frozenset({"confirmed", "affirmed", "admitted", "agreed"}),
        frozenset({"denied", "refused", "rejected", "disputed"}),
    ),
)

# A citation must clear this score to be eligible as one side of a contradiction.
# Mirrors the spirit of the faithfulness threshold: only *confident* hits.
_CONTRADICTION_MIN_SCORE = 0.4

# Minimum shared-subject overlap (content tokens) for two citations to be
# considered "about the same thing", a precondition for a real contradiction.
_MIN_SUBJECT_OVERLAP = 2


def _content_tokens(text: str) -> list[str]:
    """Lowercase, tokenise and drop stopwords, keeping content terms in order."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


# A decomposition hook: takes the question, returns a list of sub-queries (or
# raises). Injected by callers that want LLM-driven decomposition; the default
# path never constructs one.
DecomposeFn = Callable[[str], Sequence[str]]


class QueryDecomposer:
    """Split a complex / contradiction question into focused sub-queries.

    The default, always-available path is a deterministic heuristic: it strips
    the contradiction framing ("did the witness contradict himself about ...")
    down to the underlying *topic*, then emits one sub-query that seeks the
    original assertion and a second that seeks the conflicting one. A guarded
    ``llm_fn`` hook can replace the heuristic when a caller wires one in; if the
    hook raises or returns nothing usable, the heuristic result is used instead,
    so decomposition never fails the turn.

    Args:
        llm_fn: Optional ``(question) -> sub-queries`` callable (e.g. an LLM
            prompt). ``None`` (default) forces the heuristic path.
    """

    def __init__(self, llm_fn: DecomposeFn | None = None) -> None:
        """Store the optional LLM decomposition hook."""
        self._llm_fn = llm_fn

    @property
    def uses_llm(self) -> bool:
        """Whether a real LLM decomposition hook is wired (else heuristic)."""
        return callable(self._llm_fn)

    def is_multihop(self, question: str) -> bool:
        """Heuristic: does ``question`` warrant decomposition into hops?

        True when it carries a contradiction cue (``contradict``, ``conflict``,
        ``changed his story`` …) or a multi-hop cue (``and``/``both``/``compare``)
        joining two clauses.
        """
        tokens = set(_TOKEN_RE.findall(question.lower()))
        if tokens & _CONTRADICTION_CUES:
            return True
        # An "and"/"both"/"compare" only counts as multi-hop if there is enough
        # content on both sides to be two distinct asks.
        return bool(tokens & _MULTIHOP_CUES) and len(_content_tokens(question)) >= 4

    def decompose(self, question: str) -> list[str]:
        """Return the sub-queries for ``question`` (LLM hook or heuristic).

        Always returns at least one sub-query (the original question, cleaned),
        so the multi-hop retriever degrades gracefully to single-hop on a simple
        question.
        """
        if self._llm_fn is not None:
            try:
                subs = [s.strip() for s in self._llm_fn(question) if s.strip()]
            except Exception:  # noqa: BLE001 - a flaky hook must not break the turn
                logger.exception("QueryDecomposer llm_fn failed; using heuristic")
                subs = []
            if subs:
                return subs
        return self._heuristic(question)

    def _heuristic(self, question: str) -> list[str]:
        """Deterministic decomposition: topic + (original, conflicting) hops.

        Extracts the question's content topic (dropping contradiction-framing and
        discourse words), then forms two sub-queries: one seeking the witness's
        original statement on the topic, and one seeking a conflicting statement
        on the same topic. A non-contradiction question with two clauses is split
        on its conjunction instead.
        """
        topic = self._topic(question)
        if self.is_multihop(question) and self._is_contradiction(question):
            base = topic or question.strip()
            return [
                f"where {base}",
                f"conflicting statement about {base}",
            ]
        # Conjunction split for plain multi-hop questions ("X and Y").
        parts = self._split_conjunction(question)
        if len(parts) > 1:
            return parts
        return [question.strip()]

    @staticmethod
    def _is_contradiction(question: str) -> bool:
        """Whether ``question`` explicitly seeks a contradiction."""
        return bool(set(_TOKEN_RE.findall(question.lower())) & _CONTRADICTION_CUES)

    @staticmethod
    def _topic(question: str) -> str:
        """Strip contradiction framing / discourse words down to the topic.

        E.g. "did the witness contradict himself about the night of the 14th?"
        -> "night 14th". Keeps content tokens that are neither stopwords nor
        contradiction cues nor the discourse word "witness".
        """
        drop = _CONTRADICTION_CUES | {"witness", "witnesses", "himself", "herself"}
        toks = [t for t in _content_tokens(question) if t not in drop]
        return " ".join(toks)

    @staticmethod
    def _split_conjunction(question: str) -> list[str]:
        """Split a question on a top-level ``and`` into two sub-queries."""
        # Split on the standalone word "and" (with surrounding spaces).
        parts = re.split(r"\band\b", question, flags=re.IGNORECASE)
        cleaned = [p.strip(" ?.,") for p in parts if p.strip(" ?.,")]
        # Only treat as multi-hop when each side has real content.
        if len(cleaned) >= 2 and all(len(_content_tokens(p)) >= 1 for p in cleaned):
            return cleaned
        return [question.strip()]


def detect_contradiction(citations: Sequence[Citation]) -> tuple[bool, str | None]:
    """Detect a cross-page / cross-document contradiction among ``citations``.

    Principled heuristic (an "both cannot be true" check): scan high-confidence
    citation pairs that are *about the same subject* (sufficient content-token
    overlap) yet live on different pages or in different documents, and flag a
    contradiction when one asserts a predicate from one side of an opposing pair
    while the other asserts the opposing side (e.g. "remained past midnight" vs.
    "left before 8 p.m."). This reuses :mod:`verify`'s content-token notion to
    avoid firing on incidental word overlap.

    Args:
        citations: Candidate citations (already fused / ranked).

    Returns:
        ``(contradiction, primary_id)`` where ``primary_id`` is the id of the
        first (higher-ranked) citation of the conflicting pair, or ``None`` when
        no contradiction is found.
    """
    eligible = [c for c in citations if c.score >= _CONTRADICTION_MIN_SCORE]
    for i, left in enumerate(eligible):
        left_tokens = set(_content_tokens(left.chunk.text))
        for right in eligible[i + 1 :]:
            # Must be cross-page or cross-document to be a "contradiction".
            same_place = (
                left.documentId == right.documentId
                and left.chunk.page == right.chunk.page
            )
            if same_place:
                continue
            right_tokens = set(_content_tokens(right.chunk.text))
            shared = left_tokens & right_tokens
            if len(shared) < _MIN_SUBJECT_OVERLAP:
                continue
            if _predicates_oppose(left_tokens, right_tokens):
                logger.debug(
                    "contradiction.detected primary=%s vs=%s shared=%d",
                    left.chunk.id,
                    right.chunk.id,
                    len(shared),
                )
                return True, left.chunk.id
    return False, None


def _predicates_oppose(left: set[str], right: set[str]) -> bool:
    """Whether ``left`` and ``right`` assert opposing predicates.

    True when, for some opposing-predicate class, one side matches the class's
    left bucket and the other matches its right bucket (in either order).
    """
    for bucket_a, bucket_b in _OPPOSING_PREDICATES:
        a_left, b_left = left & bucket_a, left & bucket_b
        a_right, b_right = right & bucket_a, right & bucket_b
        if (a_left and b_right) or (b_left and a_right):
            return True
    return False


class MultiHopRetriever:
    """Run a decomposed multi-hop retrieval over a :class:`RetrievalIndex`.

    For each sub-query produced by the :class:`QueryDecomposer`, it queries the
    index (across documents via ``query_multi``), records a :class:`HopTrace`,
    then fuses all hops' hits into a single de-duplicated, ranked ``citations``
    list using Reciprocal Rank Fusion. Finally it runs
    :func:`detect_contradiction` over the fused citations and reports the flag
    plus the ``primary_id`` to page-jump to.

    Args:
        index: The retrieval index to query.
        decomposer: Optional decomposer (defaults to a heuristic one).
    """

    def __init__(
        self,
        index: RetrievalIndex,
        decomposer: QueryDecomposer | None = None,
    ) -> None:
        """Store the index and (optional) decomposer."""
        self._index = index
        self._decomposer = decomposer or QueryDecomposer()

    async def retrieve(
        self,
        question: str,
        *,
        top_k: int = 5,
        per_hop_k: int = 5,
        alpha: float = 0.8,
        doc_ids: list[str] | None = None,
    ) -> MultiHopResult:
        """Decompose ``question``, retrieve each hop, fuse and flag contradiction.

        Args:
            question: The (possibly complex) user-turn text.
            top_k: Max citations in the fused result.
            per_hop_k: Max citations retrieved per sub-query.
            alpha: Hybrid weight forwarded to the index.
            doc_ids: Optional document allow-list (cross-document filter).

        Returns:
            A :class:`MultiHopResult` with fused citations, hop traces, the
            contradiction flag and the primary citation id.
        """
        start = time.perf_counter()
        sub_queries = self._decomposer.decompose(question)

        hops: list[HopTrace] = []
        # rankings: one best-first list of citation-ids per hop, for RRF.
        rankings: list[list[str]] = []
        # id -> Citation, keeping the highest single-hop score seen for each.
        by_id: dict[str, Citation] = {}

        for sub in sub_queries:
            result = await self._index.query_multi(
                sub, top_k=per_hop_k, alpha=alpha, doc_ids=doc_ids
            )
            ranking: list[str] = []
            for cit in result.citations:
                cid = cit.chunk.id
                ranking.append(cid)
                prev = by_id.get(cid)
                if prev is None or cit.score > prev.score:
                    by_id[cid] = cit
            rankings.append(ranking)
            hops.append(HopTrace(subQuery=sub, citationIds=ranking))

        citations = self._fuse(rankings, by_id, top_k)
        contradiction, primary_from_pair = detect_contradiction(citations)
        primary_id = primary_from_pair or (
            citations[0].chunk.id if citations else None
        )

        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.debug(
            "multihop.retrieve q=%r hops=%d citations=%d contradiction=%s",
            question,
            len(hops),
            len(citations),
            contradiction,
        )
        return MultiHopResult(
            query=question,
            citations=citations,
            hops=hops,
            contradiction=contradiction,
            primary_id=primary_id,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _fuse(
        rankings: list[list[str]],
        by_id: dict[str, Citation],
        top_k: int,
    ) -> list[Citation]:
        """RRF-fuse per-hop rankings into one de-duplicated citation list.

        Each id keeps the best single-hop :class:`Citation` (highest score). The
        fused order comes from Reciprocal Rank Fusion over the hop rankings, so a
        citation surfaced by several hops outranks one seen by a single hop.
        Ties break by descending single-hop score then by id for determinism.
        """
        if not by_id:
            return []
        # Map string ids to indices for the integer-based RRF helper.
        ids = list(by_id)
        idx_of = {cid: i for i, cid in enumerate(ids)}
        int_rankings = [[idx_of[cid] for cid in r] for r in rankings]
        fused = reciprocal_rank_fusion(int_rankings)

        def sort_key(cid: str) -> tuple[float, float, str]:
            fused_score = fused.get(idx_of[cid], 0.0)
            return (-fused_score, -by_id[cid].score, cid)

        ordered = sorted(ids, key=sort_key)
        return [by_id[cid] for cid in ordered[:top_k]]
