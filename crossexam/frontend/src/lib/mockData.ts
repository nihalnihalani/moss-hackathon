/**
 * mockData.ts — the scripted demo, runnable with zero backend.
 *
 * Two citations matching the 90-second kill shot:
 *   1. The answer (warehouse admission, "the night of the 14th").
 *   2. The contradiction surfaced earlier in the deposition.
 *
 * IMPORTANT — these page numbers + point bboxes MUST match the actual layout of
 * the real sample PDF served at /public/sample-deposition.pdf (and ultimately the
 * pipeline-generated citation fixture). The `page` of each bbox is the page the
 * canvas navigates to AND the page the box is drawn on, so if these don't match
 * the real document the highlight will land on blank paper.
 *
 * The sample deposition is a short transcript, so the citations point at real,
 * existing pages (not the 687/203 narrative page numbers). The "searched 912
 * pages" story is carried entirely by `pagesSearched` / DEMO_TOTAL_PAGES below —
 * it is the corpus metaphor and is intentionally decoupled from the rendered page.
 *
 * Geometry assumes a US-Letter page (612 x 792 pt). Boxes wrap a single
 * transcript line; coordinates are top-left origin, y downward (see types.ts BBox).
 * If the real PDF's pages are a different size, bbox.ts reads the true page
 * dimensions from the rendered viewport (not from these constants), so the
 * mapping stays correct — only the per-line x/y here need to track the text.
 */

import type { BBox, Citation } from '../types';

/** Intrinsic size of the placeholder/demo page, in PDF points (US Letter). */
export const DEMO_PAGE_WIDTH_PT = 612;
export const DEMO_PAGE_HEIGHT_PT = 792;

/** Corpus size, drives the "found in 912 pages" chip and the page-jump animation. */
export const DEMO_TOTAL_PAGES = 912;

/**
 * The warehouse-admission line. These coordinates are the REAL line box from the
 * generated sample-deposition.pdf (pipeline chunk `pdf-p12-l1`) — kept in sync with
 * backend/fixtures/sample_chunks.json so the mock demo and the live backend agree.
 */
const warehouseBBox: BBox = {
  page: 12,
  x0: 72.0,
  y0: 123.94,
  x1: 522.0,
  y1: 159.94,
  page_width: DEMO_PAGE_WIDTH_PT,
  page_height: DEMO_PAGE_HEIGHT_PT,
};

/**
 * The contradicting testimony line. Real line box from sample-deposition.pdf
 * (pipeline chunk `pdf-p41-l1`) — the witness recants the warehouse alibi.
 */
const contradictionBBox: BBox = {
  page: 41,
  x0: 72.0,
  y0: 141.94,
  x1: 540.0,
  y1: 177.94,
  page_width: DEMO_PAGE_WIDTH_PT,
  page_height: DEMO_PAGE_HEIGHT_PT,
};

/** The primary answer the agent speaks first. */
export const ANSWER_CITATION: Citation = {
  id: 'pdf-p12-l1',
  text: 'Q. Where were you on the night of the 14th? A. I was at the Harbor Street warehouse from approximately 9:00 p.m. until nearly midnight.',
  bbox: warehouseBBox,
  confidence: 0.94,
  latencyMs: 7,
  pagesSearched: DEMO_TOTAL_PAGES,
};

/** The follow-up contradiction surfaced later in the deposition. */
export const CONTRADICTION_CITATION: Citation = {
  id: 'pdf-p41-l1',
  text: 'On further questioning the witness stated, contrary to his earlier testimony, that on the night of the 14th he had left the area before 8:00 p.m.',
  bbox: contradictionBBox,
  confidence: 0.89,
  latencyMs: 6,
  pagesSearched: DEMO_TOTAL_PAGES,
};

/** The streamed answer caption that plays alongside the first snap. */
export const ANSWER_TRANSCRIPT =
  `Yes — on page ${warehouseBBox.page} the witness admits being at the warehouse on the night of ` +
  'the 14th, staying until nearly midnight.';

/** The user question that kicks off the demo sequence. */
export const DEMO_QUESTION =
  'Did the witness admit they were at the warehouse on the night of the 14th?';

/** The contradiction follow-up caption. */
export const CONTRADICTION_TRANSCRIPT =
  `But there is a contradiction: on page ${contradictionBBox.page}, the same witness signed the ` +
  'downtown visitor log at 9:40 p.m. that night — two miles away.';

/** Ordered list of demo citations for any UI that wants to step through them. */
export const DEMO_CITATIONS: readonly Citation[] = [ANSWER_CITATION, CONTRADICTION_CITATION];
