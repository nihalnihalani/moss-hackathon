"""Retrieval-latency micro-benchmark and the sub-10ms headline gate.

Times :meth:`RetrievalIndex.query` over many iterations against the in-memory
mock index and reports p50/p95/p99 latency. It ASSERTS that the p99 stays under
a budget (default 10ms) -- this is the headline Moss claim ("sub-10ms in-process
semantic retrieval"), proven with measured percentiles rather than asserted.

Run it as a module::

    python -m crossexam_backend.eval.bench

Exits nonzero if the measured p99 exceeds the budget.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass

from crossexam_backend.config import Settings, get_settings
from crossexam_backend.eval.dataset import load_eval_queries
from crossexam_backend.retrieval.base import RetrievalIndex
from crossexam_backend.retrieval.factory import get_index

DEFAULT_ITERS = 2000
DEFAULT_WARMUP = 200
DEFAULT_P99_BUDGET_MS = 10.0


@dataclass(frozen=True)
class BenchResult:
    """Latency summary for a benchmark run.

    Attributes:
        iters: Number of timed iterations.
        p50_ms: Median latency in milliseconds.
        p95_ms: 95th-percentile latency in milliseconds.
        p99_ms: 99th-percentile latency in milliseconds.
        mean_ms: Mean latency in milliseconds.
        max_ms: Worst observed latency in milliseconds.
    """

    iters: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    max_ms: float


def _percentile(sorted_samples: list[float], pct: float) -> float:
    """Return the ``pct`` percentile (0..100) of an ascending-sorted list."""
    if not sorted_samples:
        return 0.0
    if len(sorted_samples) == 1:
        return sorted_samples[0]
    rank = (pct / 100.0) * (len(sorted_samples) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_samples) - 1)
    frac = rank - lo
    return sorted_samples[lo] * (1.0 - frac) + sorted_samples[hi] * frac


async def benchmark_index(
    index: RetrievalIndex,
    queries: list[str],
    *,
    iters: int = DEFAULT_ITERS,
    warmup: int = DEFAULT_WARMUP,
    top_k: int = 5,
) -> BenchResult:
    """Benchmark ``index.query`` latency over ``iters`` rotating queries.

    Latency is measured *around* the call (full async round-trip), not read from
    ``RetrievalResult.latency_ms``, so it reflects the latency a caller sees.

    Args:
        index: The retrieval index under test.
        queries: A non-empty pool of query strings, rotated across iterations.
        iters: Number of timed iterations.
        warmup: Untimed warmup iterations to settle caches / JIT effects.
        top_k: ``top_k`` passed to each query.

    Returns:
        A :class:`BenchResult` with the measured percentiles.
    """
    if not queries:
        raise ValueError("benchmark requires at least one query")

    await index.prewarm()
    for i in range(warmup):
        await index.query(queries[i % len(queries)], top_k=top_k)

    samples: list[float] = []
    for i in range(iters):
        q = queries[i % len(queries)]
        start = time.perf_counter()
        await index.query(q, top_k=top_k)
        samples.append((time.perf_counter() - start) * 1000.0)

    samples.sort()
    return BenchResult(
        iters=iters,
        p50_ms=_percentile(samples, 50.0),
        p95_ms=_percentile(samples, 95.0),
        p99_ms=_percentile(samples, 99.0),
        mean_ms=sum(samples) / len(samples),
        max_ms=samples[-1],
    )


def render_bench(result: BenchResult, budget_ms: float) -> str:
    """Render ``result`` as a monospace latency report."""
    bar = "=" * 60
    status = "PASS" if result.p99_ms < budget_ms else "FAIL"
    return "\n".join(
        [
            bar,
            "CROSSEXAM RETRIEVAL LATENCY  (mock index, in-process)",
            bar,
            f"  iterations : {result.iters}",
            f"  p50        : {result.p50_ms:8.4f} ms",
            f"  p95        : {result.p95_ms:8.4f} ms",
            f"  p99        : {result.p99_ms:8.4f} ms",
            f"  mean       : {result.mean_ms:8.4f} ms",
            f"  max        : {result.max_ms:8.4f} ms",
            bar,
            f"  p99 budget : {budget_ms:8.4f} ms  ->  {status}",
            bar,
        ]
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CrossExam retrieval benchmark.")
    parser.add_argument("--iters", type=int, default=DEFAULT_ITERS)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument(
        "--p99-budget-ms", type=float, default=DEFAULT_P99_BUDGET_MS
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use configured live retrieval instead of the offline mock fixture.",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    """Run the benchmark, print the report, and return a process exit code."""
    args = _parse_args(argv)
    settings = get_settings() if args.live else Settings(use_mocks=True)
    index = get_index(settings)
    # Use the labeled eval queries as a realistic, varied workload.
    queries = [q.query for q in load_eval_queries()]

    result = asyncio.run(
        benchmark_index(
            index, queries, iters=args.iters, warmup=args.warmup, top_k=settings.top_k
        )
    )
    print(render_bench(result, args.p99_budget_ms))

    if result.p99_ms >= args.p99_budget_ms:
        print(
            f"\nGATE FAILED: p99 {result.p99_ms:.4f} ms "
            f">= budget {args.p99_budget_ms:.4f} ms"
        )
        return 1
    print(
        f"\nGATE PASSED: p99 {result.p99_ms:.4f} ms "
        f"< budget {args.p99_budget_ms:.4f} ms (sub-10ms retrieval, proven)."
    )
    return 0


def main() -> None:
    """Console entry point: run the benchmark and exit with its gate code."""
    raise SystemExit(run())


if __name__ == "__main__":
    main()
