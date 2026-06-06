"""Proactive / ambient citation surfacing.

CrossExam can surface a citation *unprompted* — when a speaker makes a factual
assertion (not a question), the agent ambiently retrieves the supporting passage
and, if retrieval and faithfulness are both confident, publishes it with
``proactive: true`` so the UI can highlight the document without the user having
to ask. This is the "ambient fact-check" behaviour.

Two pure, testable pieces live here:

* :class:`ClaimDetector` — a heuristic that classifies an utterance as a
  *claim* (a declarative assertion worth checking) versus a *question* or
  filler (not worth surfacing).
* :func:`should_surface` — a confidence gate that only allows surfacing when
  BOTH the retrieval score AND the faithfulness score clear their thresholds,
  so we never push a low-confidence or unsupported highlight at the user.

Nothing here imports LiveKit or makes network calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from crossexam_backend.models import RetrievalResult
from crossexam_backend.verify import FaithfulnessVerdict

# Leading interrogatives / auxiliaries that mark a spoken question even without a
# trailing "?" (ASR often omits punctuation).
_QUESTION_LEADERS = frozenset(
    {
        "who", "what", "when", "where", "why", "how", "which", "whose", "whom",
        "is", "are", "was", "were", "do", "does", "did", "can", "could",
        "would", "should", "will", "shall", "may", "might", "have", "has",
        "had", "am",
    }
)

# Negations are strong assertion markers ("he did not sign the contract").
_NEGATIONS = frozenset(
    {"not", "no", "never", "none", "nobody", "nothing", "neither", "nor", "n't"}
)

_WORD_RE = re.compile(r"[a-z0-9']+")
# A digit run, including ordinals ("14th", "3rd") and grouped/decimal numbers.
_NUMBER_RE = re.compile(r"\b\d[\d,.]*(?:st|nd|rd|th)?\b")
# A capitalised, multi-token, or otherwise entity-like token in the RAW text.
_CAPWORD_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")


@dataclass(frozen=True)
class SurfaceThresholds:
    """Confidence thresholds for proactive surfacing.

    Attributes:
        retrieval_score: Minimum top-citation relevance score in ``[0, 1]``.
        faithfulness_score: Minimum faithfulness support score in ``[0, 1]``.
    """

    retrieval_score: float = 0.5
    faithfulness_score: float = 0.5


class ClaimDetector:
    """Heuristic detector for assertion-like (claim) utterances.

    An utterance is treated as a *claim* worth ambiently checking when it is a
    declarative statement (not a question) that carries some checkable content
    signal: a negation, a number, or an entity-like capitalised token. Pure and
    deterministic — no model, no network.

    Args:
        min_tokens: Minimum word count; shorter utterances ("yes", "okay") are
            never claims.
    """

    def __init__(self, *, min_tokens: int = 3) -> None:
        """Initialise the detector (see class docstring)."""
        self._min_tokens = max(1, min_tokens)

    def is_question(self, text: str) -> bool:
        """Return ``True`` when ``text`` reads as a question, not an assertion."""
        stripped = text.strip()
        if not stripped:
            return False
        if stripped.endswith("?"):
            return True
        words = _WORD_RE.findall(stripped.lower())
        if not words:
            return False
        return words[0] in _QUESTION_LEADERS

    def is_claim(self, text: str) -> bool:
        """Return ``True`` when ``text`` is a checkable declarative assertion.

        A claim is: not a question, at least ``min_tokens`` words long, and
        carrying at least one content signal (entity, number or negation).
        """
        stripped = text.strip()
        if not stripped or self.is_question(stripped):
            return False
        words = _WORD_RE.findall(stripped.lower())
        if len(words) < self._min_tokens:
            return False
        has_negation = any(w in _NEGATIONS for w in words)
        has_number = bool(_NUMBER_RE.search(stripped))
        # Entity signal: a capitalised word that is not merely the sentence's
        # first word (which is capitalised regardless).
        cap_matches = _CAPWORD_RE.findall(stripped)
        first_word_raw = stripped.split()[0] if stripped.split() else ""
        has_entity = any(m != first_word_raw for m in cap_matches)
        return has_negation or has_number or has_entity


def should_surface(
    result: RetrievalResult,
    faithfulness: FaithfulnessVerdict,
    thresholds: SurfaceThresholds | None = None,
) -> bool:
    """Confidence gate for proactive surfacing.

    Returns ``True`` only when there is a top citation whose retrieval score
    clears ``thresholds.retrieval_score`` AND the faithfulness verdict is
    ``supported`` with a score clearing ``thresholds.faithfulness_score``. Both
    must pass — a confident retrieval with a weak faithfulness signal (or vice
    versa) is suppressed, so an unprompted highlight is always high-confidence.

    Args:
        result: The ambient retrieval result for the detected claim.
        faithfulness: The faithfulness verdict of the claim vs the top chunk.
        thresholds: Score gates; defaults to :class:`SurfaceThresholds`.

    Returns:
        Whether to publish a proactive citation.
    """
    gate = thresholds or SurfaceThresholds()
    if not result.citations:
        return False
    top_score = result.citations[0].score
    if top_score < gate.retrieval_score:
        return False
    if not faithfulness.supported:
        return False
    return faithfulness.score >= gate.faithfulness_score
