"""Reciprocal Rank Fusion (RRF) for combining ranked retrieval lists.

RRF is a simple, robust, score-scale-free way to fuse several ranked lists into
one. Each candidate's fused score is the sum over the input rankings of
``1 / (k + rank)``, where ``rank`` is the candidate's 0-based position in that
ranking and ``k`` is a smoothing constant (the literature default is ``60``).

Because RRF consumes *ranks* rather than raw scores, it sidesteps the perennial
hybrid-retrieval headache of putting a cosine similarity and a BM25 score on a
common scale. It is the fusion method described in Cormack et al. 2009 and used
widely in production hybrid search.
"""

from __future__ import annotations

from collections.abc import Sequence

# Literature-standard RRF smoothing constant (Cormack et al., 2009).
DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]],
    *,
    weights: Sequence[float] | None = None,
    k: int = DEFAULT_RRF_K,
) -> dict[int, float]:
    """Fuse several ranked lists of item ids into a single fused-score map.

    Args:
        rankings: One ranked list per retriever. Each is a sequence of item ids
            (e.g. document indices) ordered best-first. An id may appear in some
            rankings and not others.
        weights: Optional per-ranking weights, parallel to ``rankings``. When
            omitted every ranking is weighted ``1.0``. This is how the caller
            threads the dense/lexical ``alpha`` knob through the fusion.
        k: RRF smoothing constant. Larger ``k`` flattens the contribution of
            top ranks; the default (``60``) is the literature standard.

    Returns:
        A mapping ``item_id -> fused_score`` (higher is better). Callers sort
        the keys by value to obtain the fused ranking.

    Raises:
        ValueError: If ``weights`` is given but its length differs from
            ``rankings``.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    elif len(weights) != len(rankings):
        raise ValueError("weights must be parallel to rankings")

    fused: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        if weight == 0.0:
            continue
        for rank, item_id in enumerate(ranking):
            fused[item_id] = fused.get(item_id, 0.0) + weight / (k + rank)
    return fused


def rank_order(scores: dict[int, float]) -> list[int]:
    """Return item ids ordered best-first, breaking ties by ascending id.

    Args:
        scores: A mapping of item id to score (higher is better).

    Returns:
        The item ids sorted by descending score, then ascending id for a fully
        deterministic order.
    """
    return sorted(scores, key=lambda item_id: (-scores[item_id], item_id))
