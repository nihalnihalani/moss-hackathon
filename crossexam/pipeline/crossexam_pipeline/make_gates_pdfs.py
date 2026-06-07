"""Generate the three Gates deposition / exhibit PDFs for the CrossExam demo.

Produces three born-digital PDFs from primary sources:

* ``gates-depo-aug27.pdf``  -- Aug 27, 1998 Gates deposition, sourced from the
  Techrights line-numbered plain-text reproduction.
  Key passages:
    - p.47 (transcript p.51): "Do you recall personally being worried about
      Netscape in or about April of 1995?  A. No."
    - p.63 (transcript p.66): earliest-date question / "late '95" answer.
  Physical PDF page N == transcript page N (blank pages pad the start so
  that the Moss ``page`` metadata matches the transcript citation).

* ``gates-depo-aug28.pdf``  -- Aug 28, 1998 Gates deposition, sourced from
  Techrights parts 3 (pp.391-506) and 4 (pp.510-684).
  Key passage: transcript pp.533-535 "I don't know what you mean 'concerned'."
  Same physical=transcript page convention.

* ``exhibit-internet-tidal-wave.pdf``  -- The May 26, 1995 "Internet Tidal
  Wave" memo by Bill Gates (Government Exhibit 345).  Sourced from the
  Alexander Jarvis verbatim republication.  Key passage on page 2:
  "A new competitor 'born' on the Internet is Netscape. Their browser is
  dominant, with 70% usage share ... commoditize the underlying operating
  system."  The memo is short (5 pages) so page numbers are memo-page numbers.

Usage (CLI)::

    python -m crossexam_pipeline.make_gates_pdfs [--output-dir DIR] [--doc all|aug27|aug28|memo]

Or import ``make_all`` for programmatic use.
"""

# ruff: noqa: E501
# mypy: ignore-errors

from __future__ import annotations

import argparse
import logging
import re
import sys
import textwrap
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Page geometry (US Letter, PDF points) ------------------------------------
PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
LEFT_MARGIN = 72.0
RIGHT_MARGIN = 72.0
FONT_NAME = "Courier"
FONT_SIZE = 9.0
LEADING = 11.0

_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
_CROSSEXAM_ROOT = _PIPELINE_ROOT.parent
ASSETS_DIR = _CROSSEXAM_ROOT / "assets"
FRONTEND_PUBLIC_DIR = _CROSSEXAM_ROOT / "frontend" / "public"

# Document identifiers (must match what is registered in mockData.ts)
DOC_ID_AUG27 = "gates-depo-aug27"
DOC_ID_AUG28 = "gates-depo-aug28"
DOC_ID_MEMO = "exhibit-internet-tidal-wave"

DOC_TITLE_AUG27 = "Gates Deposition — Aug 27, 1998 (US v. Microsoft)"
DOC_TITLE_AUG28 = "Gates Deposition — Aug 28, 1998 (US v. Microsoft)"
DOC_TITLE_MEMO = "Internet Tidal Wave — Bill Gates Memo, May 26, 1995"

# Techrights source URLs
_URL_AUG27 = "https://techrights.org/o/2020/10/11/bill-gates-transcripts-part-1/"
_URL_AUG28_PART3 = "https://techrights.org/o/2020/10/13/bill-gates-transcripts-part-3/"
_URL_AUG28_PART4 = "https://techrights.org/o/2020/10/14/bill-gates-transcripts-part-4/"

# Memo source URL
_URL_MEMO = "https://www.alexanderjarvis.com/the-internet-tidal-wave-by-bill-gates/"


# ---------------------------------------------------------------------------
# Boilerplate prefixes to strip from transcript lines before indexing.
# ---------------------------------------------------------------------------
_STRIP_PREFIXES = (
    "IN THE UNITED STATES DISTRICT COURT",
    "FOR THE DISTRICT OF COLUMBIA",
    "UNITED STATES OF AMERICA",
    "MICROSOFT CORPORATION",
    "DEPOSITION OF BILL GATES",
    "APPEARANCES OF COUNSEL",
    "REPORTED BY:",
    "CSR No.",
    "Our File No.",
    "BARNEY, UNGERMANN",
    "888-326-5900",
    "1-888-326-5900",
)

_STRIP_EXACT = frozenset(
    {
        "",
        "CONFIDENTIAL",
        "Plaintiff,",
        "Defendant.",
        "vs.",
    }
)


def _is_boilerplate(text: str) -> bool:
    """Return True if a line of transcript text is boilerplate to strip."""
    t = text.strip()
    if not t:
        return True
    if t in _STRIP_EXACT:
        return True
    for prefix in _STRIP_PREFIXES:
        if t.startswith(prefix):
            return True
    # Pure page numbers or case numbers
    if re.fullmatch(r"\d{1,3}", t):
        return True
    if re.fullmatch(r"No\.\s*CIV\s*\S+", t):
        return True
    return False


def _fetch_html(url: str) -> str:
    """Fetch ``url`` and return the body as a UTF-8 string.

    Args:
        url: The URL to fetch.

    Returns:
        The response body decoded as UTF-8 (with latin-1 fallback).

    Raises:
        RuntimeError: When the HTTP request fails.
    """
    logger.info("Fetching %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "crossexam-pipeline/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch {url}: {exc}") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _extract_pre(html: str) -> str:
    """Extract the first <pre>...</pre> block and strip HTML tags.

    Args:
        html: Raw HTML source containing a <pre> block.

    Returns:
        Plain text content of the first <pre> block.

    Raises:
        ValueError: If no <pre> block is found.
    """
    m = re.search(r"<pre>(.*?)</pre>", html, re.DOTALL)
    if not m:
        raise ValueError("No <pre> block found in fetched page")
    return re.sub(r"<[^>]+>", "", m.group(1))


def _clean_line(raw: str) -> str:
    """Strip tabs, carriage returns and leading line-number from a transcript line.

    Args:
        raw: A raw line from the <pre> block.

    Returns:
        Cleaned text with leading/trailing whitespace trimmed.
    """
    s = raw.replace("\r", "").replace("\t", " ")
    # Remove leading line number: one or two digits followed by whitespace
    s = re.sub(r"^\s*\d{1,2}\s+", "", s)
    return s.strip()


def _parse_pages_part1_format(text: str) -> dict[int, list[str]]:
    r"""Parse a Techrights transcript (part-1 format) into pages.

    In part-1 format each page ends with a line like::

        \\t\\t\\t\\t51 BARNEY, UNGERMANN & ASSOCIATES  1-888-326-5900

    where the number before ``BARNEY`` is the transcript page number.

    Args:
        text: Plain text of the transcript (HTML tags already stripped).

    Returns:
        Mapping of transcript page number (int) to list of cleaned content lines.
    """
    pages: dict[int, list[str]] = {}
    current: list[str] = []
    for raw in text.split("\n"):
        if "BARNEY" in raw:
            stripped = raw.strip()
            m = re.match(r"^(\d+)\s+BARNEY", stripped)
            if m:
                page_num = int(m.group(1))
                pages[page_num] = [_clean_line(line) for line in current]
                current = []
        else:
            current.append(raw)
    return pages


def _parse_pages_part4_format(text: str) -> dict[int, list[str]]:
    """Parse a Techrights transcript (part-4 format) into pages.

    In part-4 format the page number appears on the line immediately BEFORE
    a ``BARNEY, UNGERMANN`` line.

    Args:
        text: Plain text of the transcript (HTML tags already stripped).

    Returns:
        Mapping of transcript page number (int) to list of cleaned content lines.
    """
    pages: dict[int, list[str]] = {}
    current: list[str] = []
    lines = text.split("\n")
    for i, raw in enumerate(lines):
        if "BARNEY" in raw:
            prev = lines[i - 1].strip() if i > 0 else ""
            m = re.search(r"(\d{3,})\s*$", prev)
            if m:
                page_num = int(m.group(1))
                content = current[:-1] if current else current
                pages[page_num] = [_clean_line(line) for line in content]
                current = []
        else:
            current.append(raw)
    return pages


def _filter_page_lines(lines: list[str]) -> list[str]:
    """Filter a page's lines to keep only substantive Q&A content.

    Args:
        lines: Cleaned lines from one transcript page.

    Returns:
        Lines that are not boilerplate and not empty.
    """
    return [line for line in lines if line and not _is_boilerplate(line)]


def _char_capacity() -> int:
    """Return approximate character capacity per line given font metrics."""
    char_width = FONT_SIZE * 0.6
    text_width = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN
    return max(60, int(text_width / char_width))


def _wrap_text(text: str, max_chars: int) -> list[str]:
    """Word-wrap ``text`` to at most ``max_chars`` characters per line."""
    return textwrap.wrap(text, width=max_chars) or [text]


def _draw_page_content(
    canvas: object,
    text_lines: list[str],
    header: str,
    page_label: str,
) -> None:
    """Draw one page of content on ``canvas`` without calling showPage.

    Args:
        canvas: A ReportLab ``Canvas`` object.
        text_lines: Lines of text to render.
        header: Short document title drawn top-left.
        page_label: Page number label drawn top-right.
    """
    canvas.setFont("Helvetica-Bold", 8)  # type: ignore[attr-defined]
    canvas.drawString(LEFT_MARGIN, PAGE_HEIGHT - 46.0, header)  # type: ignore[attr-defined]
    canvas.setFont("Helvetica", 8)  # type: ignore[attr-defined]
    canvas.drawRightString(PAGE_WIDTH - RIGHT_MARGIN, PAGE_HEIGHT - 46.0, page_label)  # type: ignore[attr-defined]
    canvas.setLineWidth(0.5)  # type: ignore[attr-defined]
    canvas.line(LEFT_MARGIN, PAGE_HEIGHT - 52.0, PAGE_WIDTH - RIGHT_MARGIN, PAGE_HEIGHT - 52.0)  # type: ignore[attr-defined]

    canvas.setFont(FONT_NAME, FONT_SIZE)  # type: ignore[attr-defined]
    y = PAGE_HEIGHT - 68.0
    bottom_margin = 48.0
    max_chars = _char_capacity()

    for raw_line in text_lines:
        for wrapped in _wrap_text(raw_line, max_chars):
            if y < bottom_margin:
                break
            canvas.drawString(LEFT_MARGIN, y, wrapped)  # type: ignore[attr-defined]
            y -= LEADING


def _build_deposition_pdf(
    pages: dict[int, list[str]],
    output_path: Path,
    title: str,
    short_header: str,
) -> Path:
    """Render a deposition PDF where physical page N == transcript page N.

    Blank "filler" pages are inserted for transcript pages below the first
    numbered page so that the PDF page index aligns with the transcript page
    number.  This means the Moss ``page`` metadata (which is the physical
    1-based PDF page index) equals the transcript page label used in probes.

    Args:
        pages: Transcript page number -> filtered content lines.
        output_path: Destination PDF path.
        title: PDF title metadata.
        short_header: Running header text.

    Returns:
        The path written.

    Raises:
        ImportError: If ``reportlab`` is not installed.
    """
    try:
        from reportlab.pdfgen import canvas as _canvas
    except ImportError as exc:
        raise ImportError(
            "reportlab is required. Install with: pip install reportlab"
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_page_nums = sorted(pages.keys())
    first_transcript_page = sorted_page_nums[0]
    last_transcript_page = sorted_page_nums[-1]

    c = _canvas.Canvas(str(output_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    c.setTitle(title)
    c.setAuthor("CrossExam pipeline")
    c.setSubject(title)

    # Pad blank pages so physical page 1 = transcript page 1.
    # Physical page index = transcript page number (1-based).
    # Transcript pages start at 5 (aug27) or 391 (aug28), so we emit
    # (first_transcript_page - 1) blank pages before the real content.
    blank_pages = first_transcript_page - 1
    for _ in range(1, blank_pages + 1):
        # Minimal blank page with header so pdfplumber sees page dimensions
        c.setFont("Helvetica", 7)  # type: ignore[attr-defined]
        c.drawString(LEFT_MARGIN, PAGE_HEIGHT - 46.0, short_header)  # type: ignore[attr-defined]
        c.showPage()

    for transcript_page in range(first_transcript_page, last_transcript_page + 1):
        if transcript_page in pages:
            content_lines = _filter_page_lines(pages[transcript_page])
        else:
            content_lines = []  # gap between parts (e.g. 507-509 missing)
        page_label = f"Page {transcript_page}"
        _draw_page_content(c, content_lines, short_header, page_label)
        c.showPage()

    c.save()
    total_pages = blank_pages + (last_transcript_page - first_transcript_page + 1)
    logger.info(
        "Wrote deposition PDF: %d PDF pages (%d blank pad + transcript pp.%d-%d) -> %s",
        total_pages,
        blank_pages,
        first_transcript_page,
        last_transcript_page,
        output_path,
    )
    return output_path


# ---------------------------------------------------------------------------
# Internet Tidal Wave memo — verbatim text from alexanderjarvis.com
# (republication of Government Exhibit 345, introduced Aug 27, 1998).
#
# The memo is structured as sections; each section becomes one PDF page so
# the hero paragraph (Netscape / 70% usage share / commoditize) lands on
# page 2 of the memo PDF, matching the chunk probe.
#
# Para numbering below matches the alexanderjarvis.com HTML paragraphs.
# ---------------------------------------------------------------------------

# Page 1: header + opening vision paragraphs (paras 5-12)
_MEMO_PAGE_1: tuple[str, ...] = (
    "To: Executive Staff and direct Reports",
    "From: Bill Gates",
    "Date: May 26, 1995",
    "Re: The Internet Tidal Wave",
    "",
    "Our vision for the last 20 years can be summarized in a succinct way. We saw that exponential improvements in computer capabilities would make great software quite valuable. Our response was to build an organization to deliver the best software products. In the next 20 years the improvement in computer power will be outpaced by the exponential improvements in communications networks. The combination of these elements will have a fundamental impact on work, learning and play. Great software products will be crucial to delivering the benefits of these advances. Both the variety and volume of the software will increase.",
    "",
    "The Internet is at the forefront of all of this and developments on the Internet over the next several years will set the course of our industry for a long time to come. Perhaps you have already seen memos from me or others here about the importance of the Internet. I have gone through several stages of increasing my views of its importance. Now I assign the Internet the highest level of importance. In this memo I want to make clear that our focus on the Internet is crucial to every part of our business. The Internet is the most important single development to come along since the IBM PC was introduced in 1981. It is even more important than the arrival of the graphical user interface (GUI).",
    "",
    "The Internet's unique position arises from a number of elements. TCP/IP protocols that define its transport level support distributed computing and scale incredibly well. The Internet Engineering Task Force (IETF) has defined an evolutionary path that will avoid running into future problems even as eventually everyone on the planet connects up. The HTTP protocols that define HTML Web browsing are extremely simple and have allowed servers to handle incredible traffic reasonably well.",
    "",
    "Amazingly it is easier to find information on the Web than it is to find information on the Microsoft Corporate Network. This inversion where a public network solves a problem better than a private network is quite stunning. This inversion points out an opportunity for us in the corporate market. An important goal for the Office and Systems products is to focus on how our customers can create and publish information on their LANs. All work we do here can be leveraged into the HTTP/Web world.",
)

# Page 2: competitors section including the hero Netscape paragraph (paras 17-23)
_MEMO_PAGE_2: tuple[str, ...] = (
    "Competitors — The Netscape Threat",
    "",
    "Our traditional competitors are just getting involved with the Internet. Novell is surprisingly absent given the importance of networking to their position however Frankenberg recognizes its importance and is driving them in that direction. Novell has recognized that a key missing element of the Internet is a good directory service.",
    "",
    "Some competitors have a much deeper involvement in the Internet than Microsoft. All UNIX vendors are benefiting from the Internet since the default server is still a UNIX box and not Windows NT, particularly for high end demands. SUN has exploited this quite effectively.",
    "",
    # HERO PARAGRAPH — verbatim from Government Exhibit 345 / alexanderjarvis.com
    'A new competitor "born" on the Internet is Netscape. Their browser is dominant, with 70% usage share, allowing them to determine which network extensions will catch on. They are pursuing a multi-platform strategy where they move the key API into the client to commoditize the underlying operating system. They have attracted a number of public network operators to use their platform to offer information and directory services. We have to match and beat their offerings including working with MCI, newspapers, and other who are considering their products.',
    "",
    "One scary possibility being discussed by Internet fans is whether they should get together and create something far less expensive than a PC which is powerful enough for Web browsing. This new platform would optimize for the datatypes on the Web. Gordon Bell and others approached Intel on this and decided Intel didn't care about a low cost device so they started suggesting that General Magic or another operating system with a non-Intel chip is the best solution.",
)

# Page 3: coordination / response (paras 24-26)
_MEMO_PAGE_3: tuple[str, ...] = (
    "Our Response",
    "",
    "In highlighting the importance of the Internet to our future I don't want to suggest that I am alone in seeing this. There is excellent work going on in many product groups. Over the last year, a number of people have championed embracing TCP/IP, hyperlinking, HTML, and building client, tools and servers that compete on the Internet. However, we still have a lot to do. I want every product plan to try and go overboard on Internet features. One element that will be crucial is coordinating our various activities. The challenge/opportunity of the Internet is a key reason behind the recent organization. Paul Maritz will lead the Platform group to define an integrated strategy that makes it clear that Windows machines are the best choice for the Internet. This will protect and grow our Windows asset. Nathan and Pete will lead the Applications and Content group to figure out how to make money providing applications and content for the Internet.",
    "",
    "We must also invest in the Microsoft home page, so it will be clear how to find out about our various products. Today it's quite random what is on the home page and the quality of information is very low. I believe the Internet will become our most important promotional vehicle.",
    "",
    "ITG needs to take a hard look at whether we should drop our leasing arrangements for data lines to some countries and simply rely on the Internet.",
)

# Page 4: Windows platform action items (paras 27-35)
_MEMO_PAGE_4: tuple[str, ...] = (
    "Windows Platform Actions",
    "",
    "The actions required for the Windows platform are quite broad. Paul Maritz is having an Internet retreat in June which will focus on coordinating these activities. Some critical steps are the following:",
    "",
    "1. Server. BSD is working on offering the best Internet server as an integrated package. We need to understand how to make NT boxes the highest performance HTTP servers. Our server offerings need to beat what Netscape is doing including billing and security support. A major opportunity/challenge is directory. If the features required for Internet directory are not in Cairo or easily addable without a major release we will miss the window to become the world standard in directory.",
    "",
    "2. Client. First we need to offer a decent client (O'Hare) that exploits Windows 95 shortcuts. However this alone won't get people to switch away from Netscape. We need to figure out how to integrate Blackbird, and help browsing into our Internet client. We need to move all of our Internet value added from the Plus pack into Windows 95 itself as soon as we possible can with a major goal to get OEMs shipping our browser preinstalled. Without unification we will lose to Netscape/HotJava.",
    "",
    "3. File sharing/Window sharing/Multi-user. We need to give away client code that encourages Windows specific protocols to be used across the Internet.",
    "",
    "4. Forms/Languages. We need to make it very easy to design a form that presents itself as an HTML page. Today the Common Gateway Interface (CGI) is used on Web servers to give forms 'behavior' but its quite difficult to work with.",
    "",
    "5. Search engines. Verity has done good work with Notes, Netscape, AT&T and many others to get them to adopt their scalable technology. We need to come up with a strategy to bring together Office, Mediaview, Help, Cairo, and MSN.",
)

# Page 5: Applications/Content actions and conclusion (paras 36-44)
_MEMO_PAGE_5: tuple[str, ...] = (
    "Applications and Content Actions / Conclusion",
    "",
    "The actions required for the Applications and Content group are also quite broad:",
    "",
    "1. Office. Allowing for collaboration across the Internet and allowing people to publish in our file formats for both Mac and Windows with free readers is very important.",
    "",
    "2. MSN. The merger of the On-line business and Internet business creates a major challenge for MSN. It can't just be the place to find Microsoft information on the Internet.",
    "",
    "3. Consumer. The Internet will assure a large audience for a broad range of titles. However the challenge of becoming a leader in any subject area in terms of quality, depth, and price will be far more brutal than today's CD market.",
    "",
    "4. Broadband media applications. With the significant time before widescale iTV deployment we need to look hard at which applications can be delivered in an ISDN/Internet environment.",
    "",
    "5. Electronic commerce. Key elements of electronic commerce including security and billing need to be integrated into our platform strategy.",
    "",
    "We enter this new era with some considerable strengths. Among them are our people and the broad acceptance of Windows and Office.",
    "",
    "The next few years are going to be very exciting as we tackle these challenges and opportunities. The Internet is a tidal wave. It changes the rules. It is an incredible opportunity as well as incredible challenge. I am looking forward to your input on how we can improve our strategy to continue our track record of incredible success.",
)

_MEMO_PAGES: tuple[tuple[str, ...], ...] = (
    _MEMO_PAGE_1,
    _MEMO_PAGE_2,
    _MEMO_PAGE_3,
    _MEMO_PAGE_4,
    _MEMO_PAGE_5,
)


def _build_memo_pdf(output_path: Path) -> Path:
    """Render the Internet Tidal Wave memo to a born-digital PDF.

    The hero paragraph ("A new competitor 'born' on the Internet is Netscape...")
    is verbatim from the alexanderjarvis.com republication and lands on page 2.

    Args:
        output_path: Destination PDF path (parent dirs created).

    Returns:
        The path written.

    Raises:
        ImportError: If ``reportlab`` is not installed.
    """
    try:
        from reportlab.pdfgen import canvas as _canvas
    except ImportError as exc:
        raise ImportError(
            "reportlab is required. Install with: pip install reportlab"
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(_MEMO_PAGES)

    c = _canvas.Canvas(str(output_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    c.setTitle(DOC_TITLE_MEMO)
    c.setAuthor("Bill Gates / CrossExam pipeline")
    c.setSubject(
        "US v. Microsoft Government Exhibit 345 — Internet Tidal Wave (May 26, 1995)"
    )

    short_header = "Internet Tidal Wave Memo — Gates, May 26, 1995 [Gov. Exhibit 345]"
    max_chars = _char_capacity()

    for page_idx, page_lines in enumerate(_MEMO_PAGES, start=1):
        page_label = f"Page {page_idx} of {total}"
        c.setFont("Helvetica-Bold", 8)  # type: ignore[attr-defined]
        c.drawString(LEFT_MARGIN, PAGE_HEIGHT - 46.0, short_header)  # type: ignore[attr-defined]
        c.setFont("Helvetica", 8)  # type: ignore[attr-defined]
        c.drawRightString(PAGE_WIDTH - RIGHT_MARGIN, PAGE_HEIGHT - 46.0, page_label)  # type: ignore[attr-defined]
        c.setLineWidth(0.5)  # type: ignore[attr-defined]
        c.line(LEFT_MARGIN, PAGE_HEIGHT - 52.0, PAGE_WIDTH - RIGHT_MARGIN, PAGE_HEIGHT - 52.0)  # type: ignore[attr-defined]

        c.setFont(FONT_NAME, FONT_SIZE)  # type: ignore[attr-defined]
        y = PAGE_HEIGHT - 68.0
        bottom_margin = 48.0

        for raw_line in page_lines:
            if not raw_line:
                y -= LEADING
                continue
            for wrapped in _wrap_text(raw_line, max_chars):
                if y < bottom_margin:
                    break
                c.drawString(LEFT_MARGIN, y, wrapped)  # type: ignore[attr-defined]
                y -= LEADING

        c.showPage()

    c.save()
    logger.info("Wrote %d-page memo PDF -> %s", total, output_path)
    return output_path


def _fetch_aug27_pages() -> dict[int, list[str]]:
    """Fetch and parse the Aug 27 deposition from Techrights.

    Returns:
        Pages dict (transcript page number -> content lines).
    """
    html = _fetch_html(_URL_AUG27)
    text = _extract_pre(html)
    pages = _parse_pages_part1_format(text)
    logger.info("Aug 27: fetched %d pages (%d-%d)", len(pages), min(pages), max(pages))
    return pages


def _fetch_aug28_pages() -> dict[int, list[str]]:
    """Fetch and parse the Aug 28 deposition from Techrights parts 3 & 4.

    Returns:
        Merged pages dict (transcript page number -> content lines).
    """
    html3 = _fetch_html(_URL_AUG28_PART3)
    text3 = _extract_pre(html3)
    pages3 = _parse_pages_part1_format(text3)
    logger.info("Aug 28 part3: fetched %d pages", len(pages3))

    html4 = _fetch_html(_URL_AUG28_PART4)
    text4 = _extract_pre(html4)
    pages4 = _parse_pages_part4_format(text4)
    logger.info("Aug 28 part4: fetched %d pages", len(pages4))

    merged = {**pages3, **pages4}
    logger.info("Aug 28 merged: %d pages (%d-%d)", len(merged), min(merged), max(merged))
    return merged


def make_aug27_pdf(output_path: Path) -> Path:
    """Build the Aug 27, 1998 Gates deposition PDF.

    Physical PDF page N == transcript page N (blank padding at start).

    Args:
        output_path: Destination PDF path.

    Returns:
        The path written.
    """
    pages = _fetch_aug27_pages()
    return _build_deposition_pdf(
        pages,
        output_path,
        title=DOC_TITLE_AUG27,
        short_header="Gates Deposition Aug 27 1998 — US v. Microsoft",
    )


def make_aug28_pdf(output_path: Path) -> Path:
    """Build the Aug 28, 1998 Gates deposition PDF.

    Physical PDF page N == transcript page N (blank padding at start).

    Args:
        output_path: Destination PDF path.

    Returns:
        The path written.
    """
    pages = _fetch_aug28_pages()
    return _build_deposition_pdf(
        pages,
        output_path,
        title=DOC_TITLE_AUG28,
        short_header="Gates Deposition Aug 28 1998 — US v. Microsoft",
    )


def make_memo_pdf(output_path: Path) -> Path:
    """Build the Internet Tidal Wave memo PDF.

    Uses verbatim text from the alexanderjarvis.com republication of
    Government Exhibit 345.  Hero paragraph lands on page 2.

    Args:
        output_path: Destination PDF path.

    Returns:
        The path written.
    """
    return _build_memo_pdf(output_path)


def make_all(output_dir: Path | None = None) -> dict[str, Path]:
    """Build all three Gates PDFs and copy to ``frontend/public/``.

    Args:
        output_dir: Base directory for the assets.

    Returns:
        Mapping of document_id -> the written PDF path.
    """
    base = output_dir or ASSETS_DIR
    base.mkdir(parents=True, exist_ok=True)

    results: dict[str, Path] = {}

    for doc_id, make_fn in [
        (DOC_ID_AUG27, lambda p: make_aug27_pdf(p)),
        (DOC_ID_MEMO, lambda p: make_memo_pdf(p)),
        (DOC_ID_AUG28, lambda p: make_aug28_pdf(p)),
    ]:
        asset_path = base / f"{doc_id}.pdf"
        make_fn(asset_path)
        results[doc_id] = asset_path
        frontend_path = FRONTEND_PUBLIC_DIR / f"{doc_id}.pdf"
        frontend_path.parent.mkdir(parents=True, exist_ok=True)
        frontend_path.write_bytes(asset_path.read_bytes())
        logger.info("Copied %s -> %s", asset_path.name, frontend_path)

    return results


def main() -> None:
    """CLI entry point: build all three PDFs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(
        description="Build Gates deposition + Internet Tidal Wave memo PDFs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for generated PDFs (default: crossexam/assets/).",
    )
    parser.add_argument(
        "--doc",
        choices=["aug27", "aug28", "memo", "all"],
        default="all",
        help="Which document to build (default: all).",
    )
    args = parser.parse_args()

    base = args.output_dir or ASSETS_DIR
    base.mkdir(parents=True, exist_ok=True)

    def _copy_to_frontend(p: Path) -> None:
        fp = FRONTEND_PUBLIC_DIR / p.name
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(p.read_bytes())
        print(f"  copied -> {fp}")

    if args.doc in ("aug27", "all"):
        p = make_aug27_pdf(base / f"{DOC_ID_AUG27}.pdf")
        print(f"aug27 PDF written: {p}")
        _copy_to_frontend(p)

    if args.doc in ("memo", "all"):
        p = make_memo_pdf(base / f"{DOC_ID_MEMO}.pdf")
        print(f"memo PDF written: {p}")
        _copy_to_frontend(p)

    if args.doc in ("aug28", "all"):
        p = make_aug28_pdf(base / f"{DOC_ID_AUG28}.pdf")
        print(f"aug28 PDF written: {p}")
        _copy_to_frontend(p)


if __name__ == "__main__":
    main()
