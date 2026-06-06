"""CrossExam offline evaluation and benchmark harness.

This package proves retrieval quality and latency with real numbers, fully
offline (no API keys). It contains:

* :mod:`crossexam_backend.eval.dataset` -- labeled queries over the sample
  deposition with graded gold relevance.
* :mod:`crossexam_backend.eval.metrics` -- pure Precision@k, Recall@k, MRR and
  nDCG@k implementations.
* :mod:`crossexam_backend.eval.faithfulness` -- an offline groundedness scorer
  with an optional RAGAS path.
* :mod:`crossexam_backend.eval.run_eval` -- the scorecard runner / CI gate.
* :mod:`crossexam_backend.eval.bench` -- the retrieval-latency micro-benchmark.
* :mod:`crossexam_backend.eval.scenarios` -- an optional LiveKit-judge scaffold.
"""

from __future__ import annotations
