"""Tests for the real PDF generator + text-layer parser.

These guard the PDF-backed demo: the generated sample PDF must parse into chunks
that contain the warehouse admission and the contradiction on DIFFERENT pages,
with point-range bboxes (not normalized ``[0, 1]``) and correct page dims, and
the bottom-left -> top-left ``y`` conversion is unit-tested in isolation.

``reportlab``/``pdfplumber`` are optional dev deps; the PDF-dependent tests skip
cleanly if either is unavailable (the pure-math ``flip_y`` test always runs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crossexam_pipeline import make_sample_pdf
from crossexam_pipeline.models import ParsedChunk
from crossexam_pipeline.pdf_parser import PdfTextParser, flip_y

reportlab = pytest.importorskip("reportlab", reason="reportlab not installed")
pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber not installed")

# Verbatim substrings of the demo-critical sentences (must survive wrapping).
_ADMISSION = "i was at the harbor street warehouse from approximately"
_CONTRADICTION = "left the harbor street warehouse before 8:00 p.m"


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate the sample PDF once into a temp dir for the module."""
    out = tmp_path_factory.mktemp("pdf") / "sample-deposition.pdf"
    return make_sample_pdf.make_pdf(out)


@pytest.fixture(scope="module")
def chunks(sample_pdf: Path) -> list[ParsedChunk]:
    """Parse the generated sample PDF's text layer into chunks."""
    return PdfTextParser(sample_pdf).parse()


def test_flip_y_converts_bottom_left_to_top_left() -> None:
    """y_top = page_height - y_bottom (reflection about the page midline)."""
    assert flip_y(0.0, 792.0) == 792.0  # bottom edge -> bottom in top-left space
    assert flip_y(792.0, 792.0) == 0.0  # top edge -> 0
    assert flip_y(396.0, 792.0) == 396.0  # midline is its own image
    assert flip_y(660.0, 792.0) == pytest.approx(132.0)
    # Round-trip is an involution.
    assert flip_y(flip_y(123.0, 792.0), 792.0) == pytest.approx(123.0)


def test_generated_pdf_exists_and_is_multipage(sample_pdf: Path) -> None:
    """The generator writes a real multi-page PDF."""
    assert sample_pdf.exists()
    with pdfplumber.open(str(sample_pdf)) as pdf:
        assert len(pdf.pages) == make_sample_pdf.TOTAL_PAGES
        assert len(pdf.pages) > 1


def _find(chunks: list[ParsedChunk], needle: str) -> ParsedChunk:
    matches = [c for c in chunks if needle in c.text.lower()]
    assert matches, f"no chunk contained {needle!r}"
    assert len(matches) == 1, f"expected exactly one chunk with {needle!r}, got {len(matches)}"
    return matches[0]


def test_admission_and_contradiction_on_different_pages(chunks: list[ParsedChunk]) -> None:
    """The warehouse admission and its contradiction parse to different pages."""
    admission = _find(chunks, _ADMISSION)
    contradiction = _find(chunks, _CONTRADICTION)
    assert admission.page == 12
    assert contradiction.page == 41
    assert admission.page != contradiction.page


def test_key_chunks_have_point_range_bboxes(chunks: list[ParsedChunk]) -> None:
    """Key chunk boxes are PDF points (not normalized [0, 1]) with page dims."""
    for needle in (_ADMISSION, _CONTRADICTION):
        c = _find(chunks, needle)
        b = c.bbox
        assert b.page == c.page
        # Point range, not normalized to [0, 1].
        assert b.x1 > 1.0 and b.y1 > 1.0, b
        # Correct US Letter page dimensions.
        assert b.page_width == 612.0
        assert b.page_height == 792.0
        # Non-degenerate and inside the page.
        assert 0.0 <= b.x0 < b.x1 <= b.page_width
        assert 0.0 <= b.y0 < b.y1 <= b.page_height
        # Left margin starts at ~1 inch (72pt), as drawn.
        assert b.x0 == pytest.approx(72.0, abs=2.0)


def test_admission_box_matches_drawn_position(chunks: list[ParsedChunk]) -> None:
    """The parsed top-left y0 matches the flipped reportlab baseline (~known).

    The admission paragraph's FIRST line is drawn at baseline_y_bottom=660 in
    reportlab (bottom-left) space. Its top-left box top must therefore sit a few
    points ABOVE flip_y(660) = 132 (glyph ascent), well within a 10pt band.
    """
    anchored = next(a for a in make_sample_pdf.ANCHORED_LINES if a.chunk_id == "dep-0001")
    expected_top = flip_y(anchored.baseline_y_bottom, make_sample_pdf.PAGE_HEIGHT)
    box = _find(chunks, _ADMISSION).bbox
    # Box top is just above the baseline's top-left image (ascent of the font).
    assert box.y0 == pytest.approx(expected_top, abs=10.0)


def test_word_citations_present_and_boxed(chunks: list[ParsedChunk]) -> None:
    """Each key chunk carries per-word citation boxes in points."""
    for needle in (_ADMISSION, _CONTRADICTION):
        c = _find(chunks, needle)
        assert c.words, f"chunk {c.id} has no word citations"
        for w in c.words:
            assert w.bbox.page == c.page
            assert 0.0 <= w.bbox.x0 <= w.bbox.x1 <= w.bbox.page_width
            assert 0.0 <= w.bbox.y0 <= w.bbox.y1 <= w.bbox.page_height
        # The chunk box encloses all of its word boxes.
        assert c.bbox.x0 <= min(w.bbox.x0 for w in c.words) + 0.01
        assert c.bbox.x1 + 0.01 >= max(w.bbox.x1 for w in c.words)


def test_parse_is_deterministic(sample_pdf: Path) -> None:
    """Re-parsing the same PDF yields identical chunk records."""
    first = [c.model_dump() for c in PdfTextParser(sample_pdf).parse()]
    second = [c.model_dump() for c in PdfTextParser(sample_pdf).parse()]
    assert first == second


def test_parsed_chunks_validate_as_backend_records(chunks: list[ParsedChunk]) -> None:
    """Every parsed chunk projects to a valid backend-shaped record."""
    assert chunks
    for c in chunks:
        rec = c.to_index_record()
        assert set(rec["bbox"].keys()) == {
            "page", "x0", "y0", "x1", "y1", "page_width", "page_height"
        }
        ParsedChunk.model_validate(rec)  # round-trips
