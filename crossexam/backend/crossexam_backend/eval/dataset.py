"""Labeled evaluation dataset for the CrossExam retrieval harness.

The dataset is a list of :class:`EvalQuery` objects loaded from a JSON fixture
(``fixtures/eval_queries.json``). Each query carries:

* a natural-language ``query`` (what a litigator would ask the agent),
* a ``gold`` mapping of chunk id -> graded relevance (``3`` = the answer-bearing
  passage, lower values = supporting/related passages); an empty mapping marks
  an out-of-domain NEGATIVE that should retrieve nothing, and
* a reference ``answer`` used by the faithfulness scorer as the grounded claim.

Gold ids are derived from the shipped ``fixtures/sample_chunks.json`` -- e.g. the
warehouse-admission query maps to ``pdf-p12-l1`` and the contradiction query to
``pdf-p41-l1`` -- so the labels track the real corpus the mock index serves.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

# The eval fixture and the corpus fixture both live in ``backend/fixtures``.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVAL_FIXTURE = _BACKEND_ROOT / "fixtures" / "eval_queries.json"


class EvalQuery(BaseModel):
    """A single labeled evaluation query.

    Attributes:
        id: Stable identifier for the query (used in the scorecard).
        query: The natural-language question put to the retrieval index.
        gold: Mapping of gold chunk id -> graded relevance (higher is more
            relevant). An empty mapping marks an out-of-domain negative.
        answer: Reference answer text, scored for groundedness against the
            retrieved passages.
        note: Free-text provenance / rationale for the label.
    """

    id: str
    query: str
    gold: dict[str, int] = Field(default_factory=dict)
    answer: str = ""
    note: str = ""

    @property
    def gold_ids(self) -> set[str]:
        """Return the set of relevant chunk ids (relevance > 0)."""
        return {cid for cid, rel in self.gold.items() if rel > 0}

    @property
    def is_negative(self) -> bool:
        """Return ``True`` when no chunk is relevant (out-of-domain query)."""
        return len(self.gold_ids) == 0


def load_eval_queries(path: str | Path | None = None) -> list[EvalQuery]:
    """Load the labeled eval queries from a JSON fixture.

    Args:
        path: Optional path to the fixture; defaults to the shipped
            ``fixtures/eval_queries.json``.

    Returns:
        The parsed list of :class:`EvalQuery`.

    Raises:
        FileNotFoundError: If the fixture does not exist.
        ValueError: If the fixture is not a JSON list of query objects.
    """
    fixture_path = Path(path) if path is not None else DEFAULT_EVAL_FIXTURE
    if not fixture_path.is_file():
        raise FileNotFoundError(f"Eval fixture not found: {fixture_path}")
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Eval fixture must be a JSON list of query objects")
    return [EvalQuery.model_validate(item) for item in raw]
