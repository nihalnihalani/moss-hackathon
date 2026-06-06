"""CrossExam backend package.

A LiveKit Agents voice loop that interrogates large documents. On each user
turn the agent queries Moss (sub-10ms in-process semantic retrieval) and injects
the top-k results as a ``role="system"`` message into the turn context, so the
LLM always has grounding context and there is "no dead air".
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
