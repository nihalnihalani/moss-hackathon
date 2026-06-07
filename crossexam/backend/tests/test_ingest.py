"""Tests for PDF ingest helpers."""

from __future__ import annotations

from typing import Any

from crossexam_backend.ingest import _coalesce_transcript_records


def _transcript_record(chunk_id: str, page: int, text: str) -> dict[str, Any]:
    y0 = 100.0 + (int(chunk_id.rsplit("-", 1)[-1]) % 20) * 12.0
    bbox = {
        "page": page,
        "x0": 72.0,
        "y0": y0,
        "x1": 520.0,
        "y1": y0 + 10.0,
        "page_width": 612.0,
        "page_height": 792.0,
    }
    return {
        "id": chunk_id,
        "text": text,
        "page": page,
        "bbox": bbox,
        "confidence": 0.93,
        "documentId": "doc-test",
        "documentTitle": "raw.pdf",
        "quads": [bbox],
        "scanned": False,
    }


def test_transcript_records_coalesce_into_answer_sized_chunks() -> None:
    """Legal transcript line chunks should become useful Q/A turns."""
    records: list[dict[str, Any]] = []
    for i in range(1, 131):
        page = 1 if i <= 65 else 2
        line_no = ((i - 1) % 25) + 1
        if i % 10 == 1:
            body = f"Q Please describe product development item {i}."
        elif i % 10 == 2:
            body = "A Sunny Balwani was overseeing the lab and software side."
        else:
            body = f"continuation detail {i}"
        records.append(_transcript_record(f"doc-test-{i}", page, f"{line_no} {body}"))

    coalesced = _coalesce_transcript_records(
        records,
        id_prefix="doc-test",
        document_title="Holmes transcript.pdf",
    )

    assert len(coalesced) < len(records)
    assert all(record["documentId"] == "doc-test" for record in coalesced)
    assert all(
        record["documentTitle"] == "Holmes transcript.pdf" for record in coalesced
    )
    assert all(not record["text"].split(" ", 1)[0].isdigit() for record in coalesced)
    assert any(
        "Q Please describe product development item" in record["text"]
        and "A Sunny Balwani was overseeing the lab and software side."
        in record["text"]
        for record in coalesced
    )
    matching = next(
        record
        for record in coalesced
        if "A Sunny Balwani was overseeing the lab and software side."
        in record["text"]
    )
    assert matching["quads"]
    assert matching["quadTexts"]
    assert len(matching["quadTexts"]) == len(matching["quads"])
    assert any("A Sunny Balwani" in text for text in matching["quadTexts"])
    assert all(not text.split(" ", 1)[0].isdigit() for text in matching["quadTexts"])
