"""Offline retrieval + faithfulness evaluation runner and CI gate.

Loads the labeled dataset, runs every query through the configured retrieval
index (the in-memory :class:`MockIndex` by default -- no keys required), computes
the retrieval metrics and the groundedness score, prints a monospace SCORECARD,
and EXITS NONZERO when any headline metric falls below its threshold.

Run it as a module::

    python -m crossexam_backend.eval.run_eval

Thresholds (overridable via CLI flags) act as a hard quality gate so a
regression in ranking or grounding fails CI rather than silently shipping.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from crossexam_backend.config import Settings, get_settings
from crossexam_backend.eval import metrics
from crossexam_backend.eval.dataset import EvalQuery, load_eval_queries
from crossexam_backend.eval.faithfulness import FaithfulnessReport, score_faithfulness
from crossexam_backend.retrieval.base import RetrievalIndex
from crossexam_backend.retrieval.factory import get_index

# Headline cutoff for the ranking metrics (P@1, P@k, R@k, MRR, nDCG).
_K = 5

# Default gate thresholds. A run below ANY of these exits nonzero.
DEFAULT_MRR_MIN = 0.7
DEFAULT_P_AT_1_MIN = 0.6
DEFAULT_FAITHFULNESS_MIN = 0.8

# A retrieved citation below this score is treated as "no confident hit" -- this
# is how a NEGATIVE (out-of-domain) query is scored as a correct abstention.
_NEGATIVE_CONFIDENCE_FLOOR = 0.2


@dataclass(frozen=True)
class QueryEval:
    """Per-query evaluation outcome.

    Attributes:
        query: The labeled query.
        ranked: Retrieved chunk ids, best-first.
        p_at_1: Precision@1 (positives) or abstention score (negatives).
        p_at_k: Precision@k.
        recall_at_k: Recall@k.
        rr: Reciprocal rank.
        ndcg: nDCG@k.
        faithfulness: Per-query groundedness.
    """

    query: EvalQuery
    ranked: list[str]
    p_at_1: float
    p_at_k: float
    recall_at_k: float
    rr: float
    ndcg: float
    faithfulness: float


@dataclass(frozen=True)
class Scorecard:
    """Aggregate evaluation result.

    Attributes:
        rows: Per-query outcomes.
        mrr: Mean reciprocal rank over positives.
        mean_p_at_1: Mean Precision@1 over positives.
        mean_p_at_k: Mean Precision@k over positives.
        mean_recall_at_k: Mean Recall@k over positives.
        mean_ndcg: Mean nDCG@k over positives.
        negative_abstention: Fraction of negatives correctly abstained on.
        faithfulness: Aggregate faithfulness report.
    """

    rows: list[QueryEval]
    mrr: float
    mean_p_at_1: float
    mean_p_at_k: float
    mean_recall_at_k: float
    mean_ndcg: float
    negative_abstention: float
    faithfulness: FaithfulnessReport


async def _retrieve(
    index: RetrievalIndex, query: EvalQuery, settings: Settings
) -> tuple[list[str], list[str], list[float]]:
    """Return ``(ranked_ids, context_texts, scores)`` for ``query``."""
    result = await index.query(query.query, top_k=_K, alpha=settings.alpha)
    ranked = [c.chunk.id for c in result.citations]
    context = [c.chunk.text for c in result.citations]
    scores = [c.score for c in result.citations]
    return ranked, context, scores


def _negative_abstention_score(scores: list[float]) -> float:
    """Score how well a negative query abstained.

    A correct abstention returns no confident hit; ``1.0`` when nothing (or only
    sub-floor noise) came back, else ``0.0``.
    """
    confident = [s for s in scores if s >= _NEGATIVE_CONFIDENCE_FLOOR]
    return 1.0 if not confident else 0.0


async def evaluate_dataset(
    queries: list[EvalQuery],
    index: RetrievalIndex,
    settings: Settings,
    *,
    use_ragas: bool = False,
) -> Scorecard:
    """Run the full dataset through ``index`` and compute the scorecard."""
    rows: list[QueryEval] = []
    faith_samples: list[tuple[str, str, list[str]]] = []

    pos_p1: list[float] = []
    pos_pk: list[float] = []
    pos_rk: list[float] = []
    pos_rr: list[float] = []
    pos_ndcg: list[float] = []
    neg_scores: list[float] = []

    for q in queries:
        ranked, context, scores = await _retrieve(index, q, settings)

        if q.is_negative:
            abstain = _negative_abstention_score(scores)
            neg_scores.append(abstain)
            # A correct abstention is perfectly grounded (it asserts nothing the
            # document must support); a leaked confident hit is not.
            faith_answer = "" if abstain else " ".join(context[:1])
            faith_samples.append((q.id, faith_answer, context))
            rows.append(
                QueryEval(
                    query=q,
                    ranked=ranked,
                    p_at_1=abstain,
                    p_at_k=abstain,
                    recall_at_k=1.0,
                    rr=abstain,
                    ndcg=1.0,
                    faithfulness=abstain,
                )
            )
            continue

        p1 = metrics.precision_at_k(ranked, q.gold_ids, 1)
        pk = metrics.precision_at_k(ranked, q.gold_ids, _K)
        rk = metrics.recall_at_k(ranked, q.gold_ids, _K)
        rr = metrics.reciprocal_rank(ranked, q.gold_ids)
        ndcg = metrics.ndcg_at_k(ranked, q.gold, _K)

        pos_p1.append(p1)
        pos_pk.append(pk)
        pos_rk.append(rk)
        pos_rr.append(rr)
        pos_ndcg.append(ndcg)
        faith_samples.append((q.id, q.answer, context))

        rows.append(
            QueryEval(
                query=q,
                ranked=ranked,
                p_at_1=p1,
                p_at_k=pk,
                recall_at_k=rk,
                rr=rr,
                ndcg=ndcg,
                faithfulness=0.0,  # filled in after scoring below
            )
        )

    faith = score_faithfulness(faith_samples, use_ragas=use_ragas)
    rows = [
        QueryEval(
            query=r.query,
            ranked=r.ranked,
            p_at_1=r.p_at_1,
            p_at_k=r.p_at_k,
            recall_at_k=r.recall_at_k,
            rr=r.rr,
            ndcg=r.ndcg,
            faithfulness=faith.per_query.get(r.query.id, r.faithfulness),
        )
        for r in rows
    ]

    return Scorecard(
        rows=rows,
        mrr=metrics.mean(pos_rr),
        mean_p_at_1=metrics.mean(pos_p1),
        mean_p_at_k=metrics.mean(pos_pk),
        mean_recall_at_k=metrics.mean(pos_rk),
        mean_ndcg=metrics.mean(pos_ndcg),
        negative_abstention=metrics.mean(neg_scores) if neg_scores else 1.0,
        faithfulness=faith,
    )


def render_scorecard(card: Scorecard) -> str:
    """Render ``card`` as a monospace SCORECARD table string."""
    lines: list[str] = []
    bar = "=" * 78
    lines.append(bar)
    lines.append("CROSSEXAM RETRIEVAL SCORECARD  (offline, mock index, no keys)")
    lines.append(bar)
    header = f"{'query id':28} {'P@1':>5} {'P@5':>5} {'R@5':>5} {'RR':>5} {'nDCG':>6} {'faith':>6}"
    lines.append(header)
    lines.append("-" * 78)
    for r in card.rows:
        tag = "  (neg)" if r.query.is_negative else ""
        lines.append(
            f"{r.query.id[:28]:28} "
            f"{r.p_at_1:5.2f} {r.p_at_k:5.2f} {r.recall_at_k:5.2f} "
            f"{r.rr:5.2f} {r.ndcg:6.3f} {r.faithfulness:6.3f}{tag}"
        )
    lines.append("-" * 78)
    lines.append(
        f"{'AGGREGATE (positives)':28} "
        f"{card.mean_p_at_1:5.2f} {card.mean_p_at_k:5.2f} "
        f"{card.mean_recall_at_k:5.2f} {card.mrr:5.2f} {card.mean_ndcg:6.3f} "
        f"{card.faithfulness.score:6.3f}"
    )
    lines.append(bar)
    lines.append(
        f"MRR={card.mrr:.3f}  P@1={card.mean_p_at_1:.3f}  "
        f"nDCG@{_K}={card.mean_ndcg:.3f}  "
        f"faithfulness={card.faithfulness.score:.3f} "
        f"[{card.faithfulness.backend}]  "
        f"neg-abstention={card.negative_abstention:.3f}"
    )
    lines.append(bar)
    return "\n".join(lines)


def check_gates(
    card: Scorecard,
    *,
    mrr_min: float,
    p_at_1_min: float,
    faithfulness_min: float,
) -> list[str]:
    """Return a list of human-readable gate failures (empty when all pass)."""
    failures: list[str] = []
    if card.mrr < mrr_min:
        failures.append(f"MRR {card.mrr:.3f} < {mrr_min:.2f}")
    if card.mean_p_at_1 < p_at_1_min:
        failures.append(f"P@1 {card.mean_p_at_1:.3f} < {p_at_1_min:.2f}")
    if card.faithfulness.score < faithfulness_min:
        failures.append(
            f"faithfulness {card.faithfulness.score:.3f} < {faithfulness_min:.2f}"
        )
    if card.negative_abstention < 1.0:
        failures.append(
            f"negative-abstention {card.negative_abstention:.3f} < 1.00"
        )
    return failures


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CrossExam retrieval eval gate.")
    parser.add_argument(
        "--dataset", type=Path, default=None, help="Path to eval_queries.json."
    )
    parser.add_argument("--mrr-min", type=float, default=DEFAULT_MRR_MIN)
    parser.add_argument("--p-at-1-min", type=float, default=DEFAULT_P_AT_1_MIN)
    parser.add_argument(
        "--faithfulness-min", type=float, default=DEFAULT_FAITHFULNESS_MIN
    )
    parser.add_argument(
        "--use-ragas",
        action="store_true",
        help="Use RAGAS faithfulness when ragas + an LLM key are available.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use configured live retrieval instead of the offline mock fixture.",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    """Run the eval, print the scorecard, and return a process exit code."""
    args = _parse_args(argv)
    settings = get_settings() if args.live else Settings(use_mocks=True)
    queries = load_eval_queries(args.dataset)
    index = get_index(settings)

    card = asyncio.run(
        evaluate_dataset(queries, index, settings, use_ragas=args.use_ragas)
    )
    print(render_scorecard(card))

    failures = check_gates(
        card,
        mrr_min=args.mrr_min,
        p_at_1_min=args.p_at_1_min,
        faithfulness_min=args.faithfulness_min,
    )
    if failures:
        print("\nGATE FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nGATE PASSED: all retrieval + faithfulness thresholds met.")
    return 0


def main() -> None:
    """Console entry point: run the eval and exit with its gate code."""
    raise SystemExit(run())


if __name__ == "__main__":
    main()
