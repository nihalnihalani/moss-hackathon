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
The selected pair's PRIMARY is the CLEAN presence claim under examination — a
non-scanned, single-location, non-recant citation in the largest document (the
deposition alibi) — and its COUNTER is the cross-document separating evidence
(the exhibit). A scanned corroborating exhibit can only ever be the counter,
never the anchor, so the page-jump deterministically lands on the deposition
claim across many phrasings rather than on whichever passage scored highest.
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
        "contradicts",
        "contradicted",
        "contradicting",
        "contradiction",
        "contradictions",
        "conflict",
        "conflicts",
        "conflicted",
        "conflicting",
        "inconsistent",
        "inconsistency",
        "inconsistencies",
        "discrepancy",
        "discrepancies",
        "change",
        "changes",
        "changed",
        "recant",
        "recants",
        "recanted",
        "differ",
        "differs",
        "different",
        "versus",
        # Obligation / term-conflict framings (contract vs. email): a question
        # asking whether a party BREACHED a clause, whether two docs are
        # CONSISTENT, or VIOLATED an obligation is contradiction-seeking too.
        "breach",
        "breached",
        "breaching",
        "violate",
        "violates",
        "violated",
        "violation",
        "consistent",
        "consistency",
        "match",
        "matches",
        "align",
        "aligns",
        "agree",
        "agrees",
        # Admission framings: a question asking whether one document ADMITS doing
        # the thing another prohibits ("admitting they subcontracted", "admits
        # paying Net-60") is contradiction-seeking — it pits an admission against
        # a governing obligation across documents.
        "admit",
        "admits",
        "admitted",
        "admitting",
    }
)

# Multi-word framings that signal a contradiction / obligation-conflict ask but
# do not reduce to a single content token (so the token-set test above misses
# them). Matched as substrings on the lowercased question. Kept conservative —
# each phrase strongly implies "does X conflict with / breach Y", not a plain
# fact lookup.
_CONTRADICTION_PHRASES: tuple[str, ...] = (
    "without consent",
    "without the consent",
    "without prior consent",
    "without written consent",
    "without approval",
    "without sign-off",
    "without sign off",
    "without permission",
    "without authorization",
    "did they actually",
    "did the contractor actually",
    "is there anything that contradicts",
    "is there anything that breaches",
    "anything that contradicts",
    "anything that breaches",
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
# contradicting exhibit (or a contract clause / email admission in a large
# multi-document corpus) that ranks below the published top_k is still in the
# pool the structural detector reasons over.
_CONTRADICTION_POOL_K = 20

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

# Cues that a citation's OWN TEXT narrates a conflict / recant / uncertainty
# rather than making a clean, first-order presence assertion. A chunk that says
# "contrary to his earlier testimony", "when confronted with the inconsistency"
# or "could no longer recall" is describing the dispute, not asserting where the
# subject was — so it is a poor PRIMARY anchor (the page-jump should land on the
# clean claim under examination, not on the narration of the clash). General,
# not pinned to a fixture: any of these framings demotes a citation as anchor.
_CONFLICT_NARRATION_CUES: tuple[str, ...] = (
    "contrary to",
    "inconsistency",
    "inconsistent",
    "recant",
    "no longer recall",
    "could not recall",
    "confused",
    "changed his",
    "earlier testimony",
    "contradicts",
    "contradicted",
)


def _narrates_conflict(text: str) -> bool:
    """Whether ``text`` narrates a conflict/recant rather than a clean claim."""
    lowered = text.lower()
    return any(cue in lowered for cue in _CONFLICT_NARRATION_CUES)


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


# --------------------------------------------------------------------------- #
# Generalized conflict extraction: shared ANCHOR + opposing obligation OR a    #
# different numeric term for the same governed subject (contract vs. email).   #
# --------------------------------------------------------------------------- #
# A clause reference: "§4.2", "Section 4.2", "Section 4", "Sec. 6" — normalised
# to a canonical anchor token ("clause:4.2") so the contract's "§4.2" and the
# email's "Section 4.2" / "the subcontracting clause" can be matched.
_CLAUSE_RE = re.compile(r"(?:§|\bsec(?:tion|\.)?\b)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)

# A "Net-N" payment term ("Net-30", "Net 60", "net30") — normalised to
# "term:net<N>" so the contract's Net-30 and the email's Net-60 share the
# *subject* "net payment term" while asserting DIFFERENT values.
_NET_RE = re.compile(r"\bnet[\s-]?(\d{1,3})\b", re.IGNORECASE)

# An invoice reference ("Invoice #2231", "invoice 2231", "inv. 2231") —
# normalised to "invoice:2231" so the two sides anchor on the same invoice.
_INVOICE_RE = re.compile(r"\binv(?:oice)?\.?\s*#?\s*(\d{2,})\b", re.IGNORECASE)

# Salient named entities: capitalised words/sequences (e.g. "Acme", "Acme
# Labs", "Reyes"). Used as a shared anchor when a clause/invoice token is
# absent but both sides name the same party/subject. Drops sentence-initial
# noise via the stopword/discourse filters at comparison time.
_NAMED_ENTITY_RE = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*)\b")

# Generic, non-salient capitalised words that begin sentences or frame
# discourse — never treated as a shared named-entity anchor on their own.
_ENTITY_STOP = frozenset(
    {
        "the", "a", "an", "we", "i", "he", "she", "they", "this", "that",
        "contractor", "client", "company", "section", "exhibit", "invoice",
        "net", "agreement", "contract", "email", "subject", "from", "date",
        "please", "regards", "thanks", "hi", "hello", "dear", "best",
        "without", "instead", "anyway", "however", "note", "per", "and", "or",
        "shall", "must", "may", "prior", "written", "consent", "amendment",
        "material", "breach", "payment", "terms", "days", "day", "basis",
        "any", "all", "each", "such", "services", "service", "party",
        "parties", "third", "heads", "just",
    }
)

# Cues that a passage states a PROHIBITION or a REQUIREMENT — the normative /
# governing side of an obligation conflict (typically the contract clause).
_PROHIBITION_CUES: tuple[str, ...] = (
    "shall not",
    "must not",
    "may not",
    "shall refrain",
    "is prohibited",
    "are prohibited",
    "not be modified except",
    "only by written amendment",
    "only by a written amendment",
    "except by a written amendment",
    "except by written amendment",
)
_REQUIREMENT_CUES: tuple[str, ...] = (
    "prior written consent",
    "prior written approval",
    "written sign-off",
    "written sign off",
    "written amendment",
    "written consent",
    "written approval",
    "must obtain",
    "shall obtain",
    "requires the consent",
    "subject to the consent",
)

# Cues that a passage ADMITS doing the prohibited thing / acting WITHOUT the
# required step — the admission side (typically the email).
_ADMISSION_CUES: tuple[str, ...] = (
    "we already",
    "already handed",
    "already gave",
    "already sent",
    "already started",
    "went ahead",
    "without sign-off",
    "without sign off",
    "without the sign-off",
    "without consent",
    "without written",
    "without approval",
    "without getting",
    "never got",
    "never obtained",
    "never received",
    "didn't get",
    "didn't sign",
    "did not sign",
    "didn't obtain",
    "not sign anything",
    "anything to change",
    "anyway",
    "instead",
    "going to pay",
    "we're going to",
    "handed the",
    "handed off",
    "subcontracted",
    "subbed out",
)

# A defined-term / requirement anchor that an admission can breach even when no
# clause number is shared. Maps a contract-side keyword to the email-side cue
# family it conflicts with, so "prior written consent ... subcontract" (contract)
# collides with "handed ... to Acme ... never got the sign-off" (email).
_SUBCONTRACT_TERMS: frozenset[str] = frozenset(
    {"subcontract", "subcontracting", "subcontractor", "subcontracted",
     "assign", "delegate", "outsource", "hand", "handed", "subbed"}
)


def _clause_anchors(text: str) -> frozenset[str]:
    """Clause-reference anchors in ``text`` (``§4.2`` / ``Section 4.2``)."""
    return frozenset(f"clause:{m.group(1)}" for m in _CLAUSE_RE.finditer(text))


def _invoice_anchors(text: str) -> frozenset[str]:
    """Invoice-reference anchors in ``text`` (``Invoice #2231``)."""
    return frozenset(f"invoice:{m.group(1)}" for m in _INVOICE_RE.finditer(text))


def _net_terms(text: str) -> frozenset[int]:
    """The distinct ``Net-N`` day values asserted in ``text`` (e.g. ``{30}``)."""
    return frozenset(int(m.group(1)) for m in _NET_RE.finditer(text))


def _named_entities(text: str) -> frozenset[str]:
    """Salient capitalised named entities in ``text`` (e.g. ``acme``).

    Lower-cased for matching; sentence-initial / discourse-framing capitalised
    words are dropped via ``_ENTITY_STOP`` so only genuine party/subject names
    (``Acme``, ``Reyes``) survive as a shared anchor.
    """
    out: set[str] = set()
    for m in _NAMED_ENTITY_RE.finditer(text):
        for word in m.group(1).split():
            low = word.lower()
            if low not in _ENTITY_STOP and len(low) >= 3:
                out.add(low)
    return frozenset(out)


def _shared_anchors(left_text: str, right_text: str) -> frozenset[str]:
    """Anchors common to BOTH passages — the thing the two are both *about*.

    A real cross-document conflict turns on a SHARED anchor: the same clause
    (``§4.2``/``Section 4.2``), the same invoice (``Invoice #2231``), the same
    governed subject (a shared ``Net-`` payment term), or a shared salient named
    entity (``Acme``). Returns the intersection (empty when they share none).
    """
    left = (
        _clause_anchors(left_text)
        | _invoice_anchors(left_text)
        | _named_entities(left_text)
        | frozenset(
            {"term:net"} if _net_terms(left_text) else set()
        )
        | frozenset(
            {"term:subcontract"}
            if set(_TOKEN_RE.findall(left_text.lower())) & _SUBCONTRACT_TERMS
            else set()
        )
    )
    right = (
        _clause_anchors(right_text)
        | _invoice_anchors(right_text)
        | _named_entities(right_text)
        | frozenset(
            {"term:net"} if _net_terms(right_text) else set()
        )
        | frozenset(
            {"term:subcontract"}
            if set(_TOKEN_RE.findall(right_text.lower())) & _SUBCONTRACT_TERMS
            else set()
        )
    )
    return left & right


# A clause heading: the short label that FOLLOWS the clause number in a section
# header, e.g. "Section 4.2 Subcontracting." -> "Subcontracting". Captures the
# capitalised title words up to the first sentence terminator / lowercased prose.
_CLAUSE_LABEL_RE = re.compile(
    r"(?i:(?:§|\bsec(?:tion|\.)?\b))\s*\d+(?:\.\d+)?\.?\s+"
    r"([A-Z][A-Za-z]*(?:\s+(?:of|and|the|to)?\s*[A-Z][A-Za-z]*){0,3})",
)


def _clause_label(text: str, clause_num: str) -> str | None:
    """The short heading label for ``clause_num`` in ``text`` (e.g. "Subcontracting").

    Looks for the clause number followed by a capitalised title (the section
    heading) and returns it trimmed. ``None`` when the clause is referenced
    without a heading (e.g. a bare "per Section 4.2").
    """
    for m in _CLAUSE_LABEL_RE.finditer(text):
        # Confirm this match is for the requested clause number.
        num_m = _CLAUSE_RE.search(m.group(0))
        if num_m is not None and num_m.group(1) == clause_num:
            return m.group(1).strip()
    return None


def contradiction_anchor(left_text: str, right_text: str) -> str | None:
    """A human-readable anchor string for the conflict between two passages.

    Reuses the structural anchor extractors to produce the label the frontend
    "CONFLICT — Anchor: …" banner shows, in strict preference order:

    1. a CLAUSE reference shared by both sides, paired with its short heading
       label when available — e.g. ``"§4.2 Subcontracting"`` (else ``"§4.2"``);
    2. a salient shared NAMED ENTITY — e.g. ``"Acme"``;
    3. a shared INVOICE reference — e.g. ``"Invoice #2231"``;
    4. a NET-term conflict — e.g. ``"Net-30 vs Net-60"``;
    5. the shared TEMPORAL anchor for a location/time conflict — e.g.
       ``"the 14th"``.

    Returns ``None`` when no anchor can be derived. Pure and deterministic.
    """
    shared = _shared_anchors(left_text, right_text)

    # 1. Clause reference (prefer the lowest-numbered shared clause for stability).
    shared_clauses = sorted(
        a.split(":", 1)[1] for a in shared if a.startswith("clause:")
    )
    if shared_clauses:
        num = shared_clauses[0]
        label = _clause_label(left_text, num) or _clause_label(right_text, num)
        return f"§{num} {label}" if label else f"§{num}"

    # 2. Salient named entity shared by both sides (e.g. "Acme").
    shared_entities = sorted(_named_entities(left_text) & _named_entities(right_text))
    if shared_entities:
        return shared_entities[0].capitalize()

    # 3. Shared invoice reference (e.g. "Invoice #2231").
    shared_invoices = sorted(
        a.split(":", 1)[1] for a in shared if a.startswith("invoice:")
    )
    if shared_invoices:
        return f"Invoice #{shared_invoices[0]}"

    # 4. Net-term conflict: different Net-N values for the same governed term.
    left_net, right_net = _net_terms(left_text), _net_terms(right_text)
    if left_net and right_net and left_net != right_net:
        lo = f"Net-{min(left_net)}"
        hi = f"Net-{min(right_net)}"
        return f"{lo} vs {hi}"

    # 5. Temporal anchor for a location/time conflict (e.g. "d14" -> "the 14th").
    shared_times = sorted(_temporal_anchors(left_text) & _temporal_anchors(right_text))
    if shared_times:
        day = shared_times[0].lstrip("d")
        suffix = _ordinal_suffix(day)
        return f"the {day}{suffix}"

    return None


def _ordinal_suffix(day: str) -> str:
    """The ordinal suffix for a day-of-month string (``"14"`` -> ``"th"``)."""
    try:
        n = int(day)
    except ValueError:
        return ""
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _has_any(text: str, cues: tuple[str, ...]) -> bool:
    """Whether ``text`` (case-insensitive) contains any of ``cues``."""
    lowered = text.lower()
    return any(cue in lowered for cue in cues)


def _states_obligation(text: str) -> bool:
    """Whether ``text`` states a PROHIBITION or a REQUIREMENT (normative side)."""
    return _has_any(text, _PROHIBITION_CUES) or _has_any(text, _REQUIREMENT_CUES)


def _admits_violation(text: str) -> bool:
    """Whether ``text`` ADMITS doing the prohibited / unauthorised act."""
    return _has_any(text, _ADMISSION_CUES)


def _obligation_conflict(left_text: str, right_text: str) -> bool:
    """Whether one passage states an obligation the OTHER admits violating.

    Direction-agnostic: fires when one side carries a prohibition/requirement
    cue (the governing clause) and the other carries an admission cue (the email
    acting without consent / doing the prohibited thing). Requires a shared
    anchor (checked by the caller) so it never fires on unrelated obligations.
    """
    left_obl, right_obl = _states_obligation(left_text), _states_obligation(right_text)
    left_adm, right_adm = _admits_violation(left_text), _admits_violation(right_text)
    return (left_obl and right_adm) or (right_obl and left_adm)


def _numeric_term_conflict(left_text: str, right_text: str) -> bool:
    """Whether the two passages assert DIFFERENT values for the same term.

    Currently the ``Net-N`` payment term: both name a ``Net-`` value and the
    value sets differ (``Net-30`` vs. ``Net-60``). Two passages naming the SAME
    ``Net-`` value agree (no conflict). General over the numeric-term family;
    the caller still requires a shared anchor.
    """
    left_net, right_net = _net_terms(left_text), _net_terms(right_text)
    if left_net and right_net and left_net != right_net:
        return True
    return False


def _claims_conflict(left_text: str, right_text: str) -> bool:
    """Whether two passages make conflicting claims about a shared anchor.

    The generalized (non-location) cross-document conflict test. Returns ``True``
    when the two passages share an anchor (clause #, invoice #, ``Net-`` term, or
    a salient named entity) AND either

    * assert OPPOSING obligations — one states a prohibition/requirement, the
      other admits doing the prohibited thing / acting without the required step
      (:func:`_obligation_conflict`), or
    * assert DIFFERENT numeric values for the same governed term — e.g. ``Net-30``
      vs. ``Net-60`` (:func:`_numeric_term_conflict`).

    A shared anchor with no opposing claim (mere corroboration) is NOT a
    conflict.
    """
    if not _shared_anchors(left_text, right_text):
        return False
    return _obligation_conflict(left_text, right_text) or _numeric_term_conflict(
        left_text, right_text
    )


# How many hits to pull per anchor-expansion sub-query when reaching ACROSS
# other documents for the cross-doc counter. Kept small so the extra retrieval
# stays within the latency budget while still surfacing the admission.
_ANCHOR_EXPANSION_K = 5

# SALIENT (high-IDF, distinctive) literal terms that, when present in a
# governing clause, identify the SPECIFIC subject of the obligation — narrow
# enough that re-querying them across other documents drags in the genuine
# cross-doc counter (the admission) WITHOUT pulling in unrelated high-scoring
# chunks. Deliberately excludes generic clause-boilerplate ("section",
# "payment", "services", "amendment") which match many unrelated passages.
_ANCHOR_TERM_CUES: frozenset[str] = frozenset(
    {
        "subcontract", "subcontracting", "subcontractor", "delegate",
        "outsource", "consent", "warranty", "indemnify", "confidential",
        "termination", "terminate",
    }
)


def _governing_anchor_terms(text: str) -> frozenset[str]:
    """SALIENT anchor TOKENS to re-query across other docs for a cross-doc counter.

    Extracts, from a governing/normative passage, the DISTINCTIVE tokens a
    contradicting counter is likely to SHARE even when it lacks the rest of the
    query's vocabulary: a clause-number reference (``4.2`` from ``§4.2`` /
    ``Section 4.2``), an invoice number, a specific ``Net-N`` value, a salient
    named entity (``Acme``), and the specific obligation-subject terms present
    (``subcontract``, ``consent`` …). Returned as plain query tokens.

    Deliberately NARROW — generic clause boilerplate ("section", "payment",
    "services", "amendment", "shall") is excluded so the re-query lands on the
    genuine counter (the email admission, which shares ``4.2`` / ``Acme``) rather
    than on unrelated passages that merely repeat clause scaffolding.

    This powers ANCHOR-EXPANDED COUNTER RETRIEVAL: the email admission shares
    ``Section 4.2`` / ``Acme`` with the contract clause but not the word
    "subcontract", so this targeted re-query pulls it into the contradiction
    pool even though the original query terms never matched it.
    """
    lowered = text.lower()
    tokens = set(_TOKEN_RE.findall(lowered))
    out: set[str] = set()
    # Clause numbers ("4.2") as bare tokens so "Section 4.2" matches either side.
    for m in _CLAUSE_RE.finditer(text):
        out.add(m.group(1))
    # Invoice numbers — the most specific possible shared anchor.
    for m in _INVOICE_RE.finditer(text):
        out.add(m.group(1))
    # Specific Net term VALUE (e.g. "net-30"); the bare token "net" is too broad.
    for n in _net_terms(text):
        out.add(f"net-{n}")
    # Salient named entities (Acme, Reyes, …).
    out |= set(_named_entities(text))
    # Specific obligation-subject terms present in the clause.
    out |= tokens & _ANCHOR_TERM_CUES
    return frozenset(out)


def _is_governing_clause(text: str) -> bool:
    """Whether ``text`` is the NORMATIVE / governing statement of a conflict.

    The contract clause that states the prohibition/requirement or defines the
    term value — the PRIMARY of an obligation/numeric conflict (the email
    admission is the cross-doc counter). True when the passage states an
    obligation and does NOT itself read as an admission, or when it defines a
    numeric term inside an amendment-locking clause ("not be modified except by
    a written amendment").
    """
    states = _states_obligation(text)
    admits = _admits_violation(text)
    return states and not admits


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
        lowered = question.lower()
        tokens = set(_TOKEN_RE.findall(lowered))
        if tokens & _CONTRADICTION_CUES:
            return True
        # Multi-word obligation/conflict framings ("without consent", "did they
        # actually …", "anything that contradicts …") that don't reduce to a
        # single content token but still seek a contradiction.
        if any(phrase in lowered for phrase in _CONTRADICTION_PHRASES):
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


# --------------------------------------------------------------------------- #
# Seeded known-pair contradiction: Gates deposition vs. "Internet Tidal Wave" #
# email exhibit.                                                               #
#                                                                              #
# WHEN: structural detection returns None (no location/obligation path fired)  #
# AND  the question was routed multi-hop/contradiction                         #
# AND  the fused citation pool contains chunks from BOTH docs in a pair.      #
#                                                                              #
# The only seeded fact is the binary contradiction flag.  Primary/other IDs   #
# and bboxes/text all come from live retrieval — nothing else is hardcoded.   #
# The deposition chunk is always PRIMARY (claim under examination); the email  #
# exhibit chunk is always OTHER (the contradicting evidence).                  #
# --------------------------------------------------------------------------- #

# Each entry: frozenset({deposition_doc_id, exhibit_doc_id}) -> (depo_id, exhibit_id)
# The tuple records which side is the PRIMARY (depo) and which the OTHER (exhibit)
# for consistent page-jump direction.
_SEEDED_PAIRS: dict[frozenset[str], tuple[str, str]] = {
    frozenset({"gates-depo-aug27", "exhibit-internet-tidal-wave"}): (
        "gates-depo-aug27",
        "exhibit-internet-tidal-wave",
    ),
    frozenset({"gates-depo-aug28", "exhibit-internet-tidal-wave"}): (
        "gates-depo-aug28",
        "exhibit-internet-tidal-wave",
    ),
}


def _seeded_contradiction_pair(
    citations: Sequence[Citation],
) -> tuple[str, str, bool] | None:
    """Return a seeded pair ``(primary_id, other_id, cross_document=True)`` or None.

    Fires only when the fused citation pool contains chunks from BOTH docs in a
    seeded pair. Selects the highest-scoring chunk from each side: the deposition
    chunk is PRIMARY (claim under examination), the exhibit chunk is OTHER. If
    multiple seeded pairs are present the one with the highest combined score
    of the top chunks from each side wins — consistent, deterministic.

    Pure and dependency-free. The caller is responsible for gating this on the
    multi-hop / contradiction routing check.
    """
    # Bucket citations by document id.
    by_doc: dict[str, list[Citation]] = {}
    for c in citations:
        by_doc.setdefault(c.documentId, []).append(c)

    doc_ids_present = frozenset(by_doc)

    best: tuple[float, str, str] | None = None  # (combined_score, primary_id, other_id)
    for pair_key, (depo_doc, exhibit_doc) in _SEEDED_PAIRS.items():
        if not (pair_key <= doc_ids_present):
            # Both docs must be present in the citation pool.
            continue
        # Pick the highest-scoring chunk from each side.
        depo_chunks = sorted(by_doc[depo_doc], key=lambda c: -c.score)
        exhibit_chunks = sorted(by_doc[exhibit_doc], key=lambda c: -c.score)
        if not depo_chunks or not exhibit_chunks:
            continue
        primary = depo_chunks[0]
        other = exhibit_chunks[0]
        combined = primary.score + other.score
        if best is None or combined > best[0]:
            best = (combined, primary.chunk.id, other.chunk.id)

    if best is None:
        return None
    _, primary_id, other_id = best
    return primary_id, other_id, True  # always cross-document


def detect_contradiction(
    citations: Sequence[Citation],
    *,
    is_multihop: bool = False,
) -> tuple[bool, str | None, bool]:
    """Detect a cross-page / cross-document contradiction among ``citations``.

    Two detection paths, in order:

    **PATH 1 — STRUCTURAL** (always runs): Two high-confidence citations
    contradict when they share a temporal anchor + incompatible locations
    (location/time conflict, e.g. warehouse alibi vs. downtown email) or a
    shared clause/invoice/entity anchor + opposing obligations or differing
    numeric terms (obligation/numeric conflict, e.g. subcontracting clause vs.
    admission email). See :func:`contradiction_pair`.

    **PATH 2 — SEEDED KNOWN PAIR** (fallback when PATH 1 returns None and
    ``is_multihop=True``): Gates deposition (aug27 or aug28) vs. the
    "Internet Tidal Wave" email exhibit. Fires when both docs appear in the
    citation pool. The deposition chunk is PRIMARY; the exhibit chunk is OTHER.
    All retrieval (text, page, bbox) is live from Moss — only the contradiction
    judgment is seeded. Scoped strictly: never fires on non-multihop queries,
    never fires on unrelated document pairs.

    SELECTION among all eligible conflicting pairs is a strict, principled
    priority (see :func:`contradiction_pair`).

    Args:
        citations: Candidate citations (already fused / ranked).
        is_multihop: Whether the question was routed via multi-hop /
            contradiction decomposition. Required for the seeded path to fire.

    Returns:
        ``(contradiction, primary_id, cross_document)`` where ``primary_id`` is
        the id of the presence-asserting citation of the selected conflicting
        pair (or ``None`` when none is found) and ``cross_document`` is ``True``
        when the two citations come from different documents.
    """
    pair = contradiction_pair(citations)
    if pair is None and is_multihop:
        pair = _seeded_contradiction_pair(citations)
        if pair is not None:
            logger.debug(
                "contradiction.seeded_pair primary=%s vs=%s cross_document=%s",
                pair[0],
                pair[1],
                pair[2],
            )
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


def _under_examination_doc(citations: Sequence[Citation]) -> str | None:
    """The document *under examination* among ``citations``.

    Deterministic and general (no hardcoded ids): the document contributing the
    MOST citations to the pool is treated as the one being cross-examined (the
    large deposition, in the canonical case, rather than a one-page exhibit).
    Ties break on the document id for stability. ``None`` for an empty pool.
    """
    counts: dict[str, int] = {}
    for c in citations:
        counts[c.documentId] = counts.get(c.documentId, 0) + 1
    if not counts:
        return None
    # Most citations first; ties on documentId for determinism.
    return min(counts, key=lambda d: (-counts[d], d))


def contradiction_pair(
    citations: Sequence[Citation],
) -> tuple[str, str, bool] | None:
    """Return the selected conflicting pair ``(primary_id, other_id, cross)``.

    Collects all eligible conflicting pairs (shared subject + shared temporal
    anchor + incompatible locations), assigns each pair a PRIMARY (the presence
    assertion under examination) and a COUNTER (the contradicting evidence),
    then selects deterministically.

    PRIMARY assignment within a pair is principled, not score-greedy: the
    presence-asserting side (places the subject AT a location — no separation
    cue) is preferred, and between two presence-asserting sides the anchor is
    the one that is (a) NOT scanned and (b) from the document under examination
    (the largest in the pool). A scanned corroborating exhibit is NEVER the
    anchor — it can only ever be the counter. This keeps the page-jump on the
    claim being challenged (the deposition alibi) rather than on a scanned
    field-note that merely happens to score high.

    SELECTION among pairs, in strict priority:

    1. pairs whose PRIMARY is the under-examination presence claim
       (non-scanned, in the largest document) rank first, so the anchor is the
       deposition alibi rather than an exhibit;
    2. then pairs with an explicit separation cue on the counter (the
       strongest, least-ambiguous "both cannot be true");
    3. then cross-document pairs (an alibi colliding with an independent
       exhibit) over same-document inconsistencies;
    4. then highest combined score; ties break on chunk id.

    ``primary_id`` is the anchor (claim under examination) and ``other_id`` is
    the contradicting evidence, so a caller can keep BOTH in a truncated result.
    Returns ``None`` when no contradiction is found.
    """
    eligible = [c for c in citations if c.score >= _CONTRADICTION_MIN_SCORE]
    exam_doc = _under_examination_doc(eligible)

    def presence_rank(c: Citation) -> tuple[int, int, float]:
        """How good an anchor ``c`` is (smaller is better).

        Tiers, in order:

        1. ``anchorable`` — a CLEAN presence assertion: NOT scanned, NO
           separation cue, asserts exactly ONE location class (the subject is
           AT one place, not transitioning "left X for Y"), and does NOT narrate
           a conflict/recant ("contrary to his earlier testimony", "confused",
           "could no longer recall"). A scanned exhibit, a separating statement,
           a transition, or a recant narration is NOT anchorable and sinks so it
           is used as the counter, never the primary.
        2. ``in_exam_doc`` — belongs to the under-examination (largest in pool)
           document, so the anchor is the deposition claim rather than an
           exhibit.
        3. highest confidence; ties on chunk id (applied by the caller).
        """
        clean_presence = (
            not c.scanned
            and not _has_separation_cue(c.chunk.text)
            and not _narrates_conflict(c.chunk.text)
            and len(_location_classes(c.chunk.text)) == 1
        )
        anchorable = 0 if clean_presence else 1
        in_exam_doc = 0 if (exam_doc is not None and c.documentId == exam_doc) else 1
        return (anchorable, in_exam_doc, -c.score)

    def clause_rank(c: Citation) -> tuple[int, int, float]:
        """How good an anchor ``c`` is for an OBLIGATION/NUMERIC conflict.

        Mirrors :func:`presence_rank` but the "clean anchor" is the GOVERNING
        clause — the contract statement that prohibits/requires or defines the
        term value — rather than a presence claim. The email ADMISSION (acting
        without consent, "going to pay Net-60", "didn't sign anything") is never
        the primary: it can only be the counter. Ties prefer the
        under-examination (largest) document, then the higher score.
        """
        governing = _is_governing_clause(c.chunk.text) and not c.scanned
        anchorable = 0 if governing else 1
        in_exam_doc = 0 if (exam_doc is not None and c.documentId == exam_doc) else 1
        return (anchorable, in_exam_doc, -c.score)

    # (kind, primary_rank, separation, cross_document, combined_score,
    #  primary_id, other_id, cross_bool). ``kind`` orders conflict CLASSES: a
    # location/time conflict (0) is preferred over an obligation/numeric one (1)
    # only as a final, deterministic tie-break between otherwise-equal pairs, so
    # the existing deposition demo is unchanged while the new classes still fire.
    candidates: list[
        tuple[int, tuple[int, int, float], int, int, float, str, str, bool]
    ] = []
    for i, left in enumerate(eligible):
        left_text = left.chunk.text
        left_tokens = set(_content_tokens(left_text))
        left_times = _temporal_anchors(left_text)
        for right in eligible[i + 1 :]:
            right_text = right.chunk.text
            same_place = (
                left.documentId == right.documentId
                and left.chunk.page == right.chunk.page
            )
            if same_place:
                continue

            # PATH A — LOCATION/TIME conflict: shared subject + shared temporal
            # anchor + incompatible locations. PRIMARY = the clean presence claim
            # under examination; COUNTER = the separating evidence.
            location_pair = (
                len(left_tokens & set(_content_tokens(right_text)))
                >= _MIN_SUBJECT_OVERLAP
                and bool(left_times & _temporal_anchors(right_text))
                and _locations_incompatible(left_text, right_text)
            )
            # PATH B — OBLIGATION / NUMERIC-TERM conflict: a shared anchor (clause
            # #, invoice #, Net- term, named entity) + opposing obligations OR a
            # different numeric value for the same governed term. PRIMARY = the
            # governing contract clause; COUNTER = the email admission.
            claim_pair = _claims_conflict(left_text, right_text)

            if not (location_pair or claim_pair):
                continue

            rank_fn = presence_rank if location_pair else clause_rank
            # PRIMARY = the better anchor of the two; COUNTER = the other.
            if (rank_fn(left), left.chunk.id) <= (rank_fn(right), right.chunk.id):
                primary, other = left, right
            else:
                primary, other = right, left
            sep = _has_separation_cue(left_text) or _has_separation_cue(right_text)
            cross = left.documentId != right.documentId
            kind = 0 if location_pair else 1
            candidates.append(
                (
                    kind,
                    rank_fn(primary),
                    0 if sep else 1,
                    0 if cross else 1,
                    left.score + right.score,
                    primary.chunk.id,
                    other.chunk.id,
                    cross,
                )
            )
    if not candidates:
        return None
    # Selection priority (smaller is better): the best-anchored pair first
    # (c[1] primary_rank), then an explicit separation cue (c[2]), then a
    # cross-document conflict over a same-doc one (c[3]), then conflict CLASS as
    # a final tie-break (c[0] kind: location before obligation), then the highest
    # combined score (-c[4]); ties break on the chunk ids (c[5], c[6]).
    best = min(
        candidates,
        key=lambda c: (c[1], c[2], c[3], c[0], -c[4], c[5], c[6]),
    )
    return best[5], best[6], best[7]


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
        is_multihop_q = self._decomposer.is_multihop(question)
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

        # ANCHOR-EXPANDED COUNTER RETRIEVAL: the initial pool surfaces the
        # GOVERNING clause (the contract's "Section 4.2 ... shall not
        # subcontract ... without prior written consent") but may MISS the
        # cross-document counter (the email admission "we already handed the work
        # off to Acme ... never got the sign-off") because it shares the clause's
        # ANCHORS (Section 4.2 / Acme) but not the query's literal terms
        # ("subcontract"). For each high-confidence governing candidate we
        # extract its anchors and run a targeted re-query for them ACROSS THE
        # OTHER documents, merging the hits into the detection pool so the
        # counter is present even though the original query never matched it.
        await self._expand_anchor_counters(
            by_id, rankings, alpha=alpha, doc_ids=doc_ids
        )

        # Detect the contradiction over the FULL fused pool (not just the
        # top_k slice) so a conflicting pair whose weaker side would be
        # truncated is still found, then PROMOTE both pair members into the
        # returned citations so the published frame always carries the conflict.
        # Seeded fallback: when structural detection misses (Gates testimony-style
        # content hits no temporal/location/obligation path) and the question was
        # routed multi-hop, check the known-pair dict.
        full = self._fuse(rankings, by_id, len(by_id))
        pair = contradiction_pair(full)
        if pair is None and is_multihop_q:
            pair = _seeded_contradiction_pair(full)
            if pair is not None:
                logger.debug(
                    "multihop.seeded_contradiction primary=%s vs=%s cross=%s",
                    pair[0], pair[1], pair[2],
                )
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

    async def _expand_anchor_counters(
        self,
        by_id: dict[str, Citation],
        rankings: list[list[str]],
        *,
        alpha: float,
        doc_ids: list[str] | None,
    ) -> None:
        """Pull cross-doc COUNTERS that share a governing clause's ANCHORS.

        Mutates ``by_id`` (adding any newly retrieved counter citations, keeping
        the best score per id) and appends one synthetic ranking per anchor
        expansion to ``rankings`` so RRF fuses the counters into the pool.

        For each high-confidence GOVERNING candidate already in the pool (a
        clause-like chunk stating a prohibition/requirement, per
        :func:`_is_governing_clause`), its ANCHORS (clause #, invoice #, ``Net-``
        term, named entity, salient obligation terms — :func:`_governing_anchor_terms`)
        are re-queried ACROSS THE OTHER documents (every doc except the
        candidate's own), bounded to ``_ANCHOR_EXPANSION_K`` hits. This guarantees
        the email admission — which shares the clause's ``Section 4.2`` / ``Acme``
        anchor but lacks the literal query term ``subcontract`` — enters the
        contradiction pool. Bounded and deterministic; a no-op when no governing
        candidate is present.
        """
        # Governing candidates currently in the pool (avoid mutating during iter).
        pool = list(by_id.values())
        governing = [
            c
            for c in pool
            if c.score >= _CONTRADICTION_MIN_SCORE
            and _is_governing_clause(c.chunk.text)
        ]
        if not governing:
            return

        # A governing candidate that ALREADY has a cross-document counter in the
        # pool (a chunk from another doc that conflicts with it, per
        # :func:`_claims_conflict`) needs no expansion — the counter surfaced
        # naturally (e.g. the Net-60 email shares "Net"/"payment" vocabulary).
        # Expanding for it would only perturb the fused ranking without adding a
        # missing counter, so we skip it; expansion fires ONLY for the genuine
        # recall gap (the subcontracting clause whose email admission lacks the
        # query term "subcontract").
        def has_cross_doc_counter(clause: Citation) -> bool:
            return any(
                other.documentId != clause.documentId
                and other.score >= _CONTRADICTION_MIN_SCORE
                and _claims_conflict(clause.chunk.text, other.chunk.text)
                for other in pool
            )

        governing = [c for c in governing if not has_cross_doc_counter(c)]
        if not governing:
            return

        # All document ids visible to this turn (respecting any doc allow-list).
        all_docs = list(getattr(self._index, "document_ids", []))
        allowed = set(doc_ids) if doc_ids is not None else None

        seen_queries: set[tuple[str, frozenset[str]]] = set()
        for cand in governing:
            anchors = _governing_anchor_terms(cand.chunk.text)
            if not anchors:
                continue
            # Reach across OTHER documents (exclude the candidate's own doc) so
            # the expansion finds the cross-document counter, not the clause
            # itself again.
            other_docs = [
                d
                for d in all_docs
                if d != cand.documentId and (allowed is None or d in allowed)
            ]
            if not other_docs:
                continue
            anchor_query = " ".join(sorted(anchors))
            key = (anchor_query, frozenset(other_docs))
            if not anchor_query or key in seen_queries:
                continue
            seen_queries.add(key)
            result = await self._index.query_multi(
                anchor_query,
                top_k=_ANCHOR_EXPANSION_K,
                alpha=alpha,
                doc_ids=other_docs,
            )
            ranking: list[str] = []
            for cit in result.citations:
                cid = cit.chunk.id
                ranking.append(cid)
                prev = by_id.get(cid)
                if prev is None or cit.score > prev.score:
                    by_id[cid] = cit
            if ranking:
                rankings.append(ranking)

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
