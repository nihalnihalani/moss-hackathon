"""Tests for the depth-v2 pipeline features.

Covers the three pipeline-side features added per ``docs/depth-v2-contract.md``:

1. Multi-document: a SECOND document (the contradicting visitor-log exhibit)
   parses with its own ``documentId``/``documentTitle``; the regenerated backend
   fixture contains BOTH documents and still carries ``pdf-p12-l1``/
   ``pdf-p41-l1`` so existing demo ranking + eval gold ids resolve.
2. Quad-point highlights: every chunk emits per-line ``quads`` that hug its
   glyphs, each a valid in-page points rect, with union ~= the chunk ``bbox``.
3. Scanned/OCR path: the scanned fallback marks chunks ``scanned=true`` with
   region-box quads; the Unsiloed vision normalize path sets ``scanned=true``.

``reportlab``/``pdfplumber`` are optional dev deps; PDF-dependent tests skip
cleanly if either is unavailable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crossexam_pipeline import make_sample_pdf
from crossexam_pipeline.models import (
    DEFAULT_DOCUMENT_ID,
    BBox,
    ParsedChunk,
)
from crossexam_pipeline.pdf_parser import PdfTextParser
from crossexam_pipeline.unsiloed import ScannedFallbackParser, UnsiloedParser

reportlab = pytest.importorskip("reportlab", reason="reportlab not installed")
pdfplumber = pytest.importorskip("pdfplumber", reason="pdfplumber not installed")

_CONTRADICTION = "signed in at the downtown security desk at 9:40 p.m"

_PIPELINE_ROOT = Path(__file__).resolve().parents[1]
_CROSSEXAM_ROOT = _PIPELINE_ROOT.parent
BACKEND_FIXTURE = _CROSSEXAM_ROOT / "backend" / "fixtures" / "sample_chunks.json"


# --- Fixtures ----------------------------------------------------------------
@pytest.fixture(scope="module")
def visitor_log_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate the contradicting visitor-log exhibit once for the module."""
    out = tmp_path_factory.mktemp("vlog") / "exhibit-visitor-log.pdf"
    return make_sample_pdf.make_visitor_log_pdf(out)


@pytest.fixture(scope="module")
def visitor_log_chunks(visitor_log_pdf: Path) -> list[ParsedChunk]:
    """Parse the visitor-log exhibit with its own document identity."""
    return PdfTextParser(
        visitor_log_pdf,
        id_prefix=make_sample_pdf.VISITOR_LOG_DOC_ID,
        document_id=make_sample_pdf.VISITOR_LOG_DOC_ID,
        document_title=make_sample_pdf.VISITOR_LOG_DOC_TITLE,
    ).parse()


@pytest.fixture(scope="module")
def deposition_chunks(tmp_path_factory: pytest.TempPathFactory) -> list[ParsedChunk]:
    """Parse the primary deposition document (default identity)."""
    out = tmp_path_factory.mktemp("dep") / "sample-deposition.pdf"
    make_sample_pdf.make_pdf(out)
    return PdfTextParser(out, id_prefix="pdf").parse()


# --- Feature 1: multi-document ----------------------------------------------
def test_second_document_parses_with_its_own_document_id(
    visitor_log_chunks: list[ParsedChunk],
) -> None:
    """The exhibit parses and every chunk carries the exhibit's identity."""
    assert visitor_log_chunks
    for c in visitor_log_chunks:
        assert c.document_id == make_sample_pdf.VISITOR_LOG_DOC_ID
        assert c.document_title == make_sample_pdf.VISITOR_LOG_DOC_TITLE


def test_deposition_keeps_default_document_identity(
    deposition_chunks: list[ParsedChunk],
) -> None:
    """The deposition uses the default document id (back-compat)."""
    assert deposition_chunks
    assert all(c.document_id == DEFAULT_DOCUMENT_ID for c in deposition_chunks)


def test_exhibit_contradicts_warehouse_alibi(
    visitor_log_chunks: list[ParsedChunk],
) -> None:
    """The exhibit places Holloway downtown at 9:40 p.m. on the 14th."""
    matches = [c for c in visitor_log_chunks if _CONTRADICTION in c.text.lower()]
    assert len(matches) == 1, f"expected one contradiction chunk, got {len(matches)}"
    # Downtown at 9:40 p.m. conflicts with the deposition's warehouse-by-9:00.
    assert "downtown" in matches[0].text.lower()


def test_document_id_to_index_record() -> None:
    """``to_index_record`` emits documentId/documentTitle keys."""
    chunk = ParsedChunk(
        id="x",
        text="t",
        page=1,
        bbox=BBox(page=1, x0=72.0, y0=120.0, x1=540.0, y1=168.0),
        confidence=0.9,
        document_id="exhibit-visitor-log",
        document_title="Exhibit 14",
    )
    rec = chunk.to_index_record()
    assert rec["documentId"] == "exhibit-visitor-log"
    assert rec["documentTitle"] == "Exhibit 14"


def test_legacy_record_without_new_fields_still_validates() -> None:
    """A pre-depth-v2 record (no documentId/quads/scanned) still validates."""
    legacy = {
        "id": "pdf-p12-l1",
        "text": "warehouse on the night of the 14th",
        "page": 12,
        "bbox": {
            "page": 12,
            "x0": 72.0,
            "y0": 120.0,
            "x1": 540.0,
            "y1": 168.0,
            "page_width": 612.0,
            "page_height": 792.0,
        },
        "confidence": 0.94,
    }
    c = ParsedChunk.model_validate(legacy)
    # Backward-compatible defaults applied.
    assert c.document_id == DEFAULT_DOCUMENT_ID
    assert c.scanned is False
    assert c.quads == []


def test_record_with_camelcase_aliases_validates() -> None:
    """A record using the wire aliases (documentId/documentTitle) validates."""
    rec = {
        "id": "exhibit-visitor-log-p2-l1",
        "text": "downtown at 9:40 p.m.",
        "page": 2,
        "bbox": {
            "page": 2,
            "x0": 72.0,
            "y0": 120.0,
            "x1": 540.0,
            "y1": 168.0,
            "page_width": 612.0,
            "page_height": 792.0,
        },
        "confidence": 0.9,
        "documentId": "exhibit-visitor-log",
        "documentTitle": "Exhibit 14",
        "scanned": False,
    }
    c = ParsedChunk.model_validate(rec)
    assert c.document_id == "exhibit-visitor-log"
    assert c.document_title == "Exhibit 14"


# --- Feature 2: quad-point highlights ---------------------------------------
def _assert_quads_valid(c: ParsedChunk) -> None:
    assert c.quads, f"chunk {c.id} has no quads"
    for q in c.quads:
        # Each quad on the chunk's page and a valid in-page points rect.
        assert q.page == c.page
        assert 0.0 <= q.x0 <= q.x1 <= q.page_width
        assert 0.0 <= q.y0 <= q.y1 <= q.page_height
    # Union of quads approximately equals the chunk's union bbox.
    ux0 = min(q.x0 for q in c.quads)
    uy0 = min(q.y0 for q in c.quads)
    ux1 = max(q.x1 for q in c.quads)
    uy1 = max(q.y1 for q in c.quads)
    assert ux0 == pytest.approx(c.bbox.x0, abs=1.0)
    assert uy0 == pytest.approx(c.bbox.y0, abs=1.0)
    assert ux1 == pytest.approx(c.bbox.x1, abs=1.0)
    assert uy1 == pytest.approx(c.bbox.y1, abs=1.0)


def test_every_chunk_has_valid_quads(
    deposition_chunks: list[ParsedChunk],
    visitor_log_chunks: list[ParsedChunk],
) -> None:
    """Quads cover each chunk's lines, in-page, with union ~= bbox."""
    for c in (*deposition_chunks, *visitor_log_chunks):
        _assert_quads_valid(c)


def test_wrapped_chunk_has_multiple_line_quads(
    deposition_chunks: list[ParsedChunk],
) -> None:
    """The wrapped admission paragraph emits one quad per physical line."""
    admission = next(
        c
        for c in deposition_chunks
        if "i was at the harbor street warehouse from approximately" in c.text.lower()
    )
    # The admission wraps across multiple rendered lines.
    assert len(admission.quads) >= 2
    # Quads are top-to-bottom and do not all share the same y.
    ys = [q.y0 for q in admission.quads]
    assert ys == sorted(ys)
    assert len(set(ys)) == len(ys)


def test_quads_serialize_in_index_record(
    visitor_log_chunks: list[ParsedChunk],
) -> None:
    """``to_index_record`` emits quads as seven-key bbox dicts."""
    c = next(c for c in visitor_log_chunks if c.quads)
    rec = c.to_index_record()
    assert "quads" in rec
    for q in rec["quads"]:
        assert set(q.keys()) == {
            "page", "x0", "y0", "x1", "y1", "page_width", "page_height"
        }


# --- Feature 3: scanned / OCR path ------------------------------------------
def test_scanned_fallback_marks_scanned_with_region_quads() -> None:
    """The offline scanned fallback sets scanned=true with region-box quads."""
    chunks = ScannedFallbackParser().parse()
    assert chunks
    for c in chunks:
        assert c.scanned is True
        assert c.source == "scanned-fallback"
        # Region box quad (one per "OCR'd" line), valid in-page rect.
        assert c.quads
        for q in c.quads:
            assert q.page == c.page
            assert 0.0 <= q.x0 < q.x1 <= q.page_width
            assert 0.0 <= q.y0 < q.y1 <= q.page_height
        # No clean text layer => no word-level citations.
        assert c.words == []


def test_scanned_fallback_index_record_carries_scanned_flag() -> None:
    """A scanned chunk's index record carries scanned=true and quads."""
    rec = ScannedFallbackParser().parse()[0].to_index_record()
    assert rec["scanned"] is True
    assert rec["quads"]


def test_scanned_fallback_is_deterministic() -> None:
    """Repeated scanned parses yield byte-identical records."""
    first = [c.model_dump() for c in ScannedFallbackParser().parse()]
    second = [c.model_dump() for c in ScannedFallbackParser().parse()]
    assert first == second


def test_unsiloed_vision_normalize_sets_scanned_and_region_quads() -> None:
    """The Unsiloed VISION normalize path marks chunks scanned with region quads."""
    # Minimal vision-style result payload (OCR region boxes, no clean words).
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
    # Region boxes become the quads.
    assert len(c.quads) == 2
    for q in c.quads:
        assert q.page == 1


# --- Regenerated fixture round-trip -----------------------------------------
def test_backend_fixture_is_multidoc_and_keeps_demo_ids() -> None:
    """The committed fixture spans multiple docs and keeps the demo gold ids."""
    assert BACKEND_FIXTURE.exists(), BACKEND_FIXTURE
    records = json.loads(BACKEND_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(records, list) and records

    ids = {r["id"] for r in records}
    # Demo ranking + eval gold ids must survive.
    assert "pdf-p12-l1" in ids
    assert "pdf-p41-l1" in ids

    doc_ids = {r.get("documentId") for r in records}
    assert make_sample_pdf.DEPOSITION_DOC_ID in doc_ids
    assert make_sample_pdf.VISITOR_LOG_DOC_ID in doc_ids

    # At least one scanned source exists.
    assert any(r.get("scanned") for r in records)

    # Every record re-validates as a pipeline ParsedChunk.
    for rec in records:
        ParsedChunk.model_validate(rec)
