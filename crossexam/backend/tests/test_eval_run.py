"""Tests that the eval runner and benchmark pass on the current mock index."""

from __future__ import annotations

import pytest

from crossexam_backend.config import Settings
from crossexam_backend.eval import bench, run_eval
from crossexam_backend.eval.dataset import load_eval_queries
from crossexam_backend.eval.scenarios import livekit_judge_available, run_judge_scenarios
from crossexam_backend.retrieval.mock_index import MockIndex


@pytest.fixture()
def settings() -> Settings:
    """Settings forced into mock mode (no keys)."""
    return Settings(use_mocks=True)


@pytest.fixture()
def index(settings: Settings) -> MockIndex:
    """The mock index loaded from the shipped corpus fixture."""
    from crossexam_backend.retrieval.factory import get_index

    idx = get_index(settings)
    assert isinstance(idx, MockIndex)
    return idx


def test_dataset_loads_and_has_negatives() -> None:
    """The dataset has a healthy size and includes negatives + positives."""
    queries = load_eval_queries()
    assert 12 <= len(queries) <= 20
    assert any(q.is_negative for q in queries)
    assert sum(1 for q in queries if not q.is_negative) >= 10
    # Gold ids are the well-known landmark chunks.
    gold_union: set[str] = set()
    for q in queries:
        gold_union |= q.gold_ids
    assert "pdf-p12-l1" in gold_union  # warehouse admission
    assert "pdf-p41-l1" in gold_union  # contradiction


async def test_evaluate_dataset_passes_gates(
    settings: Settings, index: MockIndex
) -> None:
    """The current mock index clears the eval thresholds and prints a scorecard."""
    queries = load_eval_queries()
    card = await run_eval.evaluate_dataset(queries, index, settings)

    # Real, non-trivial quality numbers.
    assert card.mrr >= run_eval.DEFAULT_MRR_MIN
    assert card.mean_p_at_1 >= run_eval.DEFAULT_P_AT_1_MIN
    assert card.faithfulness.score >= run_eval.DEFAULT_FAITHFULNESS_MIN
    assert card.negative_abstention == 1.0

    text = run_eval.render_scorecard(card)
    assert "CROSSEXAM RETRIEVAL SCORECARD" in text
    assert "AGGREGATE" in text

    failures = run_eval.check_gates(
        card,
        mrr_min=run_eval.DEFAULT_MRR_MIN,
        p_at_1_min=run_eval.DEFAULT_P_AT_1_MIN,
        faithfulness_min=run_eval.DEFAULT_FAITHFULNESS_MIN,
    )
    assert failures == []


def test_run_eval_main_returns_zero() -> None:
    """The CLI runner exits 0 (gate passes) on the current index."""
    assert run_eval.run([]) == 0


async def test_benchmark_meets_p99_budget(index: MockIndex) -> None:
    """The mock index retrieval p99 stays comfortably under 10ms."""
    queries = [q.query for q in load_eval_queries()]
    result = await bench.benchmark_index(index, queries, iters=500, warmup=50)
    assert result.iters == 500
    assert result.p50_ms <= result.p95_ms <= result.p99_ms
    assert result.p99_ms < bench.DEFAULT_P99_BUDGET_MS


def test_bench_main_returns_zero() -> None:
    """The benchmark CLI exits 0 (sub-10ms gate passes)."""
    assert bench.run(["--iters", "300", "--warmup", "30"]) == 0


def test_percentile_interpolation() -> None:
    """The percentile helper interpolates and handles edge sizes."""
    assert bench._percentile([], 50.0) == 0.0
    assert bench._percentile([5.0], 99.0) == 5.0
    # p50 of 1..5 is the median 3.0
    assert bench._percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50.0) == 3.0


async def test_livekit_scenarios_skip_cleanly_without_deps() -> None:
    """Without livekit-agents + an LLM key the judge scenarios skip to []."""
    if livekit_judge_available():
        pytest.skip("livekit judge available; offline-skip path not exercised")
    assert await run_judge_scenarios() == []
