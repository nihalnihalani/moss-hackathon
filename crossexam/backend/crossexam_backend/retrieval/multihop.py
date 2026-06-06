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
caller injects one; with no hook it falls back to a principled topic-extraction
splitter. The contradiction detector is a STRUCTURAL "both cannot be true"
check: two high-confidence citations contradict when they (a) share a temporal
anchor (the same date/time reference, e.g. "the 14th" / "night of the 14th")
and (b) assert INCOMPATIBLE locations for the subject at that time (e.g.
"Harbor Street warehouse" vs. "downtown ... two miles from the warehouse").
Cross-document conflicting pairs are preferred over same-document ones, so a
deposition alibi colliding with an independent exhibit is surfaced ahead of an
in-document inconsistency.
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

# --------------------------------------------------------------------------- #
# Structural contradiction extraction: temporal anchors + location classes.    #
# --------------------------------------------------------------------------- #
# A citation must clear this score to be eligible as one side of a contradiction.
# Mirrors the spirit of the faithfulness threshold: only *confident* hits.
_CONTRADICTION_MIN_SCORE = 0.4

# Minimum shared-subject overlap (content tokens) for two citations to be
# considered "about the same thing", a precondition for a real contradiction.
_MIN_SUBJECT_OVERLAP = 2

# How many candidates to fetch per hop for CONTRADICTION DETECTION (independent
# of how many are published). Wide enough that an independent corroborating /
# contradicting exhibit that ranks below the published top_k is still in the
# pool the structural detector reasons over.
_CONTRADICTION_POOL_K = 12

# Spelled-out / numeric day words we normalise so "the 14th", "the fourteenth"
# and "night of the 14th" collapse to the same temporal anchor.
_ORDINAL_WORDS: dict[str, str] = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
    "eleventh": "11",
    "twelfth": "12",
    "thirteenth": "13",
    "fourteenth": "14",
    "fifteenth": "15",
    "sixteenth": "16",
    "seventeenth": "17",
    "eighteenth": "18",
    "nineteenth": "19",
    "twentieth": "20",
}

# A day-of-month reference: "14th", "14", "fourteenth" (handled separately) and
# month-name dates. Matches the bare number too so "on the 14th" anchors.
_DAY_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\b")

# Location *classes*: mutually-exclusive places a subject can be. A subject who
# is at a member of one class at a given time cannot simultaneously be at a
# member of a different class. Kept general (warehouse/site vs. downtown/office
# vs. home/away) so the detector is not pinned to a single fixture.
_LOCATION_CLASSES: dict[str, frozenset[str]] = {
    "warehouse": frozenset(
        {"warehouse", "harbor", "dock", "loading", "facility", "site"}
    ),
    "downtown": frozenset(
        {"downtown", "office", "desk", "ninth", "floor", "building", "tower"}
    ),
    "home": frozenset({"home", "house", "residence", "apartment"}),
}

# Cues that one citation explicitly places the subject *apart from* a location
# the other citation places them *at* — a direct geometric contradiction even
# before location-class reasoning ("two miles from", "away from", "elsewhere").
_SEPARATION_CUES: tuple[str, ...] = (
    "miles from",
    "mile from",
    "away from",
    "elsewhere",
    "nowhere near",
    "blocks from",
)


def _content_tokens(text: str) -> list[str]:
    """Lowercase, tokenise and drop stopwords, keeping content terms in order."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _temporal_anchors(text: str) -> frozenset[str]:
    """Extract normalised date/day anchors from ``text``.

    Collapses ``"the 14th"``, ``"the fourteenth"`` and ``"night of the 14th"``
    to the same anchor token (``"d14"``) so two citations that talk about the
    same calendar day share a temporal anchor regardless of spelling. Returns a
    set of anchors (a citation may reference more than one day).
    """
    lowered = text.lower()
    anchors: set[str] = set()
    for word, num in _ORDINAL_WORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            anchors.add(f"d{num}")
    for match in _DAY_RE.finditer(lowered):
        day = int(match.group(1))
        if 1 <= day <= 31:
            anchors.add(f"d{day}")
    return frozenset(anchors)


def _location_classes(text: str) -> frozenset[str]:
    """Extract the set of location *classes* asserted in ``text``.

    Maps each location keyword present in the text to its class
    (warehouse / downtown / home / …). A citation that names "the Harbor Street
    warehouse" yields ``{"warehouse"}``; one that names "downtown ... ninth
    floor" yields ``{"downtown"}``; one that mentions both yields both.
    """
    lowered = text.lower()
    tokens = set(_TOKEN_RE.findall(lowered))
    classes: set[str] = set()
    for klass, members in _LOCATION_CLASSES.items():
        if tokens & members:
            classes.add(klass)
    return frozenset(classes)


def _has_separation_cue(text: str) -> bool:
    """Whether ``text`` explicitly places the subject apart from a location."""
    lowered = text.lower()
    return any(cue in lowered for cue in _SEPARATION_CUES)


def _locations_incompatible(left_text: str, right_text: str) -> bool:
    """Whether two citations assert INCOMPATIBLE locations for the subject.

    Incompatible when either citation explicitly separates the subject from a
    place (``"two miles from the warehouse"``) while the other places them
    there, or when the two place the subject in DIFFERENT location classes for
    the same time (their class sets differ — one names a place the other does
    not). Two citations naming exactly the *same* class(es) are compatible
    (corroboration, not conflict).
    """
    left_classes = _location_classes(left_text)
    right_classes = _location_classes(right_text)
    if not left_classes or not right_classes:
        return False
    # An explicit separation cue on one side, against a shared place on the
    # other, is a direct geometric contradiction ("two miles from the warehouse"
    # vs. "at the warehouse").
    if (_has_separation_cue(left_text) or _has_separation_cue(right_text)) and (
        left_classes & right_classes
    ):
        return True
    # Otherwise: the two place the subject in different sets of location classes
    # for the same time — mutually exclusive presence (e.g. warehouse vs. home).
    return left_classes != right_classes


# A decomposition hook: takes the question, returns a list of sub-queries (or
# raises). Injected by callers that want LLM-driven decomposition; the default
# path never constructs one.
DecomposeFn = Callable[[str], Sequence[str]]


class QueryDecomposer:
    """Split a complex / contradiction question into focused sub-queries.

    The default, always-available path is a deterministic, principled splitter.
    For a contradiction / multi-hop question it strips the contradiction framing
    ("did the witness contradict himself about ...") down to the underlying
    subject+predicate *topic*, then emits two sub-queries: one that seeks the
    core claim/assertion to locate, and a second that seeks evidence
    contradicting that claim — NOT a literal conjunction fragment. A guarded
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
        """Deterministic decomposition into genuinely useful sub-queries.

        For a contradiction / multi-hop question it extracts the question's
        subject+predicate *topic* (dropping contradiction framing and discourse
        words) and emits a principled pair:

        * ``"<core claim>"`` — the assertion to locate in the documents, and
        * ``"evidence that contradicts <core claim>"`` — the conflicting
          statement to find elsewhere.

        These are real retrieval queries derived from the question's content,
        NOT a literal split on the word "and" (which produced fragments like
        "Did the supplier"). A plain, non-contradiction question degrades to a
        single cleaned sub-query.
        """
        topic = self._topic(question)
        if not topic:
            return [question.strip()]
        if self.is_multihop(question):
            return [topic, f"evidence that contradicts {topic}"]
        return [question.strip()]

    @staticmethod
    def _is_contradiction(question: str) -> bool:
        """Whether ``question`` explicitly seeks a contradiction."""
        return bool(set(_TOKEN_RE.findall(question.lower())) & _CONTRADICTION_CUES)

    @staticmethod
    def _topic(question: str) -> str:
        """Strip framing / discourse words down to the question's core claim.

        Keeps the substantive subject+predicate content tokens in their original
        order while dropping stopwords, contradiction cues ("contradict",
        "conflict", …), the bare conjunction "and"/"both", and the deposition
        discourse words ("witness", "testimony", "statement", "story"). E.g.
        "did the witness contradict himself about the night of the 14th?"
        -> "night 14th"; "Did the supplier and the buyer agree on the delivery
        terms?" -> "supplier buyer agree delivery terms".
        """
        drop = _CONTRADICTION_CUES | _MULTIHOP_CUES | {
            "witness",
            "witnesses",
            "himself",
            "herself",
            "testimony",
            "statement",
            "story",
            "account",
            "earlier",
            "version",
        }
        toks = [t for t in _content_tokens(question) if t not in drop]
        return " ".join(toks)


def detect_contradiction(
    citations: Sequence[Citation],
) -> tuple[bool, str | None, bool]:
    """Detect a cross-page / cross-document contradiction among ``citations``.

    STRUCTURAL "both cannot be true" check. Two high-confidence citations
    contradict when they

    1. are *about the same subject* (sufficient content-token overlap, so the
       detector never fires on incidental word overlap),
    2. **share a temporal anchor** — the same calendar day reference, e.g.
       "the 14th" / "the fourteenth" / "night of the 14th"
       (:func:`_temporal_anchors`), and
    3. assert **incompatible locations** for that subject at that time — one
       places them at a location the other separates them from
       ("two miles from the warehouse") or in a different location class
       (warehouse vs. downtown), per :func:`_locations_incompatible`.

    SELECTION among all eligible conflicting pairs is a strict, principled
    priority:

    1. pairs with an explicit **separation cue** (one side places the subject a
       measured distance from a place the other puts them at — the strongest,
       least-ambiguous "both cannot be true") rank above plain class-difference
       pairs;
    2. **cross-document** pairs (an alibi colliding with an independent exhibit)
       rank above same-document inconsistencies;
    3. then the highest combined citation score; ties break on chunk id.

    The PRIMARY is the *presence-asserting* side — the citation that places the
    subject AT a location (no separation cue) — so the page-jump lands on the
    claim under examination, with the separating evidence as the other side.

    Args:
        citations: Candidate citations (already fused / ranked).

    Returns:
        ``(contradiction, primary_id, cross_document)`` where ``primary_id`` is
        the id of the presence-asserting citation of the selected conflicting
        pair (or ``None`` when none is found) and ``cross_document`` is ``True``
        when the two citations come from different documents.
    """
    pair = contradiction_pair(citations)
    if pair is None:
        return False, None, False
    primary_id, other_id, cross_doc = pair
    logger.debug(
        "contradiction.detected primary=%s vs=%s cross_document=%s",
        primary_id,
        other_id,
        cross_doc,
    )
    return True, primary_id, cross_doc


def contradiction_pair(
    citations: Sequence[Citation],
) -> tuple[str, str, bool] | None:
    """Return the selected conflicting pair ``(primary_id, other_id, cross)``.

    Collects all eligible conflicting pairs (shared subject + shared temporal
    anchor + incompatible locations), then applies the strict selection
    priority described on :func:`detect_contradiction`:

    1. separation-cue pairs (strongest "both cannot be true") first,
    2. then cross-document pairs,
    3. then highest combined score, ties on chunk id.

    ``primary_id`` is the presence-asserting (non-separating) side — the claim
    being challenged — and ``other_id`` is the contradicting evidence, so a
    caller can keep BOTH in a truncated result. Returns ``None`` when no
    contradiction is found.
    """
    eligible = [c for c in citations if c.score >= _CONTRADICTION_MIN_SCORE]
    # (separation, cross_document, combined_score, primary_id, other_id).
    candidates: list[tuple[bool, bool, float, str, str]] = []
    for i, left in enumerate(eligible):
        left_tokens = set(_content_tokens(left.chunk.text))
        left_times = _temporal_anchors(left.chunk.text)
        for right in eligible[i + 1 :]:
            same_place = (
                left.documentId == right.documentId
                and left.chunk.page == right.chunk.page
            )
            if same_place:
                continue
            right_tokens = set(_content_tokens(right.chunk.text))
            if len(left_tokens & right_tokens) < _MIN_SUBJECT_OVERLAP:
                continue
            # (b) shared temporal anchor — same day under discussion.
            if not (left_times & _temporal_anchors(right.chunk.text)):
                continue
            # (c) incompatible locations for the subject at that time.
            if not _locations_incompatible(left.chunk.text, right.chunk.text):
                continue
            left_sep = _has_separation_cue(left.chunk.text)
            right_sep = _has_separation_cue(right.chunk.text)
            # Primary = the presence-asserting side (the one WITHOUT a
            # separation cue), so the jump lands on the claim being challenged.
            if right_sep and not left_sep:
                primary, other = left, right
            elif left_sep and not right_sep:
                primary, other = right, left
            else:
                # No (or symmetric) separation cue: anchor on the higher-scored.
                primary, other = (
                    (left, right) if left.score >= right.score else (right, left)
                )
            candidates.append(
                (
                    left_sep or right_sep,
                    left.documentId != right.documentId,
                    left.score + right.score,
                    primary.chunk.id,
                    other.chunk.id,
                )
            )
    if not candidates:
        return None
    # ``True`` sorts first via the 0/1 key in each tier.
    best = min(
        candidates,
        key=lambda c: (0 if c[0] else 1, 0 if c[1] else 1, -c[2], c[3], c[4]),
    )
    return best[3], best[4], best[1]


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

        # Fetch a wider candidate pool per hop than we ultimately publish so the
        # contradiction detector sees evidence (e.g. an independent exhibit) that
        # would otherwise be truncated. The published citations are still capped
        # at ``top_k`` below; only the conflict-detection pool is widened.
        fetch_k = max(per_hop_k, _CONTRADICTION_POOL_K)

        hops: list[HopTrace] = []
        # rankings: one best-first list of citation-ids per hop, for RRF.
        rankings: list[list[str]] = []
        # id -> Citation, keeping the highest single-hop score seen for each.
        by_id: dict[str, Citation] = {}

        for sub in sub_queries:
            result = await self._index.query_multi(
                sub, top_k=fetch_k, alpha=alpha, doc_ids=doc_ids
            )
            ranking: list[str] = []
            for cit in result.citations:
                cid = cit.chunk.id
                ranking.append(cid)
                prev = by_id.get(cid)
                if prev is None or cit.score > prev.score:
                    by_id[cid] = cit
            rankings.append(ranking)
            # Record the hop trail at the published depth so the frontend trail
            # matches the surfaced candidates, not the wider detection pool.
            hops.append(HopTrace(subQuery=sub, citationIds=ranking[:per_hop_k]))

        # Detect the contradiction over the FULL fused pool (not just the
        # top_k slice) so a conflicting pair whose weaker side would be
        # truncated is still found, then PROMOTE both pair members into the
        # returned citations so the published frame always carries the conflict.
        full = self._fuse(rankings, by_id, len(by_id))
        pair = contradiction_pair(full)
        contradiction = pair is not None
        primary_from_pair = pair[0] if pair else None
        cross_document = pair[2] if pair else False
        citations = self._fuse(rankings, by_id, top_k)
        if pair is not None:
            citations = self._promote_pair(full, citations, pair[0], pair[1], top_k)
        primary_id = primary_from_pair or (
            citations[0].chunk.id if citations else None
        )

        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.debug(
            "multihop.retrieve q=%r hops=%d citations=%d contradiction=%s cross=%s",
            question,
            len(hops),
            len(citations),
            contradiction,
            cross_document,
        )
        return MultiHopResult(
            query=question,
            citations=citations,
            hops=hops,
            contradiction=contradiction,
            cross_document=cross_document,
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

    @staticmethod
    def _promote_pair(
        full: list[Citation],
        citations: list[Citation],
        primary_id: str,
        other_id: str,
        top_k: int,
    ) -> list[Citation]:
        """Ensure both contradiction-pair members appear in ``citations``.

        The conflicting pair is detected over the full fused pool; a weaker side
        (e.g. a low-ranked corroborating exhibit) could fall outside the top_k
        slice. This guarantees both the primary (claim) and the other
        (contradicting evidence) are present — prepending any missing member and
        re-truncating to ``top_k`` — so the published frame always carries the
        full conflict rather than dropping its evidence.
        """
        present = {c.chunk.id for c in citations}
        by_id = {c.chunk.id: c for c in full}
        missing = [
            by_id[cid]
            for cid in (primary_id, other_id)
            if cid not in present and cid in by_id
        ]
        if not missing:
            return citations
        return (missing + citations)[:top_k]
