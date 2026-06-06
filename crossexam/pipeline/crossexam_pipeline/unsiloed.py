"""Unsiloed Parse/Extract client.

Submits a document to the Unsiloed async parse API, polls until the job
completes, and normalizes the response into
:class:`~crossexam_pipeline.models.ParsedChunk` records carrying page numbers,
bounding boxes, word-level citations, and confidence scores.

This path only runs when ``UNSILOED_API_KEY`` is set. When the key is missing,
:meth:`UnsiloedParser.from_env` raises a clear error instructing the caller to
use ``--dry-run`` (which routes to the deterministic fallback instead).

The Unsiloed response schema is treated defensively: the normalizer tolerates a
range of field names (``bbox``/``bounding_box``, ``confidence``/``score``,
fractional or absolute coordinates) so a real API rollout does not require code
changes here.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from crossexam_pipeline.models import BBox, ParsedChunk, WordCitation

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.unsiloed.ai/v1"
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
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        poll_interval: float = 3.0,
        timeout: float = 600.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
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

    @classmethod
    def from_env(cls, **kwargs: Any) -> UnsiloedParser:
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
        """Return auth headers for API requests."""
        return {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

    async def _acquire_client(self) -> tuple[httpx.AsyncClient, bool]:
        """Return an async client and whether the caller owns its lifecycle.

        Returns:
            ``(client, owns)`` where ``owns`` is ``True`` if the client was
            created here and must be closed by the caller.
        """
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(timeout=httpx.Timeout(60.0)), True

    async def parse(self, input_path: Path | str) -> list[ParsedChunk]:
        """Parse a document via Unsiloed: submit, poll, normalize.

        Args:
            input_path: Path to the source document (e.g. a PDF).

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
            job_id = await self._submit(client, path)
            logger.info("Submitted Unsiloed parse job %s for %s", job_id, path.name)
            result = await self._poll(client, job_id)
            chunks = self.normalize(result)
            logger.info("Unsiloed job %s yielded %d chunk(s).", job_id, len(chunks))
            return chunks
        finally:
            if owns:
                await client.aclose()

    async def _submit(self, client: httpx.AsyncClient, path: Path) -> str:
        """Upload the document and return the created job id.

        Args:
            client: Async HTTP client.
            path: Document to upload.

        Returns:
            The Unsiloed job identifier.

        Raises:
            UnsiloedError: If the API does not return a job id.
        """
        files = {"file": (path.name, path.read_bytes(), "application/pdf")}
        data = {"extract_bbox": "true", "extract_words": "true"}
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
            raise UnsiloedError(f"Unsiloed /parse returned no job id: {payload!r}")
        return str(job_id)

    async def _poll(self, client: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
        """Poll a job until it completes, fails, or times out.

        Args:
            client: Async HTTP client.
            job_id: Job to poll.

        Returns:
            The completed job's result payload.

        Raises:
            UnsiloedError: If the job fails or polling times out.
        """
        elapsed = 0.0
        while elapsed <= self.timeout:
            resp = await client.get(
                f"{self.base_url}/parse/{job_id}", headers=self._headers()
            )
            resp.raise_for_status()
            payload = resp.json()
            status = str(payload.get("status", "")).lower()
            if status in {"completed", "succeeded", "done"}:
                return payload.get("result", payload)
            if status in {"failed", "error", "cancelled"}:
                raise UnsiloedError(
                    f"Unsiloed job {job_id} ended with status {status!r}: "
                    f"{payload.get('error', 'no detail')}"
                )
            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval
        raise UnsiloedError(
            f"Unsiloed job {job_id} did not complete within {self.timeout}s."
        )

    # --- Normalization -------------------------------------------------------

    @staticmethod
    def _coerce_bbox(raw: dict[str, Any], page: int) -> BBox:
        """Coerce a raw bbox dict into a normalized :class:`BBox`.

        Accepts ``x0/y0/x1/y1`` or ``left/top/right/bottom`` keys, and either
        fractional ``[0, 1]`` coordinates or absolute coordinates accompanied by
        ``page_width``/``page_height`` for normalization.

        Args:
            raw: Raw bounding-box mapping from the API.
            page: 1-based page number to stamp on the box.

        Returns:
            A normalized :class:`BBox`.
        """
        x0 = float(raw.get("x0", raw.get("left", 0.0)))
        y0 = float(raw.get("y0", raw.get("top", 0.0)))
        x1 = float(raw.get("x1", raw.get("right", x0)))
        y1 = float(raw.get("y1", raw.get("bottom", y0)))

        page_w = float(raw.get("page_width", 0.0) or 0.0)
        page_h = float(raw.get("page_height", 0.0) or 0.0)
        # Normalize absolute pixel/point coordinates if page dims provided or if
        # any coordinate clearly exceeds 1.0.
        if page_w > 0 and page_h > 0 and max(x0, x1) > 1.0:
            x0, x1 = x0 / page_w, x1 / page_w
            y0, y1 = y0 / page_h, y1 / page_h

        clamp = lambda v: max(0.0, min(1.0, v))  # noqa: E731
        x0, y0, x1, y1 = clamp(x0), clamp(y0), clamp(x1), clamp(y1)
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        return BBox(
            page=page,
            x0=round(x0, 4),
            y0=round(y0, 4),
            x1=round(x1, 4),
            y1=round(y1, 4),
        )

    @classmethod
    def _coerce_words(cls, raw_words: list[dict[str, Any]], page: int) -> list[WordCitation]:
        """Coerce raw per-word entries into :class:`WordCitation` records.

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
    def normalize(cls, result: dict[str, Any]) -> list[ParsedChunk]:
        """Normalize a completed Unsiloed result into :class:`ParsedChunk`.

        Args:
            result: The job's result payload. Expected to contain a ``chunks``
                (or ``blocks``/``elements``) list, each with ``text``, ``page``,
                a bounding box, an optional ``words`` list, and a confidence.

        Returns:
            Normalized chunks, skipping any entry without text.

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
        for idx, rc in enumerate(raw_chunks):
            text = str(rc.get("text", rc.get("content", ""))).strip()
            if not text:
                continue
            page = int(rc.get("page", rc.get("page_number", 1)))
            bbox_raw = rc.get("bbox") or rc.get("bounding_box") or {}
            bbox = cls._coerce_bbox(bbox_raw, page)
            words = cls._coerce_words(list(rc.get("words", [])), page)
            conf = rc.get("confidence", rc.get("score"))
            if conf is None and words:
                conf = sum(w.confidence for w in words) / len(words)
            confidence = max(0.0, min(1.0, float(conf if conf is not None else 1.0)))
            chunk_id = str(rc.get("id", f"unsiloed-{idx}-p{page}"))
            chunks.append(
                ParsedChunk(
                    id=chunk_id,
                    text=text,
                    page=page,
                    bbox=bbox,
                    confidence=round(confidence, 4),
                    words=words,
                    source="unsiloed",
                )
            )
        return chunks
