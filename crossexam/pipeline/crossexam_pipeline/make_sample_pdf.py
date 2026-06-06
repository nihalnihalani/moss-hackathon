"""Generate a realistic, multi-page mock deposition PDF for the CrossExam demo.

This makes the demo PDF-backed: instead of hand-authored fixtures, we *draw*
a real PDF whose key lines sit at KNOWN coordinates, then parse that PDF's text
layer back into chunks (see :mod:`crossexam_pipeline.pdf_parser`). Because the
positions here are deterministic, the parser's boxes line up exactly with the
rendered text and the frontend can highlight them on the same document it
renders.

Coordinate systems
------------------
ReportLab draws with a **bottom-left** origin (PDF native): ``y`` grows upward.
The canonical CrossExam bbox is **top-left** origin (``y`` grows downward),
matching ``crossexam_backend.models.BBox``. The parser converts between the two
with ``y_top = page_height - y_bottom`` (see
:func:`crossexam_pipeline.pdf_parser.flip_y`).

The "anchored" lines below intentionally reuse the exact text of the committed
backend fixture chunks so the regenerated fixture keeps answering the same demo
questions (and the backend's hard-coded page assertions keep passing), while now
being grounded in a real, renderable PDF. Page 12 carries the "warehouse on the
night of the 14th" admission; page 41 carries the contradiction (the witness
recants and says he "left ... before 8:00 p.m.").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Page geometry (US Letter, PDF points) -----------------------------------
PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
LEFT_MARGIN = 72.0  # 1 inch
RIGHT_MARGIN = 72.0
FONT_NAME = "Courier"  # monospaced => predictable, easy-to-verify glyph widths
FONT_SIZE = 10.0
# Anchored paragraphs wrap with TIGHT leading so the parser can group their
# wrapped physical lines back into ONE chunk (small inter-line gap), while the
# filler block uses a LARGE leading so it never merges into a paragraph.
PARAGRAPH_LEADING = 13.0
FILLER_LEADING = 30.0  # >> PARAGRAPH_LEADING, keeps filler lines as singletons
# Courier glyph advance at FONT_SIZE (0.6 em for Courier) -> chars that fit
# between the margins. Used to wrap anchored paragraphs deterministically.
_CHAR_WIDTH = FONT_SIZE * 0.6
TEXT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN
MAX_CHARS_PER_LINE = int(TEXT_WIDTH // _CHAR_WIDTH)

# Total pages in the synthetic deposition. Large enough to read as a long
# document and to exceed every anchored/filler page index used below.
TOTAL_PAGES = 60


@dataclass(frozen=True)
class AnchoredLine:
    """A single line of text drawn at a known position on a known page.

    Attributes:
        page: 1-based page number to draw on.
        baseline_y_bottom: Text baseline ``y`` in PDF (bottom-left) points.
        text: The line's text content.
        chunk_id: Stable id the parser stamps on the resulting chunk. Reusing
            the committed fixture ids keeps downstream tooling stable.
    """

    page: int
    baseline_y_bottom: float
    text: str
    chunk_id: str


# The demo-critical lines, at fixed baselines. ``baseline_y_bottom`` is the
# reportlab (bottom-left) baseline; the parser flips it to a top-left box.
#
# These mirror backend/fixtures/sample_chunks.json so the regenerated fixture
# answers the identical demo questions. Page 12 = the warehouse admission,
# page 41 = the contradiction.
ANCHORED_LINES: tuple[AnchoredLine, ...] = (
    AnchoredLine(
        page=12,
        baseline_y_bottom=660.0,  # top-left y0 ~= 132
        text=(
            "Q. Where were you on the night of the 14th? A. I was at the "
            "Harbor Street warehouse from approximately 9:00 p.m. until well "
            "past midnight, conducting the inventory count with Mr. Reyes."
        ),
        chunk_id="dep-0001",
    ),
    AnchoredLine(
        page=12,
        baseline_y_bottom=590.0,
        text=(
            "The witness confirmed that the loading dock at the Harbor Street "
            "warehouse was illuminated by the overhead sodium lamps throughout "
            "the entire evening of the fourteenth."
        ),
        chunk_id="dep-0002",
    ),
    AnchoredLine(
        page=13,
        baseline_y_bottom=696.0,
        text=(
            "Q. Did anyone else have access to the warehouse keys that night? "
            "A. Only Mr. Reyes and the night security guard, Daniel Cole, "
            "carried keys to the Harbor Street facility."
        ),
        chunk_id="dep-0003",
    ),
    AnchoredLine(
        page=14,
        baseline_y_bottom=492.0,
        text=(
            "Counsel introduced Exhibit 7, the keycard access log, which "
            "records every entry to the Harbor Street warehouse between "
            "6:00 p.m. and 6:00 a.m."
        ),
        chunk_id="dep-0004",
    ),
    AnchoredLine(
        page=27,
        baseline_y_bottom=612.0,
        text=(
            "The security guard Daniel Cole stated that he saw the witness "
            "enter the Harbor Street warehouse at 9:05 p.m. on the night of "
            "the 14th and did not see him leave until after midnight."
        ),
        chunk_id="dep-0008",
    ),
    AnchoredLine(
        page=33,
        baseline_y_bottom=652.0,
        text=(
            "The expert witness reviewed the surveillance footage timestamps "
            "and concluded the warehouse cameras were offline between "
            "10:30 p.m. and 11:45 p.m. on the evening in question."
        ),
        chunk_id="dep-0009",
    ),
    AnchoredLine(
        page=41,
        baseline_y_bottom=642.0,  # the contradiction
        text=(
            "On further questioning the witness stated, contrary to his "
            "earlier testimony, that on the night of the 14th he had actually "
            "left the Harbor Street warehouse before 8:00 p.m. and spent the "
            "rest of the evening at home."
        ),
        chunk_id="dep-0005",
    ),
    AnchoredLine(
        page=41,
        baseline_y_bottom=542.0,
        text=(
            "When confronted with the inconsistency, the witness explained "
            "that he had confused the night of the 14th with the prior week "
            "and could no longer recall the exact time he departed the "
            "warehouse."
        ),
        chunk_id="dep-0006",
    ),
    AnchoredLine(
        page=52,
        baseline_y_bottom=682.0,
        text=(
            "Mr. Reyes testified that the inventory count on the fourteenth "
            "was cancelled and that no one performed any count at the "
            "warehouse that night."
        ),
        chunk_id="dep-0007",
    ),
    # Two contract clauses referenced by the backend retrieval tests.
    AnchoredLine(
        page=3,
        baseline_y_bottom=392.0,
        text=(
            "Section 4.2 Indemnification. The Supplier shall indemnify and "
            "hold harmless the Buyer against any losses arising out of "
            "defective goods delivered to the warehouse."
        ),
        chunk_id="con-0001",
    ),
    AnchoredLine(
        page=8,
        baseline_y_bottom=572.0,
        text=(
            "Section 9.1 Termination. Either party may terminate this "
            "Agreement upon thirty days written notice delivered to the "
            "registered address of the other party."
        ),
        chunk_id="con-0002",
    ),
)


@dataclass
class _PagePlan:
    """Lines to draw on one page."""

    page: int
    anchored: list[AnchoredLine] = field(default_factory=list)


# --- Default output locations ------------------------------------------------
_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
_CROSSEXAM_ROOT = _PIPELINE_ROOT.parent
ASSET_PDF_PATH = _CROSSEXAM_ROOT / "assets" / "sample-deposition.pdf"
FRONTEND_PDF_PATH = _CROSSEXAM_ROOT / "frontend" / "public" / "sample-deposition.pdf"

_HEADER_TEXT = "DEPOSITION OF RAYMOND T. HOLLOWAY"
_CASE_TEXT = "Vance v. Meridian Logistics, Inc.  -  No. 24-CV-00912"

# Deterministic filler so non-anchored pages read like a real transcript
# without colliding with the anchored demo lines (these never contain the
# demo keywords used by retrieval).
_FILLER_LINES: tuple[str, ...] = (
    "Q. Please state your full name for the record.",
    "A. Raymond Theodore Holloway.",
    "Q. And you understand you are under oath today?",
    "A. I do.",
    "Q. Let us turn to the events described in the complaint.",
    "A. Certainly.",
    "Q. Take your time and answer only what you recall.",
    "A. Understood.",
)


def _draw_header(canvas: object, page_no: int) -> None:
    """Draw the running header, case caption, and page footer on a page."""
    canvas.setFont("Helvetica-Bold", 9)  # type: ignore[attr-defined]
    canvas.drawString(LEFT_MARGIN, PAGE_HEIGHT - 48.0, _HEADER_TEXT)  # type: ignore[attr-defined]
    canvas.setFont("Helvetica", 8)  # type: ignore[attr-defined]
    canvas.drawString(LEFT_MARGIN, PAGE_HEIGHT - 60.0, _CASE_TEXT)  # type: ignore[attr-defined]
    canvas.setFont("Helvetica", 8)  # type: ignore[attr-defined]
    canvas.drawRightString(  # type: ignore[attr-defined]
        PAGE_WIDTH - RIGHT_MARGIN, 36.0, f"Page {page_no} of {TOTAL_PAGES}"
    )
    # Hairline rule under the header.
    canvas.setLineWidth(0.5)  # type: ignore[attr-defined]
    canvas.line(  # type: ignore[attr-defined]
        LEFT_MARGIN, PAGE_HEIGHT - 66.0, PAGE_WIDTH - RIGHT_MARGIN, PAGE_HEIGHT - 66.0
    )


def _wrap(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> list[str]:
    """Greedily word-wrap ``text`` to at most ``max_chars`` per physical line.

    Deterministic and whitespace-preserving (single spaces), so the rendered
    physical lines are reproducible and the parser can rejoin them into the
    original sentence.

    Args:
        text: The paragraph text.
        max_chars: Max characters per physical line.

    Returns:
        Physical lines (no trailing whitespace).
    """
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _filler_for_page(page_no: int) -> list[tuple[float, str]]:
    """Return ``(baseline_y_bottom, text)`` filler lines for a non-anchored page.

    Deterministic: the same page always gets the same filler. Lines sit high on
    the page (above the anchored band that starts ~700pt down) with a LARGE
    inter-line gap so the parser never merges them into a multi-line paragraph.
    """
    out: list[tuple[float, str]] = []
    y = PAGE_HEIGHT - 110.0
    # Rotate the filler block by page so the document does not read as a loop.
    rotation = page_no % len(_FILLER_LINES)
    rotated = _FILLER_LINES[rotation:] + _FILLER_LINES[:rotation]
    for line in rotated:
        out.append((y, line))
        y -= FILLER_LEADING
    return out


def build_plans() -> list[_PagePlan]:
    """Group anchored lines by page (1..TOTAL_PAGES)."""
    by_page: dict[int, _PagePlan] = {p: _PagePlan(page=p) for p in range(1, TOTAL_PAGES + 1)}
    for line in ANCHORED_LINES:
        if line.page not in by_page:  # pragma: no cover - guarded by TOTAL_PAGES
            raise ValueError(f"anchored line page {line.page} exceeds TOTAL_PAGES")
        by_page[line.page].anchored.append(line)
    return [by_page[p] for p in range(1, TOTAL_PAGES + 1)]


def make_pdf(path: Path) -> Path:
    """Render the deterministic mock deposition PDF to ``path``.

    Args:
        path: Output PDF path. Parent directories are created.

    Returns:
        The ``path`` written.

    Raises:
        ImportError: If ``reportlab`` is not installed.
    """
    try:
        from reportlab.pdfgen import canvas as _canvas
    except ImportError as exc:  # pragma: no cover - dev-only dependency
        raise ImportError(
            "reportlab is required to generate the sample PDF. "
            "Install it with `pip install reportlab`."
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    c = _canvas.Canvas(str(path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    c.setTitle("Deposition of Raymond T. Holloway (synthetic)")
    c.setAuthor("CrossExam pipeline")
    c.setSubject("Synthetic multi-page deposition for the CrossExam demo")

    for plan in build_plans():
        _draw_header(c, plan.page)
        c.setFont(FONT_NAME, FONT_SIZE)

        if plan.anchored:
            # Anchored pages: draw ONLY the demo paragraphs (wrapped) so nothing
            # overlaps them. Each paragraph's fixed baseline is its FIRST line;
            # wrapped continuation lines flow downward with tight leading.
            for line in plan.anchored:
                y = line.baseline_y_bottom
                for physical in _wrap(line.text):
                    c.drawString(LEFT_MARGIN, y, physical)
                    y -= PARAGRAPH_LEADING
        else:
            # Filler pages: deterministic transcript chatter, widely spaced.
            for baseline_y, text in _filler_for_page(plan.page):
                c.drawString(LEFT_MARGIN, baseline_y, text)

        c.showPage()

    c.save()
    logger.info("Wrote %d-page sample PDF -> %s", TOTAL_PAGES, path)
    return path


def main() -> None:
    """Generate the sample PDF into ``assets/`` and copy to ``frontend/public/``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    make_pdf(ASSET_PDF_PATH)
    # Copy bytes to the frontend public asset so VITE_PDF_URL can serve it.
    FRONTEND_PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    FRONTEND_PDF_PATH.write_bytes(ASSET_PDF_PATH.read_bytes())
    logger.info("Copied sample PDF -> %s", FRONTEND_PDF_PATH)
    print(f"sample PDF written: {ASSET_PDF_PATH}")
    print(f"sample PDF copied:  {FRONTEND_PDF_PATH}")


if __name__ == "__main__":  # pragma: no cover
    main()
