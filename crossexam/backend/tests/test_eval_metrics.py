"""Tests for :mod:`crossexam_backend.eval.metrics` against hand-computed values."""

from __future__ import annotations

import math

from crossexam_backend.eval import metrics


def test_precision_at_k_basic() -> None:
    """P@k counts relevant hits in the top k over k."""
    ranked = ["a", "b", "c", "d"]
    gold = {"a", "c"}
    assert metrics.precision_at_k(ranked, gold, 1) == 1.0
    assert metrics.precision_at_k(ranked, gold, 2) == 0.5
    assert metrics.precision_at_k(ranked, gold, 4) == 0.5


def test_precision_at_k_no_hits_and_zero_k() -> None:
    """No relevant docs or a non-positive k give 0."""
    assert metrics.precision_at_k(["x", "y"], {"z"}, 2) == 0.0
    assert metrics.precision_at_k(["x"], {"x"}, 0) == 0.0


def test_recall_at_k_basic() -> None:
    """R@k counts relevant hits in the top k over total relevant."""
    ranked = ["a", "b", "c", "d"]
    gold = {"a", "c", "e"}  # 'e' never retrieved
    assert metrics.recall_at_k(ranked, gold, 1) == 1 / 3
    assert metrics.recall_at_k(ranked, gold, 3) == 2 / 3
    assert metrics.recall_at_k(ranked, gold, 4) == 2 / 3


def test_recall_at_k_empty_gold_is_one() -> None:
    """With nothing to recall (a negative query), recall is vacuously 1.0."""
    assert metrics.recall_at_k(["a", "b"], set(), 5) == 1.0


def test_reciprocal_rank() -> None:
    """RR is 1/rank of the first relevant item, 0 if none."""
    assert metrics.reciprocal_rank(["a", "b", "c"], {"a"}) == 1.0
    assert metrics.reciprocal_rank(["a", "b", "c"], {"b"}) == 0.5
    assert metrics.reciprocal_rank(["a", "b", "c"], {"c"}) == 1 / 3
    assert metrics.reciprocal_rank(["a", "b", "c"], {"z"}) == 0.0


def test_mean_reciprocal_rank() -> None:
    """MRR averages the per-query reciprocal ranks."""
    ranked_lists = [["a", "b"], ["x", "y", "z"]]
    gold_sets = [{"a"}, {"z"}]  # RR = 1.0 and 1/3
    expected = (1.0 + 1 / 3) / 2
    assert math.isclose(
        metrics.mean_reciprocal_rank(ranked_lists, gold_sets), expected
    )


def test_ndcg_perfect_ranking_is_one() -> None:
    """An ideal graded ranking scores nDCG = 1.0."""
    ranked = ["a", "b", "c"]
    gold = {"a": 3, "b": 2, "c": 1}
    assert math.isclose(metrics.ndcg_at_k(ranked, gold, 3), 1.0)


def test_ndcg_known_value_for_swapped_top_two() -> None:
    """Check nDCG matches a hand-computed value when the top two are swapped.

    gold gains: a=3, b=2. Ranked b,a.
      DCG  = 2/log2(2) + 3/log2(3) = 2 + 3/1.585 = 2 + 1.8928 = 3.8928
      IDCG = 3/log2(2) + 2/log2(3) = 3 + 2/1.585 = 3 + 1.2619 = 4.2619
      nDCG = 3.8928 / 4.2619 = 0.91339
    """
    ranked = ["b", "a"]
    gold = {"a": 3, "b": 2}
    dcg = 2.0 / math.log2(2) + 3.0 / math.log2(3)
    idcg = 3.0 / math.log2(2) + 2.0 / math.log2(3)
    assert math.isclose(metrics.ndcg_at_k(ranked, gold, 2), dcg / idcg)
    assert math.isclose(metrics.ndcg_at_k(ranked, gold, 2), 0.91340, rel_tol=1e-4)


def test_ndcg_empty_gold_is_one() -> None:
    """A negative query (no graded relevance) scores nDCG = 1.0."""
    assert metrics.ndcg_at_k(["a", "b"], {}, 5) == 1.0


def test_ndcg_miss_is_zero() -> None:
    """Retrieving only irrelevant docs scores nDCG = 0.0."""
    assert metrics.ndcg_at_k(["x", "y"], {"a": 3}, 5) == 0.0


def test_mean_helper() -> None:
    """Mean averages a list and is 0.0 when empty."""
    assert metrics.mean([1.0, 2.0, 3.0]) == 2.0
    assert metrics.mean([]) == 0.0
