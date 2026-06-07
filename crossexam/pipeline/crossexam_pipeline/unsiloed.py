"""Unsiloed Parse/Extract client.

Submits a document to the Unsiloed async parse API, polls until the job
completes, and normalizes the response into
:class:`~crossexam_pipeline.models.ParsedChunk` records carrying page numbers,
bounding boxes, word-level citations, and confidence scores.

Real API (prod.visionapi.unsiloed.ai):
- Auth: ``api-key: <key>`` request header (NOT ``Authorization: Bearer``).
- Submit: ``POST /parse`` — multipart upload, field name ``file``.
  Form param ``ocr_strategy="force_ocr"`` for scanned docs, omitted otherwise.
- Poll: ``GET /parse/{job_id}`` until ``status == "Succeeded"`` (case-insensitive).
- Result: top-level ``chunks`` array; each chunk has a ``segments`` sub-array.
  Each segment carries ``content``, ``page_number``, ``bbox`` (left/top/width/height),
  ``ocr`` (word list), ``confidence``, and ``segment_id``.

This path only runs when ``UNSILOED_API_KEY`` is set. When the key is missing,
:meth:`UnsiloedParser.from_env` raises a clear error instructing the caller to
use ``--dry-run`` (which routes to the deterministic fallback instead).

The normalizer is defensive: it accepts both the current segment-based schema and
the legacy flat-chunk shape so fixture variants and future minor renames do not
require code changes.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from crossexam_pipeline.models import (
    DEFAULT_DOCUMENT_ID,
    DEFAULT_DOCUMENT_TITLE,
    BBox,
    ParsedChunk,
    WordCitation,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://prod.visionapi.unsiloed.ai"
ENV_API_KEY = "UNSILOED_API_KEY"
ENV_BASE_URL = "UNSILOED_BASE_URL"


class UnsiloedError(RuntimeError):
    """Raised for Unsiloed configuration or API failures."""


class MissingCredentialsError(UnsiloedError):
    """Raised when no Unsiloed API key is configured.

    The CLI catches this and points the caller at ``--dry-run``.
    """


class UnsiloedParser:
    """Async client for the Unsiloed Parse/Extract API.

    Args:
        api_key: Unsiloed API key.
        base_url: API base URL. Defaults to :data:`DEFAULT_BASE_URL`.
        poll_interval: Seconds between job-status polls.
        timeout: Total seconds to wait for a job before giving up.
        client: Optional pre-built ``httpx.AsyncClient`` (mainly for tests).
        document_id: Source document id stamped on every chunk.
        document_title: Human label for the doc switcher.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        poll_interval: float = 3.0,
        timeout: float = 600.0,
        client: httpx.AsyncClient | None = None,
        document_id: str = DEFAULT_DOCUMENT_ID,
        document_title: str | None = DEFAULT_DOCUMENT_TITLE,
    ) -> None:
        """Initialize the parser and validate that an API key is present."""
        if not api_key:
            raise MissingCredentialsError(
                f"No Unsiloed API key. Set {ENV_API_KEY} or run with --dry-run "
                "to use the deterministic fallback parser."
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._client = client
        self.document_id = document_id
        self.document_title = document_title

    @classmethod
    def from_env(cls, **kwargs: Any) -> UnsiloedParser:  # noqa: ANN401
        """Build a parser from environment variables.

        Reads ``UNSILOED_API_KEY`` and optionally ``UNSILOED_BASE_URL``.

        Returns:
            A configured :class:`UnsiloedParser`.

        Raises:
            MissingCredentialsError: If ``UNSILOED_API_KEY`` is unset/empty.
        """
        api_key = os.environ.get(ENV_API_KEY, "").strip()
        if not api_key:
            raise MissingCredentialsError(
                f"{ENV_API_KEY} is not set. Run the pipeline with --dry-run to use "
                "the deterministic, network-free fallback parser instead."
            )
        base_url = os.environ.get(ENV_BASE_URL, DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
        return cls(api_key=api_key, base_url=base_url, **kwargs)

    def _headers(self) -> dict[str, str]:
        """Return auth headers for API requests.

        The real Unsiloed API uses a bare ``api-key`` header, not
        ``Authorization: Bearer``.
        """
        return {"api-key": self.api_key, "Accept": "application/json"}

    async def _acquire_client(self) -> tuple[httpx.AsyncClient, bool]:
        """Return an async client and whether the caller owns its lifecycle.

        Returns:
            ``(client, owns)`` where ``owns`` is ``True`` if the client was
            created here and must be closed by the caller.
        """
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(timeout=httpx.Timeout(60.0)), True

    async def parse(self, input_path: Path | str, scanned: bool = False) -> list[ParsedChunk]:
        """Parse a document via Unsiloed: submit, poll, normalize.

        Args:
            input_path: Path to the source document (e.g. a PDF).
            scanned: If ``True``, send ``ocr_strategy="force_ocr"`` and mark
                the resulting chunks ``scanned=True``.

        Returns:
            Normalized :class:`ParsedChunk` records.

        Raises:
            FileNotFoundError: If ``input_path`` does not exist.
            UnsiloedError: On API or job failures.
        """
        path = Path(input_path)
        if not path.exists():
            raise FileNotFoundError(f"Input document not found: {path}")

        client, owns = await self._acquire_client()
        try:
            job_id = await self._submit(client, path, scanned=scanned)
            logger.info(
                "Submitted Unsiloed parse job %s for %s (scanned=%s)",
                job_id,
                path.name,
                scanned,
            )
            result = await self._poll(client, job_id)
            chunks = self.normalize(
                result,
                scanned=scanned,
                document_id=self.document_id,
                document_title=self.document_title,
            )
            logger.info("Unsiloed job %s yielded %d chunk(s).", job_id, len(chunks))
            return chunks
        finally:
            if owns:
                await client.aclose()

    async def parse_scanned(self, input_path: Path | str) -> list[ParsedChunk]:
        """Parse a scanned/image-only document via OCR.

        Convenience wrapper for ``parse(..., scanned=True)``: sends
        ``ocr_strategy="force_ocr"`` and marks every chunk ``scanned=True``.

        Args:
            input_path: Path to the scanned source document.

        Returns:
            Normalized :class:`ParsedChunk` records with ``scanned=True``.
        """
        return await self.parse(input_path, scanned=True)

    async def _submit(
        self, client: httpx.AsyncClient, path: Path, scanned: bool = False
    ) -> str:
        """Upload the document and return the created job id.

        Always posts to ``POST /parse`` (multipart, field name ``file``).
        Sends ``ocr_strategy="force_ocr"`` when ``scanned`` is ``True``.

        Args:
            client: Async HTTP client.
            path: Document to upload.
            scanned: When ``True``, request OCR mode via form param.

        Returns:
            The Unsiloed job identifier.

        Raises:
            UnsiloedError: If the API does not return a job id.
        """
        files = {"file": (path.name, path.read_bytes(), "application/pdf")}
        data: dict[str, str] = {"ocr_strategy": "force_ocr"} if scanned else {}
        resp = await client.post(
            f"{self.base_url}/parse",
            headers=self._headers(),
            files=files,
            data=data,
        )
        resp.raise_for_status()
        payload = resp.json()
        job_id = payload.get("job_id") or payload.get("id")
        if not job_id:
            raise UnsiloedError(
                f"Unsiloed /parse returned no job id: {payload!r}"
            )
        return str(job_id)

    async def _poll(
        self, client: httpx.AsyncClient, job_id: str
    ) -> dict[str, Any]:
        """Poll ``GET /parse/{job_id}`` until the job completes, fails, or times out.

        The completed response carries the result at top level (no ``result``
        wrapper); ``payload.get("result", payload)`` is used as a defensive
        fallback for both shapes.

        Args:
            client: Async HTTP client.
            job_id: Job to poll.

        Returns:
            The completed job's result payload.

        Raises:
            UnsiloedError: If the job fails or polling times out.
        """
        start = time.monotonic()
        while time.monotonic() - start <= self.timeout:
            resp = await client.get(
                f"{self.base_url}/parse/{job_id}", headers=self._headers()
            )
            resp.raise_for_status()
            payload = resp.json()
            status = str(payload.get("status", "")).lower()
            if status in {"completed", "succeeded", "done"}:
                result: dict[str, Any] = payload.get("result", payload)
                return result
            if status in {"failed", "error", "cancelled"}:
                raise UnsiloedError(
                    f"Unsiloed job {job_id} ended with status {status!r}: "
                    f"{payload.get('error', 'no detail')}"
                )
            await asyncio.sleep(self.poll_interval)
        raise UnsiloedError(
            f"Unsiloed job {job_id} did not complete within {self.timeout}s."
        )

    # --- Normalization -------------------------------------------------------

    # Fallback page dimensions (US Letter, points) when the API omits them.
    _DEFAULT_PAGE_W = 612.0
    _DEFAULT_PAGE_H = 792.0

    @classmethod
    def _coerce_bbox(cls, raw: dict[str, Any], page: int) -> BBox:
        """Coerce a raw bbox dict into a points-space :class:`BBox`.

        Accepts three shapes:

        * ``{left, top, width, height}`` — new Unsiloed schema; converts as
          ``x0=left, y0=top, x1=left+width, y1=top+height``.
        * ``{x0, y0, x1, y1}`` — previous / legacy shape.
        * ``{left, top, right, bottom}`` — alternative legacy shape.

        Coordinates are absolute PDF points — the Unsiloed API always returns
        absolute points, never fractional [0, 1] values. ``page_width``/
        ``page_height`` default to US Letter (612x792) when unknown.

        Args:
            raw: Raw bounding-box mapping from the API.
            page: 1-based page number to stamp on the box.

        Returns:
            A :class:`BBox` with coordinates and page dims in points.
        """
        page_w = float(raw.get("page_width", 0.0) or 0.0) or cls._DEFAULT_PAGE_W
        page_h = float(raw.get("page_height", 0.0) or 0.0) or cls._DEFAULT_PAGE_H

        # New schema: {left, top, width, height}
        if "width" in raw and "height" in raw:
            left = float(raw.get("left", 0.0))
            top = float(raw.get("top", 0.0))
            x0 = left
            y0 = top
            x1 = left + float(raw["width"])
            y1 = top + float(raw["height"])
        else:
            # Legacy: {x0,y0,x1,y1} or {left,top,right,bottom}
            x0 = float(raw.get("x0", raw.get("left", 0.0)))
            y0 = float(raw.get("y0", raw.get("top", 0.0)))
            x1 = float(raw.get("x1", raw.get("right", x0)))
            y1 = float(raw.get("y1", raw.get("bottom", y0)))

        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        # Clamp into the page so the backend's ge=0 / ordering checks hold.
        x0 = max(0.0, min(page_w, x0))
        x1 = max(0.0, min(page_w, x1))
        y0 = max(0.0, min(page_h, y0))
        y1 = max(0.0, min(page_h, y1))
        return BBox(
            page=page,
            x0=round(x0, 2),
            y0=round(y0, 2),
            x1=round(x1, 2),
            y1=round(y1, 2),
            page_width=round(page_w, 2),
            page_height=round(page_h, 2),
        )

    @classmethod
    def _coerce_words(cls, raw_words: list[dict[str, Any]], page: int) -> list[WordCitation]:
        """Coerce raw per-word entries into :class:`WordCitation` records.

        Accepts ``ocr`` list entries (new schema:
        ``{text, confidence, bbox:{left,top,width,height}}``)
        or legacy ``words`` list entries (``{text, bbox:{x0,...}, confidence}``).

        Args:
            raw_words: Raw word entries from the API.
            page: 1-based page number.

        Returns:
            Word-level citations (entries without text are skipped).
        """
        words: list[WordCitation] = []
        for w in raw_words:
            text = str(w.get("text", w.get("word", ""))).strip()
            if not text:
                continue
            bbox_raw = w.get("bbox") or w.get("bounding_box") or {}
            conf = float(w.get("confidence", w.get("score", 1.0)))
            words.append(
                WordCitation(
                    text=text,
                    bbox=cls._coerce_bbox(bbox_raw, page),
                    confidence=max(0.0, min(1.0, conf)),
                )
            )
        return words

    @classmethod
    def _quads_for(
        cls,
        rc: dict[str, Any],
        words: list[WordCitation],
        bbox: BBox,
        page: int,
    ) -> list[BBox]:
        """Build per-line quad boxes for a chunk.

        Resolution order:

        * Explicit ``quads``/``lines``/``regions`` on the raw entry — coerce each to points.
        * Otherwise group the word boxes into physical lines by their top edge.
        * Otherwise fall back to the union ``bbox`` as a single quad.
        """
        raw_quads = rc.get("quads") or rc.get("lines") or rc.get("regions")
        if raw_quads:
            out: list[BBox] = []
            for q in raw_quads:
                qbox = q.get("bbox") or q.get("bounding_box") or q
                out.append(cls._coerce_bbox(qbox, page))
            if out:
                return out
        if words:
            y_tol = 3.0
            lines: list[list[WordCitation]] = []
            for w in sorted(words, key=lambda c: (round(c.bbox.y0, 1), c.bbox.x0)):
                placed = False
                for line in lines:
                    if abs(line[0].bbox.y0 - w.bbox.y0) <= y_tol:
                        line.append(w)
                        placed = True
                        break
                if not placed:
                    lines.append([w])
            lines.sort(key=lambda ln: ln[0].bbox.y0)
            return [
                BBox(
                    page=page,
                    x0=round(min(c.bbox.x0 for c in line), 2),
                    y0=round(min(c.bbox.y0 for c in line), 2),
                    x1=round(max(c.bbox.x1 for c in line), 2),
                    y1=round(max(c.bbox.y1 for c in line), 2),
                    page_width=bbox.page_width,
                    page_height=bbox.page_height,
                )
                for line in lines
            ]
        return [bbox]

    @classmethod
    def _normalize_segment(
        cls,
        seg: dict[str, Any],
        chunk_id: str,
        seg_idx: int,
        scanned: bool,
        document_id: str,
        document_title: str | None,
    ) -> ParsedChunk | None:
        """Normalize a single segment dict into a :class:`ParsedChunk`.

        Reads ``content`` (preferred) or ``markdown`` for text; ``page_number``
        for page; ``bbox`` for the bounding box (``{left,top,width,height}``);
        ``ocr`` (new) or ``words`` (legacy) for word entries; ``confidence``
        for the score; and ``segment_id`` for the id.

        Args:
            seg: Raw segment (or legacy flat chunk) dict.
            chunk_id: Parent chunk id, used as fallback when ``segment_id`` is absent.
            seg_idx: Positional index within the chunk, used for synthetic ids.
            scanned: Whether the document was parsed in OCR/scan mode.
            document_id: Source document id.
            document_title: Human-readable document label.

        Returns:
            A :class:`ParsedChunk`, or ``None`` if the segment has no text.
        """
        # Text: prefer content (plain text) over markdown.
        text = str(
            seg.get("content", seg.get("text", seg.get("markdown", "")))
        ).strip()
        if not text:
            return None

        # Page: new schema uses page_number (1-indexed); legacy uses page.
        page = int(seg.get("page_number", seg.get("page", 1)))

        # Bounding box.
        bbox_raw = seg.get("bbox") or seg.get("bounding_box") or {}
        bbox = cls._coerce_bbox(bbox_raw, page)

        # Words: new schema uses `ocr`; legacy uses `words`.
        raw_words: list[dict[str, Any]] = seg.get("ocr") or seg.get("words") or []
        words = cls._coerce_words(raw_words, page)

        # Quads (region boxes).
        quads = cls._quads_for(seg, words, bbox, page)

        # Confidence.
        conf = seg.get("confidence", seg.get("score"))
        if conf is None and words:
            conf = sum(w.confidence for w in words) / len(words)
        confidence = max(0.0, min(1.0, float(conf if conf is not None else 1.0)))

        # ID: segment_id > parent chunk_id-segN > synthetic.
        seg_id = seg.get("segment_id") or f"{chunk_id}-s{seg_idx}"

        return ParsedChunk(
            id=seg_id,
            text=text,
            page=page,
            bbox=bbox,
            confidence=round(confidence, 4),
            document_id=document_id,
            document_title=document_title,
            scanned=bool(scanned or seg.get("scanned", False)),
            quads=quads,
            words=words,
            source="unsiloed",
        )

    @classmethod
    def normalize(
        cls,
        result: dict[str, Any],
        scanned: bool = False,
        document_id: str = DEFAULT_DOCUMENT_ID,
        document_title: str | None = DEFAULT_DOCUMENT_TITLE,
    ) -> list[ParsedChunk]:
        """Normalize a completed Unsiloed result into :class:`ParsedChunk`.

        Iterates ``result["chunks"]``; for each chunk, iterates
        ``chunk["segments"]`` (new schema) or treats the chunk itself as a
        single segment (legacy fallback). Each segment is mapped to one
        :class:`ParsedChunk`.

        Args:
            result: The job's result payload. Must contain a ``chunks``
                (or ``blocks``/``elements``) list.
            scanned: Mark every chunk ``scanned=True`` (OCR path).
            document_id: Source document id stamped on every chunk.
            document_title: Human label for the doc switcher.

        Returns:
            Normalized chunks, skipping any segment without text.

        Raises:
            UnsiloedError: If no chunk list can be found in the result.
        """
        raw_chunks = (
            result.get("chunks")
            or result.get("blocks")
            or result.get("elements")
        )
        if raw_chunks is None:
            raise UnsiloedError(
                f"Unsiloed result contained no chunks/blocks/elements: keys={list(result)}"
            )

        chunks: list[ParsedChunk] = []
        for chunk_idx, rc in enumerate(raw_chunks):
            # Derive a stable base id for this chunk.
            chunk_id = str(
                rc.get("chunk_id", rc.get("id", f"unsiloed-{chunk_idx}"))
            )

            # New schema: chunk has a `segments` array.
            # Defensive fallback: if no `segments`, treat the chunk itself as
            # a single segment (legacy flat-chunk shape).
            segments: list[dict[str, Any]] = rc.get("segments") or [rc]

            for seg_idx, seg in enumerate(segments):
                parsed = cls._normalize_segment(
                    seg=seg,
                    chunk_id=chunk_id,
                    seg_idx=seg_idx,
                    scanned=scanned,
                    document_id=document_id,
                    document_title=document_title,
                )
                if parsed is not None:
                    chunks.append(parsed)

        return chunks


# --- Offline scanned fallback (feat 3) ---------------------------------------
# Region-box geometry for the synthetic scan: a band of OCR "regions" per page.
_SCAN_PAGE_W = 612.0
_SCAN_PAGE_H = 792.0
_SCAN_LEFT = 72.0
_SCAN_RIGHT = 540.0
_SCAN_TOP = 120.0
_SCAN_REGION_H = 28.0
_SCAN_REGION_GAP = 16.0


def _scan_confidence(text: str, floor: float = 0.62, ceiling: float = 0.82) -> float:
    """Deterministic OCR-style confidence (lower band: scans are noisier)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    frac = int.from_bytes(digest[:2], "big") / 65536.0
    return round(floor + frac * (ceiling - floor), 4)


class ScannedFallbackParser:
    """Deterministic, network-free parser for scanned/image-only documents.

    This is the offline counterpart of the Unsiloed OCR path. A real scan has
    no clean text layer, so this parser does NOT try to read glyphs: it marks
    the document ``scanned=True`` and lays out one region box per "OCR'd" line,
    using those region boxes as the chunk ``quads`` (and their union as the chunk
    ``bbox``). It is fully deterministic so the fixture and tests are reproducible.

    The recovered text is supplied by the caller (``lines``) — in the offline
    demo this stands in for what an OCR engine would have returned — defaulting
    to a small set of handwritten field-note lines that match the synthetic
    scanned sample produced by :func:`make_sample_pdf.make_scanned_pdf`.

    Args:
        document_id: Source document id stamped on every chunk.
        document_title: Human label for the doc switcher.
        lines: Recovered text lines (one chunk per line).
    """

    DEFAULT_LINES: tuple[str, ...] = (
        "FIELD NOTES (scanned, handwritten)",
        "On the night of the 14th: saw R. Holloway downtown around 9:30 p.m.",
        "He was NOT at the Harbor Street warehouse when I drove past at 10 p.m.",
        "Will follow up with the night supervisor in the morning.",
    )

    def __init__(
        self,
        document_id: str = "exhibit-field-notes",
        document_title: str | None = "Exhibit 22: Handwritten Field Notes (scanned)",
        lines: tuple[str, ...] | None = None,
    ) -> None:
        """Store the document identity and the recovered text lines."""
        self.document_id = document_id
        self.document_title = document_title
        self.lines = lines or self.DEFAULT_LINES

    def parse(self) -> list[ParsedChunk]:
        """Produce scanned chunks with region-box quads.

        Returns:
            One :class:`ParsedChunk` per recovered line, each ``scanned=True``
            with a single region-box quad whose union equals the chunk ``bbox``.
        """
        chunks: list[ParsedChunk] = []
        y = _SCAN_TOP
        for idx, text in enumerate(self.lines):
            stripped = text.strip()
            if not stripped:
                continue
            y0 = round(y, 2)
            y1 = round(min(y + _SCAN_REGION_H, _SCAN_PAGE_H), 2)
            region = BBox(
                page=1,
                x0=_SCAN_LEFT,
                y0=y0,
                x1=_SCAN_RIGHT,
                y1=y1,
                page_width=_SCAN_PAGE_W,
                page_height=_SCAN_PAGE_H,
            )
            chunks.append(
                ParsedChunk(
                    id=f"{self.document_id}-p1-l{idx}",
                    text=stripped,
                    page=1,
                    bbox=region,
                    confidence=_scan_confidence(stripped),
                    document_id=self.document_id,
                    document_title=self.document_title,
                    scanned=True,
                    quads=[region],
                    words=[],
                    source="scanned-fallback",
                )
            )
            y = y1 + _SCAN_REGION_GAP
        logger.info(
            "Scanned fallback produced %d region chunk(s) for %s.",
            len(chunks),
            self.document_id,
        )
        return chunks
