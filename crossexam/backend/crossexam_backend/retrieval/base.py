"""Abstract retrieval interface.

Both the real Moss-backed index and the in-memory mock implement this contract,
so the agent and server code never need to know which one is wired up.
"""

from __future__ import annotations

import abc

from crossexam_backend.models import RetrievalResult


class RetrievalIndex(abc.ABC):
    """A semantic+keyword document index that answers user-turn queries.

    Implementations must be safe to call from an async event loop and should
    populate :attr:`RetrievalResult.latency_ms` with the measured wall-clock
    retrieval time.
    """

    @abc.abstractmethod
    async def query(
        self,
        text: str,
        top_k: int = 5,
        alpha: float = 0.8,
    ) -> RetrievalResult:
        """Return the top-``k`` citations for ``text``.

        Args:
            text: The user-turn text to search for.
            top_k: Maximum number of citations to return.
            alpha: Hybrid weight; ``1.0`` is pure semantic, ``0.0`` pure
                keyword. Implementations blend their two scores accordingly.

        Returns:
            A :class:`RetrievalResult` whose citations are sorted best-first.
        """
        raise NotImplementedError

    async def prewarm(self) -> None:
        """Eagerly load any state needed before the first query.

        The default implementation is a no-op; subclasses may override to load
        indexes or open connections during worker prewarm.
        """
        return None

    async def aclose(self) -> None:
        """Release any resources held by the index. Default no-op."""
        return None
