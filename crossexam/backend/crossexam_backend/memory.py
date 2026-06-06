"""Conversation memory — never re-surface the same citation twice (feat 5).

Across a multi-turn session the agent will often land on a passage it has
already shown. Re-snapping the same box is noisy; instead the backend remembers
which citations it has surfaced and, on a repeat, emits a
:class:`~crossexam_backend.models.MemoryRef` recall ("as we saw on page 12") so
the agent can *reference* the earlier highlight rather than draw it again.

This module is pure and fully testable: the default store is an in-memory dict
keyed by session id, and no external calls happen on any path. A pluggable
:class:`MemoryStore` protocol lets a caller swap in cross-session persistence
(e.g. Redis) without touching :class:`ConversationMemory`.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from crossexam_backend.models import Citation, MemoryRef

logger = logging.getLogger(__name__)

# The session id used when a caller does not scope memory to a session.
DEFAULT_SESSION_ID = "default"


@runtime_checkable
class MemoryStore(Protocol):
    """Pluggable persistence for surfaced-citation records.

    A store maps ``session_id -> {citation_id: MemoryRef}``. The default
    :class:`InMemoryStore` keeps this in a process dict; a cross-session
    implementation could persist it. All methods are synchronous and pure from
    the caller's perspective.
    """

    def get(self, session_id: str, citation_id: str) -> MemoryRef | None:
        """Return the recorded recall for ``citation_id`` in a session, or None."""
        ...

    def put(self, session_id: str, ref: MemoryRef) -> None:
        """Record ``ref`` for its citation id within ``session_id``."""
        ...

    def clear(self, session_id: str) -> None:
        """Forget everything recorded for ``session_id``."""
        ...


class InMemoryStore:
    """Default in-process :class:`MemoryStore` backed by a nested dict."""

    def __init__(self) -> None:
        """Initialise the empty per-session record map."""
        self._by_session: dict[str, dict[str, MemoryRef]] = {}

    def get(self, session_id: str, citation_id: str) -> MemoryRef | None:
        """Return the recorded recall for ``citation_id`` in a session, or None."""
        return self._by_session.get(session_id, {}).get(citation_id)

    def put(self, session_id: str, ref: MemoryRef) -> None:
        """Record ``ref`` for its citation id within ``session_id``."""
        self._by_session.setdefault(session_id, {})[ref.citationId] = ref

    def clear(self, session_id: str) -> None:
        """Forget everything recorded for ``session_id``."""
        self._by_session.pop(session_id, None)


def _recall_note(page: int) -> str:
    """Human phrasing for a recall, e.g. ``"as we saw on page 12"``."""
    return f"as we saw on page {page}"


class ConversationMemory:
    """Track surfaced citations per session and dedupe repeats into recalls.

    Usage::

        mem = ConversationMemory(session_id="room-abc")
        fresh, recalls = mem.dedupe(result.citations)
        # surface `fresh` as new boxes; emit `recalls` as MemoryRefs

    A citation is "surfaced" the first time it is passed through
    :meth:`note_surfaced` (or :meth:`dedupe`). On any later turn, :meth:`recall`
    returns a :class:`MemoryRef` for it instead of ``None``, so the agent can say
    "as we saw on page N" rather than re-snapping the box.

    Args:
        session_id: Scopes memory to one conversation. Defaults to a shared id.
        store: Pluggable :class:`MemoryStore`; defaults to an in-process dict.
    """

    def __init__(
        self,
        session_id: str = DEFAULT_SESSION_ID,
        *,
        store: MemoryStore | None = None,
    ) -> None:
        """Bind the memory to a session and (optional) persistence store."""
        self._session_id = session_id
        self._store: MemoryStore = store or InMemoryStore()

    @property
    def session_id(self) -> str:
        """The session this memory is scoped to."""
        return self._session_id

    def recall(self, citation: Citation) -> MemoryRef | None:
        """Return a recall if ``citation`` was already surfaced this session.

        Args:
            citation: The citation about to be surfaced.

        Returns:
            A :class:`MemoryRef` ("as we saw on page N") if this citation id was
            already surfaced in the session, else ``None`` (it is fresh).
        """
        return self._store.get(self._session_id, citation.chunk.id)

    def note_surfaced(self, citation: Citation) -> MemoryRef:
        """Record that ``citation`` has now been surfaced this session.

        Idempotent: re-noting the same citation keeps the first recorded recall
        (so the page reference stays stable). Returns the stored recall.
        """
        existing = self._store.get(self._session_id, citation.chunk.id)
        if existing is not None:
            return existing
        ref = MemoryRef(
            citationId=citation.chunk.id,
            documentId=citation.documentId,
            page=citation.chunk.page,
            note=_recall_note(citation.chunk.page),
        )
        self._store.put(self._session_id, ref)
        logger.debug(
            "memory.note_surfaced session=%s citation=%s page=%d",
            self._session_id,
            citation.chunk.id,
            citation.chunk.page,
        )
        return ref

    def dedupe(
        self, citations: list[Citation]
    ) -> tuple[list[Citation], list[MemoryRef]]:
        """Split ``citations`` into fresh ones and recalls of prior surfacings.

        For each citation, in order: if it was already surfaced this session, it
        is emitted as a :class:`MemoryRef` recall (and *not* re-surfaced);
        otherwise it is fresh — it passes through and is recorded as surfaced so
        a later turn recalls it instead of re-snapping.

        Args:
            citations: The citations the agent is about to surface this turn.

        Returns:
            ``(fresh, recalls)`` — the citations to draw as new boxes, and the
            recalls to reference instead. Order within each list follows input
            order. Duplicate ids within the same call collapse: the first is
            fresh, later repeats become recalls.
        """
        fresh: list[Citation] = []
        recalls: list[MemoryRef] = []
        for citation in citations:
            existing = self.recall(citation)
            if existing is not None:
                recalls.append(existing)
            else:
                fresh.append(citation)
                self.note_surfaced(citation)
        return fresh, recalls

    def reset(self) -> None:
        """Forget everything surfaced this session (e.g. on a new conversation)."""
        self._store.clear(self._session_id)
