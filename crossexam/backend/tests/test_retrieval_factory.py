"""Tests for retrieval factory fallback behavior."""

from __future__ import annotations

import json
from pathlib import Path

from crossexam_backend.config import Settings
from crossexam_backend.models import RetrievalResult
from crossexam_backend.retrieval.factory import MossWithMockFallback


def _record(chunk_id: str, text: str, document_id: str = "doc-1") -> dict[str, object]:
    """Build one backend-compatible fixture record."""
    return {
        "id": chunk_id,
        "text": text,
        "page": 1,
        "documentId": document_id,
        "bbox": {
            "page": 1,
            "x0": 72.0,
            "y0": 72.0,
            "x1": 300.0,
            "y1": 96.0,
            "page_width": 612.0,
            "page_height": 792.0,
        },
        "confidence": 1.0,
    }


def _write_fixture(path: Path, records: list[dict[str, object]]) -> None:
    """Write fixture records to ``path``."""
    path.write_text(json.dumps(records), encoding="utf-8")


class _BrokenMoss:
    """Primary index that behaves like a broken Moss integration."""

    is_loaded = False
    document_ids: list[str] = []

    async def prewarm(self) -> None:
        return None

    async def query(
        self, text: str, top_k: int = 5, alpha: float = 0.8
    ) -> RetrievalResult:
        raise RuntimeError("moss unavailable")


async def test_moss_fallback_uses_and_reloads_fixture(tmp_path: Path) -> None:
    """A broken Moss primary falls back to the fixture and reloads on mtime."""
    fixture = tmp_path / "chunks.json"
    _write_fixture(
        fixture,
        [_record("chunk-a", "The alpha warehouse admission appears here.")],
    )
    settings = Settings(
        use_mocks=False,
        moss_project_id="proj",
        moss_project_key="key",
        mock_fixture_path=str(fixture),
        _env_file=None,  # type: ignore[call-arg]
    )
    index = MossWithMockFallback(_BrokenMoss(), settings)  # type: ignore[arg-type]

    await index.prewarm()
    first = await index.query("alpha warehouse", top_k=1)
    assert first.citations[0].chunk.id == "chunk-a"

    _write_fixture(
        fixture,
        [
            _record("chunk-a", "The alpha warehouse admission appears here."),
            _record("chunk-b", "The beta loading dock timeline appears here."),
        ],
    )
    second = await index.query("beta loading dock", top_k=1)
    assert second.citations[0].chunk.id == "chunk-b"
