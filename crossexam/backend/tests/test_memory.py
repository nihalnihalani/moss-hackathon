"""Tests for :mod:`crossexam_backend.memory` (conversation memory / dedupe)."""

from __future__ import annotations

from crossexam_backend.memory import ConversationMemory, InMemoryStore
from crossexam_backend.models import BBox, Chunk, Citation


def _citation(cid: str = "depo-p12", page: int = 12, doc: str = "deposition") -> Citation:
    bbox = BBox(page=page, x0=72.0, y0=120.0, x1=540.0, y1=156.0)
    chunk = Chunk(id=cid, text="warehouse past midnight", page=page, bbox=bbox, documentId=doc)
    return Citation(chunk=chunk, score=0.9, documentId=doc)


def test_recall_is_none_for_fresh_citation() -> None:
    """A citation never surfaced has no recall."""
    mem = ConversationMemory(session_id="s1")
    assert mem.recall(_citation()) is None


def test_note_surfaced_then_recall_returns_memory_ref() -> None:
    """After surfacing, recall returns a MemoryRef with the page note."""
    mem = ConversationMemory(session_id="s1")
    cit = _citation(page=12)
    mem.note_surfaced(cit)
    ref = mem.recall(cit)
    assert ref is not None
    assert ref.kind == "recall"
    assert ref.citationId == "depo-p12"
    assert ref.page == 12
    assert ref.documentId == "deposition"
    assert "page 12" in ref.note


def test_dedupe_passes_fresh_and_recalls_repeat() -> None:
    """A repeat citation becomes a MemoryRef; a fresh one passes through."""
    mem = ConversationMemory(session_id="s1")
    first = _citation("depo-p12", page=12)
    mem.note_surfaced(first)  # already seen this session

    second = _citation("exhibit-p1", page=1, doc="exhibit")  # new
    fresh, recalls = mem.dedupe([first, second])

    assert [c.chunk.id for c in fresh] == ["exhibit-p1"]
    assert [r.citationId for r in recalls] == ["depo-p12"]


def test_dedupe_collapses_duplicate_within_one_call() -> None:
    """Within one dedupe call the first occurrence is fresh, repeats recall."""
    mem = ConversationMemory(session_id="s1")
    cit = _citation("depo-p12", page=12)
    fresh, recalls = mem.dedupe([cit, cit])
    assert [c.chunk.id for c in fresh] == ["depo-p12"]
    assert [r.citationId for r in recalls] == ["depo-p12"]


def test_memory_is_session_scoped() -> None:
    """Surfacings in one session do not leak into another (shared store)."""
    store = InMemoryStore()
    s1 = ConversationMemory(session_id="s1", store=store)
    s2 = ConversationMemory(session_id="s2", store=store)
    cit = _citation()
    s1.note_surfaced(cit)
    assert s1.recall(cit) is not None
    assert s2.recall(cit) is None


def test_reset_forgets_session() -> None:
    """reset() clears the session's surfaced records."""
    mem = ConversationMemory(session_id="s1")
    cit = _citation()
    mem.note_surfaced(cit)
    mem.reset()
    assert mem.recall(cit) is None


def test_note_surfaced_is_idempotent() -> None:
    """Re-noting keeps the first recorded recall (stable page reference)."""
    mem = ConversationMemory(session_id="s1")
    cit = _citation(page=12)
    first = mem.note_surfaced(cit)
    second = mem.note_surfaced(cit)
    assert first.page == second.page == 12


def test_exempt_id_always_surfaced_fresh_never_recalled() -> None:
    """An exempt id is always fresh (contradiction anchor never lost to dedupe)."""
    mem = ConversationMemory(session_id="s1")
    anchor = _citation("depo-p12", page=12)
    other = _citation("exhibit-p1", page=1, doc="exhibit")
    mem.note_surfaced(anchor)  # already seen this session
    mem.note_surfaced(other)  # already seen too

    fresh, recalls = mem.dedupe(
        [anchor, other], exempt_ids=frozenset({"depo-p12"})
    )
    # The exempt anchor stays fresh; the non-exempt repeat becomes a recall.
    assert [c.chunk.id for c in fresh] == ["depo-p12"]
    assert [r.citationId for r in recalls] == ["exhibit-p1"]


def test_dedupe_without_exempt_recalls_all_repeats() -> None:
    """Without exemption a repeated anchor is recalled (baseline behaviour)."""
    mem = ConversationMemory(session_id="s1")
    anchor = _citation("depo-p12", page=12)
    mem.note_surfaced(anchor)
    fresh, recalls = mem.dedupe([anchor])
    assert fresh == []
    assert [r.citationId for r in recalls] == ["depo-p12"]
