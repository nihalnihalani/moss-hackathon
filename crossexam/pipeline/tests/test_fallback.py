"""Tests for the deterministic, network-free fallback parser."""

from __future__ import annotations

from crossexam_pipeline.fallback import DEFAULT_SAMPLE_PATH, DeterministicParser
from crossexam_pipeline.models import ParsedChunk


def _parse() -> list[ParsedChunk]:
    return DeterministicParser().parse()


def test_sample_fixture_exists() -> None:
    assert DEFAULT_SAMPLE_PATH.exists(), DEFAULT_SAMPLE_PATH


def test_parse_produces_valid_chunks_with_bboxes() -> None:
    chunks = _parse()
    assert chunks, "expected at least one chunk"
    for c in chunks:
        assert isinstance(c, ParsedChunk)
        assert c.text.strip()
        assert c.page >= 1
        assert c.bbox.page == c.page
        # Normalized, non-degenerate box.
        assert 0.0 <= c.bbox.x0 <= c.bbox.x1 <= 1.0
        assert 0.0 <= c.bbox.y0 <= c.bbox.y1 <= 1.0
        assert 0.0 <= c.confidence <= 1.0
        assert c.source == "fallback"


def test_word_level_citations_are_present_and_boxed() -> None:
    chunks = _parse()
    for c in chunks:
        assert c.words, f"chunk {c.id} has no word citations"
        assert len(c.words) == len(c.text.split())
        for w in c.words:
            assert w.bbox.page == c.page
            assert 0.0 <= w.bbox.x0 <= w.bbox.x1 <= 1.0


def test_key_admission_and_contradiction_present() -> None:
    chunks = _parse()
    texts = [c.text.lower() for c in chunks]
    pages = {c.page for c in chunks}
    # Page 147: the warehouse admission.
    assert 147 in pages
    assert any("warehouse on the night of the 14th" in t for t in texts)
    # Page 488: the contradiction ("never went to the warehouse").
    assert 488 in pages
    assert any("never went to the warehouse" in t for t in texts)


def test_parse_is_deterministic() -> None:
    first = _parse()
    second = _parse()
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]


def test_chunk_ids_are_unique_and_stable() -> None:
    chunks = _parse()
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids)), "chunk ids must be unique"
    # IDs are derived from page/line, not call order.
    again = {c.id for c in _parse()}
    assert set(ids) == again
