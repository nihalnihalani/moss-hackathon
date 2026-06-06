"""Real text-layer PDF parser (network-free, no Unsiloed key required).

This is the third parse path, alongside the async :class:`UnsiloedParser` (for
real scans) and the JSON-driven :class:`DeterministicParser` (for the legacy
synthetic sample). It reads the **text layer** of a born-digital PDF -- the kind
produced by :mod:`crossexam_pipeline.make_sample_pdf` -- and emits
:class:`~crossexam_pipeline.models.ParsedChunk` records whose bounding boxes are
in canonical CrossExam coordinates: **PDF points, top-left origin**, with
``page_width``/``page_height`` on every box.

PDF text extractors report word boxes with a top-left or bottom-left origin
depending on the library; :func:`flip_y` is the single, unit-tested place that
normalizes bottom-left (PDF native) ``y`` to top-left. The result is that the
parser's boxes coincide with where the text was actually drawn, so the frontend
highlight lands on the rendered glyphs.

Only ``pdfplumber`` (which bundles ``pypdf``/``pdfminer``) is required; both are
declared as dependencies in ``pyproject.toml``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from crossexam_pipeline.models import BBox, ParsedChunk, WordCitation

logger = logging.getLogger(__name__)

# US Letter default, used only if a page omits its mediabox dimensions.
_DEFAULT_PAGE_W = 612.0
_DEFAULT_PAGE_H = 792.0

# Lines whose text is pure transcript chrome (headers/footers) are skipped so
# the chunk corpus is just substantive testimony.
_CHROME_PREFIXES = (
    "DEPOSITION OF",
    "Vance v. Meridian",
    "Page ",
)


def flip_y(y_bottom: float, page_height: float) -> float:
    """Convert a bottom-left-origin ``y`` to a top-left-origin ``y``.

    PDFs natively place the origin at the bottom-left with ``y`` increasing
    upward; the canonical CrossExam bbox uses a top-left origin with ``y``
    increasing downward (matching the backend and the frontend renderer). The
    conversion is a reflection about the page's vertical midpoint::

        y_top = page_height - y_bottom

    Args:
        y_bottom: A ``y`` coordinate in bottom-left (PDF native) points.
        page_height: Page height in points.

    Returns:
        The equivalent ``y`` in top-left points.
    """
    return page_height - y_bottom


def _is_chrome(text: str) -> bool:
    """Return ``True`` if ``text`` is a running header/footer to skip."""
    stripped = text.strip()
    return any(stripped.startswith(p) for p in _CHROME_PREFIXES)


def _stable_confidence(text: str, floor: float = 0.90, ceiling: float = 0.99) -> float:
    """Derive a deterministic, plausible confidence from text content.

    A born-digital text layer is extracted with high certainty, so the band is
    tighter (and higher) than the synthetic fallback's. Deterministic so re-runs
    are byte-identical.

    Args:
        text: Text to derive a score from.
        floor: Minimum confidence.
        ceiling: Maximum confidence.

    Returns:
        Confidence in ``[floor, ceiling]`` rounded to 4 decimals.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    frac = int.from_bytes(digest[:2], "big") / 65536.0
    return round(floor + frac * (ceiling - floor), 4)


def _word_citations(
    words: list[dict[str, Any]], page: int, page_w: float, page_h: float
) -> list[WordCitation]:
    """Build top-left-origin word citations from pdfplumber word dicts.

    pdfplumber reports word boxes as ``x0/x1`` (left/right, top-left x) and
    ``top``/``bottom`` already measured from the page top, so ``top`` IS the
    top-left ``y0`` and no flip is needed for those keys. We clamp into the page
    so the model's ``ge=0`` / ordering invariants always hold.

    Args:
        words: pdfplumber word dicts (``text``, ``x0``, ``x1``, ``top``,
            ``bottom``).
        page: 1-based page number.
        page_w: Page width (points).
        page_h: Page height (points).

    Returns:
        One :class:`WordCitation` per non-empty word.
    """
    out: list[WordCitation] = []
    for w in words:
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        x0 = max(0.0, min(page_w, float(w["x0"])))
        x1 = max(0.0, min(page_w, float(w["x1"])))
        y0 = max(0.0, min(page_h, float(w["top"])))
        y1 = max(0.0, min(page_h, float(w["bottom"])))
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        out.append(
            WordCitation(
                text=text,
                bbox=BBox(
                    page=page,
                    x0=round(x0, 2),
                    y0=round(y0, 2),
                    x1=round(x1, 2),
                    y1=round(y1, 2),
                    page_width=round(page_w, 2),
                    page_height=round(page_h, 2),
                ),
                confidence=_stable_confidence(text),
            )
        )
    return out


# Physical lines closer together than this (top-to-top, points) belong to the
# same wrapped paragraph and are merged into ONE chunk. The generator draws
# wrapped paragraph lines at ~13pt leading and filler lines at ~30pt leading, so
# this threshold cleanly separates a wrapped sentence from unrelated lines.
_PARAGRAPH_GAP_MAX = 18.0


def _group_words_into_lines(
    words: list[dict[str, Any]], y_tol: float = 3.0
) -> list[list[dict[str, Any]]]:
    """Cluster pdfplumber words into physical lines by their ``top`` coordinate.

    Args:
        words: pdfplumber word dicts for one page.
        y_tol: Max vertical difference (points) for words to share a line.

    Returns:
        Physical lines, each ordered left-to-right, ordered top-to-bottom.
    """
    lines: list[list[dict[str, Any]]] = []
    for w in sorted(words, key=lambda d: (round(float(d["top"]), 1), float(d["x0"]))):
        placed = False
        for line in lines:
            if abs(float(line[0]["top"]) - float(w["top"])) <= y_tol:
                line.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    lines.sort(key=lambda ln: float(ln[0]["top"]))
    for line in lines:
        line.sort(key=lambda d: float(d["x0"]))
    return lines


def _group_lines_into_paragraphs(
    lines: list[list[dict[str, Any]]], gap_max: float = _PARAGRAPH_GAP_MAX
) -> list[list[dict[str, Any]]]:
    """Merge vertically adjacent physical lines into paragraph word-groups.

    Consecutive physical lines whose top-to-top spacing is ``<= gap_max`` are a
    single wrapped paragraph and are concatenated into one word list (so a
    sentence wrapped across several rendered lines becomes ONE chunk). This is
    what keeps the demo's full admission/contradiction sentences intact for
    retrieval, while their bbox still spans the exact rendered glyphs.

    Args:
        lines: Physical lines (each a list of word dicts), top-to-bottom.
        gap_max: Max top-to-top spacing (points) to treat lines as one paragraph.

    Returns:
        Paragraphs, each a flat list of word dicts in reading order.
    """
    paragraphs: list[list[dict[str, Any]]] = []
    current: list[list[dict[str, Any]]] = []
    prev_top: float | None = None
    for line in lines:
        top = float(line[0]["top"])
        if prev_top is not None and (top - prev_top) > gap_max:
            paragraphs.append([w for ln in current for w in ln])
            current = []
        current.append(line)
        prev_top = top
    if current:
        paragraphs.append([w for ln in current for w in ln])
    return paragraphs


class PdfTextParser:
    """Parses a born-digital PDF's text layer into :class:`ParsedChunk` records.

    No network and no credentials: it reads the embedded text + per-word
    coordinates with ``pdfplumber`` and converts them to canonical top-left
    points. One rendered line of substantive text becomes one chunk.

    Args:
        pdf_path: Path to the source PDF.
        id_prefix: Prefix for generated chunk ids when a line has no known id.
    """

    def __init__(self, pdf_path: Path | str, id_prefix: str = "pdf") -> None:
        """Store the source PDF path and the chunk-id prefix."""
        self.pdf_path = Path(pdf_path)
        self.id_prefix = id_prefix

    def parse(self) -> list[ParsedChunk]:
        """Parse the PDF text layer into chunks.

        Returns:
            Chunks ordered by page then vertical position, each with a top-left
            points bbox and per-word citations.

        Raises:
            FileNotFoundError: If the PDF does not exist.
            ImportError: If ``pdfplumber`` is not installed.
        """
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {self.pdf_path}")
        try:
            import pdfplumber
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ImportError(
                "pdfplumber is required to parse a PDF text layer. "
                "Install it with `pip install pdfplumber`."
            ) from exc

        chunks: list[ParsedChunk] = []
        with pdfplumber.open(str(self.pdf_path)) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                page_w = float(page.width or _DEFAULT_PAGE_W)
                page_h = float(page.height or _DEFAULT_PAGE_H)
                words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
                lines = _group_words_into_lines(words)
                paragraphs = _group_lines_into_paragraphs(lines)
                for line_idx, para_words in enumerate(paragraphs):
                    text = " ".join(str(w["text"]).strip() for w in para_words).strip()
                    if not text or _is_chrome(text):
                        continue
                    citations = _word_citations(para_words, page_idx, page_w, page_h)
                    if not citations:
                        continue
                    x0 = min(c.bbox.x0 for c in citations)
                    x1 = max(c.bbox.x1 for c in citations)
                    y0 = min(c.bbox.y0 for c in citations)
                    y1 = max(c.bbox.y1 for c in citations)
                    conf = round(sum(c.confidence for c in citations) / len(citations), 4)
                    chunks.append(
                        ParsedChunk(
                            id=f"{self.id_prefix}-p{page_idx}-l{line_idx}",
                            text=text,
                            page=page_idx,
                            bbox=BBox(
                                page=page_idx,
                                x0=round(x0, 2),
                                y0=round(y0, 2),
                                x1=round(x1, 2),
                                y1=round(y1, 2),
                                page_width=round(page_w, 2),
                                page_height=round(page_h, 2),
                            ),
                            confidence=conf,
                            words=citations,
                            source="pdf-text",
                        )
                    )

        logger.info(
            "PDF text parse produced %d chunk(s) from %s.",
            len(chunks),
            self.pdf_path.name,
        )
        return chunks
