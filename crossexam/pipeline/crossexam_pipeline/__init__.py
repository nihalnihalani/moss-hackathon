"""CrossExam offline document pipeline.

Turns a source document (PDF) into Moss-indexable chunks carrying page numbers,
word-level citations, bounding boxes, and confidence scores. Parsing uses
Unsiloed Parse/Extract when ``UNSILOED_API_KEY`` is present, and falls back to a
deterministic, network-free parser otherwise so the demo and tests are
reproducible.

The chunk shape produced here mirrors what the CrossExam backend mock index
consumes:

    {"id": str, "text": str, "page": int,
     "bbox": {"page": int, "x0": float, "y0": float, "x1": float, "y1": float},
     "confidence": float}
"""

from __future__ import annotations

from crossexam_pipeline.models import BBox, ParsedChunk

__all__ = ["BBox", "ParsedChunk", "__version__"]

__version__ = "0.1.0"
