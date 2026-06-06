"""Build or refresh a Moss index from parsed chunks.

When Moss credentials (``MOSS_PROJECT_ID``, ``MOSS_API_KEY``,
``MOSS_INDEX_NAME``) are present, this module upserts the chunks -- with their
bbox/confidence/word-citation metadata -- into a Moss index so retrieval results
can be drawn back onto the page.

When Moss credentials are absent (the default for the offline demo), it instead
writes the backend-compatible chunk JSON to disk. The CrossExam backend mock
index loads exactly that file, so the same artifact serves both modes.

The disk write is idempotent: re-running with the same input produces a
byte-identical file (chunks are sorted by page then id, JSON is sorted/indented).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from crossexam_pipeline.models import ParsedChunk, chunks_to_index_records

logger = logging.getLogger(__name__)

ENV_PROJECT_ID = "MOSS_PROJECT_ID"
# Use the SAME var name the backend reads (crossexam_backend.config.Settings.moss_project_key)
# so a single filled .env drives both the pipeline upsert and the runtime query.
ENV_API_KEY = "MOSS_PROJECT_KEY"
ENV_INDEX_NAME = "MOSS_INDEX_NAME"

# Default location the backend mock index reads from.
_PIPELINE_ROOT = Path(__file__).resolve().parent.parent
_CROSSEXAM_ROOT = _PIPELINE_ROOT.parent
DEFAULT_CHUNKS_OUT = _CROSSEXAM_ROOT / "backend" / "fixtures" / "sample_chunks.json"


class MossConfig:
    """Resolved Moss configuration read from the environment.

    Attributes:
        project_id: Moss project id, or ``None`` if unset.
        api_key: Moss API key, or ``None`` if unset.
        index_name: Moss index name, or ``None`` if unset.
    """

    def __init__(
        self,
        project_id: str | None,
        api_key: str | None,
        index_name: str | None,
    ) -> None:
        """Store Moss configuration, coercing empty strings to ``None``."""
        self.project_id = project_id or None
        self.api_key = api_key or None
        self.index_name = index_name or None

    @classmethod
    def from_env(cls) -> MossConfig:
        """Read Moss configuration from environment variables.

        Returns:
            A :class:`MossConfig` with whatever values were present.
        """
        return cls(
            project_id=os.environ.get(ENV_PROJECT_ID, "").strip() or None,
            api_key=os.environ.get(ENV_API_KEY, "").strip() or None,
            index_name=os.environ.get(ENV_INDEX_NAME, "").strip() or None,
        )

    @property
    def is_complete(self) -> bool:
        """Whether all required Moss credentials are present."""
        return bool(self.project_id and self.api_key and self.index_name)


def _sorted_chunks(chunks: list[ParsedChunk]) -> list[ParsedChunk]:
    """Return chunks in a stable order (page, then id) for idempotency."""
    return sorted(chunks, key=lambda c: (c.page, c.id))


def write_chunks_json(chunks: list[ParsedChunk], out_path: Path | str) -> Path:
    """Write backend-compatible chunk records to disk idempotently.

    Args:
        chunks: Parsed chunks to serialize.
        out_path: Destination JSON path. Parent dirs are created.

    Returns:
        The resolved path written to.
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = chunks_to_index_records(_sorted_chunks(chunks))
    serialized = json.dumps(records, indent=2, sort_keys=True, ensure_ascii=False)
    path.write_text(serialized + "\n", encoding="utf-8")
    logger.info("Wrote %d chunk record(s) to %s", len(records), path)
    return path


def _index_payload(chunks: list[ParsedChunk], index_name: str) -> dict[str, Any]:
    """Build the Moss upsert payload from chunks.

    Each Moss document carries the chunk text plus full metadata (page, bbox,
    confidence, word-level citations) so retrieval hits can be drawn on the page.

    Args:
        chunks: Parsed chunks.
        index_name: Target Moss index name.

    Returns:
        A JSON-serializable upsert request body.
    """
    documents = []
    for c in _sorted_chunks(chunks):
        metadata: dict[str, Any] = {
            # Depth-v2 round-trip: the backend's moss_client._to_citation reads
            # documentId/documentTitle/scanned/quads back out of metadata, and
            # the frontend's isCitation REQUIRES documentId to be a string. If we
            # don't persist them here, every live citation would carry a null
            # documentId and the frontend would reject it. documentId/scanned are
            # always written; documentTitle/quads only when present.
            "documentId": c.document_id,
            "scanned": c.scanned,
            "page": c.page,
            "bbox": {
                "page": c.bbox.page,
                "x0": c.bbox.x0,
                "y0": c.bbox.y0,
                "x1": c.bbox.x1,
                "y1": c.bbox.y1,
                "page_width": c.bbox.page_width,
                "page_height": c.bbox.page_height,
            },
            "confidence": c.confidence,
            "words": [
                {
                    "text": w.text,
                    "bbox": {
                        "page": w.bbox.page,
                        "x0": w.bbox.x0,
                        "y0": w.bbox.y0,
                        "x1": w.bbox.x1,
                        "y1": w.bbox.y1,
                        "page_width": w.bbox.page_width,
                        "page_height": w.bbox.page_height,
                    },
                    "confidence": w.confidence,
                }
                for w in c.words
            ],
        }
        # documentTitle/quads are optional (single-doc back-compat); persist only
        # when present so the round-trip matches to_index_record's projection.
        if c.document_title is not None:
            metadata["documentTitle"] = c.document_title
        if c.quads:
            metadata["quads"] = [
                {
                    "page": q.page,
                    "x0": q.x0,
                    "y0": q.y0,
                    "x1": q.x1,
                    "y1": q.y1,
                    "page_width": q.page_width,
                    "page_height": q.page_height,
                }
                for q in c.quads
            ]
        documents.append(
            {
                "id": c.id,
                "text": c.text,
                # documentId is also surfaced top-level: moss_client._to_citation
                # reads it from either location, and a top-level id eases any
                # server-side documentId candidate filter.
                "documentId": c.document_id,
                "metadata": metadata,
            }
        )
    return {"index": index_name, "documents": documents}


def _upsert_to_moss(chunks: list[ParsedChunk], config: MossConfig) -> int:
    """Create/refresh a Moss index and upsert chunks into it.

    Imported lazily so the module loads without ``httpx`` when only the offline
    disk path is exercised (e.g. in CI with stdlib + pydantic only).

    Args:
        chunks: Parsed chunks to upsert.
        config: Complete Moss configuration.

    Returns:
        The number of documents upserted.

    Raises:
        RuntimeError: If the Moss API call fails.
    """
    import httpx  # local import keeps offline path dependency-free

    assert config.index_name is not None  # guaranteed by is_complete
    payload = _index_payload(chunks, config.index_name)
    base_url = os.environ.get("MOSS_BASE_URL", "https://api.moss.ai/v1").rstrip("/")
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "X-Moss-Project": str(config.project_id),
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        # Ensure the index exists (idempotent create; ignore 409 conflicts).
        create = client.post(
            f"{base_url}/indexes",
            headers=headers,
            json={"name": config.index_name},
        )
        if create.status_code not in (200, 201, 409):
            raise RuntimeError(
                f"Moss index create failed ({create.status_code}): {create.text}"
            )
        resp = client.post(
            f"{base_url}/indexes/{config.index_name}/upsert",
            headers=headers,
            json=payload,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Moss upsert failed ({resp.status_code}): {resp.text}")
    count = len(payload["documents"])
    logger.info("Upserted %d document(s) into Moss index %r", count, config.index_name)
    return count


def build_index(
    chunks: list[ParsedChunk],
    index_name: str | None = None,
    out_path: Path | str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a Moss index, or write chunks JSON for the mock when no key.

    Resolution order:

    * ``dry_run=True`` -> always write JSON to disk (never touches network).
    * Moss credentials complete -> upsert to Moss.
    * Otherwise -> write JSON to disk and log that Moss was skipped.

    Args:
        chunks: Parsed chunks to index.
        index_name: Override for the Moss index name. Falls back to
            ``MOSS_INDEX_NAME``.
        out_path: Override for the JSON output path (disk modes). Falls back to
            the backend's ``fixtures/sample_chunks.json``.
        dry_run: Force the offline disk path regardless of credentials.

    Returns:
        A summary dict describing what happened (``mode``, ``chunk_count``, and
        either ``path`` or ``index``).
    """
    config = MossConfig.from_env()
    if index_name:
        config = MossConfig(config.project_id, config.api_key, index_name)

    target_out = Path(out_path) if out_path else DEFAULT_CHUNKS_OUT

    if dry_run:
        path = write_chunks_json(chunks, target_out)
        logger.info("Dry-run: skipped Moss; wrote backend-compatible chunks to disk.")
        return {"mode": "dry-run", "chunk_count": len(chunks), "path": str(path)}

    if config.is_complete:
        count = _upsert_to_moss(chunks, config)
        return {"mode": "moss", "chunk_count": count, "index": config.index_name}

    path = write_chunks_json(chunks, target_out)
    logger.warning(
        "Moss credentials incomplete (%s/%s/%s); wrote chunks to %s for the mock "
        "index instead. Set all three env vars to upsert to Moss.",
        ENV_PROJECT_ID,
        ENV_API_KEY,
        ENV_INDEX_NAME,
        path,
    )
    return {"mode": "disk", "chunk_count": len(chunks), "path": str(path)}
