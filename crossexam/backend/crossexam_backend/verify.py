"""Inference-time faithfulness verification.

CrossExam grounds every spoken answer in a retrieved document chunk and then
highlights that chunk in the UI. Before it highlights, it should check that the
answer is actually *supported* by the cited chunk — otherwise the citation box
would point a user at a passage that does not back up what the agent just said.

This module provides a pure, offline, deterministic verifier
(:func:`verify_faithfulness`) plus a guarded LLM/NLI hook
(:class:`LLMVerifier`) that is only constructed when a key/dependency is
present. The default path makes no external calls and is fully testable.

The default proxy is a lexical-entailment heuristic: it measures how much of the
answer's *content* (its non-stopword, non-discourse tokens) is covered by the
cited chunk. A grounded answer reuses the chunk's nouns, numbers and names; an
unrelated or hallucinated answer does not. This is a coarse but reliable signal
for the "refuse to highlight when unsupported" gate, and it never produces a
false *supported* for an answer that shares no content with the chunk.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Function words carry no grounding signal: an answer and a chunk will both say
# "the", "was", "on" regardless of whether the answer is faithful. Excluding
# them stops trivial overlap from inflating the support score. Mirrors the
# retrieval stopword list so the two stages reason over the same content tokens.
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
    }
)

# Default support threshold: at least this fraction of the answer's content
# tokens must appear in the cited chunk for the answer to count as supported.
# Tuned so a paraphrase that reuses the chunk's key nouns/numbers passes while an
# answer about a different subject fails.
DEFAULT_THRESHOLD = 0.5

# Method tags recorded on the verdict so callers/telemetry can tell which path
# produced it.
METHOD_LEXICAL = "lexical_entailment"
METHOD_LLM = "llm_entailment"


class FaithfulnessVerdict(BaseModel):
    """The outcome of a faithfulness check of an answer against a cited chunk.

    Attributes:
        supported: Whether the cited chunk supports the answer (``score`` met
            the threshold). When ``False`` the agent should refuse to highlight
            and surface "not found in document".
        score: Support score in ``[0, 1]`` — the fraction of the answer's
            content covered by the chunk (lexical method) or the model's
            entailment probability (LLM method).
        method: Which verifier produced the verdict (see ``METHOD_*``).
    """

    supported: bool
    score: float = Field(ge=0.0, le=1.0)
    method: str


def _content_tokens(text: str) -> list[str]:
    """Lowercase, tokenise and drop stopwords, keeping content terms in order."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def verify_faithfulness(
    answer: str,
    cited_text: str,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> FaithfulnessVerdict:
    """Check whether ``cited_text`` supports ``answer`` (deterministic, offline).

    Computes the fraction of the answer's distinct content tokens that also
    appear in the cited chunk. An answer that paraphrases or quotes the chunk
    reuses its salient terms and scores high; an answer about something else
    scores near zero. No external calls — this is the always-available default
    used at inference to gate highlighting.

    Args:
        answer: The agent's drafted answer for the current turn.
        cited_text: The text of the chunk the agent intends to cite/highlight.
        threshold: Minimum support fraction for ``supported`` to be ``True``.

    Returns:
        A :class:`FaithfulnessVerdict`. An empty answer is treated as vacuously
        supported (nothing to contradict); an empty chunk supports nothing.
    """
    answer_tokens = set(_content_tokens(answer))
    if not answer_tokens:
        # No content claim to verify — nothing to be unfaithful about.
        return FaithfulnessVerdict(supported=True, score=1.0, method=METHOD_LEXICAL)

    cited_tokens = set(_content_tokens(cited_text))
    if not cited_tokens:
        return FaithfulnessVerdict(supported=False, score=0.0, method=METHOD_LEXICAL)

    covered = len(answer_tokens & cited_tokens)
    score = covered / len(answer_tokens)
    return FaithfulnessVerdict(
        supported=score >= threshold,
        score=score,
        method=METHOD_LEXICAL,
    )


class LLMVerifier:
    """Guarded LLM/NLI entailment verifier (optional, off the default path).

    Wraps a callable entailment scorer — typically an NLI model or an LLM
    judge — that returns a probability in ``[0, 1]`` that the cited chunk entails
    the answer. The dependency / API client is injected, so this class never
    imports a heavy model itself and is trivially testable with a stub scorer.
    When no scorer is available it falls back to :func:`verify_faithfulness`, so
    callers can always rely on a verdict.

    Args:
        entail_fn: A callable ``(answer, cited_text) -> float`` returning an
            entailment probability, or ``None`` to force the lexical fallback.
        threshold: Minimum entailment probability for ``supported``.
    """

    def __init__(
        self,
        entail_fn: object | None = None,
        *,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        """Store the (optional) entailment scorer and threshold."""
        self._entail_fn = entail_fn
        self._threshold = threshold

    @property
    def available(self) -> bool:
        """Whether a real entailment scorer is wired (else lexical fallback)."""
        return callable(self._entail_fn)

    def verify(self, answer: str, cited_text: str) -> FaithfulnessVerdict:
        """Return an entailment verdict, falling back to the lexical proxy."""
        if not callable(self._entail_fn):
            return verify_faithfulness(
                answer, cited_text, threshold=self._threshold
            )
        try:
            prob = float(self._entail_fn(answer, cited_text))
        except Exception:  # noqa: BLE001 - a flaky judge must not break the turn
            logger.exception("LLMVerifier entail_fn failed; using lexical fallback")
            return verify_faithfulness(
                answer, cited_text, threshold=self._threshold
            )
        prob = max(0.0, min(prob, 1.0))
        return FaithfulnessVerdict(
            supported=prob >= self._threshold,
            score=prob,
            method=METHOD_LLM,
        )


def first_supported(
    answer: str,
    cited_texts: Iterable[str],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> FaithfulnessVerdict | None:
    """Return the verdict for the first cited text that supports ``answer``.

    Convenience for the agent's "highlight only if supported" gate when several
    candidate citations are in hand: returns the first supporting verdict, or
    ``None`` when none of the candidates support the answer (the agent should
    then say "not found in document").
    """
    for cited_text in cited_texts:
        verdict = verify_faithfulness(answer, cited_text, threshold=threshold)
        if verdict.supported:
            return verdict
    return None
