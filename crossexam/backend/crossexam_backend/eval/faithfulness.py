"""Faithfulness / groundedness scoring for generated answers.

The DEFAULT scorer is fully offline and dependency-free: it splits the answer
into content tokens (claim tokens) and measures the fraction that are entailed
by -- i.e. present in -- the concatenated text of the retrieved chunks. This is
a deterministic *claim-token entailment proxy* for groundedness: an answer that
only asserts things found in its supporting passages scores near ``1.0``; an
answer that introduces unsupported content (a hallucination) is penalised.

A real RAGAS path can be plugged in when ``ragas`` and an LLM key are available
(the ``[eval]`` extra). It is imported lazily and guarded, so importing this
module -- and running the default scorer -- never requires those dependencies or
any network access.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, Field

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Function words carry no factual claim, so they neither help nor hurt
# groundedness; we drop them before scoring (mirrors the index stopword list
# plus a few answer-phrasing connectives).
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "of", "on", "in", "at", "to", "and", "or", "for",
        "was", "is", "were", "are", "be", "been", "did", "do", "does", "that",
        "this", "it", "as", "by", "with", "from", "what", "when", "where",
        "who", "how", "why", "i", "you", "he", "she", "they", "we", "me",
        "my", "your", "his", "her", "their", "there", "had", "has", "have",
        "not", "no", "yes", "but", "so", "than", "then", "about", "into",
        "until", "up", "out", "if", "only", "any", "all", "would", "could",
        "said", "say", "says", "stated", "testified", "confirmed",
    }
)


class FaithfulnessReport(BaseModel):
    """Aggregate faithfulness over an answer set.

    Attributes:
        score: Mean per-answer groundedness in ``[0, 1]``.
        per_query: Mapping of query id -> per-answer groundedness.
        backend: Which scorer produced the result (``"offline"`` or ``"ragas"``).
    """

    score: float = Field(ge=0.0, le=1.0)
    per_query: dict[str, float] = Field(default_factory=dict)
    backend: str = "offline"


def _claim_tokens(text: str) -> list[str]:
    """Return content (non-stopword) tokens of ``text``, lowercased."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def groundedness(answer: str, context: Sequence[str]) -> float:
    """Return the offline claim-token groundedness of ``answer`` in ``context``.

    Args:
        answer: The generated answer text whose claims are checked.
        context: The retrieved passage texts the answer is meant to be grounded
            in.

    Returns:
        Fraction of distinct answer claim-tokens that also appear in the
        context, in ``[0, 1]``. An empty answer is vacuously grounded (``1.0``);
        a non-empty answer with empty context is ungrounded (``0.0``).
    """
    claim = set(_claim_tokens(answer))
    if not claim:
        return 1.0
    context_tokens: set[str] = set()
    for passage in context:
        context_tokens.update(_claim_tokens(passage))
    if not context_tokens:
        return 0.0
    supported = sum(1 for tok in claim if tok in context_tokens)
    return supported / float(len(claim))


def score_faithfulness_offline(
    samples: Sequence[tuple[str, str, Sequence[str]]],
) -> FaithfulnessReport:
    """Score a set of ``(query_id, answer, context)`` samples offline.

    Args:
        samples: Triples of the query id, the generated answer, and the list of
            retrieved passage texts that should ground it.

    Returns:
        A :class:`FaithfulnessReport` with the mean and per-query scores.
    """
    per_query = {
        qid: groundedness(answer, context) for qid, answer, context in samples
    }
    score = sum(per_query.values()) / len(per_query) if per_query else 1.0
    return FaithfulnessReport(score=score, per_query=per_query, backend="offline")


def _ragas_available() -> bool:
    """Return ``True`` when the optional RAGAS path can be used.

    Requires both the ``ragas`` package and an OpenAI key in the environment.
    Kept import-safe: a missing dependency never raises at import time.
    """
    import importlib.util
    import os

    if importlib.util.find_spec("ragas") is None:
        return False
    return bool(os.environ.get("OPENAI_API_KEY"))


def score_faithfulness(
    samples: Sequence[tuple[str, str, Sequence[str]]],
    *,
    use_ragas: bool = False,
) -> FaithfulnessReport:
    """Score faithfulness, preferring RAGAS when explicitly enabled and present.

    The default (``use_ragas=False``) always runs the offline scorer with no
    network access and no keys. When ``use_ragas=True`` *and* ragas + an LLM key
    are available, the real RAGAS faithfulness metric is used instead; otherwise
    it transparently falls back to the offline scorer.

    Args:
        samples: Triples of ``(query_id, answer, context_passages)``.
        use_ragas: Opt in to the RAGAS path when its dependencies are present.

    Returns:
        A :class:`FaithfulnessReport`.
    """
    if use_ragas and _ragas_available():
        report = _score_faithfulness_ragas(samples)
        if report is not None:
            return report
    return score_faithfulness_offline(samples)


def _score_faithfulness_ragas(
    samples: Sequence[tuple[str, str, Sequence[str]]],
) -> FaithfulnessReport | None:
    """Best-effort RAGAS faithfulness scorer (returns ``None`` on any failure).

    Imported lazily so the module stays import-safe without the ``[eval]`` extra.
    """
    try:  # pragma: no cover - exercised only when ragas + key are installed
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import faithfulness

        data = {
            "question": [qid for qid, _, _ in samples],
            "answer": [ans for _, ans, _ in samples],
            "contexts": [list(ctx) for _, _, ctx in samples],
        }
        dataset = Dataset.from_dict(data)
        result = evaluate(dataset, metrics=[faithfulness])
        scores = list(result["faithfulness"])
        per_query = {
            qid: float(s) for (qid, _, _), s in zip(samples, scores, strict=True)
        }
        mean = sum(per_query.values()) / len(per_query) if per_query else 1.0
        return FaithfulnessReport(score=mean, per_query=per_query, backend="ragas")
    except Exception:  # noqa: BLE001 - never let an optional path break the gate
        return None
