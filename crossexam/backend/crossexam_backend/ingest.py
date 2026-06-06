"""Document ingest: parse a PDF's text layer into backend chunk records.

This module turns an uploaded PDF into the canonical backend chunk shape
(``id, text, page, bbox{...}, confidence``) that :class:`MockIndex` and the Moss
upsert both consume.

It prefers the real pipeline parser (:class:`crossexam_pipeline.pdf_parser`)
when that package is installed, so the HTTP service and the offline pipeline emit
byte-identical chunk records. When the pipeline is **not** installed, it falls
back to a self-contained ``pdfplumber`` parse that mirrors the pipeline's
line-grouping logic. Either way the only hard dependency is ``pdfplumber`` (which
the backend already needs for the real ingest path); the import is guarded so the
module is always importable and unit tests that don't exercise PDF parsing run
with no extra deps.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

_DEFAULT_PAGE_W = 612.0
_DEFAULT_PAGE_H = 792.0

# Top-to-top spacing (points) below which two physical lines belong to the same
# wrapped paragraph. Mirrors the pipeline's ``_PARAGRAPH_GAP_MAX``.
_PARAGRAPH_GAP_MAX = 18.0


class PdfParseError(RuntimeError):
    """Raised when a PDF cannot be parsed into any chunks."""


class PdfDependencyError(RuntimeError):
    """Raised when no PDF text-layer parser dependency is available."""


def _pipeline_records(pdf_path: Path, id_prefix: str) -> list[dict[str, Any]] | None:
    """Parse via the real pipeline parser if it is installed, else ``None``.

    Returns backend-compatible chunk records (``to_index_record`` shape) so the
    HTTP service and the offline pipeline produce identical artifacts.
    """
    try:
        from crossexam_pipeline.pdf_parser import PdfTextParser
    except ImportError:
        return None
    parser = PdfTextParser(pdf_path, id_prefix=id_prefix)
    chunks = parser.parse()
    return [c.to_index_record() for c in chunks]


def _stable_confidence(text: str, floor: float = 0.90, ceiling: float = 0.99) -> float:
    """Deterministic, plausible confidence derived from the text content."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    frac = int.from_bytes(digest[:2], "big") / 65536.0
    return round(floor + frac * (ceiling - floor), 4)


def _group_words_into_lines(
    words: list[dict[str, Any]], y_tol: float = 3.0
) -> list[list[dict[str, Any]]]:
    """Cluster pdfplumber words into physical lines by their ``top`` coordinate."""
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
    """Merge vertically adjacent physical lines into paragraph word-groups."""
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


def _fallback_records(pdf_path: Path, id_prefix: str) -> list[dict[str, Any]]:
    """Self-contained ``pdfplumber`` text-layer parse to backend chunk records.

    Used only when the pipeline package is not installed. Coordinates are in PDF
    points with a top-left origin (``pdfplumber`` ``top``/``bottom`` are already
    top-left), matching :class:`crossexam_backend.models.BBox`.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise PdfDependencyError(
            "pdfplumber is required to parse a PDF text layer. "
            "Install it with `pip install pdfplumber` (or install the pipeline)."
        ) from exc

    records: list[dict[str, Any]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_idx, page in enumerate(pdf.pages, start=1):
            page_w = float(page.width or _DEFAULT_PAGE_W)
            page_h = float(page.height or _DEFAULT_PAGE_H)
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            lines = _group_words_into_lines(words)
            paragraphs = _group_lines_into_paragraphs(lines)
            for line_idx, para_words in enumerate(paragraphs):
                text = " ".join(str(w["text"]).strip() for w in para_words).strip()
                if not text:
                    continue
                xs0 = [max(0.0, min(page_w, float(w["x0"]))) for w in para_words]
                xs1 = [max(0.0, min(page_w, float(w["x1"]))) for w in para_words]
                ys0 = [max(0.0, min(page_h, float(w["top"]))) for w in para_words]
                ys1 = [max(0.0, min(page_h, float(w["bottom"]))) for w in para_words]
                x0, x1 = min(xs0), max(xs1)
                y0, y1 = min(ys0), max(ys1)
                if x1 < x0:
                    x0, x1 = x1, x0
                if y1 < y0:
                    y0, y1 = y1, y0
                records.append(
                    {
                        "id": f"{id_prefix}-p{page_idx}-l{line_idx}",
                        "text": text,
                        "page": page_idx,
                        "bbox": {
                            "page": page_idx,
                            "x0": round(x0, 2),
                            "y0": round(y0, 2),
                            "x1": round(x1, 2),
                            "y1": round(y1, 2),
                            "page_width": round(page_w, 2),
                            "page_height": round(page_h, 2),
                        },
                        "confidence": _stable_confidence(text),
                    }
                )
    logger.info(
        "ingest.fallback_parse produced %d chunk(s) from %s",
        len(records),
        pdf_path.name,
    )
    return records


def parse_pdf_to_records(pdf_path: Path | str, id_prefix: str = "doc") -> list[dict[str, Any]]:
    """Parse a PDF into backend-compatible chunk records.

    Prefers the installed pipeline parser; otherwise falls back to a
    self-contained ``pdfplumber`` parse.

    Args:
        pdf_path: Path to the source PDF on disk.
        id_prefix: Prefix used for the generated chunk ids (keeps ids unique
            across uploaded documents).

    Returns:
        A list of chunk records, each shaped like the JSON fixture entries.

    Raises:
        FileNotFoundError: If ``pdf_path`` does not exist.
        PdfDependencyError: If no PDF parser dependency is available.
        PdfParseError: If the PDF yields no chunks (e.g. a scanned/empty PDF
            with no extractable text layer).
    """
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")

    records = _pipeline_records(path, id_prefix)
    if records is None:
        records = _fallback_records(path, id_prefix)

    if not records:
        raise PdfParseError(
            "No text could be extracted from the PDF. It may be a scanned image "
            "with no embedded text layer."
        )
    return records


def page_count(records: Sequence[dict[str, Any]]) -> int:
    """Return the number of distinct pages covered by ``records``."""
    return len({int(r["page"]) for r in records})
