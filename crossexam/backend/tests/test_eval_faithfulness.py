"""Tests for :mod:`crossexam_backend.eval.faithfulness` (offline scorer)."""

from __future__ import annotations

from crossexam_backend.eval.faithfulness import (
    FaithfulnessReport,
    groundedness,
    score_faithfulness,
    score_faithfulness_offline,
)


def test_fully_grounded_answer_scores_one() -> None:
    """An answer whose claims all appear in context is fully grounded."""
    context = ["The witness was at the Harbor Street warehouse past midnight."]
    answer = "The witness was at the Harbor Street warehouse."
    assert groundedness(answer, context) == 1.0


def test_ungrounded_answer_is_penalised() -> None:
    """Introducing unsupported claim tokens lowers the score below 1.0."""
    context = ["The witness was at the warehouse."]
    answer = "The witness flew to Mars on a unicorn spaceship."
    score = groundedness(answer, context)
    assert score < 0.5


def test_empty_answer_is_vacuously_grounded() -> None:
    """An empty answer asserts nothing, so it is grounded."""
    assert groundedness("", ["anything"]) == 1.0


def test_nonempty_answer_with_empty_context_is_ungrounded() -> None:
    """A real claim with no supporting context scores 0.0."""
    assert groundedness("The warehouse was open.", []) == 0.0


def test_score_faithfulness_offline_aggregates() -> None:
    """The offline scorer averages per-query groundedness."""
    samples = [
        ("q1", "warehouse open", ["the warehouse was open all night"]),
        ("q2", "completely fabricated nonsense", ["unrelated text"]),
    ]
    report = score_faithfulness_offline(samples)
    assert isinstance(report, FaithfulnessReport)
    assert report.backend == "offline"
    assert set(report.per_query) == {"q1", "q2"}
    assert report.per_query["q1"] == 1.0
    assert report.per_query["q2"] < report.per_query["q1"]
    assert 0.0 <= report.score <= 1.0


def test_default_score_faithfulness_is_offline() -> None:
    """Without opting into RAGAS, scoring stays on the offline backend."""
    report = score_faithfulness([("q", "warehouse", ["the warehouse"])])
    assert report.backend == "offline"
