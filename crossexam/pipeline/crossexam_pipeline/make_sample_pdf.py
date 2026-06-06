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

# Document identities (feat 1, multi-document). The deposition is the primary
# document; the visitor log is a CONTRADICTING exhibit.
DEPOSITION_DOC_ID = "deposition-holloway"
DEPOSITION_DOC_TITLE = "Deposition of Raymond T. Holloway"
VISITOR_LOG_DOC_ID = "exhibit-visitor-log"
VISITOR_LOG_DOC_TITLE = "Exhibit 14: Security Desk Visitor Log"

# Second exhibit (feat 1): a Security Desk Visitor Log that places Holloway
# DOWNTOWN at 9:40 p.m. on the 14th -- directly contradicting the deposition's
# warehouse alibi (p.12, "at the Harbor Street warehouse from approximately
# 9:00 p.m. until well past midnight").
VISITOR_LOG_ASSET_PATH = _CROSSEXAM_ROOT / "assets" / "exhibit-visitor-log.pdf"
VISITOR_LOG_FRONTEND_PATH = (
    _CROSSEXAM_ROOT / "frontend" / "public" / "exhibit-visitor-log.pdf"
)
_VISITOR_LOG_TOTAL_PAGES = 3

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


# --- Second document: the contradicting visitor-log exhibit (feat 1) ---------
# Each line: (page, baseline_y_bottom, text). Page 2 carries the contradiction:
# Holloway signed in DOWNTOWN at 9:40 p.m. on the 14th, conflicting with the
# warehouse alibi in the deposition.
_VISITOR_LOG_LINES: tuple[tuple[int, float, str], ...] = (
    (
        1,
        660.0,
        "MERIDIAN LOGISTICS DOWNTOWN TOWER -- SECURITY DESK VISITOR LOG",
    ),
    (
        1,
        630.0,
        "Address: 400 Commerce Plaza, Downtown. All visitors must sign the desk register.",
    ),
    (
        1,
        600.0,
        "This log is maintained by the night security supervisor and reviewed daily.",
    ),
    (
        2,
        660.0,
        (
            "Visitor log entry: R. Holloway signed the downtown security desk "
            "visitor log at 9:40 p.m. on the 14th -- two miles from the Harbor "
            "Street warehouse -- and signed out at 11:20 p.m., having attended "
            "the quarterly budget meeting on the ninth floor."
        ),
    ),
    (
        2,
        560.0,
        (
            "The security supervisor, Lena Ortiz, confirmed she personally issued "
            "a visitor badge to Mr. Holloway at the downtown tower and that he "
            "was present in the building throughout the entire evening of the "
            "fourteenth."
        ),
    ),
    (
        3,
        660.0,
        (
            "The downtown lobby camera recorded Mr. Holloway entering at 9:38 p.m. "
            "and the badge system logged his exit at 11:20 p.m. on the 14th."
        ),
    ),
)

# Filler so the exhibit reads like a real multi-page log without colliding with
# the contradiction lines or carrying the warehouse keywords.
_VISITOR_LOG_FILLER: tuple[str, ...] = (
    "Time      Visitor                 Host / Purpose            Badge",
    "08:15 PM  J. Marsh                Facilities                D-1042",
    "08:52 PM  K. Nguyen               Vendor delivery           D-1043",
    "09:10 PM  P. Okafor               Accounting                D-1044",
)

_VISITOR_LOG_HEADER = "EXHIBIT 14 - SECURITY DESK VISITOR LOG"
_VISITOR_LOG_CASE = "Vance v. Meridian Logistics, Inc.  -  No. 24-CV-00912"


def _draw_visitor_header(canvas: object, page_no: int) -> None:
    """Draw the visitor-log running header, caption, and page footer."""
    canvas.setFont("Helvetica-Bold", 9)  # type: ignore[attr-defined]
    canvas.drawString(LEFT_MARGIN, PAGE_HEIGHT - 48.0, _VISITOR_LOG_HEADER)  # type: ignore[attr-defined]
    canvas.setFont("Helvetica", 8)  # type: ignore[attr-defined]
    canvas.drawString(LEFT_MARGIN, PAGE_HEIGHT - 60.0, _VISITOR_LOG_CASE)  # type: ignore[attr-defined]
    canvas.drawRightString(  # type: ignore[attr-defined]
        PAGE_WIDTH - RIGHT_MARGIN, 36.0, f"Page {page_no} of {_VISITOR_LOG_TOTAL_PAGES}"
    )
    canvas.setLineWidth(0.5)  # type: ignore[attr-defined]
    canvas.line(  # type: ignore[attr-defined]
        LEFT_MARGIN, PAGE_HEIGHT - 66.0, PAGE_WIDTH - RIGHT_MARGIN, PAGE_HEIGHT - 66.0
    )


def make_visitor_log_pdf(path: Path) -> Path:
    """Render the contradicting Security Desk Visitor Log exhibit to ``path``.

    A short multi-page exhibit whose page-2 entry places Holloway downtown at
    9:40 p.m. on the 14th, contradicting the deposition's warehouse alibi.

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
    c.setTitle("Exhibit 14: Security Desk Visitor Log (synthetic)")
    c.setAuthor("CrossExam pipeline")
    c.setSubject("Synthetic contradicting exhibit for the CrossExam demo")

    by_page: dict[int, list[tuple[float, str]]] = {
        p: [] for p in range(1, _VISITOR_LOG_TOTAL_PAGES + 1)
    }
    for page, y, text in _VISITOR_LOG_LINES:
        by_page[page].append((y, text))

    for page_no in range(1, _VISITOR_LOG_TOTAL_PAGES + 1):
        _draw_visitor_header(c, page_no)
        c.setFont(FONT_NAME, FONT_SIZE)
        for y, text in by_page[page_no]:
            cur_y = y
            for physical in _wrap(text):
                c.drawString(LEFT_MARGIN, cur_y, physical)
                cur_y -= PARAGRAPH_LEADING
        # Widely spaced filler table low on the page (never merges with entries).
        filler_y = 300.0
        for fline in _VISITOR_LOG_FILLER:
            c.drawString(LEFT_MARGIN, filler_y, fline)
            filler_y -= FILLER_LEADING
        c.showPage()

    c.save()
    logger.info("Wrote %d-page visitor-log exhibit -> %s", _VISITOR_LOG_TOTAL_PAGES, path)
    return path


def make_scanned_pdf(path: Path, pages: int = 2) -> Path:
    """Render a SCANNED-style PDF with NO extractable text layer (feat 3).

    The text is drawn as vector outlines (``textOutline``) instead of selectable
    glyphs, so ``pdfplumber.extract_words`` finds nothing -- this simulates an
    image-only scan. The scanned parse path (see
    :mod:`crossexam_pipeline.unsiloed`) handles such a document by producing
    region-box quads and marking the chunks ``scanned=True``.

    Args:
        path: Output PDF path. Parent directories are created.
        pages: Number of pages to render.

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
    c.setTitle("Scanned handwritten field notes (synthetic, image-only)")
    scanned_lines = (
        "FIELD NOTES (scanned, handwritten)",
        "On the night of the 14th: saw R. Holloway downtown around 9:30 p.m.",
        "He was NOT at the Harbor Street warehouse when I drove past at 10 p.m.",
        "Will follow up with the night supervisor in the morning.",
    )
    for _ in range(max(1, pages)):
        text_obj = c.beginText(LEFT_MARGIN, PAGE_HEIGHT - 120.0)
        text_obj.setFont("Helvetica", 14)
        # Outline-only render => no recoverable text layer (simulates a scan).
        text_obj.setTextRenderMode(1)  # stroke (outline) only, no fill, no text
        for line in scanned_lines:
            text_obj.textLine(line)
            text_obj.textLine("")
        c.setLineWidth(0.4)
        c.drawText(text_obj)
        c.showPage()
    c.save()
    logger.info("Wrote %d-page scanned (image-only) PDF -> %s", max(1, pages), path)
    return path


def main() -> None:
    """Generate both demo PDFs into ``assets/`` and copy to ``frontend/public/``."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Primary document: the deposition.
    make_pdf(ASSET_PDF_PATH)
    FRONTEND_PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    FRONTEND_PDF_PATH.write_bytes(ASSET_PDF_PATH.read_bytes())
    logger.info("Copied sample PDF -> %s", FRONTEND_PDF_PATH)
    # Second document (feat 1): the contradicting visitor-log exhibit.
    make_visitor_log_pdf(VISITOR_LOG_ASSET_PATH)
    VISITOR_LOG_FRONTEND_PATH.parent.mkdir(parents=True, exist_ok=True)
    VISITOR_LOG_FRONTEND_PATH.write_bytes(VISITOR_LOG_ASSET_PATH.read_bytes())
    logger.info("Copied visitor-log exhibit -> %s", VISITOR_LOG_FRONTEND_PATH)
    print(f"deposition PDF written: {ASSET_PDF_PATH}")
    print(f"deposition PDF copied:  {FRONTEND_PDF_PATH}")
    print(f"visitor-log PDF written: {VISITOR_LOG_ASSET_PATH}")
    print(f"visitor-log PDF copied:  {VISITOR_LOG_FRONTEND_PATH}")


if __name__ == "__main__":  # pragma: no cover
    main()
