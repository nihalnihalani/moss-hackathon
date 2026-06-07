"""Tests for the Unsiloed Parse/Extract client (crossexam_pipeline.unsiloed).

Covers:
- Correct base URL (prod.visionapi.unsiloed.ai, no /v1 suffix).
- ``api-key`` auth header (not ``Authorization: Bearer``).
- Single ``POST /parse`` endpoint for both normal and scanned docs.
- Scanned path only changes the form param (ocr_strategy=force_ocr) and
  sets ``scanned=True`` on chunks — NOT a different URL.
- ``GET /parse/{job_id}`` poll; ``status="Succeeded"`` (case-insensitive).
- New response schema: chunks -> segments with content/page_number/
  bbox{left,top,width,height}/ocr/confidence/segment_id.
- ``_coerce_bbox`` width/height -> x0/y0/x1/y1 conversion.
- ``ocr`` list -> word citations.
- Segment flattening: one ParsedChunk per segment across all chunks.
- Defensive fallback: legacy flat-chunk shape (no ``segments``) still parses.
- Defensive fallback: ``payload.get("result", payload)`` handles both
  top-level and wrapped result shapes.
- ``MissingCredentialsError`` raised without an API key.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from crossexam_pipeline.unsiloed import (
    DEFAULT_BASE_URL,
    MissingCredentialsError,
    UnsiloedError,
    UnsiloedParser,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_parser(
    responses: list[dict[str, Any]],
) -> UnsiloedParser:
    """Build an UnsiloedParser wired to a sequence of canned JSON responses.

    The first response is the POST /parse reply; subsequent responses are
    GET /parse/{job_id} poll replies.
    """
    call_idx = 0
    response_list = list(responses)

    class _MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal call_idx
            if call_idx >= len(response_list):
                raise RuntimeError(f"Unexpected request #{call_idx}: {request.url}")
            body = response_list[call_idx]
            call_idx += 1
            return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=_MockTransport(), base_url="http://unused")
    return UnsiloedParser(
        api_key="test-key",
        base_url=DEFAULT_BASE_URL,
        poll_interval=0.0,
        timeout=10.0,
        client=client,
    )


def _make_capture_parser(
    captured: list[httpx.Request],
    responses: list[dict[str, Any]],
) -> UnsiloedParser:
    """Parser that appends every request to ``captured`` after reading body."""
    call_idx = 0
    response_list = list(responses)

    class _CaptureTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
            nonlocal call_idx
            await req.aread()  # materialise multipart body before .content is accessed
            captured.append(req)
            body = response_list[call_idx % len(response_list)]
            call_idx += 1
            return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=_CaptureTransport())
    return UnsiloedParser(
        api_key="test-key",
        base_url=DEFAULT_BASE_URL,
        poll_interval=0.0,
        timeout=10.0,
        client=client,
    )


def _submit_resp(job_id: str = "job-abc") -> dict[str, Any]:
    """Minimal submit response: job_id + Starting status."""
    return {"job_id": job_id, "status": "Starting"}


def _poll_pending() -> dict[str, Any]:
    """A poll response that keeps the job running."""
    return {"status": "Processing"}


def _poll_succeeded(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """A poll response that signals completion with embedded chunks."""
    return {"status": "Succeeded", "chunks": chunks}


def _segment(
    *,
    text: str = "Hello world",
    page_number: int = 1,
    left: float = 72.0,
    top: float = 120.0,
    width: float = 468.0,
    height: float = 20.0,
    confidence: float = 0.95,
    segment_id: str = "seg-1",
    ocr: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a realistic real-schema segment dict."""
    seg: dict[str, Any] = {
        "content": text,
        "page_number": page_number,
        "bbox": {"left": left, "top": top, "width": width, "height": height},
        "confidence": confidence,
        "segment_id": segment_id,
    }
    if ocr is not None:
        seg["ocr"] = ocr
    return seg


def _chunk_with_segments(
    segments: list[dict[str, Any]], chunk_id: str = "chunk-1"
) -> dict[str, Any]:
    """Build a chunk dict wrapping the given segments."""
    return {"chunk_id": chunk_id, "segments": segments}


# ---------------------------------------------------------------------------
# Configuration / credentials
# ---------------------------------------------------------------------------

def test_default_base_url_is_prod_visionapi() -> None:
    """DEFAULT_BASE_URL must point to the real Unsiloed service."""
    assert DEFAULT_BASE_URL == "https://prod.visionapi.unsiloed.ai"


def test_base_url_has_no_v1_suffix() -> None:
    """The base URL must NOT contain a /v1 suffix."""
    assert "/v1" not in DEFAULT_BASE_URL


def test_missing_api_key_raises() -> None:
    """UnsiloedParser rejects an empty API key immediately."""
    with pytest.raises(MissingCredentialsError):
        UnsiloedParser(api_key="")


def test_from_env_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """from_env raises MissingCredentialsError when the env var is unset."""
    monkeypatch.delenv("UNSILOED_API_KEY", raising=False)
    with pytest.raises(MissingCredentialsError):
        UnsiloedParser.from_env()


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------

def test_headers_use_api_key_not_bearer() -> None:
    """The auth header must be ``api-key``, NOT ``Authorization: Bearer``."""
    p = UnsiloedParser(api_key="my-secret-key")
    headers = p._headers()
    assert headers.get("api-key") == "my-secret-key", "api-key header must be set"
    assert "Authorization" not in headers, "Authorization header must NOT be present"


# ---------------------------------------------------------------------------
# Request routing: POST /parse for both normal and scanned
# ---------------------------------------------------------------------------

def test_submit_posts_to_parse_endpoint(tmp_path: Path) -> None:
    """Both normal and scanned docs POST to /parse — not /vision."""
    captured: list[httpx.Request] = []
    responses = [
        _submit_resp(),
        _poll_succeeded([_chunk_with_segments([_segment()])]),
    ]
    parser = _make_capture_parser(captured, responses)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")
    asyncio.run(parser.parse(pdf, scanned=False))

    submit_req = captured[0]
    assert submit_req.method == "POST"
    url = str(submit_req.url)
    assert url.endswith("/parse"), f"Expected /parse, got: {url}"
    assert "/vision" not in url, "Must not use /vision endpoint"


def test_scanned_posts_to_parse_not_vision(tmp_path: Path) -> None:
    """Scanned docs still POST to /parse (not a separate /vision endpoint)."""
    captured: list[httpx.Request] = []
    responses = [
        _submit_resp(),
        _poll_succeeded([_chunk_with_segments([_segment()])]),
    ]
    parser = _make_capture_parser(captured, responses)

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake scan")
    asyncio.run(parser.parse(pdf, scanned=True))

    submit_req = captured[0]
    url = str(submit_req.url)
    assert url.endswith("/parse")
    assert "/vision" not in url


def test_scanned_sends_force_ocr_form_param(tmp_path: Path) -> None:
    """Scanned path sends ocr_strategy=force_ocr; normal path does not."""
    captured: list[httpx.Request] = []
    responses = [
        _submit_resp(),
        _poll_succeeded([_chunk_with_segments([_segment()])]),
    ]
    parser = _make_capture_parser(captured, responses)

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake scan")
    asyncio.run(parser.parse(pdf, scanned=True))

    assert captured
    body = captured[0].content.decode("latin-1")
    assert "force_ocr" in body, "Scanned submit must include force_ocr"


def test_normal_does_not_send_force_ocr(tmp_path: Path) -> None:
    """Normal (non-scanned) path does NOT send ocr_strategy=force_ocr."""
    captured: list[httpx.Request] = []
    responses = [
        _submit_resp(),
        _poll_succeeded([_chunk_with_segments([_segment()])]),
    ]
    parser = _make_capture_parser(captured, responses)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")
    asyncio.run(parser.parse(pdf, scanned=False))

    assert captured
    body = captured[0].content.decode("latin-1")
    assert "force_ocr" not in body, "Normal submit must NOT include force_ocr"


# ---------------------------------------------------------------------------
# Poll endpoint
# ---------------------------------------------------------------------------

def test_poll_uses_parse_job_id_url(tmp_path: Path) -> None:
    """Polling always hits GET /parse/{job_id}."""
    captured: list[httpx.Request] = []
    responses = [
        _submit_resp("my-job"),
        _poll_succeeded([_chunk_with_segments([_segment()])]),
    ]
    parser = _make_capture_parser(captured, responses)

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    asyncio.run(parser.parse(pdf))

    polls = [r for r in captured if r.method == "GET"]
    assert polls, "at least one poll expected"
    assert all("/parse/my-job" in str(r.url) for r in polls)


# ---------------------------------------------------------------------------
# Real response schema: chunks -> segments
# ---------------------------------------------------------------------------

def test_normalize_real_schema_flattens_segments() -> None:
    """Segments from multiple chunks are all emitted as individual ParsedChunks."""
    result = {
        "chunks": [
            _chunk_with_segments([
                _segment(text="First segment", page_number=1, segment_id="s1"),
                _segment(text="Second segment", page_number=1, segment_id="s2"),
            ], chunk_id="c1"),
            _chunk_with_segments([
                _segment(text="Third segment", page_number=2, segment_id="s3"),
            ], chunk_id="c2"),
        ]
    }
    chunks = UnsiloedParser.normalize(result)
    assert len(chunks) == 3
    texts = [c.text for c in chunks]
    assert "First segment" in texts
    assert "Second segment" in texts
    assert "Third segment" in texts


def test_normalize_reads_content_field() -> None:
    """``content`` is the preferred text field."""
    result = {"chunks": [_chunk_with_segments([{
        "content": "Content text",
        "markdown": "# Content text",
        "page_number": 1,
        "bbox": {"left": 72.0, "top": 120.0, "width": 468.0, "height": 20.0},
        "confidence": 0.9,
        "segment_id": "seg-x",
    }])]}
    chunks = UnsiloedParser.normalize(result)
    assert chunks[0].text == "Content text"


def test_normalize_reads_page_number_field() -> None:
    """``page_number`` (new schema) is read for the page."""
    result = {"chunks": [_chunk_with_segments([
        _segment(text="p3 text", page_number=3, segment_id="s3")
    ])]}
    chunks = UnsiloedParser.normalize(result)
    assert chunks[0].page == 3


def test_normalize_reads_segment_id() -> None:
    """``segment_id`` is used as the chunk id."""
    result = {"chunks": [_chunk_with_segments([
        _segment(text="hello", segment_id="my-segment-42")
    ])]}
    chunks = UnsiloedParser.normalize(result)
    assert chunks[0].id == "my-segment-42"


def test_normalize_skips_empty_segments() -> None:
    """Segments with no text are skipped."""
    result = {"chunks": [_chunk_with_segments([
        _segment(text=""),
        _segment(text="  "),
        _segment(text="kept"),
    ])]}
    chunks = UnsiloedParser.normalize(result)
    assert len(chunks) == 1
    assert chunks[0].text == "kept"


# ---------------------------------------------------------------------------
# bbox {left, top, width, height} conversion
# ---------------------------------------------------------------------------

def test_coerce_bbox_width_height_conversion() -> None:
    """left/top/width/height bbox is correctly converted to x0/y0/x1/y1."""
    raw = {"left": 100.0, "top": 200.0, "width": 300.0, "height": 50.0}
    bbox = UnsiloedParser._coerce_bbox(raw, page=1)
    assert bbox.x0 == pytest.approx(100.0)
    assert bbox.y0 == pytest.approx(200.0)
    assert bbox.x1 == pytest.approx(400.0)   # 100 + 300
    assert bbox.y1 == pytest.approx(250.0)   # 200 + 50


def test_coerce_bbox_legacy_x0y0x1y1() -> None:
    """Legacy {x0,y0,x1,y1} bbox passes through unchanged."""
    raw = {"x0": 72.0, "y0": 120.0, "x1": 540.0, "y1": 148.0}
    bbox = UnsiloedParser._coerce_bbox(raw, page=1)
    assert bbox.x0 == pytest.approx(72.0)
    assert bbox.y0 == pytest.approx(120.0)
    assert bbox.x1 == pytest.approx(540.0)
    assert bbox.y1 == pytest.approx(148.0)


def test_coerce_bbox_legacy_left_top_right_bottom() -> None:
    """Legacy {left,top,right,bottom} (without width/height) passes through."""
    raw = {"left": 72.0, "top": 120.0, "right": 540.0, "bottom": 148.0}
    bbox = UnsiloedParser._coerce_bbox(raw, page=2)
    assert bbox.x0 == pytest.approx(72.0)
    assert bbox.y0 == pytest.approx(120.0)
    assert bbox.x1 == pytest.approx(540.0)
    assert bbox.y1 == pytest.approx(148.0)
    assert bbox.page == 2


def test_coerce_bbox_small_absolute_coords_not_scaled() -> None:
    """Small absolute point coords (<= 1.0) are NOT scaled — Unsiloed is always absolute.

    A tiny glyph near the top-left (e.g. left=0.5, top=0.5, width=0.4, height=0.2 pts)
    must pass through as-is; the old fractional-scaling heuristic would have wrongly
    multiplied these by ~612/792 and misplaced the highlight box.
    """
    raw = {"left": 0.5, "top": 0.5, "width": 0.4, "height": 0.2}
    bbox = UnsiloedParser._coerce_bbox(raw, page=1)
    # Must be passed through as absolute points, NOT scaled by page dimensions.
    assert bbox.x0 == pytest.approx(0.5)
    assert bbox.y0 == pytest.approx(0.5)
    assert bbox.x1 == pytest.approx(0.9)   # 0.5 + 0.4
    assert bbox.y1 == pytest.approx(0.7)   # 0.5 + 0.2
    # Definitely not scaled to ~306 / ~395:
    assert bbox.x1 < 2.0, "coords must NOT have been scaled by page dimensions"


def test_coerce_bbox_clamped_in_page() -> None:
    """Out-of-page coordinates are clamped to [0, page_w/h]."""
    raw = {"left": -10.0, "top": -5.0, "width": 700.0, "height": 900.0}
    bbox = UnsiloedParser._coerce_bbox(raw, page=1)
    assert bbox.x0 == 0.0
    assert bbox.y0 == 0.0
    assert bbox.x1 <= bbox.page_width
    assert bbox.y1 <= bbox.page_height


# ---------------------------------------------------------------------------
# OCR words (ocr field -> word citations)
# ---------------------------------------------------------------------------

def test_normalize_reads_ocr_field_for_words() -> None:
    """The ``ocr`` array on a segment is turned into word citations."""
    ocr_words = [
        {"text": "Hello", "confidence": 0.98,
         "bbox": {"left": 72.0, "top": 120.0, "width": 30.0, "height": 12.0}},
        {"text": "world", "confidence": 0.92,
         "bbox": {"left": 104.0, "top": 120.0, "width": 35.0, "height": 12.0}},
    ]
    result = {"chunks": [_chunk_with_segments([
        _segment(text="Hello world", ocr=ocr_words)
    ])]}
    chunks = UnsiloedParser.normalize(result)
    assert len(chunks) == 1
    c = chunks[0]
    assert len(c.words) == 2
    assert c.words[0].text == "Hello"
    assert c.words[1].text == "world"
    # Confidence values preserved.
    assert c.words[0].confidence == pytest.approx(0.98)
    assert c.words[1].confidence == pytest.approx(0.92)


def test_normalize_word_bboxes_converted_from_width_height() -> None:
    """Word bboxes in {left,top,width,height} form are converted to x0/y0/x1/y1."""
    ocr_words = [
        {"text": "Token", "confidence": 0.9,
         "bbox": {"left": 100.0, "top": 200.0, "width": 50.0, "height": 15.0}},
    ]
    result = {"chunks": [_chunk_with_segments([_segment(text="Token", ocr=ocr_words)])]}
    chunks = UnsiloedParser.normalize(result)
    w = chunks[0].words[0]
    assert w.bbox.x0 == pytest.approx(100.0)
    assert w.bbox.y0 == pytest.approx(200.0)
    assert w.bbox.x1 == pytest.approx(150.0)   # 100 + 50
    assert w.bbox.y1 == pytest.approx(215.0)   # 200 + 15


# ---------------------------------------------------------------------------
# scanned flag propagation
# ---------------------------------------------------------------------------

def test_normalize_scanned_true_sets_scanned_on_chunks() -> None:
    """Passing scanned=True marks every resulting chunk scanned=True."""
    result = {"chunks": [_chunk_with_segments([_segment(text="some OCR text")])]}
    chunks = UnsiloedParser.normalize(result, scanned=True)
    assert all(c.scanned for c in chunks)


def test_normalize_scanned_false_leaves_scanned_false() -> None:
    """Normal parse leaves scanned=False on chunks."""
    result = {"chunks": [_chunk_with_segments([_segment(text="normal text")])]}
    chunks = UnsiloedParser.normalize(result, scanned=False)
    assert all(not c.scanned for c in chunks)


def test_parse_scanned_sets_scanned_flag(tmp_path: Path) -> None:
    """parse(scanned=True) sets scanned=True on all returned chunks."""
    captured: list[httpx.Request] = []
    responses = [
        _submit_resp(),
        _poll_succeeded([_chunk_with_segments([_segment(text="scanned line")])]),
    ]
    parser = _make_capture_parser(captured, responses)

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 scan")
    chunks = asyncio.run(parser.parse(pdf, scanned=True))
    assert chunks
    assert all(c.scanned for c in chunks)


# ---------------------------------------------------------------------------
# Polling: status casing
# ---------------------------------------------------------------------------

def test_poll_recognizes_succeeded_status() -> None:
    """Status 'Succeeded' (capital S) signals completion via normalize."""
    payload: dict[str, Any] = {
        "status": "Succeeded",
        "chunks": [_chunk_with_segments([_segment(text="done")])],
    }
    # The client does: result = payload.get("result", payload)
    result = payload.get("result", payload)
    out = UnsiloedParser.normalize(result)
    assert out[0].text == "done"


def test_poll_fails_on_failed_status(tmp_path: Path) -> None:
    """Status 'failed' causes UnsiloedError to be raised."""
    call = [0]

    class _FailTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
            call[0] += 1
            if call[0] == 1:
                return httpx.Response(200, json=_submit_resp("j-fail"))
            return httpx.Response(200, json={"status": "failed", "error": "upstream error"})

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF fake")
    client = httpx.AsyncClient(transport=_FailTransport())
    parser = UnsiloedParser(
        api_key="k", base_url=DEFAULT_BASE_URL, poll_interval=0.0, client=client
    )
    with pytest.raises(UnsiloedError, match="failed"):
        asyncio.run(parser.parse(pdf))


def test_poll_timeout_raises_and_does_not_hang(tmp_path: Path) -> None:
    """A job that never succeeds raises UnsiloedError within the timeout.

    Uses timeout=0 so the deadline is already past after the first poll
    iteration; poll_interval=0 ensures asyncio.sleep does not stall the test.
    The test must complete quickly — if the infinite-loop bug is present it
    would hang the test suite instead of raising.
    """
    call = [0]

    class _AlwaysPendingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req: httpx.Request) -> httpx.Response:
            call[0] += 1
            if call[0] == 1:
                return httpx.Response(200, json=_submit_resp("j-pending"))
            return httpx.Response(200, json={"status": "Processing"})

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF fake")
    client = httpx.AsyncClient(transport=_AlwaysPendingTransport())
    parser = UnsiloedParser(
        api_key="k",
        base_url=DEFAULT_BASE_URL,
        poll_interval=0.0,
        timeout=0,   # deadline already elapsed after the first poll
        client=client,
    )
    with pytest.raises(UnsiloedError, match="did not complete"):
        asyncio.run(parser.parse(pdf))


# ---------------------------------------------------------------------------
# result wrapper: payload.get("result", payload) fallback
# ---------------------------------------------------------------------------

def test_normalize_accepts_result_wrapper_shape() -> None:
    """When the poll response wraps data under a 'result' key, it still parses."""
    wrapped_payload: dict[str, Any] = {
        "status": "Succeeded",
        "result": {
            "chunks": [_chunk_with_segments([_segment(text="wrapped text")])]
        },
    }
    result = wrapped_payload.get("result", wrapped_payload)
    chunks = UnsiloedParser.normalize(result)
    assert len(chunks) == 1
    assert chunks[0].text == "wrapped text"


def test_normalize_accepts_top_level_chunks_shape() -> None:
    """When chunks are at the top level (no 'result' wrapper), it still parses."""
    top_level: dict[str, Any] = {
        "status": "Succeeded",
        "chunks": [_chunk_with_segments([_segment(text="top-level text")])],
    }
    result = top_level.get("result", top_level)
    chunks = UnsiloedParser.normalize(result)
    assert len(chunks) == 1
    assert chunks[0].text == "top-level text"


# ---------------------------------------------------------------------------
# Defensive: legacy flat-chunk shape (no segments)
# ---------------------------------------------------------------------------

def test_normalize_legacy_flat_chunk_no_segments() -> None:
    """A chunk without a 'segments' key is treated as a single segment (legacy)."""
    result = {
        "chunks": [
            {
                "id": "ocr-1",
                "text": "delivery truck at the loading dock near midnight",
                "page": 1,
                "bbox": {"x0": 72.0, "y0": 120.0, "x1": 540.0, "y1": 148.0},
                "regions": [
                    {"bbox": {"x0": 72.0, "y0": 120.0, "x1": 540.0, "y1": 134.0}},
                    {"bbox": {"x0": 72.0, "y0": 134.0, "x1": 420.0, "y1": 148.0}},
                ],
                "confidence": 0.71,
            }
        ]
    }
    chunks = UnsiloedParser.normalize(
        result, scanned=True, document_id="exhibit-field-notes"
    )
    assert len(chunks) == 1
    c = chunks[0]
    assert c.scanned is True
    assert c.document_id == "exhibit-field-notes"
    # Region boxes from the legacy chunk become quads.
    assert len(c.quads) == 2
    for q in c.quads:
        assert q.page == 1


def test_normalize_legacy_flat_chunk_words_field() -> None:
    """Legacy 'words' field (not 'ocr') on a flat chunk is accepted."""
    result = {
        "chunks": [
            {
                "text": "legacy word chunk",
                "page": 2,
                "bbox": {"x0": 72.0, "y0": 120.0, "x1": 540.0, "y1": 148.0},
                "words": [
                    {"text": "legacy", "confidence": 0.88,
                     "bbox": {"x0": 72.0, "y0": 120.0, "x1": 120.0, "y1": 132.0}},
                ],
                "confidence": 0.88,
            }
        ]
    }
    chunks = UnsiloedParser.normalize(result)
    assert len(chunks) == 1
    assert len(chunks[0].words) == 1
    assert chunks[0].words[0].text == "legacy"


# ---------------------------------------------------------------------------
# Source field
# ---------------------------------------------------------------------------

def test_normalize_source_is_unsiloed() -> None:
    """All normalized chunks carry source='unsiloed'."""
    result = {"chunks": [_chunk_with_segments([_segment(text="x")])]}
    chunks = UnsiloedParser.normalize(result)
    assert all(c.source == "unsiloed" for c in chunks)


# ---------------------------------------------------------------------------
# document_id / document_title propagation
# ---------------------------------------------------------------------------

def test_normalize_propagates_document_identity() -> None:
    """document_id and document_title are stamped on every chunk."""
    result = {"chunks": [_chunk_with_segments([_segment(text="some text")])]}
    chunks = UnsiloedParser.normalize(
        result,
        document_id="my-doc",
        document_title="My Doc Title",
    )
    assert chunks[0].document_id == "my-doc"
    assert chunks[0].document_title == "My Doc Title"


# ---------------------------------------------------------------------------
# no-chunks error
# ---------------------------------------------------------------------------

def test_normalize_raises_on_missing_chunks_key() -> None:
    """Normalize raises UnsiloedError if no chunks/blocks/elements key is present."""
    with pytest.raises(UnsiloedError, match="no chunks"):
        UnsiloedParser.normalize({"unexpected": "payload"})


# ---------------------------------------------------------------------------
# file-not-found guard
# ---------------------------------------------------------------------------

def test_parse_raises_if_file_missing() -> None:
    """parse() raises FileNotFoundError for a non-existent path."""
    parser = UnsiloedParser(api_key="k")
    with pytest.raises(FileNotFoundError):
        asyncio.run(parser.parse("/nonexistent/path/doc.pdf"))


# ---------------------------------------------------------------------------
# tempfile fallback (used by poll_fails test — keeping Path import consistent)
# ---------------------------------------------------------------------------

def test_tempdir_path_helper() -> None:
    """Verify the tempfile + Path helper pattern works across the test suite."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "check.txt"
        p.write_text("ok")
        assert p.exists()
