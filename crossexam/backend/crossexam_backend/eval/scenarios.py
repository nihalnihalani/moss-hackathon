"""Optional LiveKit-judge scenario scaffold (import-safe, skipped if absent).

When ``livekit-agents`` (>=1.0, which ships the ``AgentSession`` test harness
and ``.judge()`` LLM assertions) is installed *and* an LLM key is configured,
this module can drive text-mode agent turns and assert the agent grounds its
answers in the right pages. Without those dependencies it degrades cleanly:
:func:`livekit_judge_available` returns ``False`` and the scenario runner is a
no-op, so importing this module never fails and never requires keys.

This is a scaffold: the heavy lifting (spinning up a real ``AgentSession``) is
guarded behind the availability check so the offline test-suite and CI gate stay
green with zero LiveKit/LLM dependencies.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class JudgeScenario:
    """A single text-mode judge scenario.

    Attributes:
        name: Human-readable scenario name.
        user_turn: The text the simulated user sends to the agent.
        judge_instructions: Natural-language assertion the LLM judge applies to
            the agent's reply (e.g. "cites page 12 and mentions the warehouse").
    """

    name: str
    user_turn: str
    judge_instructions: str


# The canonical demo turns a CTO judge would want to see proven end-to-end.
DEFAULT_SCENARIOS: tuple[JudgeScenario, ...] = (
    JudgeScenario(
        name="admission-cites-page-12",
        user_turn="Where were you on the night of the 14th?",
        judge_instructions=(
            "The reply must place the witness at the Harbor Street warehouse on "
            "the night of the 14th and cite page 12."
        ),
    ),
    JudgeScenario(
        name="contradiction-cites-page-41",
        user_turn="Did you ever change your testimony about when you left?",
        judge_instructions=(
            "The reply must surface the contradiction that the witness later said "
            "he left before 8:00 p.m. and cite page 41."
        ),
    ),
    JudgeScenario(
        name="out-of-domain-abstains",
        user_turn="What is the recipe for chocolate cake?",
        judge_instructions=(
            "The reply must decline to answer or state that the deposition "
            "contains no supporting passage, without inventing facts."
        ),
    ),
)


def livekit_judge_available() -> bool:
    """Return ``True`` when the LiveKit judge path can actually run.

    Requires the ``livekit.agents`` test harness and an LLM key. Import-safe:
    a missing dependency never raises.
    """
    if importlib.util.find_spec("livekit") is None:
        return False
    if importlib.util.find_spec("livekit.agents") is None:
        return False
    return bool(os.environ.get("OPENAI_API_KEY"))


async def run_judge_scenarios(
    scenarios: tuple[JudgeScenario, ...] = DEFAULT_SCENARIOS,
) -> list[tuple[str, bool, str]]:
    """Run the judge scenarios when LiveKit + an LLM key are present.

    Returns a list of ``(scenario_name, passed, detail)`` triples. When the
    LiveKit judge path is unavailable, returns an empty list (cleanly skipped)
    rather than raising.

    Args:
        scenarios: The scenarios to run; defaults to :data:`DEFAULT_SCENARIOS`.

    Returns:
        Per-scenario results, or ``[]`` if skipped.
    """
    if not livekit_judge_available():
        return []
    return await _run_with_livekit(scenarios)  # pragma: no cover


async def _run_with_livekit(
    scenarios: tuple[JudgeScenario, ...],
) -> list[tuple[str, bool, str]]:  # pragma: no cover - needs livekit + LLM key
    """Drive an ``AgentSession`` per scenario and apply ``.judge()`` assertions.

    Imported lazily so the module stays import-safe without livekit-agents>=1.0.
    """
    try:
        from livekit.agents import AgentSession

        from crossexam_backend.agent import CrossExamAgent
        from crossexam_backend.config import get_settings
        from crossexam_backend.retrieval.factory import get_index
    except Exception as exc:  # noqa: BLE001
        return [("livekit-import", False, f"unavailable: {exc}")]

    settings = get_settings()
    index = get_index(settings)

    results: list[tuple[str, bool, str]] = []
    for sc in scenarios:
        try:
            agent = CrossExamAgent(index, top_k=settings.top_k, alpha=settings.alpha)
            async with AgentSession() as session:
                await session.start(agent)
                result = await session.run(user_input=sc.user_turn)
                await result.expect.next_event().is_message(
                    role="assistant"
                ).judge(instructions=sc.judge_instructions)
            results.append((sc.name, True, "judge passed"))
        except Exception as exc:  # noqa: BLE001
            results.append((sc.name, False, str(exc)))
    return results
