"""Deterministic, network-free document parser.

This parser exists so the CrossExam demo and test-suite are fully reproducible
without any Unsiloed/Moss credentials or network access. It reads a bundled
sample document (the same JSON shape as ``fixtures/sample_deposition.json``)
and lays each line out on a synthetic page grid, producing
:class:`~crossexam_pipeline.models.ParsedChunk` records with plausible
normalized bounding boxes, word-level citations, and confidence scores.

Everything here is a pure function of the input: identical input always yields
byte-identical output, which keeps the generated fixture idempotent.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from crossexam_pipeline.models import BBox, ParsedChunk, WordCitation

logger = logging.getLogger(__name__)

# Default bundled sample lives next to the package, under ``fixtures/``.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLE_PATH = _PACKAGE_ROOT / "fixtures" / "sample_deposition.json"

# --- Synthetic layout constants (normalized page units in [0, 1]) -----------
_PAGE_MARGIN_X = 0.08
_PAGE_MARGIN_TOP = 0.10
_LINE_HEIGHT = 0.045
_CHAR_WIDTH = 0.011  # approximate normalized width of one monospace glyph
_LINE_GAP = 0.012


def _stable_confidence(text: str, floor: float = 0.82, ceiling: float = 0.99) -> float:
    """Derive a stable, plausible confidence score from text content.

    The score is deterministic (hash-based) so re-runs are reproducible, but
    varies per chunk so the demo does not show a wall of identical numbers.

    Args:
        text: The text to derive a score from.
        floor: Minimum returned confidence.
        ceiling: Maximum returned confidence.

    Returns:
        A confidence value in ``[floor, ceiling]`` rounded to 4 decimals.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    # Use the first two bytes as a fraction in [0, 1).
    frac = int.from_bytes(digest[:2], "big") / 65536.0
    return round(floor + frac * (ceiling - floor), 4)


def _word_boxes(line: str, page: int, y0: float, y1: float) -> list[WordCitation]:
    """Lay out word-level citation boxes for a single line of text.

    Words are placed left-to-right starting at the left margin, each box sized
    to its character count. This yields word-level highlights that line up with
    the chunk's overall box.

    Args:
        line: The line of text.
        page: 1-based page number.
        y0: Top edge of the line (normalized).
        y1: Bottom edge of the line (normalized).

    Returns:
        One :class:`WordCitation` per whitespace-delimited token.
    """
    citations: list[WordCitation] = []
    cursor = _PAGE_MARGIN_X
    space = _CHAR_WIDTH  # one glyph of inter-word space
    for token in line.split():
        width = max(len(token) * _CHAR_WIDTH, _CHAR_WIDTH)
        x0 = min(cursor, 1.0)
        x1 = min(cursor + width, 1.0)
        citations.append(
            WordCitation(
                text=token,
                bbox=BBox(page=page, x0=round(x0, 4), y0=y0, x1=round(x1, 4), y1=y1),
                confidence=_stable_confidence(token),
            )
        )
        cursor = x1 + space
    return citations


class DeterministicParser:
    """Parses a bundled sample document into :class:`ParsedChunk` records.

    No network, no credentials, fully deterministic. Each non-empty line of the
    sample becomes one chunk, anchored to its declared page with a synthetic
    bounding box and per-word citations.

    Args:
        sample_path: Path to the sample JSON. Defaults to the bundled
            ``fixtures/sample_deposition.json``.
    """

    def __init__(self, sample_path: Path | str | None = None) -> None:
        self.sample_path = Path(sample_path) if sample_path else DEFAULT_SAMPLE_PATH

    def _load_sample(self) -> dict[str, Any]:
        """Read and parse the sample JSON from disk.

        Returns:
            The decoded sample document.

        Raises:
            FileNotFoundError: If the sample file does not exist.
            ValueError: If the sample JSON is malformed or missing ``pages``.
        """
        if not self.sample_path.exists():
            raise FileNotFoundError(f"Sample document not found: {self.sample_path}")
        try:
            data: dict[str, Any] = json.loads(self.sample_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Sample document is not valid JSON: {exc}") from exc
        if "pages" not in data or not isinstance(data["pages"], list):
            raise ValueError("Sample document must contain a 'pages' list.")
        return data

    def parse(self) -> list[ParsedChunk]:
        """Parse the sample into deterministic chunks.

        Returns:
            A list of :class:`ParsedChunk`, one per non-empty line, ordered by
            page then line.
        """
        data = self._load_sample()
        title = str(data.get("document", {}).get("title", "document"))
        slug = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]

        chunks: list[ParsedChunk] = []
        for page_obj in data["pages"]:
            page = int(page_obj["page"])
            lines = [str(line) for line in page_obj.get("lines", [])]
            for line_idx, line in enumerate(lines):
                stripped = line.strip()
                if not stripped:
                    continue
                y0 = round(_PAGE_MARGIN_TOP + line_idx * (_LINE_HEIGHT + _LINE_GAP), 4)
                y1 = round(y0 + _LINE_HEIGHT, 4)
                # Chunk box spans the text width; cap at right margin.
                text_width = min(len(stripped) * _CHAR_WIDTH, 1.0 - 2 * _PAGE_MARGIN_X)
                x0 = _PAGE_MARGIN_X
                x1 = round(min(x0 + text_width, 1.0), 4)
                words = _word_boxes(stripped, page, y0, y1)
                # Aggregate confidence = mean of word confidences (stable).
                if words:
                    agg = round(sum(w.confidence for w in words) / len(words), 4)
                else:  # pragma: no cover - non-empty guaranteed above
                    agg = _stable_confidence(stripped)
                chunk = ParsedChunk(
                    id=f"{slug}-p{page}-l{line_idx}",
                    text=stripped,
                    page=page,
                    bbox=BBox(page=page, x0=round(x0, 4), y0=y0, x1=x1, y1=y1),
                    confidence=agg,
                    words=words,
                    source="fallback",
                )
                chunks.append(chunk)

        logger.info(
            "Deterministic parse produced %d chunk(s) from %s across %d page(s).",
            len(chunks),
            self.sample_path.name,
            len(data["pages"]),
        )
        return chunks
