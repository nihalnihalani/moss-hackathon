"""Tests for Reciprocal Rank Fusion."""

from __future__ import annotations

import pytest

from crossexam_backend.retrieval.fusion import (
    DEFAULT_RRF_K,
    rank_order,
    reciprocal_rank_fusion,
)


def test_agreement_at_top_wins() -> None:
    """An item ranked first by both retrievers fuses to the best score."""
    fused = reciprocal_rank_fusion([[1, 2, 3], [1, 3, 2]])
    order = rank_order(fused)
    assert order[0] == 1


def test_rrf_score_matches_formula() -> None:
    """The fused score equals the sum of 1/(k+rank) over the rankings."""
    fused = reciprocal_rank_fusion([[7, 8], [8, 7]], k=DEFAULT_RRF_K)
    # Item 7: rank 0 in list A, rank 1 in list B.
    expected_7 = 1.0 / (DEFAULT_RRF_K + 0) + 1.0 / (DEFAULT_RRF_K + 1)
    assert fused[7] == pytest.approx(expected_7)
    # Symmetric for item 8.
    assert fused[8] == pytest.approx(expected_7)


def test_item_in_one_list_only_still_scored() -> None:
    """An item appearing in only one ranking still receives a fused score."""
    fused = reciprocal_rank_fusion([[1, 2], [1]])
    assert 2 in fused
    assert fused[1] > fused[2]


def test_weights_shift_the_winner() -> None:
    """Weighting one retriever heavily promotes its top item."""
    rankings = [[1, 2], [2, 1]]
    # Favour the second retriever, whose top item is 2.
    fused = reciprocal_rank_fusion(rankings, weights=[0.1, 0.9])
    assert rank_order(fused)[0] == 2
    # Favour the first retriever, whose top item is 1.
    fused = reciprocal_rank_fusion(rankings, weights=[0.9, 0.1])
    assert rank_order(fused)[0] == 1


def test_zero_weight_ranking_is_ignored() -> None:
    """A ranking with weight 0 contributes nothing to the fusion."""
    fused = reciprocal_rank_fusion([[1, 2], [3, 4]], weights=[1.0, 0.0])
    assert set(fused) == {1, 2}


def test_mismatched_weights_raise() -> None:
    """Weights must be parallel to rankings."""
    with pytest.raises(ValueError, match="parallel"):
        reciprocal_rank_fusion([[1], [2]], weights=[1.0])


def test_rank_order_breaks_ties_by_ascending_id() -> None:
    """Equal-score items are ordered by ascending id for determinism."""
    order = rank_order({5: 1.0, 3: 1.0, 9: 1.0})
    assert order == [3, 5, 9]
