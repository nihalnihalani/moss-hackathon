"""Pure, dependency-free retrieval metrics.

Every function here is side-effect free and takes plain Python data
(``ranked``: the ordered list of retrieved chunk ids, best first; ``gold``: the
relevant ids, optionally with graded relevance). They are unit-tested against
hand-computed values in ``tests/test_eval_metrics.py``.

Implemented:

* :func:`precision_at_k`  -- fraction of the top ``k`` that are relevant.
* :func:`recall_at_k`     -- fraction of all relevant items found in the top ``k``.
* :func:`reciprocal_rank` -- ``1 / rank`` of the first relevant hit (0 if none).
* :func:`ndcg_at_k`       -- graded normalized discounted cumulative gain.

Aggregations (:func:`mean_reciprocal_rank`, :func:`mean`) operate over lists.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence


def precision_at_k(ranked: Sequence[str], gold: Iterable[str], k: int) -> float:
    """Return Precision@k: relevant share of the top ``k`` retrieved ids.

    Args:
        ranked: Retrieved chunk ids, best-first.
        gold: The set of relevant chunk ids.
        k: Cutoff rank (must be >= 1).

    Returns:
        ``|relevant in top-k| / k`` in ``[0, 1]``. Returns ``0.0`` when ``k <= 0``.
    """
    if k <= 0:
        return 0.0
    gold_set = set(gold)
    top = ranked[:k]
    if not top:
        return 0.0
    hits = sum(1 for cid in top if cid in gold_set)
    return hits / float(k)


def recall_at_k(ranked: Sequence[str], gold: Iterable[str], k: int) -> float:
    """Return Recall@k: share of all relevant ids appearing in the top ``k``.

    Args:
        ranked: Retrieved chunk ids, best-first.
        gold: The set of relevant chunk ids.
        k: Cutoff rank (must be >= 1).

    Returns:
        ``|relevant in top-k| / |relevant|`` in ``[0, 1]``. Returns ``1.0`` when
        there are no relevant ids (nothing to recall), matching the convention
        used for out-of-domain negatives.
    """
    gold_set = set(gold)
    if not gold_set:
        return 1.0
    if k <= 0:
        return 0.0
    top = set(ranked[:k])
    hits = len(top & gold_set)
    return hits / float(len(gold_set))


def reciprocal_rank(ranked: Sequence[str], gold: Iterable[str]) -> float:
    """Return the reciprocal rank of the first relevant id (0 if none found).

    Args:
        ranked: Retrieved chunk ids, best-first.
        gold: The set of relevant chunk ids.

    Returns:
        ``1 / rank`` of the first relevant hit (rank is 1-based), else ``0.0``.
    """
    gold_set = set(gold)
    for i, cid in enumerate(ranked, start=1):
        if cid in gold_set:
            return 1.0 / i
    return 0.0


def dcg_at_k(ranked: Sequence[str], gains: Mapping[str, float], k: int) -> float:
    """Return the Discounted Cumulative Gain over the top ``k`` ids.

    Uses the standard ``gain / log2(rank + 1)`` discount (rank is 1-based).

    Args:
        ranked: Retrieved chunk ids, best-first.
        gains: Mapping of chunk id -> graded relevance gain (missing = ``0``).
        k: Cutoff rank.

    Returns:
        The summed discounted gain over the top ``k``.
    """
    if k <= 0:
        return 0.0
    total = 0.0
    for i, cid in enumerate(ranked[:k], start=1):
        gain = float(gains.get(cid, 0.0))
        if gain != 0.0:
            total += gain / math.log2(i + 1)
    return total


def ndcg_at_k(ranked: Sequence[str], gold: Mapping[str, int], k: int) -> float:
    """Return graded nDCG@k normalized by the ideal ranking.

    Args:
        ranked: Retrieved chunk ids, best-first.
        gold: Mapping of chunk id -> graded relevance (higher is more relevant).
        k: Cutoff rank.

    Returns:
        ``DCG@k / IDCG@k`` in ``[0, 1]``. Returns ``1.0`` when there is no graded
        relevance to rank (out-of-domain negatives), and ``0.0`` when ``k <= 0``.
    """
    if k <= 0:
        return 0.0
    gains = {cid: float(rel) for cid, rel in gold.items() if rel > 0}
    if not gains:
        return 1.0
    ideal_order = sorted(gains.values(), reverse=True)[:k]
    idcg = sum(
        gain / math.log2(i + 1) for i, gain in enumerate(ideal_order, start=1)
    )
    if idcg == 0.0:
        return 0.0
    dcg = dcg_at_k(ranked, gains, k)
    return dcg / idcg


def mean_reciprocal_rank(
    ranked_lists: Iterable[Sequence[str]],
    gold_sets: Iterable[Iterable[str]],
) -> float:
    """Return the Mean Reciprocal Rank across paired ranked lists / gold sets.

    Args:
        ranked_lists: One ranked id list per query.
        gold_sets: One relevant-id set per query, aligned with ``ranked_lists``.

    Returns:
        The mean of the per-query reciprocal ranks (``0.0`` when empty).
    """
    rrs = [
        reciprocal_rank(ranked, gold)
        for ranked, gold in zip(ranked_lists, gold_sets, strict=True)
    ]
    return mean(rrs)


def mean(values: Iterable[float]) -> float:
    """Return the arithmetic mean of ``values`` (``0.0`` for an empty iterable)."""
    vals = list(values)
    if not vals:
        return 0.0
    return sum(vals) / float(len(vals))
